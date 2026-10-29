"""Launch an owned Windows process tree inside a Job Object before it executes.

This is a process-lifetime primitive, not a ComfyUI handoff controller. Closing
the job on supervisor failure kills its owned tree; the caller must retain the
resource lease and reconcile any interrupted external submission.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from shine.comfyops.isolated_stage import WindowsJob


def inspect_named_group(name, pid, birth):
    """Read-only cross-process membership check; never terminates or adopts a process."""
    if (os.name != "nt" or not isinstance(name, str)
            or not re.fullmatch(r"Local\\ShineHandoff-[a-f0-9]{32}", name)
            or type(pid) is not int or pid <= 0 or not isinstance(birth, str) or not birth.isdecimal()):
        raise ValueError("NAMED_JOB_ID_INVALID")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    job = kernel.OpenJobObjectW(0x0004, False, name)  # JOB_OBJECT_QUERY only.
    if not job:
        if ctypes.get_last_error() == 2:
            return False
        raise OSError(ctypes.get_last_error(), "NAMED_JOB_QUERY_UNAVAILABLE")
    try:
        process = kernel.OpenProcess(0x101000, False, pid)
        if not process:
            if ctypes.get_last_error() == 87:
                return False
            raise OSError(ctypes.get_last_error(), "NAMED_JOB_PROCESS_UNAVAILABLE")
        try:
            state = kernel.WaitForSingleObject(process, 0)
            if state == 0:
                return False
            if state != 258:
                raise OSError(ctypes.get_last_error(), "NAMED_JOB_PROCESS_UNAVAILABLE")
            times = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(process, *(ctypes.byref(value) for value in times)):
                raise OSError(ctypes.get_last_error(), "NAMED_JOB_PROCESS_UNAVAILABLE")
            if str(times[0].value) != birth:
                return False
            member = wintypes.BOOL()
            if not kernel.IsProcessInJob(process, job, ctypes.byref(member)):
                raise OSError(ctypes.get_last_error(), "NAMED_JOB_MEMBERSHIP_UNAVAILABLE")
            return bool(member.value)
        finally:
            kernel.CloseHandle(process)
    finally:
        kernel.CloseHandle(job)


class OwnedProcessGroup:
    def __init__(self, job, process_handle, pid, recipe, pins, source_handles):
        self._job = job
        self._process = process_handle
        self.pid = pid
        self._recipe = recipe
        self._pins = pins
        self._source_handles = source_handles
        self._terminated = False
        self._relaunch_attempted = False

    @classmethod
    def launch(cls, argv, *, cwd, env=None, pinned_files=(), job_name=None, _expected_pins=None):
        if os.name != "nt":
            raise RuntimeError("OWNED_PROCESS_GROUP_REQUIRES_WINDOWS")
        if not argv or not Path(argv[0]).is_absolute() or not Path(cwd).is_dir():
            raise ValueError("OWNED_PROCESS_LAUNCH_INVALID")
        pins = {}
        for source in pinned_files:
            path = Path(source).resolve(strict=True)
            if not path.is_file():
                raise ValueError("OWNED_PROCESS_SOURCE_INVALID")
            with path.open("rb") as stream:
                pins[str(path)] = hashlib.file_digest(stream, "sha256").hexdigest()
        if pins and str(Path(argv[0]).resolve(strict=True)) not in pins:
            raise ValueError("OWNED_PROCESS_EXECUTABLE_NOT_PINNED")
        if _expected_pins is not None and pins != _expected_pins:
            raise ValueError("OWNED_PROCESS_SOURCE_CHANGED")
        recipe = {"argv": tuple(str(arg) for arg in argv), "cwd": str(Path(cwd).resolve()),
                  "env": dict(os.environ if env is None else env), "job_name": job_name}
        env = recipe["env"]
        import ctypes
        from ctypes import wintypes

        class StartupInfo(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD),
                        ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                        ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD),
                        ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                        ("hStdError", wintypes.HANDLE)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                          wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                          ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]
        kernel.CreateProcessW.restype = wintypes.BOOL
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        job = WindowsJob(name=job_name)
        startup, info = StartupInfo(), ProcessInfo()
        startup.cb = ctypes.sizeof(startup)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(arg) for arg in argv]))
        environment = None
        if env is not None:
            if any("\x00" in str(k) or "\x00" in str(v) or "=" in str(k) for k, v in env.items()):
                job.close()
                raise ValueError("OWNED_PROCESS_ENV_INVALID")
            environment = ctypes.create_unicode_buffer(
                "\x00".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].upper()))
                + "\x00\x00"
            )
        created = False
        source_handles = []
        try:
            for name, expected in pins.items():
                handle = kernel.CreateFileW(name, 0x80000000, 1, None, 3, 0x80, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), "OWNED_PROCESS_SOURCE_LOCK_FAILED")
                source_handles.append(handle)
                with Path(name).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                        raise ValueError("OWNED_PROCESS_SOURCE_CHANGED")
            flags = 0x00000004 | 0x08000000 | (0x00000400 if environment is not None else 0)
            if not kernel.CreateProcessW(str(argv[0]), command, None, None, False, flags, environment,
                                         str(cwd), ctypes.byref(startup), ctypes.byref(info)):
                raise OSError(ctypes.get_last_error(), "CreateProcessW")
            created = True
            job.attach(info.dwProcessId)
            if _expected_pins is not None:
                for name, expected in _expected_pins.items():
                    try:
                        with Path(name).open("rb") as stream:
                            actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    except OSError as exc:
                        raise ValueError("OWNED_PROCESS_SOURCE_CHANGED") from exc
                    if actual != expected:
                        raise ValueError("OWNED_PROCESS_SOURCE_CHANGED")
            if kernel.ResumeThread(info.hThread) == 0xFFFFFFFF:
                raise OSError(ctypes.get_last_error(), "ResumeThread")
            return cls(job, info.hProcess, info.dwProcessId, recipe, pins, source_handles)
        except BaseException:
            if created:
                kernel.TerminateProcess(info.hProcess, 1)
                kernel.CloseHandle(info.hProcess)
            job.close()
            for handle in source_handles:
                kernel.CloseHandle(handle)
            raise
        finally:
            if created:
                kernel.CloseHandle(info.hThread)

    def terminate(self, *, timeout=10):
        """Terminate this job and confirm the kernel reports no remaining members."""
        from ctypes import wintypes

        if not self._job or not self._job._handle or timeout <= 0:
            raise RuntimeError("OWNED_PROCESS_GROUP_UNAVAILABLE")
        kernel = self._job._kernel
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        deadline = time.monotonic() + timeout
        evidence = self._job.terminate_and_wait(timeout=timeout)
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if (type(evidence.get("active_processes")) is not int or evidence["active_processes"] != 0
                or kernel.WaitForSingleObject(self._process, remaining_ms) != 0):
            raise RuntimeError("OWNED_PROCESS_GROUP_EXIT_UNCONFIRMED")
        self._terminated = True
        self._close_source_handles()
        return {"pid": self.pid, **evidence}

    def _close_source_handles(self):
        if self._source_handles:
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            for handle in self._source_handles:
                kernel.CloseHandle(handle)
            self._source_handles = []

    def relaunch(self):
        """Launch the same recorded recipe once, only after confirmed group exit."""
        if not self._terminated:
            raise ValueError("OWNED_PROCESS_EXIT_UNCONFIRMED")
        if self._relaunch_attempted:
            raise ValueError("OWNED_PROCESS_RELAUNCH_ALREADY_ATTEMPTED")
        if not self._pins:
            raise ValueError("OWNED_PROCESS_SOURCES_NOT_PINNED")
        for name, expected in self._pins.items():
            try:
                with Path(name).open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as exc:
                raise ValueError("OWNED_PROCESS_SOURCE_CHANGED") from exc
            if actual != expected:
                raise ValueError("OWNED_PROCESS_SOURCE_CHANGED")
        self._relaunch_attempted = True  # An uncertain launch is never automatically repeated.
        return type(self).launch(self._recipe["argv"], cwd=self._recipe["cwd"], env=self._recipe["env"],
                                 pinned_files=list(self._pins), _expected_pins=self._pins,
                                 job_name=("Local\\ShineHandoff-" + uuid4().hex
                                           if self._recipe["job_name"] else None))

    @property
    def starts_drained(self):
        return self._recipe["env"].get("SHINE_COMFY_START_DRAINED") == "1"

    @property
    def job_name(self):
        return self._recipe["job_name"]

    def owns(self, pid, birth):
        """Check liveness, birth and job membership using one process handle."""
        import ctypes
        from ctypes import wintypes

        if not self._job or not self._job._handle:
            raise RuntimeError("OWNED_PROCESS_GROUP_UNAVAILABLE")
        if type(pid) is not int or pid <= 0 or not isinstance(birth, str) or not birth:
            return False
        kernel = self._job._kernel
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        handle = kernel.OpenProcess(0x101000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise OSError(ctypes.get_last_error(), "OWNED_PROCESS_IDENTITY_UNKNOWN")
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            if state == 0:
                return False
            if state != 258:
                raise OSError(ctypes.get_last_error(), "OWNED_PROCESS_LIVENESS_UNKNOWN")
            times = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                raise OSError(ctypes.get_last_error(), "OWNED_PROCESS_BIRTH_UNKNOWN")
            if str(times[0].value) != birth:
                return False
            member = wintypes.BOOL()
            if not kernel.IsProcessInJob(handle, self._job._handle, ctypes.byref(member)):
                raise OSError(ctypes.get_last_error(), "OWNED_PROCESS_MEMBERSHIP_UNKNOWN")
            return bool(member.value)
        finally:
            kernel.CloseHandle(handle)

    def close(self):
        if self._job:
            self._job.close()
            self._job = None
        if self._process:
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(self._process)
            self._process = None
        self._close_source_handles()
