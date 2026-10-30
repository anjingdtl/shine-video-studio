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
    failed = False
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
                failed = True
                continue
            print(f"[registered] {name} id={profile['id']} graph_sha256={profile['graph_sha256'][:16]}")
        status, validated = request("POST", f"/api/v1/workflow-profiles/{profile['id']}/validate")
        print(f"[validate] {name}: HTTP {status} {json.dumps(validated.get('validated_against', validated), ensure_ascii=False)[:400]}")
        if status != 200:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
