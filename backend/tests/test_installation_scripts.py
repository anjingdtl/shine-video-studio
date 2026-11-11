"""The installation scripts: half-dead stacks, receipts and atomic tool installs.

Windows only: the launcher scripts are Windows PowerShell.  The checks run
the shipped functions against a synthetic process manifest; no service is
started and no byte is downloaded.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher scripts")

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
START_SHINE = SCRIPTS / "start_shine.ps1"
STARTUP_GUARD = SCRIPTS / "shine_startup_guard.ps1"
BOOTSTRAP = SCRIPTS / "bootstrap.ps1"
INSTALL_ONCE = SCRIPTS / "install_once.ps1"

FUNCTIONS = (
    "Get-PortOwner",
    "Test-WebAlive",
    "Test-RecordedProcessAlive",
    "Test-RecordedApiAlive",
    "Test-RecordedWorkerAlive",
    "Test-RecordedWebAlive",
)
# Shared helpers the extracted functions delegate to; without them the
# standalone harness would call a function that was never defined.
SHARED_FUNCTIONS = ("Test-ShineRecordedProcessAlive",)


def extract_functions(script: Path, names) -> str:
    """Cut the named function definitions out of the shipped script."""
    listed = ",".join("'" + name + "'" for name in names)
    command = (
        "$errors=$null; $tokens=$null; "
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$tokens, [ref]$errors); "
        f"$names=@({listed}); "
        "$found=$ast.FindAll({param($n) "
        "($n -is [System.Management.Automation.Language.FunctionDefinitionAst]) -and "
        "($names -contains $n.Name)}, $true); "
        "$found | ForEach-Object { $_.Extent.Text }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), f"no function definitions found in {script}"
    return result.stdout


def run_harness(tmp_path: Path, manifest: dict) -> dict:
    module = tmp_path / "checks.ps1"
    module.write_text(
        extract_functions(START_SHINE, FUNCTIONS)
        + "\n"
        + extract_functions(STARTUP_GUARD, SHARED_FUNCTIONS),
        encoding="utf-8-sig",
    )
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". '{module}'\n"
        "$record = Get-Content -LiteralPath $env:SHINE_MANIFEST -Raw -Encoding UTF8 | ConvertFrom-Json\n"
        "[ordered]@{ api = (Test-RecordedApiAlive $record); web = (Test-RecordedWebAlive $record); "
        "worker = (Test-RecordedWorkerAlive $record) } | ConvertTo-Json -Compress\n",
        encoding="utf-8-sig",
    )
    manifest_path = tmp_path / "processes.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    environment = dict(os.environ)
    environment["SHINE_MANIFEST"] = str(manifest_path)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True, text=True, timeout=120, env=environment,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_dead_worker_means_the_recorded_stack_is_not_complete(tmp_path):
    record = {
        "api_port": 1,
        "web_port": 2,
        "processes": [
            {"name": "api", "pid": 999999, "listener_pid": 999999},
            {"name": "web", "pid": 999999, "listener_pid": 999999},
            {"name": "worker", "pid": 999999, "start_time": "2026-01-01T00:00:00.0000000+00:00"},
        ],
    }
    report = run_harness(tmp_path, record)
    assert report["worker"] is False
    assert report["api"] is False
    assert report["web"] is False


def test_a_live_worker_process_is_recognised(tmp_path):
    pid = os.getpid()
    start_time = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Process -Id {pid}).StartTime.ToString('o')"],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    record = {
        "api_port": 1,
        "web_port": 2,
        "processes": [
            {"name": "api", "pid": 999999, "listener_pid": 999999},
            {"name": "web", "pid": 999999, "listener_pid": 999999},
            {"name": "worker", "pid": pid, "start_time": start_time},
        ],
    }
    report = run_harness(tmp_path, record)
    assert report["worker"] is True


def test_a_stack_started_without_a_worker_is_not_reported_as_broken(tmp_path):
    record = {
        "api_port": 1,
        "web_port": 2,
        "processes": [
            {"name": "api", "pid": 999999, "listener_pid": 999999},
            {"name": "web", "pid": 999999, "listener_pid": 999999},
        ],
    }
    report = run_harness(tmp_path, record)
    assert report["worker"] is True


def test_the_launcher_checks_the_worker_and_the_recorded_listeners():
    text = START_SHINE.read_text(encoding="utf-8-sig")
    assert "Test-RecordedWorkerAlive" in text
    assert "Test-RecordedWebAlive" in text
    # A complete stack needs the API, the Web and the Worker together.
    assert "$apiAlive -and $webAlive -and $workerAlive" in text


def test_the_launcher_records_a_success_receipt_for_every_step():
    text = START_SHINE.read_text(encoding="utf-8-sig")
    for name in ("backend_dependencies", "frontend_dependencies", "database_migration", "service_health"):
        assert f"'{name}'" in text
    assert "Save-ShineReceipt" in text
    assert "Test-ShineReceipt" in text


def test_the_installer_uses_receipts_instead_of_directory_existence():
    text = INSTALL_ONCE.read_text(encoding="utf-8-sig")
    assert "Get-ShineReceipt" in text
    for name in ("backend_dependencies", "frontend_dependencies", "database_migration"):
        assert f"'{name}'" in text


def test_the_bootstrap_continues_a_download_and_installs_atomically():
    text = BOOTSTRAP.read_text(encoding="utf-8-sig")
    # The bytes land in a .part file and curl continues them with a range.
    assert "-C -" in text
    assert ".part" in text
    # The final directory only appears after the pinned entry files are there.
    assert ".staging" in text
    assert "Move-Item -LiteralPath $staging -Destination $toolDir" in text
    # An interrupted run heals itself instead of asking for a human.
    assert ".incomplete-" in text
