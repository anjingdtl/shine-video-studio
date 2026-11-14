"""Managed tool resolution: the fix for "installed but still reported missing".

The Windows defect was not a missing installer: bootstrap wrote a shim into the
managed ``bin`` directory, but the API process kept reporting the tool missing
because a child process PATH entry cannot make ``CreateProcess`` resolve an
application name.  These contract tests exercise the resolution rules without
Windows: the managed directory is searched first, the absolute path is what is
executed, and a ``.cmd`` shim is only ever run through the command processor.
"""

from __future__ import annotations

import os
import stat

import pytest

from shine.comfyops import host_env

#: These contract tests exercise the POSIX resolution rules (an extensionless
#: sh shim in the managed directory).  ``CreateProcess`` cannot execute such a
#: script on Windows, so on a Windows host the *resolution* still runs for
#: real while the execution is injected; on a POSIX host the resolved
#: absolute path is really executed, exactly as production does.
POSIX_HOST = os.name != "nt"


def _probe_sh_tool(name, managed, output):
    if POSIX_HOST:
        return host_env.probe_command(name, search_paths=[str(managed)], windows=False)

    def runner(probe):  # noqa: ANN001 - the injected execution seam
        shim = managed / name
        if not shim.is_file():
            return False, ""
        return True, output

    return host_env.probe_command(name, search_paths=[str(managed)], windows=False, runner=runner)


def _write_tool(directory, name, body, *, executable=True):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture()
def clean_path(monkeypatch, tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def test_tool_only_in_managed_directory_is_resolved_to_an_absolute_path(tmp_path, clean_path):
    managed = tmp_path / "shine-data" / "tools" / "bin"
    shim = _write_tool(managed, "ffmpeg", "#!/bin/sh\necho 'ffmpeg version 8.0.1'\n")

    report = _probe_sh_tool("ffmpeg", managed, "ffmpeg version 8.0.1\n")

    assert report["present"] is True
    assert report["at_floor"] is True
    assert report["resolution"] == host_env.RESOLUTION_MANAGED
    assert report["resolved_path"] == str(shim)
    assert report["search_scope"] == "MANAGED_THEN_PROCESS_PATH"


def test_the_same_tool_is_missing_before_the_managed_directory_exists(tmp_path, clean_path):
    managed = tmp_path / "shine-data" / "tools" / "bin"

    before = _probe_sh_tool("ffmpeg", managed, "ffmpeg version 7.1\n")
    assert before["present"] is False

    _write_tool(managed, "ffmpeg", "#!/bin/sh\necho 'ffmpeg version 7.1'\n")
    # No restart, no PATH mutation: the next probe sees the installed tool.
    after = _probe_sh_tool("ffmpeg", managed, "ffmpeg version 7.1\n")
    assert after["present"] is True
    assert after["at_floor"] is True


def test_powershell_seven_in_the_managed_directory_meets_its_floor(tmp_path, clean_path):
    managed = tmp_path / "Tools with spaces" / "bin"
    _write_tool(managed, "pwsh", "#!/bin/sh\necho '7.4.6'\n")

    report = _probe_sh_tool("pwsh", managed, "7.4.6\n")

    assert report["present"] is True
    assert report["version"] == "7.4.6"
    assert report["at_floor"] is True
    assert report["resolved_path"].endswith("pwsh")


def test_node_and_npm_are_resolved_independently(tmp_path, clean_path):
    managed = tmp_path / "bin"
    _write_tool(managed, "node", "#!/bin/sh\necho 'v22.11.0'\n")
    _write_tool(managed, "npm", "#!/bin/sh\necho '10.9.0'\n")

    node = _probe_sh_tool("node", managed, "v22.11.0\n")
    npm = _probe_sh_tool("npm", managed, "10.9.0\n")

    assert node["at_floor"] is True and npm["at_floor"] is True
    assert node["resolved_path"] != npm["resolved_path"]


def test_uv_and_ffprobe_are_whitelisted_and_detected(tmp_path, clean_path):
    managed = tmp_path / "bin"
    _write_tool(managed, "uv", "#!/bin/sh\necho 'uv 0.9.5'\n")
    _write_tool(managed, "ffprobe", "#!/bin/sh\necho 'ffprobe version 8.0'\n")

    uv = _probe_sh_tool("uv", managed, "uv 0.9.5\n")
    ffprobe = _probe_sh_tool("ffprobe", managed, "ffprobe version 8.0\n")

    assert uv["at_floor"] is True
    assert ffprobe["at_floor"] is True


def test_an_unknown_or_path_shaped_tool_is_refused_before_anything_runs(tmp_path, clean_path):
    executable = _write_tool(tmp_path / "bin", "custom.exe", "not a real program")

    with pytest.raises(ValueError, match="UNKNOWN_TOOL"):
        host_env.resolve_tool_executable("custom")
    with pytest.raises(ValueError, match="UNKNOWN_TOOL"):
        # A path is not a tool name.  The client can never choose the binary.
        host_env.resolve_tool_executable(str(executable))
    with pytest.raises(ValueError, match="UNKNOWN_TOOL"):
        host_env.probe_command("powershell")


def test_windows_executable_resolution_prefers_exe_before_cmd(tmp_path, clean_path):
    managed = tmp_path / "bin"
    managed.mkdir()
    (managed / "ffmpeg.cmd").write_text("@echo off\r\necho ffmpeg version 6.1\r\n")
    (managed / "ffmpeg.exe").write_bytes(b"synthetic")

    resolution = host_env.resolve_tool_executable(
        "ffmpeg", [str(managed)], windows=True,
    )

    assert resolution["resolved"] is True
    assert resolution["candidate"] == "ffmpeg.exe"
    assert resolution["is_script"] is False
    assert resolution["source"] == host_env.RESOLUTION_MANAGED


def test_windows_cmd_shim_is_run_through_the_command_processor(tmp_path, clean_path):
    managed = tmp_path / "shine data" / "bin"
    shim = managed / "npm.cmd"
    managed.mkdir(parents=True)
    shim.write_text("@echo off\r\necho 10.9.0\r\n", encoding="utf-8")
    resolution = host_env.resolve_tool_executable("npm", [str(managed)], windows=True)
    assert resolution["is_script"] is True

    argv = host_env.executable_argv(resolution, ["--version"], windows=True)

    assert argv[1:4] == ["/d", "/s", "/c"]
    assert argv[0].lower().endswith("cmd.exe")
    # The command is one argument, quoted so a space in the managed path works.
    assert argv[4].startswith('"')
    assert str(shim) in argv[4]
    assert argv[4].endswith('--version"')


def test_windows_exe_is_executed_directly(tmp_path, clean_path):
    managed = tmp_path / "bin"
    managed.mkdir()
    (managed / "ffmpeg.exe").write_bytes(b"synthetic")
    resolution = host_env.resolve_tool_executable("ffmpeg", [str(managed)], windows=True)

    argv = host_env.executable_argv(resolution, ["-version"], windows=True)

    assert argv == [str(managed / "ffmpeg.exe"), "-version"]


def test_relative_path_entries_are_never_used(tmp_path, clean_path, monkeypatch):
    monkeypatch.setenv("PATH", "bin" + os.pathsep + str(clean_path))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ffmpeg").write_text("#!/bin/sh\nexit 0\n")

    resolution = host_env.resolve_tool_executable("ffmpeg")

    assert resolution["resolved"] is False
