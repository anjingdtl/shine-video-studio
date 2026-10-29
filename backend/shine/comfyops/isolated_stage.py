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

    def __init__(self, name=None):
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
        if name is not None:
            import re

            if not isinstance(name, str) or not re.fullmatch(r"Local\\ShineHandoff-[a-f0-9]{32}", name):
                raise ValueError("NAMED_JOB_ID_INVALID")
        ctypes.set_last_error(0)
        self._handle = k.CreateJobObjectW(None, name)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        if name is not None and ctypes.get_last_error() == 183:
            self.close()
            raise OSError(183, "NAMED_JOB_ALREADY_EXISTS")
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

    def terminate_and_wait(self, *, timeout):
        """Stop remaining owned descendants and prove the Job Object is empty."""
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = (
                [(name, ctypes.c_longlong) for name in (
                    "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
                + [(name, wintypes.DWORD) for name in (
                    "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
            )

        if not self._handle or timeout <= 0:
            raise RuntimeError("ISOLATED_GROUP_EXIT_UNCONFIRMED")
        kernel = self._kernel
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wintypes.DWORD, ctypes.c_void_p]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        handles = []
        deadline = time.monotonic() + timeout
        try:
            capacity = 64
            while True:
                size = 8 + capacity * ctypes.sizeof(ctypes.c_size_t)
                members = ctypes.create_string_buffer(size)
                if kernel.QueryInformationJobObject(self._handle, 3, members, size, None):
                    break
                if ctypes.get_last_error() != 234 or capacity >= 65536:
                    raise OSError(ctypes.get_last_error(), "QueryInformationJobObjectMembers")
                capacity *= 2
            count = ctypes.c_uint32.from_buffer_copy(members, 4).value
            if count > capacity:
                raise RuntimeError("ISOLATED_GROUP_MEMBERS_UNCONFIRMED")
            for index in range(count):
                pid = ctypes.c_size_t.from_buffer_copy(members, 8 + index * ctypes.sizeof(ctypes.c_size_t)).value
                handle = kernel.OpenProcess(0x101000, False, pid)
                if not handle:
                    if ctypes.get_last_error() == 87:  # Already gone.
                        continue
                    raise OSError(ctypes.get_last_error(), "OpenProcessGroupMember")
                member = wintypes.BOOL()
                if not kernel.IsProcessInJob(handle, self._handle, ctypes.byref(member)):
                    kernel.CloseHandle(handle)
                    raise OSError(ctypes.get_last_error(), "IsProcessInJob")
                if member.value:
                    handles.append(handle)
                else:
                    kernel.CloseHandle(handle)  # PID was reused after the group snapshot.
            if not kernel.TerminateJobObject(self._handle, 0):
                raise OSError(ctypes.get_last_error(), "TerminateJobObject")
            for handle in handles:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if kernel.WaitForSingleObject(handle, remaining_ms) != 0:
                    raise RuntimeError("ISOLATED_GROUP_EXIT_UNCONFIRMED")
            while True:
                info = Accounting()
                if not kernel.QueryInformationJobObject(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                    raise OSError(ctypes.get_last_error(), "QueryInformationJobObject")
                if info.ActiveProcesses == 0:
                    return {"active_processes": 0, "total_processes": info.TotalProcesses,
                            "members_waited": len(handles)}
                if time.monotonic() >= deadline:
                    raise RuntimeError("ISOLATED_GROUP_EXIT_UNCONFIRMED")
                time.sleep(0.05)
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)

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
        from shine.comfyops.installer import process_identity

        birth = process_identity(process.pid)
        if not birth:
            raise RuntimeError("ISOLATED_STAGE_IDENTITY_UNCONFIRMED")
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
        group = job.terminate_and_wait(timeout=max(0, deadline - time.monotonic()))
        if type(group.get("active_processes")) is not int or group["active_processes"] != 0:
            raise RuntimeError("ISOLATED_GROUP_EXIT_UNCONFIRMED")
        if status != "RESULT":
            raise RuntimeError("ISOLATED_STAGE_FAILED:" + str(value))
        evidence = {"pid": process.pid, "birth": birth, "exitcode": process.exitcode, "joined": True,
                    "group_active_processes": 0, "group_members_waited": group["members_waited"]}
        return (value, evidence) if return_evidence else value
    finally:
        parent.close()
        child.close()
        job.close()  # closes/kills the owned process even if an exception occurred
        if started:
            process.join(timeout=10)
            if process.is_alive():
                raise RuntimeError("ISOLATED_STAGE_EXIT_UNCONFIRMED")
