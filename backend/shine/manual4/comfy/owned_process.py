"""Mirrored Windows Job Object + process birth identity for Manual4 ComfyUI.

Uses ``Local\\ShineM4Handoff-<hex>`` job names so Manual4 managed ComfyUI
instances can never collide with or terminate seven-stage ``ShineHandoff-*``
instances.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

M4_JOB_RE = re.compile(r"Local\\ShineM4Handoff-[a-f0-9]{32}")


def m4_process_identity(pid: int) -> str | None:
    """Return OS birth stamp when ``pid`` is alive, or ``None`` when provably gone."""
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.OpenProcess(0x101000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return None
            raise ValueError("M4_PROCESS_IDENTITY_UNKNOWN")
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            if state == 0:
                return None
            if state != 258:
                raise ValueError("M4_PROCESS_IDENTITY_UNKNOWN")
            values = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                raise ValueError("M4_PROCESS_IDENTITY_UNKNOWN")
            return str(values[0].value)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return str(pid)
    except OSError:
        return None


def m4_pick_free_port(start: int = 8198, *, exclude: set[int] | None = None) -> int:
    excluded = set(exclude or {8188, 8000, 5173, 8790, 8116, 8117})
    for port in range(start, min(65535, start + 60)):
        if port in excluded:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free loopback port found starting at {start}")


def m4_group_active_processes(job_name: str) -> int:
    if os.name != "nt" or not M4_JOB_RE.fullmatch(job_name or ""):
        return 0

    class Accounting(ctypes.Structure):
        _fields_ = (
            [(f, ctypes.c_longlong) for f in (
                "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime"
            )]
            + [(f, wintypes.DWORD) for f in (
                "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses"
            )]
        )

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.OpenJobObjectW(0x0004, False, job_name)
    if not job:
        if ctypes.get_last_error() == 2:
            return 0
        raise OSError(ctypes.get_last_error(), "M4_JOB_QUERY_UNAVAILABLE")
    try:
        info = Accounting()
        if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError(ctypes.get_last_error(), "QueryInformationJobObject")
        return int(info.ActiveProcesses)
    finally:
        kernel.CloseHandle(job)


def m4_terminate_named_group(job_name: str, *, timeout_s: float = 10.0) -> dict[str, int | str]:
    if os.name != "nt":
        return {"job_name": job_name, "active_processes": 0}
    if not M4_JOB_RE.fullmatch(job_name or ""):
        raise ValueError("M4_JOB_NAME_INVALID")

    class Accounting(ctypes.Structure):
        _fields_ = (
            [(f, ctypes.c_longlong) for f in (
                "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime"
            )]
            + [(f, wintypes.DWORD) for f in (
                "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses"
            )]
        )

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.OpenJobObjectW(0x0008 | 0x0004, False, job_name)
    if not job:
        if ctypes.get_last_error() == 2:
            return {"job_name": job_name, "active_processes": 0}
        raise OSError(ctypes.get_last_error(), "M4_JOB_TERMINATE_UNAVAILABLE")
    try:
        if not kernel.TerminateJobObject(job, 0):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject")
        deadline = time.monotonic() + timeout_s
        while True:
            info = Accounting()
            if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise OSError(ctypes.get_last_error(), "QueryInformationJobObject")
            if info.ActiveProcesses == 0:
                return {"job_name": job_name, "active_processes": 0}
            if time.monotonic() >= deadline:
                raise RuntimeError("M4_JOB_EXIT_UNCONFIRMED")
            time.sleep(0.05)
    finally:
        kernel.CloseHandle(job)


class M4WindowsJob:
    def __init__(self, job_name: str):
        if os.name != "nt" or not M4_JOB_RE.fullmatch(job_name):
            raise ValueError("M4_JOB_NAME_INVALID")

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(f, ctypes.c_ulonglong) for f in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self._kernel
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        ctypes.set_last_error(0)
        self._handle = k.CreateJobObjectW(None, job_name)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        if ctypes.get_last_error() == 183:
            self.close()
            raise OSError(183, "M4_NAMED_JOB_ALREADY_EXISTS")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(err, "SetInformationJobObject")
        self.job_name = job_name

    def attach(self, pid: int) -> None:
        proc = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not proc:
            raise OSError(ctypes.get_last_error(), "OpenProcess")
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, proc):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        finally:
            self._kernel.CloseHandle(proc)

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel.CloseHandle(self._handle)
            self._handle = None


class M4OwnedProcess:
    def __init__(self, job: M4WindowsJob | None, pid: int, birth: str, job_name: str):
        self.job = job
        self.pid = pid
        self.birth = birth
        self.job_name = job_name

    @classmethod
    def launch(cls, argv: list[str], *, cwd: Path, env: dict[str, str], job_name: str) -> M4OwnedProcess:
        return launch_m4_owned_process(argv, cwd=cwd, env=env, job_name=job_name)


def launch_m4_owned_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    job_name: str,
) -> M4OwnedProcess:
    if os.name != "nt":
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env)
        return M4OwnedProcess(None, proc.pid, str(proc.pid), job_name)

    class StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo),
    ]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    job = M4WindowsJob(job_name)
    startup, info = StartupInfo(), ProcessInfo()
    startup.cb = ctypes.sizeof(startup)
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
    env_block = ctypes.create_unicode_buffer(
        "\x00".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda x: x[0].upper())) + "\x00\x00"
    )
    created = False
    try:
        flags = 0x00000004 | 0x08000000 | 0x00000400  # SUSPENDED | NO_WINDOW | UNICODE_ENV
        if not kernel.CreateProcessW(
            str(argv[0]), command, None, None, False, flags, env_block,
            str(cwd), ctypes.byref(startup), ctypes.byref(info),
        ):
            raise OSError(ctypes.get_last_error(), "CreateProcessW")
        created = True
        job.attach(info.dwProcessId)
        birth = m4_process_identity(info.dwProcessId) or ""
        if kernel.ResumeThread(info.hThread) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
        kernel.CloseHandle(info.hProcess)
        return M4OwnedProcess(job, int(info.dwProcessId), birth, job_name)
    except BaseException:
        if created:
            kernel.TerminateProcess(info.hProcess, 1)
            kernel.CloseHandle(info.hProcess)
        job.close()
        raise
    finally:
        if created:
            kernel.CloseHandle(info.hThread)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
