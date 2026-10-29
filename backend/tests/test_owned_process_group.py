"""Real Windows process-tree smoke, no models or ComfyUI required."""

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are required")


def test_managed_group_stops_child_and_grandchild(tmp_path):
    from shine.comfyops.installer import process_identity
    from shine.comfyops.owned_process import OwnedProcessGroup

    marker = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)"
    )
    group = OwnedProcessGroup.launch([sys.executable, "-c", code, str(marker)], cwd=tmp_path)
    try:
        deadline = time.monotonic() + 15
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.is_file()
        child_pid = int(marker.read_text())
        root_birth, child_birth = process_identity(group.pid), process_identity(child_pid)
        assert root_birth and child_birth
        assert group.owns(group.pid, root_birth)
        assert group.owns(child_pid, child_birth)
        assert not group.owns(os.getpid(), process_identity(os.getpid()))
        assert not group.owns(child_pid, "not-the-process-birth")
        assert group.terminate(timeout=10)["active_processes"] == 0
        assert process_identity(group.pid) is None and process_identity(child_pid) is None
    finally:
        group.close()


def test_supervisor_crash_kills_its_owned_group(tmp_path):
    from shine.comfyops.installer import process_identity

    marker = tmp_path / "owned.pid"
    code = (
        "import os,sys,pathlib; from shine.comfyops.owned_process import OwnedProcessGroup; "
        "g=OwnedProcessGroup.launch([sys.executable,'-c','import time; time.sleep(120)'],cwd=sys.argv[1]); "
        "pathlib.Path(sys.argv[2]).write_text(str(g.pid)); os._exit(0)"
    )
    supervisor = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(marker)],
                                capture_output=True, timeout=20, check=False)
    assert supervisor.returncode == 0, supervisor.stderr.decode(errors="replace")
    assert marker.is_file()
    pid = int(marker.read_text())
    deadline = time.monotonic() + 10
    while process_identity(pid) is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process_identity(pid) is None


def test_managed_group_accepts_explicit_unicode_environment(tmp_path):
    from shine.comfyops.owned_process import OwnedProcessGroup

    marker = tmp_path / "env.txt"
    env = dict(os.environ, SHINE_TEST_PROBE="隔离值")
    code = ("import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text("
            "os.environ['SHINE_TEST_PROBE'],encoding='utf-8'); time.sleep(60)")
    group = OwnedProcessGroup.launch([sys.executable, "-c", code, str(marker)], cwd=tmp_path, env=env)
    try:
        deadline = time.monotonic() + 10
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.read_text(encoding="utf-8") == "隔离值"
        assert group.terminate()["active_processes"] == 0
    finally:
        group.close()


def test_restart_requires_confirmed_exit_and_unchanged_pinned_sources(tmp_path):
    from shine.comfyops.owned_process import OwnedProcessGroup

    script = tmp_path / "renderer.py"
    script.write_text("import time; time.sleep(120)", encoding="utf-8")
    group = OwnedProcessGroup.launch([sys.executable, str(script)], cwd=tmp_path,
                                     pinned_files=[sys.executable, script])
    replacement = None
    try:
        with pytest.raises(ValueError, match="EXIT_UNCONFIRMED"):
            group.relaunch()
        assert group.terminate()["active_processes"] == 0
        script.write_text("import time; time.sleep(121)", encoding="utf-8")
        with pytest.raises(ValueError, match="SOURCE_CHANGED"):
            group.relaunch()
        script.write_text("import time; time.sleep(120)", encoding="utf-8")
        replacement = group.relaunch()
        assert replacement.pid != group.pid
        assert replacement.terminate()["active_processes"] == 0
        with pytest.raises(ValueError, match="RELAUNCH_ALREADY_ATTEMPTED"):
            group.relaunch()
    finally:
        if replacement:
            replacement.close()
        group.close()


def test_renderer_group_never_confirms_exit_without_waiting_owned_members(tmp_path, monkeypatch):
    from shine.comfyops.owned_process import OwnedProcessGroup

    group = OwnedProcessGroup.launch([sys.executable, "-c", "import time; time.sleep(120)"], cwd=tmp_path)
    try:
        def uncertain(*, timeout):
            raise RuntimeError("GROUP_EXIT_UNCONFIRMED")

        monkeypatch.setattr(group._job, "terminate_and_wait", uncertain)
        with pytest.raises(RuntimeError, match="GROUP_EXIT_UNCONFIRMED"):
            group.terminate()
    finally:
        group.close()


def test_restart_rejects_source_changed_between_check_and_launch(tmp_path):
    from shine.comfyops.owned_process import OwnedProcessGroup

    script = tmp_path / "renderer.py"
    script.write_text("import time; time.sleep(120)", encoding="utf-8")

    class RacingGroup(OwnedProcessGroup):
        race = False
        leaked = None

        @classmethod
        def launch(cls, *args, **kwargs):
            if cls.race:
                script.write_text("import time; time.sleep(121)", encoding="utf-8")
            result = super().launch(*args, **kwargs)
            if cls.race:
                cls.leaked = result
            return result

    group = RacingGroup.launch([sys.executable, str(script)], cwd=tmp_path, pinned_files=[sys.executable, script])
    try:
        group.terminate()
        RacingGroup.race = True
        with pytest.raises(ValueError, match="SOURCE_CHANGED"):
            group.relaunch()
    finally:
        if RacingGroup.leaked:
            RacingGroup.leaked.terminate()
            RacingGroup.leaked.close()
        group.close()


def test_pinned_source_cannot_be_replaced_while_group_runs(tmp_path):
    from shine.comfyops.owned_process import OwnedProcessGroup

    script = tmp_path / "renderer.py"
    script.write_text("import time; time.sleep(120)", encoding="utf-8")
    group = OwnedProcessGroup.launch([sys.executable, str(script)], cwd=tmp_path,
                                     pinned_files=[sys.executable, script])
    try:
        with pytest.raises(PermissionError):
            script.write_text("changed", encoding="utf-8")
        with pytest.raises(PermissionError):
            script.unlink()
        group.terminate()
        script.write_text("changed", encoding="utf-8")
    finally:
        group.close()


def test_named_group_cross_process_verification_and_collision_safety(tmp_path):
    from uuid import uuid4

    from shine.comfyops.installer import process_identity
    from shine.comfyops.owned_process import OwnedProcessGroup, inspect_named_group

    name = "Local\\ShineHandoff-" + uuid4().hex
    group = OwnedProcessGroup.launch([sys.executable, "-c", "import time; time.sleep(120)"], cwd=tmp_path,
                                     job_name=name)
    try:
        birth = process_identity(group.pid)
        assert birth and inspect_named_group(name, group.pid, birth)
        assert not inspect_named_group(name, group.pid, "1")
        code = ("import sys; from shine.comfyops.owned_process import inspect_named_group; "
                "sys.exit(0 if inspect_named_group(sys.argv[1], int(sys.argv[2]), sys.argv[3]) else 1)")
        result = subprocess.run([sys.executable, "-c", code, name, str(group.pid), birth],
                                capture_output=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        with pytest.raises(OSError, match="NAMED_JOB_ALREADY_EXISTS"):
            OwnedProcessGroup.launch([sys.executable, "-c", "pass"], cwd=tmp_path, job_name=name)
        assert group.owns(group.pid, birth)
        group.terminate()
        assert not inspect_named_group(name, group.pid, birth)
    finally:
        group.close()


def test_named_group_does_not_survive_supervisor_crash(tmp_path):
    from uuid import uuid4

    from shine.comfyops.installer import process_identity
    from shine.comfyops.owned_process import inspect_named_group

    name = "Local\\ShineHandoff-" + uuid4().hex
    marker = tmp_path / "owned.pid"
    code = (
        "import os,sys,pathlib; from shine.comfyops.owned_process import OwnedProcessGroup; "
        "g=OwnedProcessGroup.launch([sys.executable,'-c','import time; time.sleep(120)'],"
        "cwd=sys.argv[1],job_name=sys.argv[3]); pathlib.Path(sys.argv[2]).write_text(str(g.pid)); os._exit(0)"
    )
    supervisor = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(marker), name],
                                capture_output=True, timeout=20, check=False)
    assert supervisor.returncode == 0 and marker.is_file()
    pid = int(marker.read_text())
    deadline = time.monotonic() + 10
    while process_identity(pid) is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process_identity(pid) is None
    assert not inspect_named_group(name, pid, "1")
