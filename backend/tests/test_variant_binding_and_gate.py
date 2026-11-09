"""The official W6A8 low-end candidate: its own identity and its own gates.

A candidate variant is not a second INT8 baseline with a different label.  It
has its own deployment catalog, its own workflow profile, its own weight pins
and its own acceptance status, and it never inherits a conclusion from the
audited baseline.  These tests pin that separation:

* registering the candidate registers *its* profile, never the baseline's;
* a variant and a catalog that disagree are refused, not merged;
* a candidate this machine cannot execute is refused by the API, and the
  refusal names its reason instead of falling back to the baseline;
* an explicitly selected candidate keeps one identity from the setup page to
  the director package to the production series;
* the ordinary INT8 path is untouched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from shine.comfyops import deployment
from shine.comfyops.variant_binding import load_binding, resolve_registration_target
from shine.comfyops.variant_readiness import _required_components
from shine.models import ProductionSeries, WorkflowProfile

from .conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    upload_asset,
)

LOWEND_VARIANT = "ref2va-lowend-candidate"
LOWEND_CATALOG = "h3-ref2va-lowend-candidate-windows-x64"
LOWEND_PROFILE = "local-h3-ref2va-lowend-pdd-free-2img"
BASELINE_VARIANT = "h3-ref2va-int8-convrot"
LOCAL = {"X-Shine-Local-Action": "1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _studio(client, *, shots: int = 1, title: str = "低显存候选"):
    """A project with a frozen style, one approved subject and real shots."""
    project_id = create_project(client, title)
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="角色.png")
    approve_lock(client, subject["id"])
    created = []
    for index in range(shots):
        created.append(create_shot(
            client, project_id, style["id"],
            title=f"镜头{index + 1}",
            beat=f"第{index + 1}个动作",
            asset_ids=[subject["id"]],
            bindings=[{"asset_id": subject["id"], "use_as": "subject",
                       "role": "CHAR_A", "ordinal": 0}],
        ))
    return {"project": project_id, "style": style, "shots": created, "subject": subject}


def _register_candidate(client, project_id: str) -> dict:
    response = client.post("/api/v1/setup/profiles",
                           json={"project_id": project_id, "variant_id": LOWEND_VARIANT})
    assert response.status_code == 200, response.text
    rows = response.json()["profiles"]
    assert [row["name"] for row in rows] == [LOWEND_PROFILE]
    return rows[0]


def _mark_weights_verified(factory) -> list[str]:
    """The candidate's own model files, hash-verified in the package registry.

    This is exactly what the approved local-import path does after it checked
    the bytes against the pinned revision, byte count and SHA-256.
    """
    catalog = deployment.load_deployment_catalog(LOWEND_CATALOG)
    binding = load_binding(LOWEND_VARIANT)
    component_ids = [c["component_id"]
                     for c in _required_components(catalog, binding.profiles)]
    assert component_ids, "the candidate profile must name the weights it loads"
    with factory.begin() as db:
        rows = deployment.register_catalog_packages(db, catalog, component_ids)
        for row in rows.values():
            row.status = "FILES_VERIFIED_PENDING_RUNTIME"
    return component_ids


def _mark_validated(factory, profile_id: str) -> None:
    """The recorded proof that the graph was checked on a real renderer."""
    with factory.begin() as db:
        profile = db.get(WorkflowProfile, profile_id)
        profile.validated_against = {
            "missing_nodes": [], "missing_inputs": [],
            "validated_at": "2026-11-09T00:00:00", "source": "managed_live_object_info",
            "generation_verified": False, "services_stopped": True,
        }


def _fake_managed_runtime(monkeypatch) -> None:
    """A managed runtime configuration exists (nothing is started)."""
    monkeypatch.setattr(
        "shine.comfyops.runtime_configuration._current_identity",
        lambda db, settings: {
            "instance_id": "local:http://127.0.0.1:8188",
            "recipe": "synthetic", "recipe_sha256": "a" * 64,
            "instance_sha256": "b" * 64,
        },
    )


def _gate(client, project_id: str, profile_id: str) -> dict:
    response = client.get(
        f"/api/v1/setup/production-gate?project_id={project_id}&profile_id={profile_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _codes(gate: dict) -> list[str]:
    return [blocker["code"] for blocker in gate["blockers"]]


# ---------------------------------------------------------------------------
# Registration: the candidate owns its catalog and its profile
# ---------------------------------------------------------------------------


def test_registering_the_candidate_registers_its_own_profile_not_the_baseline(api):
    client, _factory, _settings = api
    project_id = create_project(client, "候选登记")

    row = _register_candidate(client, project_id)

    assert row["status"] == "REGISTERED"
    names = {item["name"] for item in
             client.get(f"/api/v1/workflow-profiles?project_id={project_id}").json()}
    assert names == {LOWEND_PROFILE}
    baseline = deployment.load_default_catalog()["workflow_profiles"]
    assert LOWEND_PROFILE not in baseline
    assert not names & set(baseline)


def test_registering_the_candidate_twice_is_idempotent(api):
    client, _factory, _settings = api
    project_id = create_project(client, "候选幂等")

    first = _register_candidate(client, project_id)
    second = client.post("/api/v1/setup/profiles",
                         json={"project_id": project_id, "variant_id": LOWEND_VARIANT})
    assert second.status_code == 200, second.text
    row = second.json()["profiles"][0]
    assert row["status"] == "ALREADY_REGISTERED"
    assert row["profile_id"] == first["profile_id"]
    assert len(client.get(f"/api/v1/workflow-profiles?project_id={project_id}").json()) == 1


def test_a_variant_and_a_catalog_that_disagree_are_refused(api):
    client, _factory, _settings = api
    project_id = create_project(client, "目录错配")

    response = client.post("/api/v1/setup/profiles", json={
        "project_id": project_id,
        "variant_id": LOWEND_VARIANT,
        "catalog": "h3-ref2va-windows-x64",
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "VARIANT_CATALOG_MISMATCH"
    # Nothing was registered: the refusal is not a partial success.
    assert client.get(f"/api/v1/workflow-profiles?project_id={project_id}").json() == []


def test_a_same_name_profile_with_different_content_is_refused(api):
    client, factory, _settings = api
    project_id = create_project(client, "同名冲突")
    graph = {"1": {"class_type": "UnrelatedWorkflow", "inputs": {}}}
    from shine.comfy.workflow import graph_hash

    with factory.begin() as db:
        db.add(WorkflowProfile(
            project_id=project_id, name=LOWEND_PROFILE, mode="Ref2VA", graph=graph,
            graph_sha256=graph_hash(graph), parameter_map={}, required_nodes=[],
        ))
    response = client.post("/api/v1/setup/profiles",
                           json={"project_id": project_id, "variant_id": LOWEND_VARIANT})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "PROFILE_CONTENT_CHANGED"


def test_an_unknown_variant_is_refused_by_name(api):
    client, _factory, _settings = api
    project_id = create_project(client, "未知变体")
    response = client.post("/api/v1/setup/profiles",
                           json={"project_id": project_id, "variant_id": "no-such-variant"})
    assert response.status_code == 404, response.text
    assert "VARIANT_MANIFEST_UNKNOWN" in response.text


def test_the_candidate_profile_carries_its_own_variant_identity(api):
    """The identity chain starts at the profile: variant, graph, checkpoint."""
    client, factory, _settings = api
    project_id = create_project(client, "身份链")
    row = _register_candidate(client, project_id)

    manifest = load_binding(LOWEND_VARIANT).manifest
    with factory() as db:
        profile = db.get(WorkflowProfile, row["profile_id"])
        assert profile.model_identity["variant_id"] == LOWEND_VARIANT
        assert profile.model_identity["variant_acceptance_status"] == "CANDIDATE"
        assert profile.model_identity["variant_verified"] is False
        assert profile.graph_sha256 == manifest["graph_sha256"][LOWEND_PROFILE]
        assert profile.checkpoint_id == manifest["checkpoint_id"]
        assert profile.checkpoint_id != load_binding(BASELINE_VARIANT).manifest["checkpoint_id"]


# ---------------------------------------------------------------------------
# The production gate: a candidate this machine cannot run stays out
# ---------------------------------------------------------------------------


def test_the_gate_names_what_is_missing_instead_of_claiming_a_pass(api):
    client, _factory, _settings = api
    project_id = create_project(client, "门禁缺项")
    row = _register_candidate(client, project_id)

    gate = _gate(client, project_id, row["profile_id"])
    assert gate["candidate"] is True
    assert gate["acceptance_status"] == "CANDIDATE"
    assert gate["selectable"] is False
    codes = _codes(gate)
    assert "PROFILE_NOT_VALIDATED" in codes
    assert "PROFILE_WEIGHTS_NOT_AVAILABLE" in codes
    assert "MANAGED_INSTANCE_NOT_CONFIGURED" in codes
    for blocker in gate["blockers"]:
        assert blocker["message"], "a refusal must say why"


def test_the_gate_reports_the_missing_nodes_and_the_missing_weights(api):
    client, factory, _settings = api
    project_id = create_project(client, "缺权重与节点")
    row = _register_candidate(client, project_id)

    gate = _gate(client, project_id, row["profile_id"])
    weights = [blocker for blocker in gate["blockers"]
               if blocker["code"] == "PROFILE_WEIGHTS_NOT_AVAILABLE"]
    assert weights, "the missing weights must be named"
    assert weights[0]["missing_components"], "the missing component must be named"

    _mark_weights_verified(factory)
    gate = _gate(client, project_id, row["profile_id"])
    assert "PROFILE_WEIGHTS_NOT_AVAILABLE" not in _codes(gate)


def test_the_gate_reports_a_runtime_that_is_not_there(api, monkeypatch):
    client, factory, _settings = api
    project_id = create_project(client, "运行时失效")
    row = _register_candidate(client, project_id)
    _mark_weights_verified(factory)
    _mark_validated(factory, row["profile_id"])

    gate = _gate(client, project_id, row["profile_id"])
    assert "MANAGED_INSTANCE_NOT_CONFIGURED" in _codes(gate)
    assert gate["selectable"] is False

    _fake_managed_runtime(monkeypatch)
    gate = _gate(client, project_id, row["profile_id"])
    assert gate["selectable"] is True, gate["blockers"]
    assert _codes(gate) == []


def test_a_candidate_profile_of_another_project_cannot_be_used(api):
    client, factory, _settings = api
    owner = create_project(client, "方案主人")
    other = create_project(client, "越权者")
    row = _register_candidate(client, owner)

    gate = _gate(client, other, row["profile_id"])
    assert _codes(gate) == ["PROFILE_SCOPE_MISMATCH"]
    assert gate["selectable"] is False


def test_the_gate_leaves_the_audited_baseline_alone(api):
    """An accepted variant keeps its historic behaviour - no new blockers."""
    client, factory, _settings = api
    project_id = create_project(client, "基线不受影响")
    registered = client.post("/api/v1/setup/profiles", json={"project_id": project_id})
    assert registered.status_code == 200, registered.text
    baseline_row = next(row for row in registered.json()["profiles"]
                         if row["name"] == "local-h3-ref2va-pdd8-2img-1aud")

    gate = _gate(client, project_id, baseline_row["profile_id"])
    assert gate["candidate"] is False
    assert gate["acceptance_status"] == "SUPPORTED"
    assert gate["selectable"] is True
    assert gate["blockers"] == []


# ---------------------------------------------------------------------------
# The API cannot be used to bypass the gate
# ---------------------------------------------------------------------------


def test_the_candidate_cannot_be_queued_by_the_api(api):
    """A request that skips the interface is refused in exactly the same way."""
    client, _factory, _settings = api
    studio = _studio(client)
    row = _register_candidate(client, studio["project"])

    response = client.post(
        f"/api/v1/projects/{studio['project']}/production-series",
        json={
            "shot_ids": [shot["id"] for shot in studio["shots"]],
            "provider": "comfyui",
            "workflow_profile_id": row["profile_id"],
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] in {
        "PROFILE_NOT_VALIDATED", "PROFILE_WEIGHTS_NOT_AVAILABLE", "MANAGED_INSTANCE_NOT_CONFIGURED"}
    assert detail["message"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])) is None


def test_the_candidate_cannot_be_queued_as_a_single_job(api):
    client, _factory, _settings = api
    studio = _studio(client)
    row = _register_candidate(client, studio["project"])

    response = client.post(
        f"/api/v1/projects/{studio['project']}/jobs",
        json={"shot_id": studio["shots"][0]["id"], "provider": "comfyui",
              "workflow_profile_id": row["profile_id"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"]


def test_an_explicitly_selected_candidate_keeps_one_identity(api, monkeypatch):
    """Setup page → director package → production series: one profile id.

    The point of the test is that nothing in the chain substitutes the
    baseline: the package pins the candidate's graph and checkpoint, and the
    series carries the same profile id the person chose.
    """
    client, factory, settings = api
    studio = _studio(client)
    row = _register_candidate(client, studio["project"])
    _mark_weights_verified(factory)
    _mark_validated(factory, row["profile_id"])
    _fake_managed_runtime(monkeypatch)

    plan = client.post(f"/api/v1/projects/{studio['project']}/episode-plans",
                       json={"target_seconds": 8, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans/{plan.json()['version']}/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    package = client.post(
        f"/api/v1/projects/{studio['project']}/director-packages",
        json={"workflow_profile_id": row["profile_id"], "generation_mode": "Ref2VA"},
        headers=LOCAL)
    assert package.status_code == 201, package.text
    binding = package.json()["binding"]
    assert binding["workflow_profile_id"] == row["profile_id"]
    manifest = load_binding(LOWEND_VARIANT).manifest
    assert binding["graph_sha256"] == manifest["graph_sha256"][LOWEND_PROFILE]
    assert binding["checkpoint_id"] == manifest["checkpoint_id"]

    series = client.post(
        f"/api/v1/projects/{studio['project']}/production-series",
        json={
            "shot_ids": [shot["id"] for shot in studio["shots"]],
            "provider": "comfyui",
            "workflow_profile_id": row["profile_id"],
        })
    assert series.status_code == 201, series.text
    assert series.json()["workflow_profile_id"] == row["profile_id"]
    with factory() as db:
        stored = db.scalar(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"]))
        assert stored.workflow_profile_id == row["profile_id"]


def test_a_candidate_the_machine_cannot_run_never_reaches_a_director_package(api):
    client, factory, _settings = api
    studio = _studio(client)
    row = _register_candidate(client, studio["project"])
    plan = client.post(f"/api/v1/projects/{studio['project']}/episode-plans",
                       json={"target_seconds": 8, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans/{plan.json()['version']}/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    response = client.post(
        f"/api/v1/projects/{studio['project']}/director-packages",
        json={"workflow_profile_id": row["profile_id"], "generation_mode": "Ref2VA"},
        headers=LOCAL)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {
        "PROFILE_NOT_VALIDATED", "PROFILE_WEIGHTS_NOT_AVAILABLE"}


# ---------------------------------------------------------------------------
# The readiness report: five layers and exactly one next step
# ---------------------------------------------------------------------------


def test_the_readiness_report_has_five_layers_and_one_next_step(api):
    client, _factory, _settings = api
    project_id = create_project(client, "就绪报告")

    response = client.get(
        f"/api/v1/setup/variant-readiness?project_id={project_id}&variant_id={LOWEND_VARIANT}")
    assert response.status_code == 200, response.text
    report = response.json()
    assert [layer["id"] for layer in report["layers"]] == [
        "WEIGHTS_PENDING", "FILES_VERIFIED", "WORKFLOW_REGISTERED",
        "RUNTIME_VALIDATED", "REAL_RENDER_VERIFIED",
    ]
    assert report["reached_layer"] == "WEIGHTS_PENDING"
    assert report["production_ready"] is False
    assert report["next_step"]["code"]
    assert report["next_step"]["reason"]
    assert report["next_step"]["action"]
    assert report["next_step"]["blocked"] is True, "the candidate is not downloadable"
    assert "auto_download_allowed" in report
    assert report["auto_download_allowed"] is False


def test_the_readiness_next_step_moves_forward_with_the_evidence(api):
    client, factory, _settings = api
    project_id = create_project(client, "下一步推进")

    def next_step():
        report = client.get(
            f"/api/v1/setup/variant-readiness?project_id={project_id}&variant_id={LOWEND_VARIANT}").json()
        return report["next_step"], report

    step, report = next_step()
    assert step["code"] == "VARIANT_NOT_DOWNLOADABLE"
    assert report["layers"][0]["state"] == "PENDING"

    _mark_weights_verified(factory)
    step, report = next_step()
    assert step["code"] == "PROFILE_NOT_REGISTERED"
    assert report["layers"][1]["state"] == "DONE"
    assert report["layers"][2]["state"] == "PENDING"

    _register_candidate(client, project_id)
    step, report = next_step()
    assert step["code"] == "PROFILE_NOT_VALIDATED"
    assert report["layers"][2]["state"] == "DONE"

    _mark_validated(factory, report["profiles"][0]["profile_id"])
    step, report = next_step()
    assert step["code"] == "REAL_RENDER_NOT_DONE"
    assert report["layers"][3]["state"] == "DONE"
    assert report["layers"][4]["state"] == "PENDING"


def test_a_layer_after_an_unmet_one_is_never_green(api):
    """The five layers are a dependency order, not five independent facts.

    Weights that are not on this machine cannot make "the files are verified"
    true, and a profile that was never registered cannot make "the runtime
    was verified" true.  The card must not report a green layer that rests
    on an unmet one.
    """
    client, _factory, _settings = api
    project_id = create_project(client, "层级累积")

    report = client.get(
        f"/api/v1/setup/variant-readiness?project_id={project_id}&variant_id={LOWEND_VARIANT}").json()

    states = [layer["state"] for layer in report["layers"]]
    assert states[0] == "PENDING"
    assert all(state == "PENDING" for state in states), states
    assert report["reached_layer"] == "WEIGHTS_PENDING"
    assert report["production_ready"] is False


def test_the_layers_open_up_in_order_as_the_evidence_arrives(api):
    client, factory, _settings = api
    project_id = create_project(client, "层级推进")
    _mark_weights_verified(factory)
    _register_candidate(client, project_id)

    report = client.get(
        f"/api/v1/setup/variant-readiness?project_id={project_id}&variant_id={LOWEND_VARIANT}").json()

    states = {layer["id"]: layer["state"] for layer in report["layers"]}
    assert states["WEIGHTS_PENDING"] == "DONE"
    assert states["FILES_VERIFIED"] == "DONE"
    assert states["WORKFLOW_REGISTERED"] == "DONE"
    assert states["RUNTIME_VALIDATED"] == "PENDING"
    assert states["REAL_RENDER_VERIFIED"] == "PENDING"
    assert report["next_step"]["code"] == "PROFILE_NOT_VALIDATED"


def test_the_readiness_report_states_the_host_facts_it_read(api):
    client, _factory, _settings = api
    project_id = create_project(client, "本机事实")
    report = client.get(
        f"/api/v1/setup/variant-readiness?project_id={project_id}&variant_id={LOWEND_VARIANT}").json()
    host = report["host"]
    for key in ("gpu", "vram_total_mb", "ram_total_mb", "commit_total_mb",
                "cuda_status", "torch"):
        assert key in host, key


# ---------------------------------------------------------------------------
# The local-import path is variant aware
# ---------------------------------------------------------------------------


def test_the_existing_install_path_compares_against_the_candidate_catalog(api):
    """The approved reuse path verifies the candidate's own pinned bytes."""
    from shine.comfyops.existing_installation import inspect

    client, _factory, settings = api
    project_id = create_project(client, "本地导入")
    _register_candidate(client, project_id)

    # A synthetic ComfyUI root: the import path only reads its file list.
    from pathlib import Path

    root = Path(settings.data_dir) / "comfy-root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_bytes(b"# synthetic entrypoint")
    (root / "comfy").mkdir(exist_ok=True)

    baseline = inspect(str(root), deployment.load_default_catalog())
    candidate = inspect(str(root), deployment.load_deployment_catalog(LOWEND_CATALOG))
    assert baseline["catalog_id"] != candidate["catalog_id"]
    assert candidate["catalog_id"] == "shine-h3-ref2va-lowend-candidate-windows-x64"
    # The candidate's own DiT is the file the candidate profile loads.
    w6a8 = [row for row in candidate["missing"]
            if row["relative_path"].endswith("minimax_h3_ref2va_pruned_w6a8.safetensors")]
    assert w6a8, "the candidate's DiT must be part of what the import path looks for"
    assert w6a8[0]["size"] == 15983746636


def test_the_resolution_helper_refuses_a_mismatched_catalog():
    with pytest.raises(Exception) as excinfo:
        resolve_registration_target(variant_id=LOWEND_VARIANT, catalog_id="h3-ref2va-windows-x64")
    assert "VARIANT_CATALOG_MISMATCH" in str(excinfo.value)


def test_the_resolution_helper_without_a_variant_keeps_the_default_catalog():
    binding = resolve_registration_target()
    assert binding.variant_id == BASELINE_VARIANT
    assert binding.profile_names == deployment.load_default_catalog()["workflow_profiles"]