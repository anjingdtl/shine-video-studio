"""Build a per-file SHA-256 evidence index for one acceptance directory.

The index is the auditable map between the recorded evidence and the bytes on
disk.  Every file under the root is hashed, so a later reader can prove that a
screenshot, a log or a rendered movie is the very file the run produced.
The index never lists itself: the output file is excluded by path, so repeating the build stays stable.

    python scripts/build_evidence_index.py --root .local-acceptance/final-closure-v3 \
        --out .local-acceptance/final-closure-v3/evidence-index.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build(root: Path, *, skip: list[str], out: Path) -> dict:
    """Hash every evidence file except the index itself.

    The output index is excluded by path, so repeating the build never
    compares a fresh index against the stale hash of its own predecessor.
    """
    try:
        out_relative = out.resolve().relative_to(root).as_posix()
    except ValueError:
        out_relative = None
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == out_relative:
            continue
        if any(relative == pattern or relative.startswith(pattern) for pattern in skip):
            continue
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_of(path)})
    return {
        "root": root.as_posix(),
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Hash every evidence file under a directory")
    parser.add_argument("--root", required=True, help="the evidence directory to index")
    parser.add_argument("--out", required=True, help="where the index is written")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        help="a relative path prefix that is left out of the index (repeatable)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2
    index = build(root, skip=args.skip, out=Path(args.out))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"indexed {index['file_count']} files ({index['total_bytes']} bytes) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
