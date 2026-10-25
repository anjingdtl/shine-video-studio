"""Run the eight film roles against an explicitly selected local acceptance API.

Creates proposals only. Does not apply plans, approve assets or submit GPU jobs.
Remote text requires --allow-remote-text. Each result is persisted immediately;
resume uses successful artifact IDs and never replays uncertain requests.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8110")
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-remote-text", action="store_true")
    args = parser.parse_args()
    if httpx.URL(args.api).host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Acceptance API must be loopback")
    state = (
        json.loads(args.output.read_text(encoding="utf-8"))
        if args.output.exists()
        else {
            "api": args.api,
            "project_id": args.project,
            "started_at": datetime.now(UTC).isoformat(),
            "roles": {},
        }
    )
    if state["api"] != args.api or state["project_id"] != args.project:
        parser.error("Existing evidence belongs to a different API/project")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    dependencies = {
        "scriptwriter": [],
        "director": ["scriptwriter"],
        "cinematographer": ["director"],
        "continuity_supervisor": ["director", "cinematographer"],
        "sound_director": ["scriptwriter"],
        "producer": [],
        "comfyops": [],
        "editor": [],
    }
    brief = (
        "Create proposals for one seven-second shot. CHAR_LIANG, a Chinese female undercover "
        "operative, stands on the left of SCENE_NEON, a rainy neon street. She says in Mandarin: "
        "你终于来了。 Keep this dialogue verbatim. Use only exact asset entity IDs, beat IDs and "
        "asset IDs provided in context. The director must bind the approved character image. "
        "Use 4-15 second shots, with English visual descriptions. Do not create or approve masters. "
        "For editing, use the three current eligible real clips in A, B, C order, each from zero "
        "to its listed duration. Missing capabilities remain blockers, never invent verification."
    )
    with httpx.Client(base_url=args.api + "/api/v1", timeout=240) as client:
        for role, required in dependencies.items():
            previous = state["roles"].get(role)
            if previous:
                if previous.get("http") == 201:
                    continue
                raise RuntimeError(
                    f"{role} has a recorded unresolved call; inspect evidence before retrying"
                )
            missing = [
                r for r in required if state["roles"].get(r, {}).get("http") != 201
            ]
            if missing:
                state["roles"][role] = {"status": "NOT_RUN", "missing": missing}
                save()
                continue
            body = {
                "brief": brief,
                "locked_story_facts": ["CHAR_LIANG is an undercover operative."],
                "input_revision_ids": [
                    state["roles"][r]["artifact_id"] for r in required
                ],
                "allow_remote_text": args.allow_remote_text,
                "offline_preview": False,
            }
            state["roles"][role] = {
                "status": "SUBMIT_INTENT",
                "at": datetime.now(UTC).isoformat(),
            }
            save()
            try:
                response = client.post(
                    f"/projects/{args.project}/film-agents/{role}/run", json=body
                )
            except httpx.HTTPError:
                state["roles"][role]["status"] = "OUTCOME_UNKNOWN"
                save()
                raise
            result = response.json()
            state["roles"][role].update(
                http=response.status_code,
                status="SUCCEEDED" if response.status_code == 201 else "REJECTED",
            )
            if response.status_code == 201:
                state["roles"][role].update(
                    artifact_id=result["id"],
                    kind=result["kind"],
                    sha256=result["sha256"],
                )
            else:
                state["roles"][role]["detail"] = result.get("detail")
            save()
            print(
                role, response.status_code, state["roles"][role]["status"], flush=True
            )
    return int(any(row.get("http") != 201 for row in state["roles"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
