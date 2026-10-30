"""Index the final local acceptance evidence directory.

The report quotes this index, so the evidence list in the report and the files
on disk cannot drift apart.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".local-acceptance/final-local-pass")


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
