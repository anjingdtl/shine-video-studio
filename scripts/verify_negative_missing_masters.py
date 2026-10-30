"""Real negative acceptance: a script that names a character without a master.

The product must refuse to storyboard a character it has no approved, locked
master for, and it must name exactly that character in the refusal.  The
assertions are driven by the project's own approved masters, not by a
hardcoded answer, so the check cannot drift away from the database.

    python scripts/verify_negative_missing_masters.py --api http://127.0.0.1:8140 \
        --project-id <id> --script <file> --out <evidence dir>
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def approved_character_masters(client: httpx.Client, api: str, project_id: str) -> list[str]:
    rows = client.get(f"{api}/api/v1/projects/{project_id}/assets").json()
    return sorted(
        row["entity_id"] for row in rows
        if row.get("role") == "character" and row.get("entity_id")
        and row.get("approved") and row.get("locked")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Negative acceptance for missing character masters")
    parser.add_argument("--api", default="http://127.0.0.1:8140")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--script", required=True, help="the script text file to submit")
    parser.add_argument("--out", required=True, help="the evidence directory to write")
    parser.add_argument("--expect-missing", action="append", default=[],
                        help="a character code the refusal is expected to name (informative)")
    parser.add_argument("--expect-present", action="append", default=[],
                        help="a character code the refusal must NOT name")
    parser.add_argument("--allow-remote-text", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    api = args.api.rstrip("/")
    text = Path(args.script).read_text(encoding="utf-8")

    with httpx.Client(timeout=1800) as client:
        masters = approved_character_masters(client, api, args.project_id)
        payload = {"text": text, "allow_remote_text": args.allow_remote_text}
        started = time.time()
        response = client.post(
            f"{api}/api/v1/projects/{args.project_id}/storyboard/generate", json=payload
        )
        elapsed = time.time() - started

    body: dict
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:2000]}

    status = body.get("status")
    missing = body.get("missing_characters") or []
    failures: list[str] = []
    if status != "BLOCKED_MISSING_MASTERS":
        failures.append(f"status was {status!r}, expected BLOCKED_MISSING_MASTERS")
    if not isinstance(missing, list) or not missing:
        failures.append("the refusal carried no missing character list")
    wrongly_named = [name for name in missing if name in masters]
    if wrongly_named:
        failures.append(f"the refusal wrongly named approved masters: {wrongly_named}")
    for name in args.expect_present:
        if name in missing:
            failures.append(f"the refusal wrongly named the mastered character {name!r}: {missing!r}")
    not_in_script = [name for name in missing if name not in text]
    if not_in_script:
        failures.append(f"the refusal named characters that the script never mentions: {not_in_script}")

    evidence = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": api,
        "project_id": args.project_id,
        "request": {
            "path": f"/api/v1/projects/{args.project_id}/storyboard/generate",
            "body": {"text": "negative-missing-masters-script.md (see this directory)",
                     "allow_remote_text": args.allow_remote_text},
        },
        "approved_character_masters": masters,
        "http_status": response.status_code,
        "elapsed_s": round(elapsed, 1),
        "response": body,
        "expected_missing": args.expect_missing,
        "expected_present": args.expect_present,
        "assertions": {
            "status_is_BLOCKED_MISSING_MASTERS": status == "BLOCKED_MISSING_MASTERS",
            "missing_list_not_empty": bool(missing),
            "names_no_approved_master": not wrongly_named,
            "names_only_characters_of_the_script": not not_in_script,
            "names_every_expected_character": all(n in missing for n in args.expect_missing),
        },
        "failures": failures,
        "verdict": "PASS" if not failures else "FAIL",
    }
    (out / "negative-missing-masters-response.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "negative-missing-masters-script.md").write_text(text, encoding="utf-8")

    print(json.dumps({
        "verdict": evidence["verdict"],
        "http_status": response.status_code,
        "status": status,
        "missing_characters": missing,
        "approved_character_masters": masters,
        "elapsed_s": evidence["elapsed_s"],
        "failures": failures,
    }, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
