"""安装器读自己的文件时必须显式用 UTF-8（中文 Windows 的默认编码是 GBK）。

症状：本机 8 分片里出现过 UnicodeDecodeError('gbk')。安装器写的 owner/journal 都是
UTF-8 JSON，而 Path.read_text() 不带 encoding 时按 locale 编码读——中文 Windows 上就是
GBK，含中文的 owner 文件会直接抛解码错误，而且它不在 except 列表里，会冒成未处理异常。
"""

import json
import locale
import os
from pathlib import Path

import pytest

from shine.comfyops import installer

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "backend" / "shine" / "comfyops" / "installer.py"


def test_the_installer_reads_its_own_owner_file_as_utf8(tmp_path, monkeypatch):
    """安装锁读 owner 文件时不能按 GBK 解码。"""
    owner = tmp_path / "owner.json"
    owner.write_text(
        json.dumps({"pid": os.getpid(), "birth": installer.process_identity(os.getpid()),
                    "note": "安装中"}),
        encoding="utf-8",
    )
    # 模拟中文 Windows 的默认编码。
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "gbk")

    with pytest.raises(ValueError) as excinfo:
        with installer.exclusive_file_lock(owner):
            pass
    # 修复前这里抛出的是 UnicodeDecodeError('gbk')，不是安装锁自己的具名拒绝。
    assert "INSTALL_OWNER" in str(excinfo.value)


def test_the_installer_never_reads_a_file_without_an_explicit_encoding():
    """结构性护栏：安装器读自己的文件不得依赖 locale 编码。"""
    source = INSTALLER.read_text(encoding="utf-8")
    bare = [line.strip() for line in source.splitlines() if ".read_text()" in line]
    assert not bare, bare
