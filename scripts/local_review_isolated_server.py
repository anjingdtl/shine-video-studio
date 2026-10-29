"""Loopback review supervisor: one owned model process per request on Windows.

The wrapped executor is the same hash-verified local review server.  Results
are returned only after its child exits; no request is replayed on uncertainty.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import secrets
import sys
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from shine.comfyops.isolated_stage import run_isolated  # noqa: E402


class ReviewSealed(Exception):
    pass


def _forward_once(manifest, packages, method, path, body, content_type):
    """Run entirely in the supervised child; do not log request or response."""
    sys.path.insert(0, packages)
    sys.path.insert(0, str(REPO / "scripts"))
    from local_review_server import build_app

    app = build_app(manifest)

    async def invoke():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://local-review") as client:
            identity = await client.get("/identity")
            health = await client.get("/health")
            if identity.status_code != 200 or health.status_code != 200:
                raise ValueError("ISOLATED_EXECUTOR_IDENTITY_UNAVAILABLE")
            response = await client.request(method, path, content=body,
                                            headers={"Content-Type": content_type} if content_type else {})
            return {"status": response.status_code, "body": response.content,
                    "content_type": response.headers.get("content-type", "application/json"),
                    "health": health.json(), "identity": identity.json()}

    return asyncio.run(invoke())


def _supervised_runtime(inner):
    files = [Path(__file__), REPO / "backend/shine/comfyops/isolated_stage.py"]
    payload = {"protocol": "windows-isolated-review-v1", "inner": inner,
               "files": [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _live_identity(manifest, packages):
    """Rehash the executor inputs without importing or starting a model."""
    for path in (str(packages), str(REPO / "scripts")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import local_review_server as inner

    verified = {manifest["model_id"]: inner.verified_model_identity(manifest)}
    if manifest.get("asr"):
        asr = manifest["asr"]
        if asr["model_id"] in verified:
            raise ValueError("DUPLICATE_EXECUTOR_MODEL_ID")
        verified[asr["model_id"]] = inner.verified_model_identity(asr)
    decoder = inner.decoder_runtime_identity()
    runtime = {
        "python": sys.version,
        "packages": {name: importlib.metadata.version(name) for name in (
            "torch", "transformers", "pydantic", "packaging", "pyyaml",
        )},
        "adapter_sha256": hashlib.sha256(Path(inner.__file__).read_bytes()).hexdigest(),
        "structured_output": decoder,
    }
    identity = hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"status": "ok", "is_mock": False, "verified_models": verified,
            "runtime_sha256": identity, "structured_output": decoder}


def build_isolated_app(manifest, packages, *, runner=run_isolated, identity_probe=_live_identity):
    lock = Lock()
    last_stage = {}
    state = {"uncertain": False, "sealed": False, "reservation": None, "review_ids": set(),
             "cold_fence": None, "pristine": True}
    handoff_token = os.environ.get("SHINE_REVIEW_HANDOFF_TOKEN", "")

    def invoke(method, path, body=b"", content_type=""):
        if state["uncertain"]:
            raise ValueError("ISOLATED_EXECUTOR_RECONCILIATION_REQUIRED")
        last_stage.clear()
        try:
            result, proof = runner(_forward_once, (manifest, str(packages), method, path, body, content_type),
                                   timeout=600, return_evidence=True)
        except Exception:
            state["uncertain"] = True
            raise
        if (not isinstance(proof, dict) or type(proof.get("pid")) is not int or proof["pid"] <= 0
                or not isinstance(proof.get("birth"), str) or not proof["birth"]
                or proof.get("joined") is not True or type(proof.get("exitcode")) is not int
                or proof["exitcode"] != 0 or type(proof.get("group_active_processes")) is not int
                or proof["group_active_processes"] != 0 or type(proof.get("group_members_waited")) is not int
                or proof["group_members_waited"] < 0):
            state["uncertain"] = True
            raise ValueError("ISOLATED_EXECUTOR_EXIT_UNCONFIRMED")
        last_stage.update(proof)
        health, identity = result["health"], result["identity"]
        if (health.get("status") != "ok" or health.get("is_mock") is not False
                or health.get("loaded") is not False or health.get("asr_loaded") is not False
                or health.get("allocated_bytes") != 0 or health.get("reserved_bytes") != 0
                or identity.get("runtime_sha256") != health.get("runtime_sha256")
                or identity.get("verified_models") != health.get("verified_models")):
            state["uncertain"] = True
            raise ValueError("ISOLATED_EXECUTOR_IDENTITY_INVALID")
        return result

    initial = invoke("GET", "/health")
    baseline = initial["health"]
    runtime = _supervised_runtime(baseline["runtime_sha256"])
    models = baseline["verified_models"]

    def rewrite(data):
        if isinstance(data, dict):
            return {k: (runtime if k == "runtime_sha256" and v == baseline["runtime_sha256"] else rewrite(v))
                    for k, v in data.items()}
        if isinstance(data, list):
            return [rewrite(v) for v in data]
        return data

    def dispatch(method, path, body=b"", content_type="", token="", review_id=""):
        with lock:
            if state["sealed"]:
                raise ReviewSealed()
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise ValueError("ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            reservation = state["reservation"]
            if reservation is not None:
                if (reservation["consumed"] or not secrets.compare_digest(handoff_token.encode(), token.encode())
                        or reservation["id"] != review_id):
                    raise ReviewSealed()
                reservation["consumed"] = True
            state["pristine"] = False
            result = invoke(method, path, body, content_type)
            if _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                state["uncertain"] = True
                raise ValueError("ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            health = result["health"]
            if health["runtime_sha256"] != baseline["runtime_sha256"] or health["verified_models"] != models:
                state["uncertain"] = True
                raise ValueError("ISOLATED_EXECUTOR_IDENTITY_CHANGED")
            try:
                data = json.loads(result["body"])
            except (ValueError, UnicodeError) as exc:
                state["uncertain"] = True
                raise ValueError("ISOLATED_EXECUTOR_RESPONSE_INVALID") from exc
            data = rewrite(data)
            if not isinstance(data, dict) or "isolation_receipt" in data:
                state["uncertain"] = True
                raise ValueError("ISOLATED_EXECUTOR_RESPONSE_INVALID")
            try:
                canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode("utf8")
            except (TypeError, ValueError) as exc:
                state["uncertain"] = True
                raise ValueError("ISOLATED_EXECUTOR_RESPONSE_INVALID") from exc
            receipt = {**last_stage, "stage_id": str(uuid4()), "method": method, "path": path,
                       "request_sha256": hashlib.sha256(body).hexdigest(),
                       "response_sha256": hashlib.sha256(canonical).hexdigest(), "runtime_sha256": runtime}
            last_stage.clear()
            last_stage.update(receipt)
            data["isolation_receipt"] = receipt
            return result["status"], json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf8")

    app = FastAPI(title="Shine isolated review supervisor", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            return {**rewrite(baseline), "last_stage": dict(last_stage), "handoff_sealed": state["sealed"]}

    def seal_receipt(receipt):
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise ValueError("ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            if state["reservation"] is not None and not state["reservation"]["consumed"]:
                raise ValueError("ISOLATED_REVIEW_NOT_SUBMITTED")
            if not isinstance(receipt, dict) or not receipt.get("stage_id") or receipt != last_stage:
                raise ValueError("ISOLATED_REVIEW_RECEIPT_MISMATCH")
            state["sealed"] = True
            return {"sealed": True, "stage_id": receipt["stage_id"], "runtime_sha256": runtime}

    @app.get("/handoff/ready")
    async def handoff_ready(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if (len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode())):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            if state["sealed"] or state["reservation"] is not None:
                raise HTTPException(409, "ISOLATED_REVIEW_SEALED")
            return {"ready": True, "runtime_sha256": runtime}

    @app.post("/handoff/reserve")
    async def handoff_reserve(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if (len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode())):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        body = await request.json()
        try:
            review_id = body["review_id"]
            if not isinstance(review_id, str) or str(UUID(review_id)) != review_id:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(422, "HANDOFF_REVIEW_ID_INVALID") from None
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            if state["sealed"] or state["reservation"] is not None or review_id in state["review_ids"]:
                raise HTTPException(409, "ISOLATED_REVIEW_SEALED")
            state["reservation"] = {"id": review_id, "consumed": False}
            state["review_ids"].add(review_id)
            return {"reserved": True, "review_id": review_id, "runtime_sha256": runtime}

    @app.post("/handoff/cold-fence")
    async def handoff_cold_fence(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode()):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            if (not state["pristine"] or state["sealed"] or state["reservation"] is not None or state["review_ids"]
                    or state["cold_fence"] is not None or "stage_id" in last_stage):
                raise HTTPException(409, "ISOLATED_REVIEW_NOT_COLD")
            state["sealed"] = True
            state["cold_fence"] = str(uuid4())
            return {"fenced": True, "fence_id": state["cold_fence"], "runtime_sha256": runtime,
                    "initial_stage": dict(last_stage)}

    @app.get("/handoff/cold-status")
    async def handoff_cold_status(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode()):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            if not state["sealed"] or not state["cold_fence"] or state["reservation"] is not None:
                raise HTTPException(409, "ISOLATED_REVIEW_NOT_COLD")
            return {"fenced": True, "fence_id": state["cold_fence"], "runtime_sha256": runtime,
                    "initial_stage": dict(last_stage)}

    @app.post("/handoff/cold-reserve")
    async def handoff_cold_reserve(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode()):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        body = await request.json()
        try:
            review_id = body["review_id"]
            if not isinstance(review_id, str) or str(UUID(review_id)) != review_id:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(422, "HANDOFF_REVIEW_ID_INVALID") from None
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            if (not state["sealed"] or state["reservation"] is not None or not state["cold_fence"]
                    or body.get("fence_id") != state["cold_fence"] or review_id in state["review_ids"]):
                raise HTTPException(409, "ISOLATED_REVIEW_NOT_COLD")
            state["sealed"] = False
            state["cold_fence"] = None
            state["reservation"] = {"id": review_id, "consumed": False}
            state["review_ids"].add(review_id)
            return {"reserved": True, "review_id": review_id, "runtime_sha256": runtime}

    @app.post("/handoff/seal")
    async def seal(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if (len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode())):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        receipt = await request.json()
        try:
            return await run_in_threadpool(seal_receipt, receipt)
        except ValueError:
            raise HTTPException(409, "ISOLATED_REVIEW_RECEIPT_MISMATCH") from None

    @app.post("/handoff/rearm-reserve")
    async def handoff_rearm_reserve(request: Request):
        supplied = request.headers.get("X-Shine-Handoff-Token", "")
        if len(handoff_token) < 32 or not secrets.compare_digest(handoff_token.encode(), supplied.encode()):
            raise HTTPException(401, "HANDOFF_AUTH_REQUIRED")
        body = await request.json()
        try:
            review_id = body["review_id"]
            if not isinstance(review_id, str) or str(UUID(review_id)) != review_id:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(422, "HANDOFF_REVIEW_ID_INVALID") from None
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            previous = body.get("previous_receipt")
            reservation = state["reservation"]
            if (not state["sealed"] or not isinstance(previous, dict) or previous != last_stage
                    or not previous.get("stage_id") or review_id == previous["stage_id"]
                    or review_id in state["review_ids"]
                    or (reservation is not None and (not reservation["consumed"] or reservation["id"] == review_id))):
                raise HTTPException(409, "ISOLATED_REVIEW_RECEIPT_MISMATCH")
            state["sealed"] = False
            state["reservation"] = {"id": review_id, "consumed": False}
            state["review_ids"].add(review_id)
            return {"reserved": True, "review_id": review_id, "runtime_sha256": runtime}

    @app.post("/free")
    async def free(request: Request):
        if (await request.json()).get("unload_models") is not True:
            raise HTTPException(422, "EXPLICIT_UNLOAD_REQUIRED")
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            return {"released": True, "allocated_bytes": 0, "is_mock": False}

    def checked_identity():
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise ValueError("ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            identity = identity_probe(manifest, packages)
            if (identity.get("status") != "ok" or identity.get("is_mock") is not False
                    or identity.get("runtime_sha256") != baseline["runtime_sha256"]
                    or identity.get("verified_models") != models
                    or identity.get("structured_output") != baseline.get("structured_output")):
                state["uncertain"] = True
                raise ValueError("ISOLATED_EXECUTOR_IDENTITY_CHANGED")
            return rewrite(identity)

    @app.get("/identity")
    async def get_identity():
        try:
            return await run_in_threadpool(checked_identity)
        except Exception:
            raise HTTPException(503, "ISOLATED_EXECUTOR_OUTCOME_UNKNOWN") from None

    @app.get("/v1/models")
    async def get_models():
        try:
            identity = await run_in_threadpool(checked_identity)
        except Exception:
            raise HTTPException(503, "ISOLATED_EXECUTOR_OUTCOME_UNKNOWN") from None
        decoder = identity["structured_output"]
        return {"data": [{"id": value} for value in identity["verified_models"]], "is_mock": False,
                "capabilities": {"review_response_protocol": decoder["protocol"],
                                 "review_decoder_sha256": decoder["decoder_sha256"]}}

    @app.post("/v1/chat/completions")
    @app.post("/v1/audio/transcriptions")
    async def post_proxy(request: Request):
        body = await request.body()
        if len(body) > 200 * 1024**2:
            raise HTTPException(413, "REVIEW_REQUEST_TOO_LARGE")
        try:
            status, response = await run_in_threadpool(
                dispatch, "POST", request.url.path, body, request.headers.get("content-type", ""),
                request.headers.get("X-Shine-Handoff-Token", ""), request.headers.get("X-Shine-Review-Id", ""))
        except ReviewSealed:
            raise HTTPException(409, "ISOLATED_REVIEW_SEALED") from None
        except Exception:
            raise HTTPException(503, "ISOLATED_EXECUTOR_OUTCOME_UNKNOWN") from None
        return Response(response, status_code=status, media_type="application/json")

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-packages", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8116)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf8"))
    import uvicorn

    uvicorn.run(build_isolated_app(manifest, args.runtime_packages.resolve()), host="127.0.0.1",
                port=args.port, access_log=False)


if __name__ == "__main__":
    main()
