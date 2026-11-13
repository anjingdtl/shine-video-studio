"""The native "choose a folder" dialog of the local workbench.

A browser cannot hand back the absolute path of a directory it did not upload:
``<input webkitdirectory>`` only yields file handles.  The workbench runs on the
same Windows desktop as the person using it, so the folder is chosen in the
operating system's own dialog, opened by this process, and the absolute path is
returned to the page.

The dialog is a *read-only* choice: it never creates, moves or deletes
anything.  Saving the path is a separate, explicit action.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: The operator may take a while; the request is a deliberate blocking action.
PICK_TIMEOUT_SECONDS = 240

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择作品输出目录'
$dialog.ShowNewFolderButton = $true
$dialog.UseDescriptionForTitle = $true
$initial = $env:SHINE_PICK_INITIAL
if ($initial -and (Test-Path -LiteralPath $initial)) { $dialog.SelectedPath = $initial }
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.WindowState = 'Minimized'
$result = $dialog.ShowDialog($owner)
$owner.Dispose()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dialog.SelectedPath)
}
"""


def _powershell() -> str | None:
    for name in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return None


def pick_directory(initial: str | None = None) -> dict[str, Any]:
    """Open the OS folder dialog and return the chosen absolute path.

    ``cancelled`` is an ordinary answer, not an error.  When the desktop is not
    reachable at all the caller is told exactly that instead of being handed a
    directory it never chose.
    """
    if os.name != "nt":
        return {
            "available": False,
            "cancelled": False,
            "path": None,
            "message": "当前系统没有可用的原生目录选择窗口，请直接填写绝对路径。",
        }
    shell = _powershell()
    if not shell:
        return {
            "available": False,
            "cancelled": False,
            "path": None,
            "message": "没有找到 PowerShell，无法打开系统目录选择窗口，请直接填写绝对路径。",
        }
    environment = dict(os.environ)
    if initial and Path(initial).is_dir():
        environment["SHINE_PICK_INITIAL"] = str(Path(initial))
    try:
        completed = subprocess.run(
            [shell, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=PICK_TIMEOUT_SECONDS,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "cancelled": False,
            "path": None,
            "message": "目录选择窗口没有在 4 分钟内返回；请重新点一次，或直接填写绝对路径。",
        }
    chosen = (completed.stdout or "").strip()
    if completed.returncode != 0 and not chosen:
        detail = (completed.stderr or "").strip().splitlines()
        return {
            "available": False,
            "cancelled": False,
            "path": None,
            "message": "系统目录选择窗口打不开（可能没有桌面会话）。请直接填写绝对路径。"
                       + (f"（{detail[-1][:160]}）" if detail else ""),
        }
    if not chosen:
        return {"available": True, "cancelled": True, "path": None, "message": "已取消选择。"}
    return {
        "available": True,
        "cancelled": False,
        "path": str(Path(chosen).resolve()),
        "message": "已选择目录。",
    }
