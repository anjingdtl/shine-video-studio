"""Seed the real LLM provider profile and the five crew routes.

The provider key is loaded from a previous acceptance run's DPAPI secret
store into this process, then handed to the running stack over loopback HTTP
only.  The key value is never printed, logged or written to the evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

CREW_ROLES = ("scriptwriter", "director", "cinematographer", "continuity_supervisor", "editor")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the storyboard LLM provider and crew routes")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--source-secret-dir", required=True,
                        help="The secrets directory of a previous acceptance data dir")
    parser.add_argument("--source-ref", required=True,
                        help="The api_key_ref of the previous profile (dpapi:...secret)")
    parser.add_argument("--name", default="minimax-agent-storyboard")
    parser.add_argument("--base-url", default="https://api.minimax.cn/v1")
    parser.add_argument("--model", default="MiniMax-M3.1-Flash")
    parser.add_argument("--timeout-s", type=float, default=600)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-completion-tokens", type=int, default=12288,
                        help="The completion budget of one crew call, thinking tokens included")
    parser.add_argument("--reasoning-effort", default="medium",
                        choices=["low", "medium", "high", "xhigh", "max"],
                        help="The thinking depth of the crew model; the provider default is its longest")
    parser.add_argument("--routes", default=",".join(CREW_ROLES))
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "backend"))
    from shine.llm.secrets import SecretStore

    store = SecretStore(Path(args.source_secret_dir).resolve())
    api_key = store.load(args.source_ref)
    if not api_key:
        print("The source secret could not be loaded", file=sys.stderr)
        return 2

    client = httpx.Client(timeout=300)
    routes = [role.strip() for role in args.routes.split(",") if role.strip()]
    # The crew model is a reasoning model whose thinking depth defaults to the
    # provider's longest setting, and whose thinking tokens count against the
    # completion budget.  An unbounded crew call can outlast the upstream
    # connection window, so the acceptance run states both bounds explicitly.
    generation_options = {
        "max_completion_tokens": args.max_completion_tokens,
        "reasoning_effort": args.reasoning_effort,
    }
    evidence = {"api": args.api, "name": args.name, "model": args.model,
                "base_url": args.base_url, "routes": routes,
                "generation_options": generation_options, "steps": []}

    profile = client.post(args.api + "/api/v1/provider-profiles", json={
        "name": args.name, "kind": "openai_compatible",
        "base_url": args.base_url, "model": args.model, "api_key": api_key,
        "timeout_s": args.timeout_s, "temperature": args.temperature,
        "generation_options": generation_options,
    })
    if profile.status_code not in (200, 201):
        existing = client.get(args.api + "/api/v1/provider-profiles").json()
        match = next((row for row in existing if row["name"] == args.name), None)
        if match is None:
            print(f"provider profile creation failed: {profile.status_code} {profile.text[:200]}",
                  file=sys.stderr)
            return 2
        profile_id = match["id"]
    else:
        profile_id = profile.json()["id"]
    evidence["steps"].append({"step": "profile_ready", "profile_id": profile_id})

    for role in routes:
        row = client.put(args.api + "/api/v1/agent-routes", json={
            "role": role, "provider_profile_id": profile_id,
        })
        if row.status_code != 200:
            print(f"route {role} failed: {row.status_code} {row.text[:200]}", file=sys.stderr)
            return 2
        evidence["steps"].append({"step": "route_set", "role": role, "profile_id": profile_id})

    probe_body = None
    for attempt in (1, 2):
        try:
            probe = client.post(f"{args.api}/api/v1/provider-profiles/{profile_id}/test")
            probe_body = probe.json() if probe.status_code == 200 else {"detail": probe.text[:400]}
            break
        except httpx.HTTPError as exc:
            probe_body = {"detail": f"probe transport error: {type(exc).__name__}"}
            if attempt == 2:
                break
    evidence["probe"] = probe_body
    print(f"provider probe: {__import__('json').dumps(probe_body, ensure_ascii=False)[:400]}")
    ok = bool(probe_body.get("ok")) or bool(probe_body.get("available"))
    if probe.status_code != 200 or not ok:
        print("The provider probe did not succeed", file=sys.stderr)
        if args.evidence:
            Path(args.evidence).write_text(
                __import__("json").dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return 3

    if args.evidence:
        Path(args.evidence).write_text(
            __import__("json").dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"provider ready: profile={profile_id} routes={len(routes)} probe=available")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
