"""Register the real local MiniMax-H3 Ref2VA workflow profiles against a running Shine API.

Reads the canonical workflow JSON artifacts from config/workflows/ and POSTs
them to /api/v1/workflow-profiles, then triggers the live object_info
validation. Idempotent: an existing profile with the same name is reported and
skipped (delete it first to re-register a changed graph).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
WORKFLOWS = [
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-2img.json",
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-2img-1aud.json",
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-pdd8-2img.json",
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-pdd8-2img-1aud.json",
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-pdd8-2img-1aud-portrait.json",
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-pdd8-6img.json",
    Path(__file__).resolve().parents[1] / "config" / "workflows" / "ref2va-pdd8-upscale-2img.json",
]


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def main() -> int:
    registration_failed = False
    validation_failed = False
    existing = {row["name"]: row for row in request("GET", "/api/v1/workflow-profiles")[1]}
    for artifact in WORKFLOWS:
        body = json.loads(artifact.read_text(encoding="utf-8"))
        name = body["name"]
        body.pop("description", None)  # documentation-only field, not part of the API schema
        if name in existing:
            print(f"[skip] {name} already registered (id={existing[name]['id']})")
            profile = existing[name]
        else:
            status, profile = request("POST", "/api/v1/workflow-profiles", body)
            if status != 201:
                print(f"[error] {name}: HTTP {status} {json.dumps(profile, ensure_ascii=False)[:500]}")
                registration_failed = True
                continue
            print(f"[registered] {name} id={profile['id']} graph_sha256={profile['graph_sha256'][:16]}")
        status, validated = request("POST", f"/api/v1/workflow-profiles/{profile['id']}/validate")
        print(f"[validate] {name}: HTTP {status} {json.dumps(validated.get('validated_against', validated), ensure_ascii=False)[:400]}")
        if status != 200:
            # The live check needs a running renderer.  A profile that is
            # registered but never validated must not look "done": the worker
            # now fails closed on STRATEGY_RENDERER_OBJECT_INFO_UNAVAILABLE /
            # missing nodes instead of submitting an unverifiable graph, so a
            # non-zero exit is the honest signal that this run did not finish.
            validation_failed = True
    if validation_failed:
        print("[error] 部分工作流已登记，但节点完整性校验未完成（渲染器不可达或缺失节点）。")
        print("  这些 profile 处于「已登记、未验证」状态：生产提交会被 worker 以明确错误码拒绝，")
        print("  直到受管渲染器确认运行后再重新执行本脚本验证。")
        return 2 if not registration_failed else 1
    return 1 if registration_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
