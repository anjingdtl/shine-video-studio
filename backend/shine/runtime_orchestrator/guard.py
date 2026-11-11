"""Installation-scoped lifecycle guard.

The GPU lease proves *resource admission*; it is not an OS process lock.  A
lifecycle action additionally needs mutual exclusion per **physical
installation identity** so two executors (an image start and an H3 start, or
two workers during a restart overlap) can never both pass their "nothing else
runs here" check and each launch a renderer for one ComfyUI install.

The guard is a named Windows mutex held only for short critical sections
(checks + record flips).  It is deliberately NOT held across process startup,
model loading or HTTP polling: those windows are protected by the durable
startup records written inside the section.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from contextlib import contextmanager

_GUARD_PREFIX = "Global\\ShineRendererLifecycle-"
_GUARD_TIMEOUT_SECONDS = 30.0
_ABANDONED = 0x80
_WAIT_OBJECT_0 = 0x0
_SYNCHRONIZE = 0x00100000


def lifecycle_guard_name(installation_id: str) -> str:
    canonical = str(installation_id).strip().lower()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]
    return _GUARD_PREFIX + digest


class LifecycleGuardBusy(RuntimeError):
    """Another executor holds this installation's lifecycle section."""


@contextmanager
def renderer_lifecycle_guard(installation_id: str, timeout_s: float = _GUARD_TIMEOUT_SECONDS):
    """Serialize lifecycle decisions for one installation across processes.

    Non-Windows platforms fall back to no OS lock; there the SQLite record
    checks plus the single resident Worker remain the serialization, and the
    tests exercise the decision logic, not the mutex.
    """
    if os.name != "nt":
        yield None
        return
    name = lifecycle_guard_name(installation_id)
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if not handle:
        # Without the OS lock the durable records are still checked below;
        # failing open on lock-creation failure is safer for a personal
        # workstation than refusing every lifecycle action forever.
        yield None
        return
    try:
        wait = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
        if wait not in (_WAIT_OBJECT_0, _ABANDONED):
            raise LifecycleGuardBusy(
                f"another executor holds the lifecycle guard for {installation_id}"
            )
        try:
            yield handle
        finally:
            ctypes.windll.kernel32.ReleaseMutex(handle)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
