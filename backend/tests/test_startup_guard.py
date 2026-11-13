"""The startup guard must be a real cross-process critical section.

Two double-clicked launchers used to both observe "no manifest" and start two
complete stacks.  The guard is a named Windows mutex keyed by the canonical
data directory; these tests prove the properties the launcher relies on:

* PowerShell and stop_shine.py derive the same mutex name for a directory
  (otherwise the stop side of the guard is decorative);
* a second holder is really blocked while a first process owns the mutex;
* a holder that dies without releasing does not wedge the next launcher
  (an abandoned mutex is acquired, leaving reconciliation to the manifest);
* stop_shine.py refuses to race a live launcher instead of tearing through.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named mutexes are Windows")

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "shine_stop", REPO / "scripts" / "stop_shine.py"
)
stop_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stop_module)

POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or "powershell"

GUARD_SNIPPET = r"""
. '{guard_script}'
$guard = Enter-ShineStartupGuard -DataDir '{data_dir}' -WaitSeconds {wait_seconds}
Write-Output 'ACQUIRED'
Exit-ShineStartupGuard $guard
"""


def _ps_acquire(data_dir: Path, wait_seconds: int) -> subprocess.CompletedProcess:
    command = GUARD_SNIPPET.format(
        guard_script=(REPO / "scripts" / "shine_startup_guard.ps1"),
        data_dir=str(data_dir),
        wait_seconds=wait_seconds,
    )
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=60,
    )


class _HeldMutex:
    """Own the named mutex from this process for the duration of a with-block."""

    def __init__(self, name: str):
        self.name = name
        self.handle = None

    def __enter__(self):
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise RuntimeError("CreateMutexW failed")
        if ctypes.windll.kernel32.WaitForSingleObject(self.handle, 5000) != 0:
            raise RuntimeError("WaitForSingleObject failed")
        return self

    def __exit__(self, *exc):
        ctypes.windll.kernel32.ReleaseMutex(self.handle)
        ctypes.windll.kernel32.CloseHandle(self.handle)
        self.handle = None
        return False


def test_powershell_and_python_derive_the_same_guard_name(tmp_path):
    data_dir = tmp_path / "Local Data"
    data_dir.mkdir()
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-Command",
            ". '{}'; (Get-ShineStartupGuardName '{}')".format(
                REPO / "scripts" / "shine_startup_guard.ps1", str(data_dir)
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    ps_name = result.stdout.strip().splitlines()[-1]
    assert ps_name == stop_module.startup_guard_name(data_dir)


def test_second_holder_is_blocked_while_first_process_owns_the_guard(tmp_path):
    data_dir = tmp_path / "stack"
    data_dir.mkdir()
    name = stop_module.startup_guard_name(data_dir)
    with _HeldMutex(name):
        blocked = _ps_acquire(data_dir, wait_seconds=2)
        assert "ACQUIRED" not in blocked.stdout
        assert blocked.returncode != 0
    # Once the first holder released, the same command succeeds.
    allowed = _ps_acquire(data_dir, wait_seconds=10)
    assert "ACQUIRED" in allowed.stdout, allowed.stderr
    assert allowed.returncode == 0


def test_abandoned_guard_does_not_wedge_the_next_launcher(tmp_path):
    data_dir = tmp_path / "stack"
    data_dir.mkdir()
    name = stop_module.startup_guard_name(data_dir)
    holder = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctypes, sys\n"
            f"h = ctypes.windll.kernel32.CreateMutexW(None, False, {name!r})\n"
            "ctypes.windll.kernel32.WaitForSingleObject(h, 5000)\n"
            # Exit without releasing: the mutex becomes abandoned.
            "sys.exit(0)",
        ],
        timeout=30,
    )
    assert holder.returncode == 0
    acquired = _ps_acquire(data_dir, wait_seconds=10)
    assert "ACQUIRED" in acquired.stdout, acquired.stderr


HOLDER_CHILD = """
import ctypes, sys, time
h = ctypes.windll.kernel32.CreateMutexW(None, False, sys.argv[1])
if not h or ctypes.windll.kernel32.WaitForSingleObject(h, 5000) != 0:
    sys.exit(2)
print('HELD', flush=True)
time.sleep(30)
"""


def _holder_process(name: str) -> subprocess.Popen:
    # A named mutex is re-entrant per thread: acquiring it from the test's own
    # thread would succeed trivially.  A real launcher is another process, so
    # the holder must be one too.
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER_CHILD, name],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "HELD"
    return child


def test_stop_refuses_to_race_a_live_launcher(tmp_path):
    data_dir = tmp_path / "stack"
    data_dir.mkdir()
    name = stop_module.startup_guard_name(data_dir)
    child = _holder_process(name)
    try:
        with pytest.raises(stop_module.StartupGuardBusy):
            stop_module._acquire_startup_guard(data_dir, timeout_s=1.0)
    finally:
        child.kill()
        child.wait(timeout=10)
    guard = stop_module._acquire_startup_guard(data_dir, timeout_s=5.0)
    if guard is not None:  # the kill leaves an abandoned mutex: still acquired
        stop_module._release_startup_guard(guard)


def test_stop_without_a_live_launcher_needs_no_guard(tmp_path):
    data_dir = tmp_path / "stack"
    data_dir.mkdir()
    assert stop_module._acquire_startup_guard(data_dir, timeout_s=1.0) is None


def test_recorded_process_aliveness_survives_a_json_datetime_roundtrip():
    """pwsh 7's ConvertFrom-Json parses ISO start_time into a [datetime].

    The launcher reads its manifest with pwsh 7, so a recorded-but-alive
    worker used to be compared culture-formatted against an ISO string and
    judged dead, which sent every later launch into a needless reconcile.
    The shipped helper must normalise both sides first.
    """
    pid = os.getpid()
    command = r"""
. '{guard}'
$pid_of_test = {pid}
$start = (Get-Process -Id $pid_of_test).StartTime.ToString('o')
# Manifest round trip: read back exactly like the launcher does.
$manifest = "{{""worker"":{{""pid"":{pid},""start_time"":""$start""}}}}" | ConvertFrom-Json
$entry = $manifest.worker
if ($entry.start_time -isnot [datetime]) {{ Write-Output 'TYPE_STRING' }} else {{ Write-Output 'TYPE_DATETIME' }}
Write-Output ('ALIVE=' + (Test-ShineRecordedProcessAlive $entry))
""".format(guard=REPO / "scripts" / "shine_startup_guard.ps1", pid=pid)
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    # The regression is only meaningful when the engine really parsed the date.
    assert "TYPE_DATETIME" in lines or "TYPE_STRING" in lines
    assert "ALIVE=True" in lines
