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


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    repo = backend.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None, help="source commit (defaults to git HEAD)")
    parser.add_argument("--output", default=None, help="manifest path")
    args = parser.parse_args()

    from shine.support.project_knowledge import KNOWLEDGE_VERSION, PROJECT_ENTRIES, _content_digest

    output = Path(args.output) if args.output else backend / "shine" / "support" / "knowledge_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "knowledge_version": KNOWLEDGE_VERSION,
        "source_commit": args.commit if args.commit is not None else _git_head(repo),
        "content_sha256": _content_digest(),
        "entry_count": len(PROJECT_ENTRIES),
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": "backend/scripts/generate_copilot_knowledge.py",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} commit={payload['source_commit']} digest={payload['content_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
