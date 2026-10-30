"""Create the local reviewer profiles and routes, and pin their real capabilities.

The provider profile learns the strict review response protocol from a live
probe of the local executor.  The probe is the only honest source for that
protocol and for the decoder hash, so this script refuses to record a protocol
it has not read back from the running executor.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

ROLES = {
    "vlm_reviewer": ("local-vlm-hash-verified", "local-qwen3-vl-4b"),
    "asr_reviewer": ("local-asr-hash-verified", "local-whisper-tiny"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the local reviewer routes")
    parser.add_argument("--api", required=True)
    parser.add_argument("--vlm-port", type=int, default=8116)
    parser.add_argument("--asr-port", type=int, default=8117)
    args = parser.parse_args()

    base = args.api.rstrip("/") + "/api/v1"
    client = httpx.Client(timeout=300)
    existing = {row["name"]: row for row in client.get(base + "/provider-profiles").json()}

    for role, (name, model) in ROLES.items():
        port = args.vlm_port if role == "vlm_reviewer" else args.asr_port
        profile = existing.get(name)
        if profile is None:
            body = {
                "name": name,
                "kind": "openai_compatible",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "model": model,
                "timeout_s": 600,
                "max_retries": 0,
                "temperature": 0.0,
                "enabled": True,
            }
            response = client.post(base + "/provider-profiles", json=body)
            if response.status_code != 201:
                print(f"[error] {name}: HTTP {response.status_code} {response.text[:400]}", file=sys.stderr)
                return 1
            profile = response.json()
            print(f"[created] {name} id={profile['id']} {profile['base_url']} {profile['model']}")
        else:
            print(f"[existing] {name} id={profile['id']}")

        probe = client.post(base + f"/provider-profiles/{profile['id']}/test")
        if probe.status_code != 200:
            print(f"[error] probing {name}: HTTP {probe.status_code} {probe.text[:400]}", file=sys.stderr)
            return 1
        result = probe.json()
        if not result.get("ok"):
            print(f"[error] {name} probe failed: {result.get('message')}", file=sys.stderr)
            return 1
        capabilities = result.get("capabilities") or {}
        print(f"[probed] {name} capabilities={json.dumps(capabilities, ensure_ascii=False)}")
        if role == "vlm_reviewer":
            protocol = capabilities.get("review_response_protocol")
            decoder = capabilities.get("review_decoder_sha256")
            if protocol != "local-review-json-schema-v1" or not isinstance(decoder, str):
                print("[error] the local VLM executor did not declare the strict review protocol", file=sys.stderr)
                return 1

        body = {"role": role, "provider_profile_id": profile["id"]}
        response = client.put(base + "/agent-routes", json=body)
        if response.status_code != 200:
            print(f"[error] route {role}: HTTP {response.status_code} {response.text[:400]}", file=sys.stderr)
            return 1
        print(f"[routed] {role} -> {profile['id']}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
