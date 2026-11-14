"""The read-only environment repair plan and its single controlled executor.

Two rules shape this module:

1. **Detection never acts.**  Building a plan is a pure function of the host
   report and the pinned tool manifest.  It downloads nothing, changes no
   PATH entry and starts no process.  A GET that returns a plan is safe to
   call on every page load.
2. **Execution is one permitted operation, not a command channel.**  The
   request carries only a plan id.  The executable, the script, the manifest,
   the install root and the argument vector are fixed here; nothing from the
   browser reaches the command line.  The installer is the existing
   ``scripts/bootstrap.ps1``, which itself runs on stock Windows PowerShell
   5.1 - it must not require the very PowerShell 7 it is about to install.

A successful install is only a claim until the tools are *re-probed from the
managed directory*.  The result therefore reports both what the installer said
and what the detector can still see.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from shine.comfyops.tool_inventory import (
    DEFAULT_TOOLS_MANIFEST,
    load_tools_manifest,
    manifest_sha256,
    packages_download_bytes,
    repairable_packages,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap.ps1"
REPAIR_STATE_KEY = "tool_repair_state"
PLAN_SCHEMA = "shine.tool-repair-plan.v1"
PLAN_COMPLETE = "COMPLETE"
PLAN_NOTHING_TO_REPAIR = "NOTHING_TO_REPAIR"
PLAN_UNSUPPORTED = "PLATFORM_UNSUPPORTED"

STATUS_AWAITING = "AWAITING_OPERATOR_CONFIRMATION"
STATUS_GRANTED = "GRANTED"


class RepairPlanChanged(ValueError):
    """The plan the operator approved is not the plan this host would produce."""


class RepairRefused(RuntimeError):
    """The repair cannot run here; the message is a machine token."""


def managed_tool_roots(settings) -> dict[str, Path]:
    """Where a Shine-managed tool is installed and where its shims live.

    Both directories are derived from the configured data directory; the
    caller cannot supply a path, so a repair can never write elsewhere.
    """
    base = Path(settings.data_dir).resolve() / "tools"
    return {"tools_root": base, "bin_root": base / "bin"}


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plan_identity(packages: list[dict], roots: dict[str, Path], manifest_sha: str) -> dict:
    return {
        "schema": PLAN_SCHEMA,
        "manifest_sha256": manifest_sha,
        "install_root": str(roots["bin_root"]),
        "tools_root": str(roots["tools_root"]),
        "packages": [
            {
                "package_id": row["package_id"],
                "version": (row.get("download") or {}).get("version"),
                "sha256": (row.get("download") or {}).get("sha256"),
                "size": (row.get("download") or {}).get("size"),
            }
            for row in packages
        ],
    }


def read_repair_state(db) -> dict:
    from shine.models import AppSetting

    if db is None:
        return {}
    try:
        row = db.get(AppSetting, REPAIR_STATE_KEY)
    except Exception:  # noqa: BLE001 - an unreadable state is simply absent
        return {}
    return dict(row.value or {}) if row else {}


def _save_repair_state(db, payload: dict) -> None:
    from shine.models import AppSetting, utcnow

    if db is None:
        return
    row = db.get(AppSetting, REPAIR_STATE_KEY)
    if row is None:
        db.add(AppSetting(key=REPAIR_STATE_KEY, value=payload, updated_at=utcnow()))
    else:
        row.value = payload
        row.updated_at = utcnow()
    db.commit()


def build_repair_plan(settings, report: dict, *, manifest_path: Path | None = None,
                      state: dict | None = None) -> dict:
    """The plan a person is asked to approve.  Read-only and deterministic."""
    manifest_file = Path(manifest_path or DEFAULT_TOOLS_MANIFEST)
    try:
        manifest = load_tools_manifest(manifest_file)
        manifest_sha = manifest_sha256(manifest_file)
    except ValueError:
        manifest = {"tools": {}}
        manifest_sha = ""
    packages = repairable_packages(report.get("tools") or {}, manifest)
    roots = managed_tool_roots(settings)
    identity = _plan_identity(packages, roots, manifest_sha)
    plan_id = "toolrepair-" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:32]
    stored = state if state is not None else {}
    same_plan = stored.get("plan_id") == plan_id
    return {
        "schema": PLAN_SCHEMA,
        "plan_id": plan_id,
        "content_sha256": hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest(),
        "status": PLAN_COMPLETE if packages else PLAN_NOTHING_TO_REPAIR,
        "packages": packages,
        "download_bytes": packages_download_bytes(packages),
        "install_root": str(roots["bin_root"]),
        "tools_root": str(roots["tools_root"]),
        "source": {
            "kind": "PINNED_TOOLS_MANIFEST",
            "manifest_id": manifest.get("manifest_id"),
            "manifest_path": "config/deployment/tools-windows-x64.json",
            "manifest_sha256": manifest_sha,
            "hash_algorithm": "sha256",
        },
        "bootstrap": {
            # Stock Windows PowerShell 5.1: the existing installer is already
            # compatible with it, so missing PowerShell 7 is not a circular
            # dependency any more.
            "executable": "powershell.exe",
            "script": "scripts/bootstrap.ps1",
            "parameters": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/bootstrap.ps1",
                           "-ToolsManifest", "-InstallRoot", "-ToolsRoot"],
            "requires_administrator": False,
            "downloads_only_pinned_packages": True,
        },
        "authorization": {
            "action": "tool_repair",
            "status": (STATUS_GRANTED if same_plan and stored.get("status")
                       else STATUS_AWAITING) if packages else "NOT_REQUIRED",
            "operator_required": True,
            "remote_clients_allowed": False,
        },
        "execution": {
            "status": "NOT_STARTED" if not same_plan else stored.get("execution_status", "NOT_STARTED"),
            "attempt": stored.get("result") if same_plan else None,
            "idempotent": True,
        },
        "side_effects": False,
        "created_from_report_at": (report.get("evidence") or {}).get("probed_at"),
    }


def _bootstrap_argv(plan: dict) -> list[str]:
    """The one argument vector this service may run.

    None of these values comes from a request body.  The manifest, the roots
    and the script are fixed server-side; ``-NoLaunch`` keeps the installer
    from starting the workbench while an API request is being served.
    """
    return [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(BOOTSTRAP_SCRIPT),
        "-ToolsManifest", str(DEFAULT_TOOLS_MANIFEST),
        "-InstallRoot", plan["install_root"],
        "-ToolsRoot", plan["tools_root"],
        "-NoLaunch",
    ]


def _default_runner(argv: list[str], *, cwd: str, timeout: int) -> tuple[int, str, str]:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no request input
        argv, cwd=cwd, shell=False, capture_output=True, text=True,
        timeout=timeout, errors="replace",
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _parse_report(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(stdout[start:end + 1])
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_roots(plan: dict, settings) -> None:
    data_dir = Path(settings.data_dir).resolve()
    for key in ("install_root", "tools_root"):
        path = Path(plan[key]).resolve()
        if not path.is_absolute() or ".." in Path(plan[key]).parts:
            raise RepairRefused("TOOL_REPAIR_ROOT_INVALID")
        if data_dir not in path.parents and path != data_dir:
            raise RepairRefused("TOOL_REPAIR_ROOT_OUTSIDE_DATA_DIR")


def execute_repair(db, settings, actor, plan_id: str, *, report: dict | None = None,
                   runner=None, probe=None, is_windows: bool | None = None,
                   timeout: int = 3600) -> dict:
    """Run the approved plan and re-verify the tools from the managed directory.

    ``runner`` and ``probe`` are injectable so the exact decision logic is
    testable without Windows or a real download.  Nothing here accepts a
    command, a URL or a path from the caller.
    """
    from shine.comfyops.host_env import probe_host_environment

    if report is None:
        roots = managed_tool_roots(settings)
        report = probe_host_environment(
            Path(settings.data_dir), deployment_dir=Path(settings.comfyops_dir),
            tool_paths=[str(roots["bin_root"])],
        )
    state = read_repair_state(db)
    plan = build_repair_plan(settings, report, state=state)
    if plan_id != plan["plan_id"]:
        raise RepairPlanChanged("TOOL_REPAIR_PLAN_CHANGED")
    if not plan["packages"]:
        return {"status": "ALREADY_AVAILABLE", "plan_id": plan_id,
                "message": "工具都已达标，没有执行任何安装。", "packages": []}
    windows = os.name == "nt" if is_windows is None else is_windows
    if not windows:
        return {"status": PLAN_UNSUPPORTED, "plan_id": plan_id,
                "message": "自动补装只在 Windows 本机执行；当前环境未运行任何安装。",
                "packages": [row["package_id"] for row in plan["packages"]]}
    _validate_roots(plan, settings)
    runner = runner or _default_runner
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    returncode, stdout, stderr = runner(_bootstrap_argv(plan), cwd=str(REPO_ROOT), timeout=timeout)
    installer_report = _parse_report(stdout)
    tools_probe = probe or (lambda: probe_host_environment(
        Path(settings.data_dir), deployment_dir=Path(settings.comfyops_dir),
        tool_paths=[plan["install_root"]],
    )["tools"])
    after = tools_probe()
    from shine.comfyops.tool_inventory import inspect_packages

    verified = inspect_packages(after)
    required = {row["package_id"] for row in plan["packages"]}
    satisfied = [row["package_id"] for row in verified
                 if row["package_id"] in required and row["installed"]]
    unsatisfied = sorted(required - set(satisfied))
    if not unsatisfied:
        status = "SUCCEEDED"
    elif satisfied:
        status = "PARTIAL"
    else:
        status = "FAILED"
    result = {
        "status": status,
        "plan_id": plan_id,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "exit_code": returncode,
        "installed": installer_report.get("installed") or [],
        "installer_blockers": installer_report.get("blockers") or [],
        "verification": [
            {"package_id": row["package_id"], "installed": row["installed"],
             "tools": [{"tool": tool["tool"], "present": tool["present"],
                        "version": tool["version"], "at_floor": tool["at_floor"]}
                       for tool in row["tools"]]}
            for row in verified if row["package_id"] in required
        ],
        "unsatisfied_packages": unsatisfied,
        "retryable": status != "SUCCEEDED" and bool(installer_report),
        # A failed download keeps the verified archive cache; a retry resumes
        # instead of re-downloading, so no bytes are thrown away here.
        "kept_bytes": True,
        "stderr_tail": (stderr or "").strip().splitlines()[-3:],
        "evidence": "tool_repair.bootstrap_report+managed_path_reprobe",
    }
    if returncode != 0 and status == "SUCCEEDED":
        # The installer reported a blocker that the re-probe does not see
        # (for example an unrelated package).  The tools that matter passed,
        # so the status stays SUCCEEDED and the installer output is kept.
        result["installer_exit_note"] = "non_zero_exit_but_required_packages_verified"
    _save_repair_state(db, {
        "plan_id": plan_id,
        "status": STATUS_GRANTED,
        "execution_status": status,
        "result": result,
        "actor": str(actor),
        "recorded_at": result["finished_at"],
    })
    from shine.services import add_audit

    if db is not None:
        add_audit(db, actor, "TOOL_REPAIR_EXECUTED", "tool_repair", plan_id, {
            "status": status,
            "packages": sorted(required),
            "installed": [row.get("name") for row in result["installed"]],
            "verification": [{row["package_id"]: row["installed"]} for row in result["verification"]],
            "download_bytes": plan["download_bytes"],
            "manifest_sha256": plan["source"]["manifest_sha256"],
        })
        db.commit()
    return result
