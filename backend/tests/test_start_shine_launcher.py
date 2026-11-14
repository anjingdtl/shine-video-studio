"""启动器回归：被拉起的子进程不得继承启动脚本的 stdin。

症状：scripts/start_shine.ps1 用 Start-Process 拉起 vite 时没有重定向 stdin，
父 shell 退出后管道断开，vite 因未处理的 EPIPE 崩溃，API 一并消失，
隔离实例表现为「凭空死亡」。

两道断言：
1. 脚本里每个 Start-Process 都把 stdin 指向空设备；
2. 用同样的参数真起一个读 stdin 的子进程：它读到 EOF 并正常退出，而不是崩溃。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "scripts" / "start_shine.ps1"
VENV_PYTHON = REPO / "backend" / ".venv" / "Scripts" / "python.exe"


def test_every_started_child_redirects_stdin_to_the_empty_device():
    text = LAUNCHER.read_text(encoding="utf-8-sig")
    # PowerShell refuses a device name such as 'NUL' here, so the empty device
    # is a real empty file that every child reads EOF from.
    assert "Get-ShineEmptyStdin" in text, "the launcher must prepare an empty stdin file"
    assert "stdin.empty" in text
    # 两个后台启动点（服务栈与常驻监督进程）都必须带上 stdin 重定向，
    # 否则父 shell 一退就断管道，子进程死于未处理的 EPIPE。
    for marker in ("Start-Process @startArgs", "Start-Process @supArgs"):
        assert marker in text, marker
        window = text[max(0, text.index(marker) - 2000): text.index(marker)]
        assert "RedirectStandardInput" in window, (
            f"{marker} must redirect stdin to the empty device")
    # 旧写法（只重定向输出与错误）必须已经不在了。
    assert "-RedirectStandardOutput $stdout -RedirectStandardError $stderr" not in text


def test_a_child_started_the_same_way_reads_eof_instead_of_dying_on_epipe(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh or not VENV_PYTHON.exists():
        pytest.skip("this launcher runs under PowerShell with the project virtualenv")
    child = tmp_path / "child.py"
    child.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        "print('EOF' if data == '' else 'DATA')\n",
        encoding="utf-8",
    )
    out = tmp_path / "child.out"
    err = tmp_path / "child.err"
    empty = tmp_path / "stdin.empty"
    empty.write_text("", encoding="utf-8")
    command = (
        "$p = Start-Process -PassThru -WindowStyle Hidden -FilePath $env:SHINE_TEST_PY "
        "-ArgumentList $env:SHINE_TEST_ARG "
        "-RedirectStandardOutput $env:SHINE_TEST_OUT "
        "-RedirectStandardError $env:SHINE_TEST_ERR "
        "-RedirectStandardInput $env:SHINE_TEST_STDIN; "
        "$p.WaitForExit(); exit $p.ExitCode"
    )
    env = dict(
        os.environ,
        SHINE_TEST_PY=str(VENV_PYTHON),
        # The quoted argument travels through the environment: PowerShell must
        # not rebuild the quotes when the command line is parsed.
        SHINE_TEST_ARG=f'"{child}"',
        SHINE_TEST_OUT=str(out),
        SHINE_TEST_ERR=str(err),
        SHINE_TEST_STDIN=str(empty),
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", command],
        env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"the child died instead of reading EOF: {result.stderr} / {err.read_text(errors='replace')}")
    assert "EOF" in out.read_text(encoding="utf-8", errors="replace"), (
        "the child must read EOF on stdin, never the launcher's console handle")
