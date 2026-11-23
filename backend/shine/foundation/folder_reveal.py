"""Reveal a produced local video file in Windows File Explorer.

The workbench API server is normally started with ``WindowStyle = 'Hidden'``
(``SW_HIDE`` in its inherited ``STARTUPINFO``).  Calling
``os.startfile(dir, "explore")`` from that hidden background process inherits
hidden-window activation behavior on Windows 11 and silently fails to pop open
Explorer, nor does it highlight the requested file.  This module is the single
foundation executor that spawns ``explorer.exe /select, <file>`` with explicit
``SW_SHOWNORMAL`` and ``AllowSetForegroundWindow``.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path

_SW_SHOWNORMAL = 1
_ASFW_ANY = 0xFFFFFFFF


class DesktopRevealUnsupported(RuntimeError):
    """Raised when revealing a local file is unavailable on this host OS."""


def reveal_file_in_explorer(path: Path) -> Path:
    """Open the containing directory in File Explorer and select ``path``."""
    resolved = path.resolve()
    if os.name != "nt":
        raise DesktopRevealUnsupported("打开文件夹需要在 Windows 本机运行工作台。")

    explorer = shutil.which("explorer.exe") or shutil.which("explorer") or "explorer.exe"
    windll = getattr(ctypes, "windll", None)
    user32 = getattr(windll, "user32", None) if windll is not None else None
    if user32 is not None:
        try:
            user32.AllowSetForegroundWindow(_ASFW_ANY)
        except Exception:
            pass

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = _SW_SHOWNORMAL

    subprocess.Popen(
        [explorer, "/select,", str(resolved)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startupinfo,
        close_fds=True,
    )
    return resolved
