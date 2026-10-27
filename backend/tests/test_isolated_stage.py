"""Exercise real Windows child lifetime without loading any model."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shine.comfyops.installer import process_identity
from shine.comfyops.isolated_stage import run_isolated


def _return_pid():
    return os.getpid()


def _wait():
    time.sleep(30)


def _fail():
    raise ValueError("must not expose a model response")


def _write_pid_wait(path):
    Path(path).write_text(str(os.getpid()), encoding="utf8")
    time.sleep(30)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows job objects")
def test_stage_result_only_after_child_exits():
    pid = run_isolated(_return_pid, timeout=30)
    assert process_identity(pid) is None


@pytest.mark.skipif(os.name != "nt", reason="requires Windows job objects")
def test_stage_timeout_is_unknown_and_owned_child_is_killed():
    with pytest.raises(RuntimeError, match="OUTCOME_UNKNOWN"):
        run_isolated(_wait, timeout=3)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows job objects")
def test_stage_error_does_not_return_success_or_error_contents():
    with pytest.raises(RuntimeError, match="ISOLATED_STAGE_FAILED:ValueError") as exc:
        run_isolated(_fail, timeout=30)
    assert "must not expose" not in str(exc.value)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows job objects")
def test_supervisor_crash_kills_its_owned_stage(tmp_path):
    marker = tmp_path / "child.pid"
    code = """
import os,sys,threading,time
from shine.comfyops.isolated_stage import run_isolated
from tests.test_isolated_stage import _write_pid_wait
def crash():
    for _ in range(200):
        if os.path.exists(sys.argv[1]): os._exit(0)
        time.sleep(.05)
    os._exit(2)
threading.Thread(target=crash,daemon=True).start()
run_isolated(_write_pid_wait,(sys.argv[1],),timeout=30)
"""
    completed = subprocess.run([sys.executable, "-c", code, str(marker)], timeout=30, check=False)
    assert completed.returncode == 0
    pid = int(marker.read_text(encoding="utf8"))
    for _ in range(100):
        if process_identity(pid) is None:
            break
        time.sleep(.05)
    assert process_identity(pid) is None
