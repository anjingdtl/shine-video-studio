"""Index one acceptance evidence directory.

The report quotes this index, so the evidence list in the report and the files
on disk cannot drift apart.  The directory is always given by the caller; no
machine directory is hardcoded.

    python scripts/index_acceptance_evidence.py <test-root>/acceptance/<round>
"""

import hashlib
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: python scripts/index_acceptance_evidence.py <evidence-dir>")
ROOT = Path(sys.argv[1]).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    index = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name == "evidence-index.json":
            continue
        entry = {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
        }
        if path.suffix.lower() in {".mp4", ".json", ".log", ".png", ".txt"}:
            entry["sha256"] = sha256(path)
        index.append(entry)
    target = ROOT / "evidence-index.json"
    target.write_text(json.dumps({"root": ROOT.as_posix(), "files": index}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"{len(index)} files indexed into {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
