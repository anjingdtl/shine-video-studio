"""Stop the exact process identities recorded by start_shine.ps1.

Legacy manifests without OS birth identities require manual reconciliation.
A reused PID never grants authority to stop its new owner.

A PID that the OS refuses to describe is classified with a read-only probe:
a recycled PID is released without touching the process that holds it now,
because the recorded process is provably gone. Only a PID that still holds
the recorded process, or one that cannot be classified at all, is retained
for manual reconciliation.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

from shine.comfyops.installer import process_identity
from shine.comfyops.instances import ProcessRunner
from shine.comfyops.process_probe import (
    GONE,
    RECYCLED,
    SAME,
    UNKNOWN,
    classify_unverifiable,
    creation_stamps,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / ".local-data"

STARTUP_GUARD_TIMEOUT_SECONDS = 120.0


class StartupGuardBusy(RuntimeError):
    """A launcher holds the startup critical section for this data directory."""


def startup_guard_name(data_dir: Path) -> str:
    """Same derivation as scripts/shine_startup_guard.ps1 (Get-ShineStartupGuardName)."""
    import hashlib

    canonical = os.path.abspath(data_dir).rstrip("\\").upper()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]
    return f"Global\\ShineStackStartup-{digest}"


def _acquire_startup_guard(data_dir: Path, timeout_s: float = STARTUP_GUARD_TIMEOUT_SECONDS):
    """Coordinate with a concurrent launcher; a stop must not race a start.

    Returns an opaque guard to release, or ``None`` when no launcher is active
    (no named mutex exists).  Best effort on non-Windows platforms, where the
    launcher mutex does not exist either.  A caller that already holds the
    launcher's critical section (start_shine.ps1 reconciling a stale stack)
    sets SHINE_STARTUP_GUARD_HELD=1 so the nested stop cannot deadlock on the
    very mutex its caller owns.
    """
    if os.name != "nt" or os.environ.get("SHINE_STARTUP_GUARD_HELD") == "1":
        return None
    import ctypes

    SYNCHRONIZE = 0x00100000
    name = startup_guard_name(data_dir)
    handle = ctypes.windll.kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if not handle:
        return None  # No launcher holds the section; nothing to coordinate with.
    try:
        wait = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
        if wait not in (0, 0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
            raise StartupGuardBusy(
                "A Shine launcher is still starting this data directory; "
                "stop it or wait for it to finish first."
            )
    except Exception:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise
    return ("mutex", handle)


def _release_startup_guard(guard) -> None:
    if not guard or os.name != "nt":
        return
    import ctypes

    handle = guard[1]
    ctypes.windll.kernel32.ReleaseMutex(handle)
    ctypes.windll.kernel32.CloseHandle(handle)


def _process_owners(value):
    """Normalize verified process identities from current and PS 5.1 manifests.

    Windows PowerShell 5.1 wraps a piped root JSON array in a single object
    containing ``value`` and ``Count``. Older isolated manifests can contain
    that shape; accept it only when the count and every identity are valid.
    """
    if not isinstance(value, list):
        return None
    if (
        len(value) == 1
        and isinstance(value[0], dict)
        and set(value[0]) >= {"value", "Count"}
    ):
        wrapper = value[0]
        unwrapped = wrapper.get("value")
        if not isinstance(unwrapped, list) or wrapper.get("Count") != len(unwrapped):
            return None
        value = unwrapped
    if not value or any(
        not isinstance(owner, dict)
        or not isinstance(owner.get("pid"), int)
        or not owner.get("birth")
        for owner in value
    ):
        return None
    return value


def stop_manifest(manifest_path: Path, *, runner=None, identity=None, classify=None) -> int:
    runner = runner or ProcessRunner()
    identity = identity or process_identity
    stamps = None

    def resolve(pid, birth):
        """Classify a PID the OS would not describe, without touching it."""
        nonlocal stamps
        if stamps is None:
            stamps = creation_stamps()
        return classify_unverifiable(pid, birth, stamps)

    classify = classify or resolve
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    unresolved = []
    for entry in reversed(manifest.get("processes", [])):
        owners = _process_owners(entry.get("owners", []))
        if not owners:
            print(f"[refused] {entry.get('name')}: missing or malformed birth identities; reconcile manually")
            unresolved.append(entry)
            continue
        remaining = []
        # Snapshots list parents before children. Stop descendants first; all
        # identities are independently verified, with no recursive PID-only kill.
        for owner in reversed(owners):
            pid, birth = owner["pid"], owner.get("birth")
            try:
                current = identity(pid)
            except ValueError:
                # The OS refused to describe this PID, for example because a
                # higher privileged process holds it now. A read-only probe can
                # still tell a recycled PID from a process that never died.
                verdict = classify(pid, birth)
                if verdict == RECYCLED:
                    print(
                        f"[released] pid {pid}: the recorded process is gone; "
                        "this PID now belongs to another process"
                    )
                    continue
                if verdict == GONE:
                    print(f"[gone] pid {pid}: the recorded process is gone")
                    continue
                if verdict == SAME:
                    print(
                        f"[refused] pid {pid}: the recorded process is still running "
                        "but cannot be verified"
                    )
                else:
                    print(f"[refused] pid {pid}: liveness unavailable")
                remaining.append(owner)
                continue
            if current is None:
                continue
            if not birth or current != birth:
                print(f"[refused] pid {pid}: identity changed or unavailable")
                remaining.append(owner)
                continue
            try:
                runner.stop(
                    owner
                )  # Verifies birth again on the same OS handle used to stop.
                print(f"[stopped] {entry.get('name')} pid {pid}")
            except (OSError, ValueError) as exc:
                # A launcher may exit its child between inspection and the OS
                # termination call. Confirm death instead of treating an already
                # exited Windows process object as a failed shutdown.
                try:
                    deadline = time.monotonic() + 5
                    observed = identity(pid)
                    while observed is not None and time.monotonic() < deadline:
                        time.sleep(0.05)
                        observed = identity(pid)
                    if observed is None:
                        print(f"[exited] {entry.get('name')} pid {pid}")
                        continue
                except ValueError:
                    pass
                print(f"[refused] pid {pid}: {exc}")
                remaining.append(owner)
        if remaining:
            unresolved.append({**entry, "owners": remaining})
    if unresolved:
        manifest["processes"] = list(reversed(unresolved))
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("Unresolved process identities retained for reconciliation.")
        return 1
    manifest_path.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", default=os.environ.get("SHINE_DATA_DIR", str(DEFAULT_DATA_DIR))
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    guard = None
    try:
        # The stop and a concurrent launcher share one critical section keyed by
        # the data directory, so a stop never tears down a stack the launcher
        # is halfway through recording (or vice versa).
        guard = _acquire_startup_guard(data_dir)
        return _stop_with_manifest(data_dir)
    finally:
        _release_startup_guard(guard)


def _stop_with_manifest(data_dir: Path) -> int:
    manifest = data_dir / "run" / "processes.json"
    if not manifest.is_file():
        print(f"No process manifest at {manifest}; nothing to stop.")
        return 0
    result = stop_manifest(manifest)
    # WAL is durable on its own. A checkpoint after shutdown avoids racing active
    # writers; it is housekeeping, not a claim that force termination is graceful.
    database = data_dir / "shine.db"
    if not result and database.is_file():
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
