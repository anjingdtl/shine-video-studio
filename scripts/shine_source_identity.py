"""CLI for the launcher: print this checkout's source identity as JSON.

Loads the single implementation by file path so any Python >= 3.9 can run it
before the project's dependencies exist.  Stdlib only; never writes anything.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO_ROOT
    module_path = REPO_ROOT / "backend" / "shine" / "source_identity.py"
    spec = importlib.util.spec_from_file_location("shine_source_identity", module_path)
    if spec is None or spec.loader is None:
        print(json.dumps({"kind": "unknown", "digest": "", "display": "unknown"}))
        return 0
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(json.dumps(module.compute_source_identity(root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
