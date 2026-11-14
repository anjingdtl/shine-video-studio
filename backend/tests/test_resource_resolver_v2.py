"""The resource preview derives pins from the selected graph, never installs."""
import pytest
from fastapi.testclient import TestClient

# Load before identity is patched, so snapshot imports never retain a fake.
import shine.runtime_orchestrator.status  # noqa: F401
from tests.conftest import create_project, make_studio


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def test_candidate_preview_is_exact_and_does_not_claim_trial(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="资源预检")
    response = client.post("/api/v1/setup/resources/resolve", json={
        "project_id": project,
        "strategy": {"model_variant_id": "ref2va-lowend-candidate", "accelerator_id": "native20"},
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert {row["component_id"] for row in data["components"]} == {
        "minimax-h3-diffusion-w6a8", "minimax-h3-vae-int8",
        "minimax-h3-audio-vae-fp32", "minimax-h3-text-encoder-nvfp4",
    }
    assert data["missing_download_bytes"] == 35087209179
    assert data["trial_ready"] is False and data["production_ready"] is False
    assert data["node_abi"] == "LIVE_CHECK_PENDING"
    assert any(row["code"] for row in data["production_blockers"])


def test_candidate_pdd_preview_refuses_instead_of_substitution(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="资源预检")
    response = client.post("/api/v1/setup/resources/resolve", json={
        "project_id": project, "strategy": {"model_variant_id": "ref2va-lowend-candidate"},
    })
    assert response.status_code == 200
    assert response.json()["blockers"][0]["code"] == "VARIANT_RECIPE_UNVERIFIED"
    assert response.json()["download_eligible"] is False


def test_candidate_plan_is_approval_required_and_revalidates_catalog(studio, tmp_path, monkeypatch):
    from shine.comfyops.models import InstallationPlan
    from shine.comfyops.resource_resolver import validate_resource_plan

    client, factory, settings = studio
    monkeypatch.setattr("shine.comfyops.storage.require_storage", lambda settings: tmp_path)
    monkeypatch.setattr("shine.comfyops.storage.disk_info", lambda path: {"free_bytes": 10**12})
    monkeypatch.setattr("shine.comfyops.resource_resolver.managed_installation_pin",
                        lambda db, instance_id: {"id": instance_id, "sha256": "a" * 64})
    project = create_project(client, title="授权资源计划")
    strategy = {"model_variant_id": "ref2va-lowend-candidate", "accelerator_id": "native20"}
    preview = client.post("/api/v1/setup/resources/resolve", json={"project_id": project, "strategy": strategy}).json()
    request = {"project_id": project, "strategy": strategy, "snapshot_sha256": preview["snapshot_sha256"],
               "instance_id": "managed-one", "source_preference": "MIRROR_FIRST", "disk_free": 10**12}
    response = client.post("/api/v1/setup/resources/install-plan", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "AWAITING_APPROVAL"
    with factory() as db:
        plan = db.get(InstallationPlan, response.json()["plan_id"])
        assert plan.payload["resource_context"]["source_preference"] == "MIRROR_FIRST"
        validate_resource_plan(db, settings, plan.payload)
        context = dict(plan.payload["resource_context"])
        originally_cached = context["authorized_download_files"][0]
        context["authorized_download_files"] = context["authorized_download_files"][1:]
        changed = {**plan.payload, "resource_context": context,
                   "download_bytes": plan.payload["download_bytes"] - originally_cached["size"]}
        with pytest.raises(ValueError, match="RESOURCE_DOWNLOAD_CHANGED"):
            validate_resource_plan(db, settings, changed)
        bad = {**plan.payload, "packages": plan.payload["packages"][:-1]}
        with pytest.raises(ValueError, match="RESOURCE_PLAN_COMPONENTS_CHANGED"):
            validate_resource_plan(db, settings, bad)
        from copy import deepcopy
        from dataclasses import replace

        from shine.comfyops import resource_resolver

        real_binding = resource_resolver.load_binding
        def changed_binding(variant, **kwargs):
            binding = real_binding(variant, **kwargs)
            changed = deepcopy(binding.manifest)
            changed["summary"] = "changed since approval"
            return replace(binding, manifest=changed)
        monkeypatch.setattr(resource_resolver, "load_binding", changed_binding)
        with pytest.raises(ValueError, match="RESOURCE_PLAN_IDENTITY_CHANGED"):
            validate_resource_plan(db, settings, plan.payload)
        monkeypatch.setattr(resource_resolver, "load_binding", real_binding)
    request["snapshot_sha256"] = "0" * 64
    assert client.post("/api/v1/setup/resources/install-plan", json=request).status_code == 409


def test_candidate_catalog_cannot_be_installed_without_variant(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="候选门禁")
    response = client.post("/api/v1/setup/install-plan", json={
        "project_id": project, "catalog": "h3-ref2va-lowend-candidate-windows-x64",
        "components": ["minimax-h3-diffusion-w6a8"], "disk_free": 10**12,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "VARIANT_ID_REQUIRED"


def test_generic_plan_cannot_bypass_candidate_variant_binding():
    from shine.comfyops.resource_resolver import reject_unbound_candidate_checkpoint
    from shine.comfyops.variants import load_variant_manifest

    manifest = load_variant_manifest("ref2va-lowend-candidate")
    dit = next(row for row in manifest["files"] if row["role"] == "dit")
    payload = {"packages": [{"id": "renamed", "spec": {"files": [{"sha256": dit["sha256"]}]}}]}
    with pytest.raises(ValueError, match="VARIANT_ID_REQUIRED"):
        reject_unbound_candidate_checkpoint(payload)
    reject_unbound_candidate_checkpoint({**payload, "resource_context": {"schema": "shine.resource-plan.v2"}})


def test_catalog_pin_mismatch_is_not_download_eligible(studio, monkeypatch):
    from copy import deepcopy
    from dataclasses import replace

    from shine.comfyops import resource_resolver

    client, _factory, _settings = studio
    binding = resource_resolver.load_binding("ref2va-lowend-candidate")
    catalog = deepcopy(binding.catalog)
    component = next(row for row in catalog["components"] if row["component_id"] == "minimax-h3-diffusion-w6a8")
    component["files"][0]["sha256"] = "0" * 64
    monkeypatch.setattr(resource_resolver, "load_binding", lambda variant, **kwargs: replace(binding, catalog=catalog))
    project = create_project(client, title="错位清单")
    data = client.post("/api/v1/setup/resources/resolve", json={
        "project_id": project, "strategy": {"model_variant_id": binding.variant_id, "accelerator_id": "native20"},
    }).json()
    assert not data["download_eligible"]
    assert any(row["code"] == "VARIANT_CATALOG_MISMATCH" for row in data["blockers"])


def test_resource_install_holds_lifecycle_guard_and_refuses_busy_renderer(studio, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from shine.comfyops import installation_worker

    _client, factory, settings = studio
    held = []

    @contextmanager
    def guard(instance_id):
        held.append(instance_id)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr("shine.runtime_orchestrator.guard.renderer_lifecycle_guard", guard)
    monkeypatch.setattr("shine.comfyops.resource_resolver.validate_resource_plan", lambda *args: None)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.windows_port_listeners", lambda port: [])
    from shine.comfyops.models import ComfyInstance
    with factory.begin() as db:
        db.add(ComfyInstance(id="shared", payload={"port": 8188}, sha256="a" * 64, status="STOPPED"))
    monkeypatch.setattr("shine.runtime_orchestrator.identity.installation_activity", lambda *args: {
        "renderer_state": "H3", "gpu_leases": [], "in_flight_h3_jobs": [], "in_flight_image_work": [],
    })
    finished = []
    monkeypatch.setattr(installation_worker, "_finish", lambda *args, **kwargs: finished.append(kwargs))
    monkeypatch.setattr(installation_worker, "_run_local_attempt", lambda *args, **kwargs: pytest.fail("downloaded"))
    plan = SimpleNamespace(payload={"resource_context": {"instance_id": "shared"}})
    result = installation_worker._run_resource_attempt(factory, "worker", "attempt", plan, settings=settings)
    assert result == "FAILED_RETRYABLE"
    assert finished[0]["error_code"] == "RESOURCE_INSTALLATION_BUSY"
    assert held == []


def test_resource_instance_requires_real_integrity_pinned_physical_installation(studio):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.instances import instance_identity
    from shine.comfyops.models import ComfyInstance
    from shine.comfyops.resource_resolver import managed_installation_pin

    _client, factory, _settings = studio
    identity = instance_identity("local", 8188)
    payload = {"managed": True, "is_mock": False, "port": 8188}
    with factory.begin() as db:
        db.add(ComfyInstance(id=identity, payload=payload, sha256=graph_hash(payload), status="STOPPED"))
    with factory() as db:
        assert managed_installation_pin(db, identity) == {"id": identity, "sha256": graph_hash(payload)}
    with factory.begin() as db:
        db.get(ComfyInstance, identity).payload = {**payload, "is_mock": True}
    with factory() as db, pytest.raises(ValueError, match="RESOURCE_INSTANCE_UNVERIFIED"):
        managed_installation_pin(db, identity)


def test_resource_binding_rechecks_manifest_instead_of_using_process_cache(monkeypatch):
    from copy import deepcopy

    from shine.comfyops import variant_binding

    original = variant_binding.load_variant_manifest
    revised = {"value": False}

    def load(variant_id):
        manifest = deepcopy(original(variant_id))
        if revised["value"]:
            manifest["summary"] = "changed after approval"
        return manifest

    variant_binding._manifest_and_catalog.cache_clear()
    monkeypatch.setattr(variant_binding, "load_variant_manifest", load)
    try:
        first = variant_binding.load_binding("ref2va-lowend-candidate")
        revised["value"] = True
        assert variant_binding.load_binding("ref2va-lowend-candidate").manifest == first.manifest
        fresh = variant_binding.load_binding("ref2va-lowend-candidate", fresh=True)
        assert fresh.manifest["summary"] == "changed after approval"
    finally:
        variant_binding._manifest_and_catalog.cache_clear()


@pytest.mark.parametrize("contents,verified", [(b"exact bytes", True), (b"wrong bytes", False)])
def test_managed_inventory_reuses_renamed_file_only_after_sha_check(studio, tmp_path, monkeypatch,
                                                                     contents, verified):
    import hashlib
    from types import SimpleNamespace

    from shine.comfy.workflow import graph_hash
    from shine.comfyops import resource_inventory
    from shine.comfyops.models import ComfyInstance
    from shine.strategy import GenerationStrategy

    _client, factory, settings = studio
    root = tmp_path / "comfy"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("", encoding="utf-8")
    (root / "models" / "renamed").mkdir(parents=True)
    source = root / "models" / "renamed" / "existing.bin"
    source.write_bytes(contents)
    digest = hashlib.sha256(b"exact bytes").hexdigest()
    component = {"component_id": "needed-model", "kind": "model", "status": "READY", "files": [{
        "relative_path": "model.bin", "sha256": digest, "size": len(b"exact bytes"),
    }]}
    catalog = {"catalog_id": "test", "catalog_version": "1", "components": [component]}
    preview = {"download_eligible": True, "manifest_sha256": graph_hash({}), "catalog_sha256": graph_hash(catalog),
               "components": [{"component_id": "needed-model", "state": "MISSING"}]}
    monkeypatch.setattr(resource_inventory, "managed_installation_pin", lambda db, iid: {"id": iid, "sha256": "x"})
    monkeypatch.setattr(resource_inventory, "resolve_resources", lambda *args: preview)
    monkeypatch.setattr(resource_inventory, "load_binding",
                        lambda *args, **kwargs: SimpleNamespace(catalog=catalog, manifest={}))
    monkeypatch.setattr("shine.comfyops.storage.require_storage", lambda settings: tmp_path)
    monkeypatch.setattr("shine.comfyops.storage.disk_info", lambda path: {"free_bytes": 10**9})
    with factory.begin() as db:
        db.add(ComfyInstance(id="physical", payload={"root": str(root)}, sha256="x", status="STOPPED"))
    strategy = GenerationStrategy(model_variant_id="ref2va-lowend-candidate", accelerator_id="native20")
    with factory() as db:
        scan = resource_inventory.inspect_managed_resources(db, settings, strategy, "physical")
        assert scan["candidates"][0]["state"] == "PRESENT_UNVERIFIED"
        from fastapi import HTTPException

        old_catalog_hash = preview["catalog_sha256"]
        preview["catalog_sha256"] = "changed"
        with pytest.raises(HTTPException, match="RESOURCE_INVENTORY_CHANGED"):
            resource_inventory.import_managed_resources(db, settings, strategy, "physical", scan["snapshot_sha256"])
        preview["catalog_sha256"] = old_catalog_hash
        result = resource_inventory.import_managed_resources(
            db, settings, strategy, "physical", scan["snapshot_sha256"],
        )
    target = tmp_path / "isolated-packages" / ".cache" / digest
    if verified:
        assert result["reused_bytes"] == len(b"exact bytes")
        assert target.read_bytes() == b"exact bytes"
    else:
        assert result["reused_bytes"] == 0
        assert result["rejected"] == ["needed-model"]
        assert not target.exists()
    assert source.read_bytes() == contents


def test_managed_inventory_rejects_changed_source_snapshot(studio, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from shine.comfy.workflow import graph_hash
    from shine.comfyops import resource_inventory
    from shine.comfyops.models import ComfyInstance
    from shine.strategy import GenerationStrategy

    _client, factory, settings = studio
    root = tmp_path / "comfy"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("", encoding="utf-8")
    (root / "models").mkdir()
    source = root / "models" / "file.bin"
    source.write_bytes(b"same")
    component = {"component_id": "needed", "kind": "model", "status": "READY", "files": [{
        "relative_path": "file.bin", "sha256": "0" * 64, "size": 4,
    }]}
    catalog = {"catalog_id": "test", "catalog_version": "1", "components": [component]}
    preview = {"download_eligible": True, "manifest_sha256": graph_hash({}), "catalog_sha256": graph_hash(catalog),
               "components": [{"component_id": "needed", "state": "MISSING"}]}
    monkeypatch.setattr(resource_inventory, "managed_installation_pin", lambda db, iid: {"id": iid, "sha256": "x"})
    monkeypatch.setattr(resource_inventory, "resolve_resources", lambda *args: preview)
    monkeypatch.setattr(resource_inventory, "load_binding",
                        lambda *args, **kwargs: SimpleNamespace(catalog=catalog, manifest={}))
    with factory.begin() as db:
        db.add(ComfyInstance(id="physical", payload={"root": str(root)}, sha256="x", status="STOPPED"))
    strategy = GenerationStrategy(model_variant_id="ref2va-lowend-candidate", accelerator_id="native20")
    with factory() as db:
        scan = resource_inventory.inspect_managed_resources(db, settings, strategy, "physical")
        source.write_bytes(b"changed")
        with pytest.raises(HTTPException, match="RESOURCE_INVENTORY_CHANGED"):
            resource_inventory.import_managed_resources(db, settings, strategy, "physical", scan["snapshot_sha256"])


def test_local_inventory_api_rejects_invalid_strategy(studio):
    client, _factory, _settings = studio
    project = create_project(client, title="本机资源")
    response = client.post("/api/v1/setup/resources/inspect-local", json={
        "project_id": project, "instance_id": "physical", "strategy": {"generation_mode": "T2VA"},
    })
    assert response.status_code == 422


def test_phase_a_plan_needs_no_managed_instance(studio, tmp_path, monkeypatch):
    """A fresh machine can freeze a download plan before any ComfyUI exists.

    This is the circular-dependency fix: preparing bytes (phase A) must not
    require a managed renderer that itself needs the bytes to be installed.
    """
    from shine.comfyops.models import InstallationPlan
    from shine.comfyops.resource_resolver import validate_resource_plan

    client, factory, settings = studio
    monkeypatch.setattr("shine.comfyops.storage.require_storage", lambda settings: tmp_path)
    monkeypatch.setattr("shine.comfyops.storage.disk_info", lambda path: {"free_bytes": 10**12})
    project = create_project(client, title="新装机离线整备")
    strategy = {"model_variant_id": "ref2va-lowend-candidate", "accelerator_id": "native20"}
    preview = client.post("/api/v1/setup/resources/resolve",
                          json={"project_id": project, "strategy": strategy}).json()
    # No instance_id at all: phase A.
    response = client.post("/api/v1/setup/resources/install-plan", json={
        "project_id": project, "strategy": strategy, "snapshot_sha256": preview["snapshot_sha256"],
        "source_preference": "AUTO", "disk_free": 10**12,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "AWAITING_APPROVAL"
    with factory() as db:
        plan = db.get(InstallationPlan, response.json()["plan_id"])
        context = plan.payload["resource_context"]
        assert context["instance_id"] is None and context["instance_pin"] is None
        # The offline plan still revalidates cleanly.
        validate_resource_plan(db, settings, plan.payload)
        # A phase-A plan may not smuggle in an instance pin.
        forged = {**plan.payload, "resource_context": {**context, "instance_pin": {"id": "x", "sha256": "a" * 64}}}
        with pytest.raises(ValueError, match="RESOURCE_PLAN_IDENTITY_CHANGED"):
            validate_resource_plan(db, settings, forged)


def test_variant_availability_decouples_preparation_from_production(studio, monkeypatch):
    """W6A8 is selectable for preparation even when nothing is installed.

    The candidate manifest declares ``required_component_ids=[]``; that must
    not make ``installed`` permanently false once the graph's weights really
    are present, and it must never unlock production for a candidate.
    """
    client, _factory, _settings = studio
    project = create_project(client, title="解耦可用性")
    rows = client.get(f"/api/v1/setup/variant-availability?project_id={project}").json()["variants"]
    candidate = next(row for row in rows if row["variant_id"] == "ref2va-lowend-candidate")
    # Nothing installed yet: still selectable for preparation, never shootable.
    assert candidate["selectable_for_preparation"] is True
    assert candidate["available"] is False
    assert candidate["production_ready"] is False
    assert candidate["installed"] is False
    assert candidate["acceptance_status"] == "CANDIDATE"

    # Now the graph's four weights are hash-verified on disk.  The empty
    # required_component_ids must not hide that fact.
    import shine.comfyops.variant_readiness as vr

    real_weights = vr.weight_evidence

    def prepared_weights(db, settings, binding):
        rows = real_weights(db, settings, binding)
        return [{**row, "prepared": True, "verified": True, "consumable": True} for row in rows]

    monkeypatch.setattr(vr, "weight_evidence", prepared_weights)
    rows = client.get(f"/api/v1/setup/variant-availability?project_id={project}").json()["variants"]
    candidate = next(row for row in rows if row["variant_id"] == "ref2va-lowend-candidate")
    assert candidate["installed"] is True, "graph-derived weights count even with required_component_ids=[]"
    assert candidate["verified"] is True and candidate["bound"] is True
    # A candidate stays a candidate: bytes alone never grant production.
    assert candidate["available"] is False
    assert candidate["production_ready"] is False


def test_live_node_check_never_queries_unowned_service(studio, monkeypatch):
    from shine.comfyops.resource_diagnostics import check_owned_node_inputs
    from shine.strategy import GenerationStrategy

    _client, factory, _settings = studio
    monkeypatch.setattr("shine.runtime_orchestrator.identity.installation_activity", lambda db, iid: {
        "h3_records": [{"state": "ACTIVE", "process": {"pid": 1}, "port": 8188}], "image_record": None,
    })
    monkeypatch.setattr("shine.runtime_orchestrator.identity.verified_live_record",
                        lambda record: {"alive": True, "owned": False})
    monkeypatch.setattr("httpx.stream", lambda *args, **kwargs: pytest.fail("unowned service was queried"))
    with factory() as db:
        result = check_owned_node_inputs(db, "physical", GenerationStrategy())
    assert result["state"] == "OWNERSHIP_UNVERIFIED"
    assert result["production_ready"] is False


def test_live_node_check_reports_exact_missing_classes(studio, monkeypatch):
    from contextlib import contextmanager

    import httpx

    from shine.comfyops.resource_diagnostics import check_owned_node_inputs
    from shine.strategy import GenerationStrategy

    _client, factory, _settings = studio
    monkeypatch.setattr("shine.runtime_orchestrator.identity.installation_activity", lambda db, iid: {
        "h3_records": [{"state": "ACTIVE", "process": {"pid": 1}, "port": 8188}], "image_record": None,
    })
    monkeypatch.setattr("shine.runtime_orchestrator.identity.verified_live_record",
                        lambda record: {"alive": True, "owned": True})
    monkeypatch.setattr("shine.runtime_orchestrator.status._owned_listener_pid", lambda port, process: 1)

    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "stream", stream)
    with factory() as db:
        result = check_owned_node_inputs(db, "physical", GenerationStrategy())
    assert result["state"] == "NODE_INPUTS_MISSING"
    assert "MiniMaxH3MemoryEfficientSageAttentionPatch" in result["missing_nodes"]
    assert result["gpu_compatibility"] == "UNVERIFIED"
    assert result["production_ready"] is False


def test_live_node_check_discards_evidence_if_listener_changes(studio, monkeypatch):
    from contextlib import contextmanager

    import httpx

    from shine.comfyops.resource_diagnostics import check_owned_node_inputs
    from shine.strategy import GenerationStrategy

    _client, factory, _settings = studio
    monkeypatch.setattr("shine.runtime_orchestrator.identity.installation_activity", lambda db, iid: {
        "h3_records": [{"state": "ACTIVE", "process": {"pid": 1}, "port": 8188}], "image_record": None,
    })
    monkeypatch.setattr("shine.runtime_orchestrator.identity.verified_live_record",
                        lambda record: {"alive": True, "owned": True})
    listeners = iter([1, 2])
    monkeypatch.setattr("shine.runtime_orchestrator.status._owned_listener_pid", lambda *args: next(listeners))

    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "stream", stream)
    with factory() as db:
        result = check_owned_node_inputs(db, "physical", GenerationStrategy())
    assert result == {"state": "OWNERSHIP_UNVERIFIED", "production_ready": False}


def test_live_node_check_refuses_empty_input_schema(studio, monkeypatch):
    from contextlib import contextmanager

    import httpx

    from shine.comfyops.resource_diagnostics import check_owned_node_inputs
    from shine.strategy import GenerationStrategy

    _client, factory, _settings = studio
    monkeypatch.setattr("shine.runtime_orchestrator.identity.installation_activity", lambda db, iid: {
        "h3_records": [{"state": "ACTIVE", "process": {"pid": 1}, "port": 8188}], "image_record": None,
    })
    monkeypatch.setattr("shine.runtime_orchestrator.identity.verified_live_record",
                        lambda record: {"alive": True, "owned": True})
    monkeypatch.setattr("shine.runtime_orchestrator.status._owned_listener_pid", lambda *args: 1)

    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200, json={"UNETLoader": {"input": {}}})

    monkeypatch.setattr(httpx, "stream", stream)
    with factory() as db:
        result = check_owned_node_inputs(db, "physical", GenerationStrategy())
    assert result["state"] == "NODE_SCHEMA_UNVERIFIED"
    assert "UNETLoader" in result["missing_schema"]
    assert result["production_ready"] is False
