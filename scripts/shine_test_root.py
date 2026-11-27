"""The caller's test root for development and acceptance scripts.

Machine-specific directories are never hardcoded in this repository.  A script
that writes disposable test or acceptance data takes the root from its caller:

* scripts with their own argument parsing declare ``-WorkRoot`` / ``--work-root``
  (the same parameter ``run_backend_shards.ps1`` already uses);
* scripts without argument parsing of their own (a pytest module, a bare
  browser journey) read ``SHINE_TEST_ROOT`` from the environment.

``<test root>\\acceptance\\<round>\\`` is the only place disposable acceptance
data may live.  Evidence that must outlive the round goes to ``docs/evidence``.
"""

from __future__ import annotations

import os
from pathlib import Path

TEST_ROOT_ENV = "SHINE_TEST_ROOT"


def work_root(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the test root: an explicit argument wins, the environment is the fallback."""
    raw = value if value not in (None, "") else os.environ.get(TEST_ROOT_ENV, "")
    if not raw:
        raise RuntimeError(
            "test root not given: pass --work-root (or -WorkRoot), or set "
            f"{TEST_ROOT_ENV}; no machine directory is hardcoded in this repository"
        )
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"test root does not exist: {root}")
    return root


def acceptance_dir(root: Path | str, *parts: str) -> Path:
    """``<test root>/acceptance/<round>/...`` for one acceptance round."""
    return Path(root).joinpath("acceptance", *parts)
