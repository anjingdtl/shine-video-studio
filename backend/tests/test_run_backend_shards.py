"""Tests for Windows backend shard runner proxy sanitization & retention logging."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx


def test_run_backend_shards_proxy_sanitization_handles_windows_entries(monkeypatch):
    """Sanitized NO_PROXY always keeps loopback entries and never breaks httpx."""
    repo = Path(__file__).resolve().parents[2]
    script = repo / "run_backend_shards.ps1"
    raw = script.read_text(encoding="utf-8-sig")
    pwsh = shutil.which("pwsh")
    if pwsh:
        start = raw.index("function Resolve-LoopbackNoProxy")
        end = raw.index("$loopbackNoProxy = Resolve-LoopbackNoProxy")
        func_def = raw[start:end]
        cmd = func_def + "\nResolve-LoopbackNoProxy @('<local>;[::1];127.*;*.corp.local;http://bad;localhost;10.0.0.0/8')"
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, check=True,
        )
        result = proc.stdout.strip()
        assert result.startswith("127.0.0.1,localhost,::1")
        assert ".corp.local" in result
        assert "<local>" not in result
        assert "[::1]" not in result
        assert "127.*" not in result
        assert "10.0.0.0/8" not in result

        monkeypatch.setenv("NO_PROXY", result)
        monkeypatch.setenv("no_proxy", result)
        proxies = httpx._utils.get_environment_proxies()
        assert proxies.get("all://127.0.0.1") is None
        assert proxies.get("all://localhost") is None


def test_run_backend_shards_keeps_utf8_bom_and_clear_basetemp_logs():
    repo = Path(__file__).resolve().parents[2]
    script = repo / "run_backend_shards.ps1"
    raw_bytes = script.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")
    text = raw_bytes.decode("utf-8-sig")
    assert "显式指定 -KeepBasetemp，且有" in text
    assert "失败自动保留" in text
    assert "$env:NO_PROXY = $loopbackNoProxy" in text
    pwsh = shutil.which("pwsh")
    if pwsh:
        start = text.index("$shouldKeepBasetemp =")
        anchor = text.index('Write-Output "basetemp kept', start)
        end = text.index("}", anchor) + 1
        snippet = text[start:end]
        preamble = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false);"
        res_fail = subprocess.run(
            [
                pwsh, "-NoProfile", "-Command",
                f"{preamble} [switch]$KeepBasetemp = $false; $bad = 1; $outDir = 'D:/tmp';\n{snippet}",
            ],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        assert "失败自动保留（有 1 个分片未通过，留作排障）" in res_fail.stdout
        assert "显式指定 -KeepBasetemp" not in res_fail.stdout
        res_explicit = subprocess.run(
            [
                pwsh, "-NoProfile", "-Command",
                f"{preamble} [switch]$KeepBasetemp = $true; $bad = 0; $outDir = 'D:/tmp';\n{snippet}",
            ],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        assert "显式指定 -KeepBasetemp" in res_explicit.stdout
