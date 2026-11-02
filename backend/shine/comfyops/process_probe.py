"""Read-only process probes used to reconcile a stale process record.

`process_identity` stays the authoritative identity source: it opens the
process and reads the OS creation stamp, and it refuses to guess when the OS
will not describe the process. A Medium integrity caller cannot open a SYSTEM
process, so a PID that Windows recycled to a service host cannot be inspected
that way and the record looks permanently unresolved.

This module adds a second, strictly read-only source. The WMI provider can
describe processes the caller is not allowed to open, so it can tell a
recycled PID apart from a process that is still running under another
identity. It never stops anything and never terminates a process; it only
answers "does this PID still hold the recorded process".

Absence of a PID is death. A failed query is unknown, and unknown is never
read as death.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

# The Windows creation stamp counts 100 ns intervals since 1601-01-01.
_FILETIME_UNIX_DELTA = 116444736000000000

# Two stamps closer than this describe the same process. The authoritative
# stamp has 100 ns resolution and the WMI stamp is reported in microseconds,
# so one process differs by well under a millisecond, while a recycled PID
# differs by however long the OS needed to hand the number to somebody else.
SAME_PROCESS_TOLERANCE = 10_000_000

# The PID no longer holds the recorded process: the number was recycled.
RECYCLED = "recycled"
# The PID still holds the recorded process, but it cannot be opened here.
SAME = "same"
# No process holds this PID any more.
GONE = "gone"
# The answer could not be established. Never read this as death.
UNKNOWN = "unknown"

_POWERSHELL = "powershell" if os.name == "nt" else "pwsh"

_STAMP_QUERY = (
    "Get-CimInstance Win32_Process | ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CreationDate.ToString('o') }"
)


def _stamp_from_iso(value: str) -> int | None:
    """Convert an ISO 8601 creation time into a Windows creation stamp."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    # Integer arithmetic only: epoch seconds times 10^7 exceeds the exact
    # integer range of a float and would silently lose the low digits.
    elapsed = moment.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (elapsed // timedelta(microseconds=1)) * 10 + _FILETIME_UNIX_DELTA


def creation_stamps(*, timeout: int = 120) -> dict[int, int]:
    """Map every visible PID to its creation stamp.

    Returns an empty mapping when the query cannot run. An empty mapping is
    unknown evidence, never proof that a process is gone.
    """
    try:
        completed = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-Command", _STAMP_QUERY],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    stamps: dict[int, int] = {}
    for line in (completed.stdout or "").splitlines():
        pid_text, _, created = line.strip().partition("|")
        if not created:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        stamp = _stamp_from_iso(created)
        if stamp is not None:
            stamps[pid] = stamp
    return stamps


def classify_unverifiable(pid: int, birth, stamps: dict[int, int]) -> str:
    """Say what an unreadable PID means, without touching the process."""
    if not stamps:
        return UNKNOWN
    current = stamps.get(pid)
    if current is None:
        return GONE
    if not birth:
        return UNKNOWN
    try:
        recorded = int(birth)
    except (TypeError, ValueError):
        return UNKNOWN
    if abs(current - recorded) <= SAME_PROCESS_TOLERANCE:
        return SAME
    return RECYCLED
