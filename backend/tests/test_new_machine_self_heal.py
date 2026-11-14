"""P1: new-machine self-healing - tool packages, repair plan, RAM evidence, port identity.

The RTX 4070 sample from the field report is reproduced as *data*: no 4070, no
Windows and no download is required to exercise the decision logic.  What these
tests prove is the classification and the contracts.  What they do not prove is
stated in each test: real Windows installation and real 4070 inference remain
``PENDING_WINDOWS_VALIDATION``.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from shine.comfyops import environment_repair, host_env, tool_inventory
from shine.comfyops.recommendation import recommend_variants
from shine.comfyops.variants import qualify
from tests.test_v13_preparation import HEADERS, approve, post, prep  # noqa: F401

DEFAULT_MANIFEST = tool_inventory.load_tools_manifest()


# ---------------------------------------------------------------------------
# The real sample, as host facts
# ---------------------------------------------------------------------------

def tool(present: bool, version: str, at_floor: bool = True, min_version: str = "0") -> dict:
    return {"present": present, "version": version, "at_floor": present and at_floor,
            "min_version": min_version, "tool": "x",
            "search_scope": "MANAGED_THEN_PROCESS_PATH" if present else "PROCESS_PATH",
            "requires_environment_refresh": present}


def tools_4070() -> dict:
    return {
        "ffmpeg": tool(False, "", False, "6"),
        "ffprobe": tool(False, "", False, "6"),
        "pwsh": tool(False, "", False, "7.3"),
        "node": tool(True, "22.15.0", True, "20"),
        "npm": tool(True, "10.9.2", True, "10"),
        "uv": tool(True, "0.13.7", True, "0.9"),
    }


def tools_all_installed() -> dict:
    rows = tools_4070()
    rows.update({
        "ffmpeg": tool(True, "8.1.3", True, "6"),
        "ffprobe": tool(True, "8.1.3", True, "6"),
        "pwsh": tool(True, "7.4.12", True, "7.3"),
    })
    return rows


def ram_4070() -> dict:
    return {
        "status": "OK", "ram_mb": 31525, "ram_available_mb": 18122,
        "installed_mb": 32768, "installed_source": "Win32_PhysicalMemory",
        "installed_inventory_status": "OK", "declared_floor_mb": 32768,
        "floor_mb": 31744, "deficit_mb": 219, "at_floor": True,
        "at_floor_basis": "HARDWARE_INSTALLED_MEMORY",
        "review_required": False, "note": "HARDWARE_RESERVED_MEMORY",
    }


def port_4070() -> dict:
    return {
        "status": "OK", "port": 8188, "selected": 8189, "identity": "FOREIGN_PROCESS",
        "preferred_available": False, "reusable": False, "reason": None,
        "next_action": None,
        "evidence": {"bind": "OCCUPIED", "reachable": True, "comfyui": False,
                     "alternative_port": 8189, "identity_rule": "http_responder"},
    }


def report_4070(**overrides) -> dict:
    report = {
        "platform": {"system": "Windows", "machine": "AMD64", "arch": "x64",
                     "is_windows": True, "is_x64": True},
        "tools": tools_4070(),
        "gpu": {"status": "OK", "name": "GeForce RTX 4070", "uuid": "GPU-4070",
                "driver_version": "610.88", "vram_total_mb": 12282,
                "vram_mb": 12282, "vram_free_mb": 11000},
        "gpus": [{"uuid": "GPU-4070", "name": "GeForce RTX 4070",
                  "driver_version": "610.88", "vram_total_mb": 12282,
                  "vram_free_mb": 11000, "selected": True}],
        "cuda": {"status": "OK", "compute_capability": "8.9"},
        "ram": ram_4070(),
        "commit": {"status": "OK", "commit_total_mb": 40000, "commit_available_mb": 22000,
                   "page_file_status": "PAGE_FILE_PRESENT", "page_file_headroom_mb": 8475},
        "disk": {"status": "OK", "free_gb": 503.06, "at_floor": True, "runtime_floor_gb": 5},
        "port": port_4070(),
        "data_dir": {"path": "D:/Shine/data", "writable": True, "status": "OK"},
        "deployment_dir": {"path": "D:/Shine/runtime", "writable": True, "status": "OK"},
        "evidence": {"probed_at": "2026-11-14T00:00:00Z", "failures": []},
    }
    report.update(overrides)
    return report


def settings_for(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(data_dir=tmp_path / "data", comfyops_dir=tmp_path / "runtime")


# ---------------------------------------------------------------------------
# L1: tool inventory and the two missing packages
# ---------------------------------------------------------------------------

def test_4070_reports_two_repairable_packages_not_five_missing_tools():
    packages = tool_inventory.inspect_packages(tools_4070(), DEFAULT_MANIFEST)
    by_id = {row["package_id"]: row for row in packages}
    assert by_id["ffmpeg"]["missing_tools"] == ["ffmpeg", "ffprobe"]
    assert by_id["pwsh"]["missing_tools"] == ["pwsh"]
    assert by_id["node"]["installed"] and by_id["uv"]["installed"]
    repairable = [row["package_id"] for row in tool_inventory.repairable_packages(
        tools_4070(), DEFAULT_MANIFEST)]
    assert repairable == ["ffmpeg", "pwsh"], "Node/npm/uv must never be reinstalled"
    assert by_id["ffmpeg"]["download"]["sha256"] == DEFAULT_MANIFEST["tools"]["ffmpeg"]["sha256"]
    assert by_id["ffmpeg"]["download_bytes"] == DEFAULT_MANIFEST["tools"]["ffmpeg"]["size"]


def test_package_inventory_is_per_command_and_marks_environment_refresh():
    packages = {row["package_id"]: row for row in
                tool_inventory.inspect_packages(tools_4070(), DEFAULT_MANIFEST)}
    node_tools = {row["tool"]: row for row in packages["node"]["tools"]}
    assert node_tools["node"]["version"] == "22.15.0"
    assert node_tools["npm"]["version"] == "10.9.2"
    assert node_tools["npm"]["min_version"] == "10"
    assert packages["ffmpeg"]["requires_environment_refresh"] is True
    assert packages["node"]["requires_environment_refresh"] is False


def test_stale_tool_is_a_repairable_package_not_a_new_install():
    stale = tools_4070()
    stale["node"] = tool(True, "18.19.0", False, "20")
    packages = {row["package_id"]: row for row in
                tool_inventory.inspect_packages(stale, DEFAULT_MANIFEST)}
    assert packages["node"]["status"] == tool_inventory.PACKAGE_STALE
    assert packages["node"]["stale_tools"] == ["node"]
    assert packages["node"]["auto_repairable"] is True


# ---------------------------------------------------------------------------
# L1: the repair plan is read-only, stable and content-bound
# ---------------------------------------------------------------------------

def test_repair_plan_is_stable_and_has_no_side_effects(tmp_path):
    settings = settings_for(tmp_path)
    first = environment_repair.build_repair_plan(settings, report_4070())
    second = environment_repair.build_repair_plan(settings, report_4070())
    assert first["plan_id"] == second["plan_id"]
    assert first["plan_id"].startswith("toolrepair-")
    assert first["status"] == environment_repair.PLAN_COMPLETE
    assert [row["package_id"] for row in first["packages"]] == ["ffmpeg", "pwsh"]
    assert first["download_bytes"] == (
        DEFAULT_MANIFEST["tools"]["ffmpeg"]["size"] + DEFAULT_MANIFEST["tools"]["pwsh"]["size"])
    assert first["authorization"]["status"] == environment_repair.STATUS_AWAITING
    assert first["execution"]["status"] == "NOT_STARTED"
    assert first["side_effects"] is False
    assert first["source"]["hash_algorithm"] == "sha256"
    assert not (tmp_path / "data").exists(), "planning must not create directories"


def test_repair_plan_identity_changes_when_a_package_is_added(tmp_path):
    settings = settings_for(tmp_path)
    missing_pwsh = environment_repair.build_repair_plan(settings, report_4070())
    only_ffmpeg = report_4070(tools={**tools_4070(), "pwsh": tool(True, "7.4.12", True, "7.3")})
    fewer = environment_repair.build_repair_plan(settings, only_ffmpeg)
    assert fewer["plan_id"] != missing_pwsh["plan_id"]
    assert [row["package_id"] for row in fewer["packages"]] == ["ffmpeg"]


def test_nothing_to_repair_is_not_an_install(tmp_path):
    settings = settings_for(tmp_path)
    plan = environment_repair.build_repair_plan(settings, report_4070(tools=tools_all_installed()))
    assert plan["status"] == environment_repair.PLAN_NOTHING_TO_REPAIR
    assert plan["packages"] == []
    assert plan["authorization"]["status"] == "NOT_REQUIRED"


# ---------------------------------------------------------------------------
# L1: P1-B decoupling - BOOTSTRAP is not held hostage by GPU/RAM
# ---------------------------------------------------------------------------

def test_4070_bootstrap_scope_is_only_blocked_by_repairable_tools():
    findings = host_env.environment_findings(report_4070())
    bootstrap = [row for row in findings if "BOOTSTRAP" in row["scopes"]]
    assert bootstrap, "the tool findings must be classified"
    unrepairable = [row for row in bootstrap
                    if row["repairability"] not in {
                        host_env.REPAIRABILITY_AUTO, host_env.REPAIRABILITY_AVAILABLE}]
    assert unrepairable == [], "no VRAM/RAM condition may block the bootstrap scope"
    assert {row["package_id"] for row in bootstrap} == {"ffmpeg", "pwsh", "node", "uv"}


def test_gpu_and_ram_findings_name_their_real_scope_and_next_action():
    findings = {row["code"]: row for row in host_env.environment_findings(report_4070())}
    vram = findings["GPU_VRAM_BELOW_FLOOR"]
    assert vram["repairability"] == host_env.REPAIRABILITY_HARDWARE
    assert "BOOTSTRAP" not in vram["scopes"]
    assert "VARIANT_QUALIFICATION" in vram["scopes"]
    assert vram["actual"] == 12282 and vram["required"] == 15360
    assert vram["next_action"] == "SELECT_LOWER_TIER_VARIANT"
    assert findings["RAM_MEETS_FLOOR"]["repairability"] == host_env.REPAIRABILITY_AVAILABLE
    port = findings["PORT_OCCUPIED_SELECTED_ALTERNATIVE"]
    assert port["scope"] == ["SERVICE_CONFIGURATION"]
    assert port["selected_port"] == 8189


def test_legacy_blocker_list_keeps_codes_and_adds_classification():
    blockers = host_env.setup_blockers(report_4070())
    codes = {row["code"] for row in blockers}
    assert {"TOOL_MISSING_INSTALLABLE", "GPU_VRAM_BELOW_FLOOR"} <= codes
    assert "RAM_BELOW_FLOOR" not in codes, "a measured 32 GB kit is not below the floor"
    for row in blockers:
        assert row["repairability"] in host_env.REPAIRABILITIES
        assert isinstance(row["scope"], list)
        assert row["next_action"]


# ---------------------------------------------------------------------------
# L1: P1-D memory boundary matrix
# ---------------------------------------------------------------------------

def memory(total: int, available: int | None = None) -> dict:
    return {
        "status": "OK", "ram_mb": total,
        "ram_available_mb": available if available is not None else total // 2,
        "commit_total_mb": total + 2048, "commit_available_mb": total // 2,
        "memory_load_percent": 40,
    }


def installed(mb: int | None) -> dict:
    return {"status": "OK" if mb else "UNKNOWN",
            "installed_mb": mb, "source": "Win32_PhysicalMemory",
            "reason": None if mb else "HARDWARE_INVENTORY_UNAVAILABLE"}


@pytest.mark.parametrize("total,installed_mb,at_floor,basis,note", [
    (31525, 32768, True, "HARDWARE_INSTALLED_MEMORY", "HARDWARE_RESERVED_MEMORY"),
    (31525, None, False, "OS_REPORTED_TOTAL", "INSTALLED_CAPACITY_UNKNOWN"),
    (31744, None, True, "OS_REPORTED_TOTAL", None),
    (32768, None, True, "OS_REPORTED_TOTAL", None),
    (16384, 16384, False, "OS_REPORTED_TOTAL", "BELOW_FLOOR"),
])
def test_ram_boundary_contract(total, installed_mb, at_floor, basis, note):
    result = host_env.probe_ram(
        installed_reader=lambda: installed(installed_mb),
        memory_reader=lambda: memory(total),
    )
    assert result["floor_mb"] == 31744, "the floor is the declared 32768 minus the fixed slack"
    assert result["at_floor"] is at_floor
    assert result["at_floor_basis"] == basis
    assert result["note"] == note
    assert result["review_required"] is (installed_mb is None and not at_floor)


def test_failing_memory_probe_is_unknown_not_a_fabricated_floor():
    result = host_env.probe_ram(
        installed_reader=lambda: installed(None),
        memory_reader=lambda: {"status": "BLOCKED", "reason": "RAM_UNAVAILABLE"},
    )
    assert result["status"] == "BLOCKED"
    assert result["at_floor"] is False
    assert result["review_required"] is True


def test_page_file_state_is_reported_separately_from_physical_memory():
    result = host_env.probe_ram(
        installed_reader=lambda: installed(32768),
        memory_reader=lambda: memory(31525, available=1024),
    )
    assert result["ram_available_mb"] == 1024
    assert result["at_floor"] is True, "free memory is not the installed capacity"

    commit = host_env.probe_commit()
    assert commit["status"] in {"OK", "BLOCKED"}
    if commit["status"] == "OK":
        assert commit["page_file_status"] in {
            "PAGE_FILE_PRESENT", "PAGE_FILE_DISABLED_OR_TINY"}


def test_unreadable_inventory_keeps_default_variant_unverified():
    env = report_4070()
    env["ram"] = host_env.probe_ram(
        installed_reader=lambda: installed(None), memory_reader=lambda: memory(31525))
    verdict = qualify({"os": "windows-x64", "arch": "x64", "min_ram_mb": 32768}, env)
    assert verdict["status"] == "UNKNOWN"
    assert any(row["code"] == "RAM_UNVERIFIED" for row in verdict["unknowns"])
    assert not any(row["code"] == "RAM_INSUFFICIENT" for row in verdict["blockers"])


def test_measured_32gb_kit_passes_the_declared_ram_floor():
    verdict = qualify({"os": "windows-x64", "arch": "x64", "min_ram_mb": 32768}, report_4070())
    assert not any(row["code"] == "RAM_INSUFFICIENT" for row in verdict["blockers"])
    assert any(row["code"] == "RAM_RESERVED_MEMORY" for row in verdict["warnings"])


# ---------------------------------------------------------------------------
# L1: P1-C 4070 variant tiering
# ---------------------------------------------------------------------------

def test_4070_is_lowend_and_default_int8_is_not_production_ready():
    result = recommend_variants(report_4070())
    assert result["tier"] == "lowend_12gb"
    assert result["recommended"] == []
    candidate_ids = {row["variant_id"] for row in result["candidates"]}
    assert "ref2va-lowend-candidate" in candidate_ids
    assert "ref2va-nvfp4-lowend" in candidate_ids
    int8 = next(row for row in result["options"]
                if row["variant_id"] == "h3-ref2va-int8-convrot")
    assert int8["usable_in_production"] is False
    assert any(row["code"] == "VRAM_INSUFFICIENT" for row in int8["blockers"])
    for candidate in result["candidates"]:
        assert candidate["usable_in_production"] is False
        assert candidate["acceptance_status"] == "CANDIDATE"


# ---------------------------------------------------------------------------
# L1: P1-E port identity
# ---------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def occupied_socket(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def test_free_port_identity():
    state = host_env.probe_port(free_port(), http=lambda _port: {"reachable": False})
    assert state["identity"] == "FREE"
    assert state["selected"] == state["port"]


@pytest.mark.parametrize("http,identity,reusable", [
    ({"reachable": True, "comfyui": True}, "ADOPTABLE_COMFYUI", True),
    ({"reachable": True, "comfyui": False}, "FOREIGN_PROCESS", False),
    ({"reachable": False, "comfyui": False}, "UNKNOWN_IDENTITY", False),
])
def test_occupied_port_identity_is_not_guessed_from_a_bind(http, identity, reusable):
    port = free_port()
    sock = occupied_socket(port)
    try:
        state = host_env.probe_port(port, http=lambda _port: http)
    finally:
        sock.close()
    assert state["identity"] == identity
    assert state["reusable"] is reusable
    if reusable:
        assert state["selected"] == port
    else:
        assert state["selected"] and state["selected"] != port
        assert "alternative_port" in state["evidence"]


def test_owned_port_is_reused_even_when_another_process_holds_the_socket():
    port = free_port()
    sock = occupied_socket(port)
    try:
        state = host_env.probe_port(port, owned_ports={port},
                                    http=lambda _port: {"reachable": True, "comfyui": False})
    finally:
        sock.close()
    assert state["identity"] == "OWNED_COMFYUI"
    assert state["reusable"] is True
    assert state["selected"] == port
    assert state["evidence"]["identity_rule"] == "owned_record"


def test_http_200_without_comfy_signature_is_never_comfyui():
    assert host_env._probe_comfy_http(1)["comfyui"] is False


# ---------------------------------------------------------------------------
# L1: the controlled executor
# ---------------------------------------------------------------------------

def test_executor_uses_stock_powershell_and_a_fixed_argument_vector(tmp_path):
    settings = settings_for(tmp_path)
    plan = environment_repair.build_repair_plan(settings, report_4070())
    argv = environment_repair._bootstrap_argv(plan)
    assert argv[0] == "powershell.exe", "PowerShell 7 must not be its own prerequisite"
    assert argv[1:4] == ["-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert argv[argv.index("-File") + 1].endswith("bootstrap.ps1")
    assert argv[argv.index("-InstallRoot") + 1] == plan["install_root"]
    assert argv[argv.index("-ToolsRoot") + 1] == plan["tools_root"]
    assert plan["bootstrap"]["requires_administrator"] is False
    assert plan["bootstrap"]["downloads_only_pinned_packages"] is True


def test_execute_repair_verifies_from_the_managed_path(tmp_path):
    settings = settings_for(tmp_path)
    report = report_4070()
    captured = {}

    def runner(argv, *, cwd, timeout):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return 0, json.dumps({"installed": [{"name": "ffmpeg"}, {"name": "pwsh"}],
                              "blockers": []}), ""

    result = environment_repair.execute_repair(
        None, settings, "local-user", environment_repair.build_repair_plan(settings, report)["plan_id"],
        report=report, runner=runner, probe=tools_all_installed, is_windows=True)
    assert result["status"] == "SUCCEEDED"
    assert result["unsatisfied_packages"] == []
    assert {row["package_id"] for row in result["verification"]} == {"ffmpeg", "pwsh"}
    assert captured["argv"][0] == "powershell.exe"
    assert "ffmpeg" not in " ".join(captured["argv"]), "no tool name from a request is executed"


def test_execute_repair_is_idempotent(tmp_path):
    settings = settings_for(tmp_path)
    called = []

    def runner(argv, *, cwd, timeout):
        called.append(argv)
        return 0, "{}", ""

    report = report_4070(tools=tools_all_installed())
    plan = environment_repair.build_repair_plan(settings, report)
    result = environment_repair.execute_repair(
        None, settings, "local-user", plan["plan_id"],
        report=report, runner=runner, probe=tools_all_installed, is_windows=True)
    assert result["status"] == "ALREADY_AVAILABLE"
    assert called == [], "a machine that is already complete must not run the installer"
    assert result["packages"] == []


def test_execute_repair_rejects_a_changed_plan(tmp_path):
    settings = settings_for(tmp_path)
    with pytest.raises(environment_repair.RepairPlanChanged):
        environment_repair.execute_repair(
            None, settings, "local-user", "toolrepair-" + "f" * 32,
            report=report_4070(), runner=lambda *a, **k: (0, "{}", ""),
            probe=tools_4070, is_windows=True)


def test_execute_repair_reports_failure_with_preserved_bytes(tmp_path):
    settings = settings_for(tmp_path)
    report = report_4070()

    def runner(argv, *, cwd, timeout):
        return 1, json.dumps({"installed": [], "blockers": [
            {"name": "ffmpeg", "status": "install_failed", "detail": "sha256 mismatch"}]}), "sha256 mismatch"

    result = environment_repair.execute_repair(
        None, settings, "local-user",
        environment_repair.build_repair_plan(settings, report)["plan_id"],
        report=report, runner=runner, probe=tools_4070, is_windows=True)
    assert result["status"] == "FAILED"
    assert result["retryable"] is True
    assert result["kept_bytes"] is True
    assert result["unsatisfied_packages"] == ["ffmpeg", "pwsh"]
    assert "sha256 mismatch" in " ".join(result["stderr_tail"])


def test_execute_repair_is_windows_only(tmp_path):
    settings = settings_for(tmp_path)
    called = []
    result = environment_repair.execute_repair(
        None, settings, "local-user",
        environment_repair.build_repair_plan(settings, report_4070())["plan_id"],
        report=report_4070(),
        runner=lambda *a, **k: (called.append(1), (0, "{}", ""))[1],
        probe=tools_4070, is_windows=False)
    assert result["status"] == environment_repair.PLAN_UNSUPPORTED
    assert called == []


def test_executor_refuses_a_root_outside_the_data_directory(tmp_path):
    settings = settings_for(tmp_path)
    plan = environment_repair.build_repair_plan(settings, report_4070())
    plan["install_root"] = str(tmp_path / "elsewhere" / "bin")
    with pytest.raises(environment_repair.RepairRefused):
        environment_repair._validate_roots(plan, settings)


def test_web_repair_uses_the_stock_powershell_entry_and_a_managed_tools_root(tmp_path):
    """PowerShell 7 cannot be its own prerequisite: the Web repair is launched
    by the stock Windows PowerShell, and the script accepts a managed tools
    root without changing the historical default."""
    repo = environment_repair.REPO_ROOT
    bootstrap = (repo / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert "[string]$ToolsRoot = \"\"" in bootstrap
    assert "$toolBase = if ($ToolsRoot) { $ToolsRoot }" in bootstrap
    plan = environment_repair.build_repair_plan(settings_for(tmp_path), report_4070())
    assert plan["bootstrap"]["executable"] == "powershell.exe"
    assert plan["bootstrap"]["parameters"][0] == "-NoProfile"


# ---------------------------------------------------------------------------
# L2: API surface (read-only detection, guarded execution)
# ---------------------------------------------------------------------------

def test_environment_endpoint_exposes_findings_packages_and_plan_without_side_effects(prep, monkeypatch):
    client, _, settings = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_environment",
                        lambda *a, **k: report_4070())
    response = client.get("/api/v1/setup/environment")
    assert response.status_code == 200
    body = response.json()
    assert {row["package_id"] for row in body["tool_packages"]} == {"node", "uv", "ffmpeg", "pwsh"}
    assert any(row["repairability"] == "AUTO_REPAIRABLE" for row in body["findings"])
    assert body["repair_plan"]["status"] == "COMPLETE"
    assert body["repair_plan"]["side_effects"] is False
    assert body["blockers"], "legacy consumers still receive a blocker list"


def test_tool_repair_execute_requires_local_execution(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.delenv("SHINE_ENABLE_LOCAL_EXECUTION", raising=False)
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_environment",
                        lambda *a, **k: report_4070())
    plan = client.get("/api/v1/setup/tool-repair-plan").json()
    response = client.post("/api/v1/setup/tool-repair/execute",
                           json={"plan_id": plan["plan_id"]}, headers={"X-Shine-Local-Action": "1"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LOCAL_EXECUTION_NOT_ENABLED"


def test_tool_repair_execute_refuses_a_fabricated_plan(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.setenv("SHINE_ENABLE_LOCAL_EXECUTION", "1")
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_environment",
                        lambda *a, **k: report_4070())
    response = client.post("/api/v1/setup/tool-repair/execute",
                           json={"plan_id": "toolrepair-" + "a" * 32},
                           headers={"X-Shine-Local-Action": "1"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TOOL_REPAIR_PLAN_CHANGED"


def test_tool_repair_execute_rejects_extra_command_fields(prep):
    client, _, _ = prep
    response = client.post("/api/v1/setup/tool-repair/execute",
                           json={"plan_id": "toolrepair-" + "a" * 32, "command": "calc.exe"},
                           headers={"X-Shine-Local-Action": "1"})
    assert response.status_code == 422, "the request schema is closed to command injection"
