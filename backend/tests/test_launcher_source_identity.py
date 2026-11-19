"""Direction 1 regressions: a running stack is reused only when it IS this request.

Counterexamples from the 2026-11-19 closeout audit:

* a manual pull / local commit / uncommitted runtime edit left the old processes
  serving, and the launcher delivered their old page as a successful start;
* a manifest written by an older launcher carried no code identity at all;
* a -NoWorker record satisfied a full-stack request (worker absence read as alive);
* the supervisor was never checked on the reuse path;
* "Git fast-forwarded this run" (update_checkout exit 10) was the *only* trigger
  for replacing a running stack, so every code change outside that one path stuck.

The fix separates the two decisions: update_checkout.ps1 owns fast-forward;
start_shine.ps1 owns generation replacement, from ONE source identity
implementation (backend/shine/source_identity.py) recorded at process start.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(REPO / "backend"))
from shine.source_identity import (  # noqa: E402
    compute_source_identity,
    same_identity,
)

GIT = shutil.which("git")


def _git(repo: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def git_checkout(tmp_path):
    if not GIT:
        pytest.skip("git is not available")
    repo = tmp_path / "checkout"
    (repo / "backend" / "shine").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "backend" / "shine" / "main.py").write_text("print('v1')\n", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "v1")
    return repo


def test_clean_checkout_identity_is_stable(git_checkout):
    first = compute_source_identity(git_checkout)
    second = compute_source_identity(git_checkout)
    assert first["kind"] == "git"
    assert first["dirty"] is False
    assert first["digest"] == second["digest"]
    assert first["commit"]


def test_uncommitted_runtime_edit_changes_the_identity(git_checkout):
    before = compute_source_identity(git_checkout)
    (git_checkout / "backend" / "shine" / "main.py").write_text("print('v2')\n", encoding="utf-8")
    after = compute_source_identity(git_checkout)
    assert after["dirty"] is True
    assert after["digest"] != before["digest"]
    assert after["commit"] == before["commit"]  # HEAD did not move; the code did


def test_docs_only_edit_does_not_replace_a_running_stack(git_checkout):
    before = compute_source_identity(git_checkout)
    (git_checkout / "docs" / "guide.md").write_text("changed docs only\n", encoding="utf-8")
    after = compute_source_identity(git_checkout)
    assert after["digest"] == before["digest"]


def test_untracked_runtime_module_changes_the_identity(git_checkout):
    before = compute_source_identity(git_checkout)
    (git_checkout / "backend" / "shine" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    after = compute_source_identity(git_checkout)
    assert after["digest"] != before["digest"]
    # Editing that untracked module again is still a new generation.
    (git_checkout / "backend" / "shine" / "extra.py").write_text("X = 2\n", encoding="utf-8")
    third = compute_source_identity(git_checkout)
    assert third["digest"] != after["digest"]


def test_local_commit_changes_the_identity(git_checkout):
    before = compute_source_identity(git_checkout)
    (git_checkout / "backend" / "shine" / "main.py").write_text("print('v2')\n", encoding="utf-8")
    _git(git_checkout, "add", ".")
    _git(git_checkout, "commit", "-qm", "v2")
    after = compute_source_identity(git_checkout)
    assert after["dirty"] is False
    assert after["commit"] != before["commit"]
    assert after["digest"] != before["digest"]


def test_package_without_git_uses_build_info(tmp_path):
    pkg = tmp_path / "package"
    pkg.mkdir()
    (pkg / "BUILD_INFO.json").write_text(json.dumps({
        "source_commit": "a" * 40, "source_short_commit": "aaaaaaa",
        "package_version": "0.3.0", "built_at": "2026-11-19T10:00:00+08:00",
    }), encoding="utf-8")
    identity = compute_source_identity(pkg)
    assert identity["kind"] == "package"
    assert identity["commit"] == "a" * 40
    assert identity["package_version"] == "0.3.0"
    assert identity["digest"]
    # A newer build of the same commit is still a different generation.
    (pkg / "BUILD_INFO.json").write_text(json.dumps({
        "source_commit": "a" * 40, "source_short_commit": "aaaaaaa",
        "package_version": "0.3.1", "built_at": "2026-11-20T10:00:00+08:00",
    }), encoding="utf-8")
    assert compute_source_identity(pkg)["digest"] != identity["digest"]


def test_neither_git_nor_build_info_is_explicitly_unknown(tmp_path):
    identity = compute_source_identity(tmp_path)
    assert identity["kind"] == "unknown"
    assert not same_identity(identity, identity) or identity["digest"] == ""


def test_old_manifest_without_identity_is_not_proven_current(git_checkout):
    current = compute_source_identity(git_checkout)
    assert same_identity(None, current) is False
    assert same_identity({}, current) is False
    assert same_identity({"digest": ""}, current) is False
    assert same_identity({"digest": "other"}, current) is False
    assert same_identity({"digest": current["digest"]}, current) is True


# ---------------------------------------------------------------------------
# Launcher contract (text-level, enforced on every platform)
# ---------------------------------------------------------------------------


def test_launcher_records_and_compares_the_source_identity():
    text = (SCRIPTS / "start_shine.ps1").read_text(encoding="utf-8-sig")
    assert "Get-ShineSourceIdentity" in text
    assert "source_identity = $sourceIdentity" in text  # manifest carries the fact
    assert "Test-ShineSourceIdentityMatch $record.source_identity $sourceIdentity" in text
    # The reuse decision covers provider, data dir and both worker shapes.
    assert "render_provider" in text
    assert "started with -NoWorker; this request needs the full stack" in text
    assert "asks for -NoWorker but the running stack has a Worker" in text


def test_generation_replacement_uses_the_one_stop_executor_and_never_delivers_a_stale_page():
    text = (SCRIPTS / "start_shine.ps1").read_text(encoding="utf-8-sig")
    assert "stop_shine.py" in text
    assert "Cannot replace the running stack" in text
    assert "Its old page is NOT a successful start" in text
    # The supervisor is ensured on BOTH paths through one function, under the
    # same identity rule as the stack manifest (legacy record = not proven current).
    assert text.count("Ensure-ShineSupervisor") >= 3  # definition + two call sites
    assert "--supervisor-only" in text
    assert "Test-ShineSourceIdentityMatch $saved.source_identity $sourceIdentity" in text


def test_fast_forward_and_generation_replacement_are_separate_decisions():
    dev = (SCRIPTS / "start_dev.ps1").read_text(encoding="utf-8-sig")
    # start_dev no longer stops the stack itself on exit 10: start_shine owns
    # replacement from the recorded identity, which also sees a manual pull.
    assert "update_checkout.ps1" in dev
    assert "stop_shine.ps1" not in dev
    update = (SCRIPTS / "update_checkout.ps1").read_text(encoding="utf-8-sig")
    assert "merge', '--ff-only'" in update or "'merge', '--ff-only'" in update
    assert "reset" not in update.split("#")[0] or "--ff-only" in update


def test_stop_executor_has_a_supervisor_only_mode():
    text = (SCRIPTS / "stop_shine.py").read_text(encoding="utf-8")
    assert "--supervisor-only" in text
    assert "return stop_supervisor(data_dir)" in text


def test_supervisor_records_and_names_its_code_identity():
    text = (SCRIPTS / "shine_supervisor.py").read_text(encoding="utf-8")
    assert '"source_identity": Handler.source_identity' in text
    assert '"source_identity": getattr(self, "source_identity", None)' in text


def test_api_and_status_expose_the_running_identity():
    main = (REPO / "backend" / "shine" / "main.py").read_text(encoding="utf-8")
    assert '"source_identity": source_identity()' in main
    assert "source_identity=_source_identity()" in main
    schemas = (REPO / "backend" / "shine" / "schemas.py").read_text(encoding="utf-8")
    assert "source_identity: dict[str, Any] | None" in schemas
    report = (REPO / "backend" / "shine" / "support" / "reports.py").read_text(encoding="utf-8")
    assert "运行代码身份" in report


# ---------------------------------------------------------------------------
# Real PowerShell behaviour (Windows only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="needs Windows + pwsh")
def test_powershell_identity_wrapper_matches_the_python_authority(git_checkout):
    script = SCRIPTS / "shine_source_identity.ps1"
    command = (
        f". '{script}'; "
        f"$id = Get-ShineSourceIdentity '{git_checkout}'; "
        "$id | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout.strip())
    expected = compute_source_identity(git_checkout)
    assert parsed["digest"] == expected["digest"]
    assert parsed["kind"] == "git"


@pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="needs Windows + pwsh")
def test_powershell_match_refuses_a_missing_or_different_digest():
    script = SCRIPTS / "shine_source_identity.ps1"
    command = (
        f". '{script}'; "
        "$cur = [pscustomobject]@{ digest = 'abc' }; "
        "$a = Test-ShineSourceIdentityMatch $null $cur; "
        "$b = Test-ShineSourceIdentityMatch ([pscustomobject]@{ digest = '' }) $cur; "
        "$c = Test-ShineSourceIdentityMatch ([pscustomobject]@{ digest = 'other' }) $cur; "
        "$d = Test-ShineSourceIdentityMatch ([pscustomobject]@{ digest = 'abc' }) $cur; "
        "\"$a|$b|$c|$d\""
    )
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False|False|False|True"
