"""Windows bootstrap entry: pinned-manifest download, hash/size verification, shims.

Uses a file:// zip with real bytes and real hashes so the exact production
algorithm runs without network. Skipped where PowerShell is unavailable.
"""

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap entry")

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "scripts" / "bootstrap.ps1"


def run_bootstrap(manifest_path, install_root, *arguments, environment_overrides=None):
    environment = dict(os.environ)
    system = Path(environment.get('SYSTEMROOT', environment.get('SystemRoot', 'C:/Windows')))
    environment['PATH'] = str(system / 'System32') + ';' + str(system / 'System32/WindowsPowerShell/v1.0')
    environment = {k: v for k, v in environment.items() if k.upper() != 'PSMODULEPATH'}
    environment['LOCALAPPDATA'] = str(manifest_path.parent / 'isolated-appdata')
    environment.update(environment_overrides or {})
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BOOTSTRAP),
         "-ToolsManifest", str(manifest_path),
         *(["-InstallRoot", str(install_root)] if install_root else []), "-NoPathUpdate", *arguments],
        capture_output=True, timeout=300, env=environment,
        # Subprocess encoding contract: bootstrap.ps1 pins its stdout/stderr to
        # UTF-8 on PS 5.1 and PS 7, so tests decode UTF-8 explicitly instead of
        # trusting the host locale or PYTHONUTF8; errors='replace' keeps the
        # reader thread alive so assertions always see real output, never None.
        encoding="utf-8", errors="replace",
    )


def _report(stdout):
    return json.loads(stdout[stdout.index("{"): stdout.rindex("}") + 1])


def test_installs_pinned_tool_from_local_zip_and_creates_working_shim(tmp_path):
    zip_path = tmp_path / "cache" / "ffmpeg-1.0.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zipped:
        zipped.writestr("ffmpeg-1.0/ffmpeg.cmd", "@echo off\r\necho shim-test-ffmpeg ok\r\n")
    manifest = {
        "manifest_id": "test", "manifest_version": "0.0.1", "generated": "test",
        "tools": {"ffmpeg": {"version": "1.0", "url": "file:///" + zip_path.as_posix(),
                             "size": zip_path.stat().st_size,
                             "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                             "entries": ["ffmpeg.cmd"]}},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_bootstrap(manifest_path, tmp_path / "bin")
    assert result.returncode in {0, 1}  # blockers from the fixed tool list are allowed
    report = _report(result.stdout)
    assert "ffmpeg" in {row["name"] for row in report["installed"]}, report
    shim = tmp_path / "bin" / "ffmpeg.cmd"
    assert shim.exists()
    executed = subprocess.run([str(shim)], capture_output=True, text=True)
    assert "shim-test-ffmpeg ok" in executed.stdout


def test_hash_mismatch_blocks_install(tmp_path):
    zip_path = tmp_path / "cache" / "broken-1.0.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zipped:
        zipped.writestr("broken-1.0/tool.cmd", "@echo off\r\n")
    manifest = {
        "manifest_id": "test", "manifest_version": "0.0.1", "generated": "test",
        "tools": {"ffmpeg": {"version": "1.0", "url": "file:///" + zip_path.as_posix(),
                             "size": zip_path.stat().st_size, "sha256": "0" * 64,
                             "entries": ["tool.cmd"]}},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_bootstrap(manifest_path, tmp_path / "bin")
    report = _report(result.stdout)
    assert all(row["name"] != "ffmpeg" for row in report["installed"])
    assert any(b["name"] == "ffmpeg" and b["status"] == "install_failed"
               for b in report["blockers"])


def test_selected_bootstrap_directory_owns_tools_and_archive_cache(tmp_path):
    zipped = tmp_path / 'fixture.zip'
    with zipfile.ZipFile(zipped, 'w') as archive:
        archive.writestr('ffmpeg/ffmpeg.cmd', '@echo off\r\necho ffmpeg version 8.1\r\n')
    manifest = tmp_path / 'tools.json'
    manifest.write_text(json.dumps({'tools': {'ffmpeg': {
        'version': '8.1', 'url': zipped.as_uri(), 'sha256': hashlib.sha256(zipped.read_bytes()).hexdigest(),
        'size': zipped.stat().st_size, 'entries': ['ffmpeg.cmd'],
    }}}), encoding='utf-8')
    target = tmp_path / '用户选择 with spaces'
    # Contract test uses a simulated system-drive marker; all real bytes stay in tmp_path.
    result = run_bootstrap(manifest, None, '-StorageRoot', str(target),
                           environment_overrides={'SYSTEMDRIVE': 'Z:'})
    report = _report(result.stdout)
    assert any(row['name'] == 'ffmpeg' for row in report['installed']), report
    assert (target / 'tools/ffmpeg-8.1/ffmpeg/ffmpeg.cmd').is_file()
    assert (target / 'bin/ffmpeg.cmd').is_file()
    assert (target / 'temp/shine-bootstrap-cache/ffmpeg.zip').is_file()
    assert not (tmp_path / 'isolated-appdata/Shine').exists()


def test_bootstrap_system_drive_is_rejected_before_any_download(tmp_path):
    manifest = tmp_path / 'tools.json'
    manifest.write_text('{"tools": {}}', encoding='utf-8')
    target = tmp_path / 'system-drive-install'
    result = run_bootstrap(manifest, None, '-StorageRoot', str(target),
                           environment_overrides={'SYSTEMDRIVE': target.drive})
    assert result.returncode != 0
    assert 'non-system drive' in result.stderr
    assert not target.exists()


@pytest.mark.parametrize('existing_data', [False, True])
@pytest.mark.parametrize('execution_optout', [False, True])
def test_no_launch_persists_selected_paths_and_preserves_existing_default_data(
        tmp_path, existing_data, execution_optout):
    """PS5.1 with six synthetic tool shims: no network, password prompt or app launch."""
    from dotenv import dotenv_values

    repo = tmp_path / 'source-copy'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'config/deployment').mkdir(parents=True)
    shutil.copyfile(BOOTSTRAP, repo / 'scripts/bootstrap.ps1')
    shutil.copyfile(REPO / 'config/deployment/tools-windows-x64.json',
                    repo / 'config/deployment/tools-windows-x64.json')
    initial_env = 'UNRELATED=preserve-me\n' + ('SHINE_ENABLE_LOCAL_EXECUTION=0\n' if execution_optout else '')
    (repo / '.env').write_text(initial_env, encoding='utf-8')
    legacy = repo / '.local-data'
    if existing_data:
        legacy.mkdir()
        (legacy / 'shine.db').write_bytes(b'preserve-existing-database-fixture')
    tools = tmp_path / 'tool-fixtures'
    tools.mkdir()
    for name, output in {'uv': 'uv 0.9.5', 'node': 'v20.19.5', 'npm': '10.8.2', 'pwsh': '7.4.12',
                         'ffmpeg': 'ffmpeg version 8.1.3', 'ffprobe': 'ffprobe version 8.1.3'}.items():
        (tools / f'{name}.cmd').write_text(f'@echo off\necho {output}\n', encoding='ascii')
    environment = {k: v for k, v in os.environ.items() if k.upper() not in {
        'PSMODULEPATH', 'SHINE_DATA_DIR', 'SHINE_DATABASE_URL', 'SHINE_BACKEND_ENV', 'SHINE_BOOTSTRAP_ROOT',
    }}
    system = Path(environment.get('SYSTEMROOT', 'C:/Windows'))
    environment['PATH'] = ';'.join(map(str, [tools, system / 'System32', system / 'System32/WindowsPowerShell/v1.0']))
    environment['SYSTEMDRIVE'] = 'Z:'  # Simulated host policy; only tiny fixtures are written.
    selected = tmp_path / '用户所选安装盘 with spaces'
    result = subprocess.run([
        str(system / 'System32/WindowsPowerShell/v1.0/powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(repo / 'scripts/bootstrap.ps1'), '-StorageRoot', str(selected), '-NoLaunch', '-NoPathUpdate',
    ], env=environment, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(b'}'), result.stdout
    values = dotenv_values(repo / '.env')
    expected_data = legacy if existing_data else selected / 'data'
    assert Path(values['SHINE_DATA_DIR']) == expected_data
    assert Path(values['SHINE_BACKEND_ENV']).is_relative_to(selected)
    assert Path(values['UV_CACHE_DIR']).is_relative_to(selected)
    assert values['UNRELATED'] == 'preserve-me'
    assert values['SHINE_ENABLE_LOCAL_EXECUTION'] == ('0' if execution_optout else '1')
    assert 'SHINE_OPERATOR_TOKEN_SHA256' not in values
    preferences = json.loads((expected_data / 'deployment-storage.json').read_text(encoding='utf-8'))
    assert Path(preferences['root']) == selected / 'runtime'
    if existing_data:
        assert (legacy / 'shine.db').read_bytes() == b'preserve-existing-database-fixture'


def test_first_install_forwards_free_ports_instead_of_defaults():
    """A first install must not fall back to occupied 8000/5173."""
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8-sig")
    installer = (REPO / "scripts" / "install_once.ps1").read_text(encoding="utf-8-sig")
    assert "[int]$ApiPort = 0" in bootstrap and "[int]$WebPort = 0" in bootstrap
    assert "$startArgs += @('-ApiPort', \"$ApiPort\")" in bootstrap
    assert "$startArgs += @('-WebPort', \"$WebPort\")" in bootstrap
    assert "ApiPort = $apiPort" in installer and "WebPort = $webPort" in installer


def test_pinned_tools_manifest_matches_recorded_pins():
    """The committed tools manifest stays in sync with the upstream pins file."""
    pins = json.loads((REPO / "config" / "deployment" / "_upstream-pins" / "tools.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO / "config" / "deployment" / "tools-windows-x64.json").read_text(encoding="utf-8"))
    assert manifest["tools"] == pins


def _initialize_identity(tmp_path, initial, password="isolated-test-password-12345-abcdef"):
    env_file = tmp_path / ".env"
    env_file.write_text(initial, encoding="utf-8")
    script = tmp_path / "initialize.ps1"
    helper = str(REPO / "scripts/bootstrap_identity.ps1").replace("'", "''")
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{helper}'\n"
        "Initialize-ShineOperator (Join-Path $PSScriptRoot '.env') {\n"
        f"    ConvertTo-SecureString '{password}' -AsPlainText -Force\n"
        "}\n", encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=30,
        env={k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'},
    )
    return result, env_file.read_text(encoding="utf-8")


def test_bootstrap_creates_hashed_operator_and_preserves_other_configuration(tmp_path):
    password = "isolated-test-password-12345-abcdef"
    result, content = _initialize_identity(tmp_path, "# keep\nUNRELATED=中文value\n", password)
    assert result.returncode == 0, result.stderr
    assert "UNRELATED=中文value" in content
    assert "SHINE_OPERATOR_ID=local-operator" in content
    assert f"SHINE_OPERATOR_TOKEN_SHA256={hashlib.sha256(password.encode()).hexdigest()}" in content
    assert "SHINE_ENABLE_LOCAL_EXECUTION=1" in content
    assert password not in content + result.stdout + result.stderr


def test_bootstrap_preserves_operator_and_repairs_missing_id_without_password_reset(tmp_path):
    initial = "SHINE_OPERATOR_TOKEN_SHA256=" + "a" * 64 + "\nSHINE_ENABLE_LOCAL_EXECUTION=0\n"
    result, content = _initialize_identity(tmp_path, initial, "short")
    assert result.returncode == 0, result.stderr
    assert "SHINE_OPERATOR_TOKEN_SHA256=" + "a" * 64 in content
    assert "SHINE_OPERATOR_ID=local-operator" in content
    assert content.count("SHINE_ENABLE_LOCAL_EXECUTION=") == 1
    existing = content.replace("local-operator", "existing-human")
    result, content = _initialize_identity(tmp_path, existing, "short")
    assert result.returncode == 0, result.stderr
    assert "SHINE_OPERATOR_ID=existing-human" in content


@pytest.mark.parametrize("initial,password", [
    ("UNRELATED=value\n", "short"),
    ("SHINE_OPERATOR_TOKEN_SHA256=invalid\n", "isolated-test-password-12345-abcdef"),
])
def test_bootstrap_rejects_bad_identity_without_mutating_configuration(tmp_path, initial, password):
    result, content = _initialize_identity(tmp_path, initial, password)
    assert result.returncode != 0
    assert content == initial
