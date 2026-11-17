"""Regenerate the copilot's version-bound project knowledge manifest.

Run this at build/release time (after the code that the catalogue describes is
committed):

    cd backend && python -m scripts.generate_copilot_knowledge --commit "$(git rev-parse HEAD)"

It writes ``shine/support/knowledge_manifest.json`` with the knowledge
version, the source commit and the content hash.  At runtime the copilot compares
that manifest with ``SHINE_BUILD_COMMIT``; a mismatch is reported as STALE and
the answer says so instead of presenting outdated prose as current fact.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def _git_head(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _source_commit(repo: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip()
    injected = (os.environ.get("SHINE_KNOWLEDGE_SOURCE_COMMIT") or "").strip()
    if injected:
        return injected
    return _git_head(repo)


def _check(path: Path, expected: dict) -> int:
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"manifest missing or unreadable: {path}")
        return 1
    mismatched = [
        key
        for key in ("knowledge_version", "content_sha256", "entry_count")
        if recorded.get(key) != expected.get(key)
    ]
    if mismatched:
        print(f"manifest is stale: {', '.join(mismatched)}")
        return 1
    print(f"manifest ok: {path} version={recorded.get('knowledge_version')}")
    return 0


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    repo = backend.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None, help="knowledge source commit")
    parser.add_argument("--output", default=None, help="manifest path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify an existing manifest instead of writing it",
    )
    args = parser.parse_args()

    from shine.support.project_knowledge import KNOWLEDGE_VERSION, PROJECT_ENTRIES, _content_digest

    output = Path(args.output) if args.output else backend / "shine" / "support" / "knowledge_manifest.json"
    payload = {
        "knowledge_version": KNOWLEDGE_VERSION,
        "source_commit": _source_commit(repo, args.commit),
        "content_sha256": _content_digest(),
        "entry_count": len(PROJECT_ENTRIES),
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": "backend/scripts/generate_copilot_knowledge.py",
    }
    if args.check:
        return _check(output, payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} commit={payload['source_commit']} digest={payload['content_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
