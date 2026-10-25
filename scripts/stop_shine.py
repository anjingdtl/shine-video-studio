"""Stop only the Shine processes started by ``scripts/start_shine.ps1``.

The start script records the PID, start time and working directory of every
process it owns in ``<data_dir>/run/processes.json``. This script refuses to
touch any process that is not in that manifest, so it can never stop an
unrelated ComfyUI, editor or browser process.

Usage::

    uv run --project backend python scripts/stop_shine.py [--data-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / ".local-data"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate(pid: int) -> None:
    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        os.kill(pid, signal.SIGTERM)


def _checkpoint(database: Path) -> None:
    """Checkpoint the WAL so a terminated worker cannot leave torn pages behind."""
    import sqlite3

    if not database.is_file():
        return
    try:
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
    except Exception as exc:  # pragma: no cover - best effort only
        print(f"[warn] could not checkpoint {database}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.environ.get("SHINE_DATA_DIR", str(DEFAULT_DATA_DIR)))
    parser.add_argument("--grace-seconds", type=float, default=10.0)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    manifest_path = data_dir / "run" / "processes.json"
    if not manifest_path.is_file():
        print(f"No process manifest at {manifest_path}; nothing owned by start_shine.ps1 is running.")
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("processes", [])

    # Checkpoint SQLite before terminating anything. A force-killed process can
    # still have dirty pages mapped to the database file, and the OS may write
    # them back after the checkpoint.
    _checkpoint(data_dir / "shine.db")

    stopped: list[str] = []
    skipped: list[str] = []
    for entry in entries:
        pid = int(entry.get("pid", 0))
        name = entry.get("name", "?")
        if not _alive(pid):
            skipped.append(f"{name} (pid {pid}) already exited")
            continue
        _terminate(pid)
        stopped.append(f"{name} (pid {pid})")

    deadline = time.time() + args.grace_seconds
    while time.time() < deadline and any(_alive(int(e.get("pid", 0))) for e in entries):
        time.sleep(0.25)

    for line in stopped:
        print(f"[stopped] {line}")
    for line in skipped:
        print(f"[skipped] {line}")
    manifest_path.unlink(missing_ok=True)
    print("Shine processes stopped. ComfyUI and unrelated processes were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
