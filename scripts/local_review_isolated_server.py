"""Loopback review supervisor: one owned model process per request on Windows.

The wrapped executor is the same hash-verified local review server.  Results
are returned only after its child exits; no request is replayed on uncertainty.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from threading import Lock

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from shine.comfyops.isolated_stage import run_isolated  # noqa: E402


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


def build_isolated_app(manifest, packages, *, runner=run_isolated):
    lock = Lock()
    last_stage = {}
    state = {"uncertain": False}

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
        if proof.get("joined") is not True or proof.get("exitcode") != 0:
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

    def dispatch(method, path, body=b"", content_type=""):
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise ValueError("ISOLATED_SUPERVISOR_SOURCE_CHANGED")
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
                raise ValueError("ISOLATED_EXECUTOR_RESPONSE_INVALID") from exc
            return result["status"], json.dumps(rewrite(data), ensure_ascii=False).encode("utf8")

    app = FastAPI(title="Shine isolated review supervisor", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            return {**rewrite(baseline), "last_stage": dict(last_stage)}

    @app.post("/free")
    async def free(request: Request):
        if (await request.json()).get("unload_models") is not True:
            raise HTTPException(422, "EXPLICIT_UNLOAD_REQUIRED")
        with lock:
            if state["uncertain"] or _supervised_runtime(baseline["runtime_sha256"]) != runtime:
                raise HTTPException(503, "ISOLATED_SUPERVISOR_SOURCE_CHANGED")
            return {"released": True, "allocated_bytes": 0, "is_mock": False}

    @app.get("/identity")
    @app.get("/v1/models")
    async def get_proxy(request: Request):
        try:
            status, body = await run_in_threadpool(dispatch, "GET", request.url.path)
        except Exception:
            raise HTTPException(503, "ISOLATED_EXECUTOR_OUTCOME_UNKNOWN") from None
        return Response(body, status_code=status, media_type="application/json")

    @app.post("/v1/chat/completions")
    @app.post("/v1/audio/transcriptions")
    async def post_proxy(request: Request):
        body = await request.body()
        if len(body) > 200 * 1024**2:
            raise HTTPException(413, "REVIEW_REQUEST_TOO_LARGE")
        try:
            status, response = await run_in_threadpool(
                dispatch, "POST", request.url.path, body, request.headers.get("content-type", ""))
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
