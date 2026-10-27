"""A model stage whose owned process must exit before its result is returned.

The child waits for an explicit start message.  On Windows it is first attached
to a kill-on-close Job Object, so a supervisor crash cannot leave an inference
process running outside the shared resource ledger.
"""

from __future__ import annotations

import multiprocessing
import os
import time


def _child(connection, target, args):
    try:
        connection.send("READY")
        if connection.recv() != "START":
            return
        try:
            value = target(*args)
        except BaseException as exc:
            detail = type(exc).__name__
            if isinstance(exc, ModuleNotFoundError) and exc.name:
                detail += ":" + exc.name
            connection.send(("ERROR", detail))
        else:
            connection.send(("RESULT", value))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class WindowsJob:
    """Bind one owned child to the lifetime of this supervisor process."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("ISOLATED_STAGE_REQUIRES_WINDOWS_JOB_OBJECT")
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self._kernel
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._handle = k.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject")

    def attach(self, pid):
        import ctypes

        process = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise OSError(ctypes.get_last_error(), "OpenProcess")
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        finally:
            self._kernel.CloseHandle(process)

    def close(self):
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def run_isolated(target, args=(), *, timeout=300, job_factory=WindowsJob, return_evidence=False):
    """Execute exactly once; unknown/failed outcomes never return as success."""
    if timeout <= 0:
        raise ValueError("ISOLATED_STAGE_TIMEOUT_INVALID")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=_child, args=(child, target, args), daemon=False)
    job = job_factory()
    started = False
    deadline = time.monotonic() + timeout
    try:
        process.start()
        started = True
        child.close()
        if not parent.poll(max(0, deadline - time.monotonic())) or parent.recv() != "READY":
            raise RuntimeError("ISOLATED_STAGE_START_UNCONFIRMED")
        job.attach(process.pid)
        parent.send("START")
        if not parent.poll(max(0, deadline - time.monotonic())):
            raise RuntimeError("ISOLATED_STAGE_OUTCOME_UNKNOWN")
        try:
            status, value = parent.recv()
        except (EOFError, OSError) as exc:
            raise RuntimeError("ISOLATED_STAGE_OUTCOME_UNKNOWN") from exc
        process.join(max(0, deadline - time.monotonic()))
        if process.is_alive() or process.exitcode != 0:
            raise RuntimeError("ISOLATED_STAGE_EXIT_UNCONFIRMED")
        if status != "RESULT":
            raise RuntimeError("ISOLATED_STAGE_FAILED:" + str(value))
        evidence = {"pid": process.pid, "exitcode": process.exitcode, "joined": True}
        return (value, evidence) if return_evidence else value
    finally:
        parent.close()
        child.close()
        job.close()  # closes/kills the owned process even if an exception occurred
        if started:
            process.join(timeout=10)
            if process.is_alive():
                raise RuntimeError("ISOLATED_STAGE_EXIT_UNCONFIRMED")
