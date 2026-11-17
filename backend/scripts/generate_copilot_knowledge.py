"""Backward-compatible entry point for the V2 knowledge manifest.

V2.1 moved generation to ``generate_shine_copilot_contracts.py``.  This module
keeps the documented command working and can still write a single manifest to
``--output`` (used by the acceptance tests and by anyone binding a manifest
without touching the repository artefact).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _load():
    sys.path.insert(0, str(BACKEND))
    from scripts import generate_shine_copilot_contracts as generator

    return generator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--output", default=None, help="manifest path (single-file mode)")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    generator = _load()
    if not args.output:
        forwarded = ["--check"] if args.check else []
        if args.commit:
            forwarded.extend(["--commit", args.commit])
        return generator.main(forwarded)

    class _Args:
        commit = args.commit
        output_dir = None
        extra_declarations = None

    bundle, manifest, issues = generator.build(_Args())
    if issues:
        for issue in issues:
            print(f"FAIL {issue}", file=sys.stderr)
        return 1
    target = Path(args.output)
    if args.check:
        try:
            recorded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"manifest missing or unreadable: {target}", file=sys.stderr)
            return 1
        mismatched = [
            key
            for key in ("knowledge_version", "content_sha256", "entry_count", "contracts_digest")
            if recorded.get(key) != manifest.get(key)
        ]
        if mismatched:
            print(f"manifest is stale: {', '.join(mismatched)}", file=sys.stderr)
            return 1
        print(f"manifest ok: {target} version={recorded.get('knowledge_version')}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {target} commit={manifest['source_commit']} digest={manifest['content_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
