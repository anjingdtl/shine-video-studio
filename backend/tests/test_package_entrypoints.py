"""Distribution package entry points: encoding invariants, one-click flow, credentials.

These are the parts a tester touches before any Python runs, so they are checked
without a full install: cmd.exe cannot read UTF-8 batch text reliably, and
Windows PowerShell 5.1 reads a BOM-less UTF-8 script as ANSI.  Both failures are
silent until a Chinese-language tester sees mojibake or a parse error, so they
are asserted here instead.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows entry points")

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
PACKAGING = REPO / "packaging" / "windows"
BUILD_SCRIPT = SCRIPTS / "build_test_package.ps1"
INSTALL_ONCE = SCRIPTS / "install_once.ps1"
IDENTITY = SCRIPTS / "bootstrap_identity.ps1"

UTF8_BOM = b"\xef\xbb\xbf"
ENTRY_SCRIPTS = [
    "一键安装并启动.cmd",
    "SETUP.cmd",
    "START_APP.cmd",
    "STOP_APP.cmd",
    "CHECK_ENVIRONMENT.cmd",
]


def _powershell() -> str:
    return "powershell"


def test_packaged_entry_scripts_exist_and_are_pure_ascii():
    """cmd.exe decodes batch text with the active code page; keep it 7-bit."""
    for name in ENTRY_SCRIPTS:
        path = PACKAGING / name
        assert path.is_file(), f"missing packaging asset: {name}"
        raw = path.read_bytes()
        assert raw.startswith(UTF8_BOM) is False, f"{name} must not start with a BOM"
        non_ascii = [byte for byte in raw if byte > 0x7F]
        assert not non_ascii, f"{name} contains non-ASCII bytes; move the text into PowerShell"


def test_packaging_entry_list_matches_the_build_script():
    """A new entry script must be added to the build script, and vice versa."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    block = re.search(r"\$entryScripts\s*=\s*@\((.*?)\)\s*\n", text, re.S)
    assert block, "build_test_package.ps1 no longer declares an entryScripts array"
    declared = set(re.findall(r"'([^']+\.cmd)'", block.group(1)))
    assert declared == set(ENTRY_SCRIPTS), f"declared {sorted(declared)}"


def test_build_script_excludes_models_evidence_and_virtualenvs():
    text = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    assert "docs/evidence/" in text
    assert "git" in text and "ls-files" in text, "the package must come from a committed tree"
    assert "PACKAGE_MANIFEST.json" in text and "Get-FileHash" in text
    assert "-AllowDirty" in text, "dirty trees must be refused unless explicitly allowed"


@pytest.mark.parametrize("script", ["install_once.ps1", "build_test_package.ps1"])
def test_non_ascii_powershell_scripts_carry_a_utf8_bom(script):
    """PS 5.1 reads BOM-less UTF-8 as ANSI, which turns Chinese text into a parse error."""
    raw = (SCRIPTS / script).read_bytes()
    assert any(byte > 0x7F for byte in raw), f"{script} is expected to contain Chinese text"
    assert raw.startswith(UTF8_BOM), f"{script} needs a UTF-8 BOM"


def test_every_ps1_with_non_ascii_text_is_bom_encoded():
    offenders = []
    for path in SCRIPTS.glob("*.ps1"):
        raw = path.read_bytes()
        if any(byte > 0x7F for byte in raw) and not raw.startswith(UTF8_BOM):
            offenders.append(path.name)
    assert not offenders, f"PS 5.1 would misread these scripts: {offenders}"


def test_one_click_installer_never_writes_to_the_system_drive_and_opens_the_wizard():
    text = INSTALL_ONCE.read_text(encoding="utf-8-sig")
    assert "非系统盘" in text
    assert "180GB" in text
    assert "#setup" in text, "the installer must land in the guided setup"
    assert "先检查本机已有 ComfyUI 和模型" in text
    assert "只有明确选择新建环境并确认后" in text
    assert "OperatorPasswordFile =" not in text
    assert "Get-ShineOperatorPasswordFile" not in text
    assert "$hasOperator -and" not in text, "idempotent restart must work without credentials"
    assert "Get-ShineFreePort" in text, "busy ports must be handled instead of failing"
    # Idempotence: a second double-click must not install twice.
    assert "processes.json" in text


def _run_ps(script_path: Path, timeout=120):
    return subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"},
    )


def test_generated_operator_password_is_reused_and_hashed_into_env(tmp_path):
    """One-click installs must not depend on a human inventing a 16+ char secret."""
    credential = tmp_path / "secrets" / "operator-credentials.txt"
    env_file = tmp_path / ".env"
    env_file.write_text("UNRELATED=keep-me\n", encoding="utf-8")
    helper = str(IDENTITY).replace("'", "''")
    script = tmp_path / "provision.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{helper}'\n"
        f"$path = '{str(credential).replace(chr(39), chr(39) * 2)}'\n"
        "Initialize-ShineOperator (Join-Path $PSScriptRoot '.env') { Get-ShineOperatorPasswordFile $path }\n"
        "$second = Get-ShineOperatorPasswordFile $path\n"
        "$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR("
        "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($second))\n"
        "Write-Output ('LEN=' + $plain.Length)\n",
        encoding="utf-8",
    )

    result = _run_ps(script)
    assert result.returncode == 0, result.stderr

    stored = credential.read_text(encoding="utf-8").strip()
    # operator_review.authenticate rejects anything shorter than 32 characters,
    # so a shorter generated secret would leave a tester unable to approve plans.
    assert len(stored) >= 32
    assert stored.isalnum(), "the generated password must be retypeable"
    assert f"LEN={len(stored)}" in result.stdout, "the second call must reuse the stored secret"
    assert stored not in result.stdout + result.stderr

    content = env_file.read_text(encoding="utf-8")
    assert content.count("SHINE_OPERATOR_TOKEN_SHA256=") == 1
    assert hashlib.sha256(stored.encode()).hexdigest() in content
    assert "UNRELATED=keep-me" in content
    assert stored not in content


def test_operator_credential_file_with_short_secret_is_rejected(tmp_path):
    credential = tmp_path / "operator-credentials.txt"
    credential.write_text("short\n", encoding="utf-8")
    helper = str(IDENTITY).replace("'", "''")
    script = tmp_path / "reject.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{helper}'\n"
        "try {\n"
        f"    Get-ShineOperatorPasswordFile '{str(credential).replace(chr(39), chr(39) * 2)}' | Out-Null\n"
        "    Write-Output 'NO_ERROR'\n"
        "} catch { Write-Output 'REJECTED' }\n",
        encoding="utf-8",
    )
    result = _run_ps(script)
    assert "REJECTED" in result.stdout, result.stdout + result.stderr


def test_bootstrap_accepts_the_operator_password_file_switch():
    text = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert "[string]$OperatorPasswordFile" in text
    assert "Get-ShineOperatorPasswordFile $operatorCredentialPath" in text
    # Existing explicit callers retain compatibility, ordinary installs never prompt.
    assert "if (-not $NoLaunch -and $OperatorPasswordFile)" in text
    assert "Read-Host 'Choose a local operator password" not in text


def test_install_once_reports_a_machine_readable_verdict_for_each_gate():
    """The window is the only UI a tester has before the browser opens."""
    text = INSTALL_ONCE.read_text(encoding="utf-8-sig")
    for code in ["exit 10", "exit 11", "exit 12", "exit 13", "exit 14"]:
        assert code in text, f"missing distinct failure exit code {code}"
    assert "install-log-" in text, "the installer must leave a log next to the install root"
    assert "Stop-Transcript" in text
    # A crashed previous run must not deadlock the only entry point.
    assert "stop_shine.py" in text
    assert "不会盲杀进程" in text


def test_package_documents_are_rendered_from_templates():
    for name in ["START_HERE.md", "TESTER_CHECKLIST.md"]:
        text = (PACKAGING / name).read_text(encoding="utf-8")
        assert "{{PACKAGE_VERSION}}" in text, f"{name} must be a template"
    start_here = (PACKAGING / "START_HERE.md").read_text(encoding="utf-8")
    assert "一键安装并启动.cmd" in start_here
    assert "SETUP.cmd" in start_here
    assert "acceptance_status" in start_here, "the completion/acceptance split must be documented"
    assert "标记为“已安装”" in start_here
    assert "不会复制、覆盖或重复下载" in start_here
    assert "向导会暂停真实制作配置并说明下一步" in start_here
    checklist = (PACKAGING / "TESTER_CHECKLIST.md").read_text(encoding="utf-8")
    assert "验证服务与工作流" in checklist


def test_personal_desktop_package_does_not_claim_an_operator_password_is_required():
    start_here = (PACKAGING / "START_HERE.md").read_text(encoding="utf-8")
    checklist = (PACKAGING / "TESTER_CHECKLIST.md").read_text(encoding="utf-8")
    installer = INSTALL_ONCE.read_text(encoding="utf-8-sig")
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    assert "无需操作员密码" in start_here
    assert "不会生成 `operator-credentials.txt`" in start_here
    assert "没有要求输入或抄录操作员密码" in checklist
    assert "无需密码" in installer
    assert "not_required_for_personal_desktop" in build
    assert "generated 40-char secret" not in build


def test_build_info_template_lists_the_real_catalog_numbers():
    """Disk budget and package count must match the catalog, not the old 120GB/93 docs."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    assert "154" in text
    assert "60804054569" in text
    assert "free_disk_gib = 180" in text
