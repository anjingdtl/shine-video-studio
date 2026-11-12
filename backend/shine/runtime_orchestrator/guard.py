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

Lock order (fixed, never reversed):

    1. installation lifecycle mutex (this module), ascending installation id
    2. SQLite write transaction
    3. OS process handles / waits

A section never opens a write transaction and then waits for a mutex.  The only
place that needs several installations at once (a global release) takes them in
sorted order, so two executors cannot form a cycle.

Failure policy: a lifecycle decision that cannot take its OS lock is
**refused**, never silently treated as "no other executor".  Failing open here
is what allows a second managed renderer for one physical installation; the
durable records alone cannot close the window between "checked" and "launched".
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from contextlib import contextmanager

_GUARD_PREFIX = "Global\\ShineRendererLifecycle-"
_GUARD_TIMEOUT_SECONDS = 30.0
_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF

_held = threading.local()


class LifecycleGuardBusy(RuntimeError):
    """Another executor holds this installation's lifecycle section."""


class LifecycleGuardUnavailable(RuntimeError):
    """The OS lock could not be created or taken; the action must be refused."""


def lifecycle_guard_name(installation_id: str) -> str:
    canonical = str(installation_id).strip().lower()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]
    return _GUARD_PREFIX + digest


def _kernel32():
    """kernel32 with 64-bit-correct signatures for the three calls we make.

    Without explicit ``restype`` ctypes truncates the returned handle to a C
    int, so ``ReleaseMutex``/``CloseHandle`` would operate on a different
    handle than the one created — a silent way to lose the lock.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _held_names() -> dict[str, int]:
    names = getattr(_held, "names", None)
    if names is None:
        names = {}
        _held.names = names
    return names


def guard_is_held(installation_id: str) -> bool:
    """Whether this thread already holds the guard (test/diagnostic helper)."""
    return lifecycle_guard_name(installation_id) in _held_names()


@contextmanager
def renderer_lifecycle_guard(installation_id: str, timeout_s: float = _GUARD_TIMEOUT_SECONDS):
    """Serialize lifecycle decisions for one installation across processes.

    Re-entrant within a thread: a section that already holds this installation
    nests instead of waiting on itself (a stop invoked from inside a start's
    critical section is the ordinary case).  A *different* installation inside
    a section is a programming error unless it is taken in sorted order, which
    only the global release path does.

    Non-Windows platforms have no OS lock; there the durable record checks plus
    the single resident Worker remain the serialization, and the tests exercise
    the decision logic rather than the mutex.
    """
    if os.name != "nt":
        yield None
        return
    name = lifecycle_guard_name(installation_id)
    held = _held_names()
    if name in held:
        held[name] += 1
        try:
            yield None
        finally:
            held[name] -= 1
            if not held[name]:
                del held[name]
        return
    kernel32 = _kernel32()
    handle = kernel32.CreateMutexW(None, 0, name)
    if not handle:
        raise LifecycleGuardUnavailable(
            f"the lifecycle mutex for {installation_id} could not be created "
            f"(winerror={ctypes.get_last_error()})"
        )
    try:
        wait = kernel32.WaitForSingleObject(handle, int(max(timeout_s, 0.0) * 1000))
        if wait == _WAIT_FAILED:
            raise LifecycleGuardUnavailable(
                f"the lifecycle guard for {installation_id} could not be waited on "
                f"(winerror={ctypes.get_last_error()})"
            )
        if wait == _WAIT_TIMEOUT:
            raise LifecycleGuardBusy(
                f"another executor holds the lifecycle guard for {installation_id}"
            )
        if wait not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            raise LifecycleGuardUnavailable(
                f"the lifecycle guard for {installation_id} returned wait state {wait}"
            )
        held[name] = 1
        try:
            yield handle
        finally:
            del held[name]
            kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)
