"""Independent closeout regressions; synthetic media and isolated human operator only."""
# ruff: noqa: F811

import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path

import pytest

from shine.comfyops.assets import fake_image
from shine.comfyops.installer import LocalInstaller
from shine.comfyops.scheduler import ResourceScheduler
from shine.voiceops.providers import fake_audio
from tests.conftest import create_project, create_shot, upload_asset
from tests.test_v13_preparation import (  # noqa: F401
    HEADERS,
    approve,
    candidate,
    post,
    prep,
    rights,
    workflow,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def clone_request(client, project):
    asset_right = rights(client, project)
    audio, _ = fake_audio("声音参考", "Lin")
    ref = client.post(
        f"/api/v1/projects/{project}/candidate-assets/import",
        data={"family": "voice", "entity_id": "Lin", "rights_id": asset_right["id"]},
        files={"file": ("reference.wav", audio, "audio/wav")},
    ).json()
    consent = rights(client, project, "voice_clone", reference_candidate_id=ref["id"])
    return {
        "character": "Lin",
        "description": "test",
        "text": "声音参考",
        "rights_id": consent["id"],
        "asset_rights_id": asset_right["id"],
        "source": "AUTHORIZED_CLONE",
        "reference_id": ref["id"],
    }


def test_a01_new_bytes_legacy_approval_requires_operator(prep):
    client, _, _ = prep
    project = create_project(client)
    asset = upload_asset(client, project, fake_image("independent-new-bytes"))
    for action in ("approve", "lock"):
        response = client.post(f"/api/v1/assets/{asset['id']}/{action}")
        assert response.status_code == 403, response.text


def test_a02_design_clone_requires_execution_identity(prep):
    client, _, _ = prep
    project = create_project(client)
    body = clone_request(client, project)
    response = client.post(f"/api/v1/projects/{project}/voice-profiles/design", json=body)
    assert response.status_code == 403, response.text


def test_a04_clone_respects_h3_exclusive_lease(prep):
    client, factory, _ = prep
    project = create_project(client)
    body = clone_request(client, project)
    body = authorize_clone(client, body)
    with factory.begin() as db:
        lease = ResourceScheduler().enqueue(db, "independent-h3", 16384, True, 100)
        identity = lease.id
    with factory() as db:
        assert ResourceScheduler().admit(db, identity)
    response = client.post(f"/api/v1/projects/{project}/voice-profiles/clone", json=body, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "GPU_BUSY_RETRY_OR_QUEUE"


def test_a03_extra_revoked_reference_cannot_use_frozen_manifest(prep):
    client, _, _ = prep
    project = create_project(client)
    from tests.test_film_crew import ENGLISH_STYLE

    client.put(f"/api/v1/projects/{project}/style/draft", json=ENGLISH_STYLE)
    style = post(client, f"/projects/{project}/style/freeze", expected=201)
    doc = client.post(f"/api/v1/projects/{project}/documents", files={"file": ("s.txt", b"A rainy street.")}).json()
    plan = post(
        client, f"/projects/{project}/preproduction/analyze", {"document_id": doc["id"], "h3_category": "H3_T2VA"}
    )
    for category in ("H3_T2VA", "VLM_REVIEW", "ASR"):
        workflow(client, category)
    post(client, f"/projects/{project}/rulepack/freeze")
    approve(client, plan, "manifest_freeze", f"/projects/{project}/preproduction/freeze")
    right = rights(client, project)
    row = candidate(client, project, right, workflow(client), entity="extra")
    row = approve(client, row, "promote", f"/candidate-assets/{row['id']}/promote")
    post(client, f"/preparation/rights/{right['id']}/revoke", operator=True)
    shot = create_shot(
        client,
        project,
        style["id"],
        beat="Rain falls on an empty street.",
        bindings=[{"asset_id": row["formal_asset_id"], "use_as": "picture", "ordinal": 0}],
    )
    response = client.post(f"/api/v1/projects/{project}/jobs", json={"shot_id": shot["id"]})
    assert response.status_code == 409, response.text


def test_a05_recover_dead_installer_allows_reinstall(tmp_path):
    # A real child process dies inside a Fake downloader; no network or third-party execution.
    script = """
import os, sys
from pathlib import Path
from shine.comfyops.installer import LocalInstaller, PackageSpec
class FakeDownloader:
    def download(self, *args, **kwargs):
        os._exit(23)
spec = PackageSpec(name="crashed", kind="model", revision="v1", license_id="TEST", usage="research",
    files=[dict(url="https://example.com/f", relative_path="f", size=1, sha256="a"*64)])
LocalInstaller(Path(sys.argv[1]), FakeDownloader()).install(spec)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=False,
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
    )
    assert result.returncode == 23
    assert (tmp_path / "crashed.lock").exists()
    LocalInstaller(tmp_path, None).recover("crashed")
    assert not (tmp_path / "crashed.lock").exists()


def authorize_clone(client, body):
    from shine.api_preparation import VoiceDesign
    from shine.voiceops.authorization import execution_context

    grant = post(
        client,
        "/preparation/approval-grants",
        {
            "action": "voice_execute",
            "target_id": body["rights_id"],
            "context": execution_context(VoiceDesign.model_validate(body)),
        },
        operator=True,
    )
    return {
        **body,
        "execution_decision": {
            "grant_id": grant["grant_id"],
            "snapshot": grant["snapshot"],
            "evidence": "Explicit isolated test execution authorization",
        },
    }


@pytest.mark.parametrize("route", ["design", "clone"])
def test_clone_authorization_is_one_use_and_request_bound(prep, route):
    client, _, _ = prep
    project = create_project(client)
    body = authorize_clone(client, clone_request(client, project))
    url = f"/api/v1/projects/{project}/voice-profiles/{route}"
    assert client.post(url, json={**body, "character": "Other"}, headers=HEADERS).status_code == 409
    result = client.post(url, json=body, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PREVIEW"
    assert client.post(url, json=body, headers=HEADERS).status_code == 409


def test_asset_grant_replay_snapshot_and_target_isolation(prep):
    client, _, _ = prep
    project = create_project(client)
    asset = upload_asset(client, project, fake_image("fresh"))
    other = upload_asset(client, create_project(client), fake_image("other"))
    grant = post(
        client, "/preparation/approval-grants", {"action": "asset_approve", "target_id": asset["id"]}, operator=True
    )
    body = {k: grant[k] for k in ("grant_id", "snapshot")} | {"evidence": "test human inspected"}
    for headers in ({}, {"Authorization": "Bearer fake", "X-Operator-ID": "v13-test-operator"}):
        assert client.post(f"/api/v1/assets/{asset['id']}/approve", json=body, headers=headers).status_code == 403
    assert client.post(f"/api/v1/assets/{other['id']}/approve", json=body, headers=HEADERS).status_code == 409
    response = client.post(f"/api/v1/assets/{asset['id']}/approve", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert client.post(f"/api/v1/assets/{asset['id']}/approve", json=body, headers=HEADERS).status_code == 409
    # Lock is a separate action and binds the approval state.
    assert client.post(f"/api/v1/assets/{asset['id']}/lock", json=body, headers=HEADERS).status_code == 409


@pytest.mark.parametrize(
    "phase",
    [
        "DOWNLOADING",
        "STAGED",
        "BACKUP_INTENT",
        "BACKED_UP",
        "SWAP_INTENT",
        "INSTALLED",
        "VERIFIED",
        "CLEANED",
    ],
)
def test_installer_every_crash_window_reconciles_then_reinstalls(tmp_path, phase):
    script = """
import os, sys, hashlib
from pathlib import Path
from shine.comfyops.installer import ControlledDownloader, LocalInstaller, PackageSpec
root = Path(sys.argv[1])
source = root / "synthetic-source"
source.write_bytes(b"new")
class FakeDownloader:
    valid = staticmethod(ControlledDownloader.valid)
    def download(self, *args, **kwargs):
        return source
spec = PackageSpec(name="model", kind="model", revision="v1", license_id="TEST", usage="research",
    files=[dict(url="https://example.com/f", relative_path="f", size=3,
                sha256=hashlib.sha256(b"new").hexdigest())])
def checkpoint(phase):
    if phase == sys.argv[2]:
        os._exit(23)
LocalInstaller(root, FakeDownloader(), checkpoint=checkpoint).install(spec)
"""
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "f").write_bytes(b"old")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), phase], check=False, cwd=BACKEND_ROOT,
    )
    assert result.returncode == 23
    installer = LocalInstaller(tmp_path, None)
    outcome = installer.recover("model")
    committed = phase in {"VERIFIED", "CLEANED"}
    assert outcome["status"] == ("AVAILABLE" if committed else "ROLLED_BACK")
    assert (tmp_path / "model" / "f").read_bytes() == (b"new" if committed else b"old")
    assert not (tmp_path / "model.lock").exists()
    assert not (tmp_path / "model.journal").exists()
    # Retry uses the actual installer in a fresh process and completes successfully.
    assert subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), "NO_FAILURE"], check=False, cwd=BACKEND_ROOT,
    ).returncode == 0


def test_installer_active_owner_and_unknown_owner_are_protected(tmp_path):
    from shine.comfyops.installer import exclusive_file_lock

    marker = tmp_path / "active.lock"
    with exclusive_file_lock(marker):
        with pytest.raises(ValueError, match="INSTALL_OWNER_ACTIVE"):
            LocalInstaller(tmp_path, None).recover("active")
        assert marker.exists()
    marker.write_text("unknown legacy owner")
    with pytest.raises(ValueError, match="LOCK_OWNER_UNVERIFIABLE"):
        LocalInstaller(tmp_path, None).recover("active")
    assert marker.read_text() == "unknown legacy owner"


def ready_production(client, *, bind_prop=False):
    from tests.conftest import register_workflow_profile
    from tests.test_film_crew import ENGLISH_STYLE

    project = create_project(client)
    client.put(f"/api/v1/projects/{project}/style/draft", json=ENGLISH_STYLE)
    style = post(client, f"/projects/{project}/style/freeze", expected=201)
    right = rights(client, project)
    row = candidate(client, project, right, workflow(client), family="prop", entity="Ledger")
    row = approve(client, row, "promote", f"/candidate-assets/{row['id']}/promote")
    doc = client.post(f"/api/v1/projects/{project}/documents", files={"file": ("s.txt", b"A street.")}).json()
    plan = post(
        client,
        f"/projects/{project}/preproduction/analyze",
        {
            "document_id": doc["id"],
            "h3_category": "H3_I2VA" if bind_prop else "H3_T2VA",
            "requirements": [{"family": "prop", "entity_id": "Ledger", "critical": True}],
        },
    )
    profile = register_workflow_profile(client, mode="I2VA" if bind_prop else "T2VA")
    from tests.conftest import COMFY_GRAPH, COMFY_PARAM_MAP

    workflow(
        client,
        "H3_I2VA" if bind_prop else "H3_T2VA",
        graph=COMFY_GRAPH,
        parameter_map=COMFY_PARAM_MAP,
        legacy_profile_id=profile["id"],
    )
    for category in ("VLM_REVIEW", "ASR"):
        workflow(client, category)
    post(client, f"/projects/{project}/rulepack/freeze")
    approve(client, plan, "manifest_freeze", f"/projects/{project}/preproduction/freeze")
    shot = create_shot(
        client,
        project,
        style["id"],
        beat="Rain falls on an empty street.",
        bindings=[{"asset_id": row["formal_asset_id"], "use_as": "picture", "ordinal": 0}] if bind_prop else [],
    )
    return project, shot, right, row, profile


def test_worker_final_submission_rechecks_revocation_after_prepare(prep):
    from dataclasses import replace

    from shine.comfy.client import ComfyUIClient
    from shine.comfy.provider import ComfyUIRenderService
    from shine.worker import run_one
    from tests.fake_http import FakeComfyServer

    client, factory, settings = prep
    project, shot, right, _, profile = ready_production(client, bind_prop=True)
    settings = replace(settings, offline_simulation=True)
    client.app.state.settings = settings
    job = post(
        client,
        f"/projects/{project}/jobs",
        {
            "shot_id": shot["id"],
            "provider": "comfyui",
            "workflow_profile_id": profile["id"],
        },
        expected=201,
    )
    server = FakeComfyServer().start()
    try:
        comfy = ComfyUIClient(server.base_url, max_retries=0)

        class RevokeAfterPrepare(ComfyUIRenderService):
            def prepare(self, *args, **kwargs):
                prepared = super().prepare(*args, **kwargs)
                post(client, f"/preparation/rights/{right['id']}/revoke", operator=True)
                return prepared

        run_one(
            factory, settings=settings, comfy_service=RevokeAfterPrepare(comfy, settings.data_dir), comfy_client=comfy
        )
        current = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert current["status"] == "NEEDS_REVIEW", current
        assert current["failure_code"] in {"PREPRODUCTION_OUTDATED", "LICENSE_NOT_APPROVED"}
        assert not current["provider_job_id"]
        assert server.prompt_count == 0
    finally:
        server.stop()


def test_worker_rejects_changed_bindings_before_provider(prep):
    from shine.worker import run_one

    client, factory, settings = prep
    project, shot, _, row, _ = ready_production(client)
    job = post(client, f"/projects/{project}/jobs", {"shot_id": shot["id"]}, expected=201)
    response = client.put(
        f"/api/v1/shots/{shot['id']}/bindings",
        headers={"X-Shine-Local-Action": "1"},
        json={
            "bindings": [
                {"asset_id": row["formal_asset_id"], "use_as": "picture", "ordinal": 0},
            ]
        },
    )
    assert response.status_code == 200, response.text
    run_one(factory, settings=settings)
    current = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert current["status"] == "NEEDS_REVIEW", current
    assert current["attempts"] == []


@pytest.mark.parametrize("change", ["revoke", "expire", "wrong_reference", "cross_project", "mixed_source"])
def test_clone_consent_scope_and_state_changes_reject_execution(prep, change):
    from datetime import datetime, timedelta

    client, _, _ = prep
    project = create_project(client)
    body = clone_request(client, project)
    if change == "expire":
        consent = rights(
            client,
            project,
            "voice_clone",
            reference_candidate_id=body["reference_id"],
            expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
        body["rights_id"] = consent["id"]
    body = authorize_clone(client, body)
    if change == "revoke":
        post(client, f"/preparation/rights/{body['rights_id']}/revoke", operator=True)
    elif change == "wrong_reference":
        body["reference_id"] = clone_request(client, project)["reference_id"]
    elif change == "cross_project":
        project = create_project(client)
    elif change == "mixed_source":
        body["source"] = "ORIGINAL_DESIGN"
    response = client.post(f"/api/v1/projects/{project}/voice-profiles/design", json=body, headers=HEADERS)
    assert response.status_code == 409, response.text


def test_concurrent_asset_approval_and_stale_metadata_snapshot(prep):
    from concurrent.futures import ThreadPoolExecutor

    client, _, _ = prep
    asset = upload_asset(client, create_project(client), fake_image("race"))

    def grant():
        return post(
            client, "/preparation/approval-grants", {"action": "asset_approve", "target_id": asset["id"]}, operator=True
        )

    g = grant()
    old = {k: g[k] for k in ("grant_id", "snapshot")} | {"evidence": "isolated human race test"}
    assert client.patch(f"/api/v1/assets/{asset['id']}/metadata", json={"entity_id": "changed"}).status_code == 200
    assert client.post(f"/api/v1/assets/{asset['id']}/approve", json=old, headers=HEADERS).status_code == 409
    g = grant()
    body = {k: g[k] for k in ("grant_id", "snapshot")} | {"evidence": "isolated human race test"}

    def submit(_):
        return client.post(f"/api/v1/assets/{asset['id']}/approve", json=body, headers=HEADERS).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(submit, range(2))) == [200, 409]


def test_inventory_snapshots_append_without_duplicate_current_instances(prep):
    client, _, _ = prep
    first = post(client, "/comfyops/inventory/refresh")
    instances = client.get("/api/v1/comfyops/instances").json()
    second = post(client, "/comfyops/inventory/refresh")
    current = client.get("/api/v1/comfyops/instances").json()
    assert first["id"] != second["id"]
    assert {r["id"] for r in instances} == {r["id"] for r in current}
    assert len(current) == 2
    assert all(r["payload"]["snapshot_id"] == second["id"] for r in current)


def test_revocation_blocks_consumers_but_independent_shot_continues(prep):
    from shine.worker import run_one

    client, factory, settings = prep
    project, shot, right, _, _ = ready_production(client)
    queued = post(client, f"/projects/{project}/jobs", {"shot_id": shot["id"]}, expected=201)
    post(client, f"/preparation/rights/{right['id']}/revoke", operator=True)
    run_one(factory, settings=settings)
    assert client.get(f"/api/v1/jobs/{queued['id']}").json()["status"] == "PASSED"
    assert post(client, f"/projects/{project}/jobs", {"shot_id": shot["id"]}, expected=201)


@pytest.mark.parametrize("boundary", ["worker_recovery", "human_review"])
def test_revoked_frozen_reference_is_rechecked_at_recovery_and_review(prep, boundary):
    from dataclasses import replace

    from shine.worker import _recover_one_job

    client, factory, settings = prep
    project, shot, right, _, profile = ready_production(client, bind_prop=True)
    settings = replace(settings, offline_simulation=True)
    client.app.state.settings = settings
    job = post(
        client,
        f"/projects/{project}/jobs",
        {"shot_id": shot["id"], "provider": "comfyui", "workflow_profile_id": profile["id"]},
        expected=201,
    )
    post(client, f"/preparation/rights/{right['id']}/revoke", operator=True)
    if boundary == "worker_recovery":

        class NoExternalCall:
            def __getattr__(self, name):
                raise AssertionError("Revoked request must stop before recovery reads/submits external work")

        assert (
            _recover_one_job(factory, "isolated-recovery", job["id"], settings=settings, comfy_client=NoExternalCall())
            == 1
        )
        result = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert result["status"] == "NEEDS_REVIEW" and result["failure_code"] == "LICENSE_NOT_APPROVED"
        assert not result["attempts"] and not result["provider_job_id"]
    else:
        response = client.get(f"/api/v1/operator/jobs/{job['id']}", headers=HEADERS)
        assert response.status_code == 409 and response.json()["detail"] == "LICENSE_NOT_APPROVED"
        response = client.post(
            f"/api/v1/operator/jobs/{job['id']}/review",
            headers=HEADERS,
            json={
                "snapshot": "old",
                "request_id": "isolated-review-0001",
                "items": {},
                "watched": True,
                "listened": True,
            },
        )
        assert response.status_code == 409 and response.json()["detail"] == "LICENSE_NOT_APPROVED"
        assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "QUEUED"


def test_h3_live_worker_cannot_release_lease_and_expired_final_lease_blocks_prompt(prep):
    from dataclasses import replace
    from datetime import timedelta

    from sqlalchemy import select

    from shine.comfy.client import ComfyUIClient
    from shine.comfy.provider import ComfyUIRenderService
    from shine.comfyops.models import ResourceLease
    from shine.models import utcnow
    from shine.worker import run_one
    from tests.fake_http import FakeComfyServer

    client, factory, settings = prep
    project, shot, _, _, profile = ready_production(client, bind_prop=True)
    settings = replace(settings, offline_simulation=True)
    client.app.state.settings = settings
    job = post(
        client,
        f"/projects/{project}/jobs",
        {"shot_id": shot["id"], "provider": "comfyui", "workflow_profile_id": profile["id"]},
        expected=201,
    )
    server = FakeComfyServer().start()
    try:
        comfy = ComfyUIClient(server.base_url, max_retries=0)

        class ExpireAfterPrepare(ComfyUIRenderService):
            def prepare(self, *args, **kwargs):
                prepared = super().prepare(*args, **kwargs)
                with factory() as db:
                    lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + job["id"]))
                    lease_id, owner = lease.id, lease.owner
                response = client.post(
                    f"/api/v1/comfyops/resource-reservations/{lease_id}/release",
                    headers=HEADERS,
                    json={"owner": owner, "reconciled": True},
                )
                assert response.status_code == 409
                assert response.json()["detail"] == "H3_WORKER_MUST_RECONCILE_BEFORE_RESOURCE_RELEASE"
                # Explicit failure injection into the resource ledger, never approval fields.
                with factory.begin() as db:
                    db.get(ResourceLease, lease_id).expires_at = utcnow() - timedelta(seconds=1)
                return prepared

        run_one(
            factory, settings=settings, comfy_service=ExpireAfterPrepare(comfy, settings.data_dir), comfy_client=comfy
        )
        current = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert current["status"] == "NEEDS_REVIEW" and current["failure_code"] == "GPU_RESOURCE_LEASE_LOST"
        assert server.prompt_count == 0
    finally:
        server.stop()


def test_operator_grant_does_not_convert_renderer_unload_into_physical_release(prep, monkeypatch):
    from shine.comfyops.models import ApprovalGrant, ResourceLease
    from shine.models import Job, utcnow
    from tests.conftest import create_comfy_job, freeze_style

    client, factory, _settings = prep
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    with factory.begin() as db:
        row = db.get(Job, job["id"])
        row.status = "NEEDS_REVIEW"
        row.failure_code = "COMFY_HANDOFF_OWNERSHIP_UNVERIFIED"
        lease = ResourceLease(owner="h3:" + row.id, budget_mb=16384, exclusive=True, priority=100,
                              status="RECONCILE_REQUIRED", expires_at=utcnow())
        db.add(lease)
        db.flush()
        lease_id, owner = lease.id, lease.owner
    grant = post(client, "/preparation/approval-grants", {"action": "resource_reconcile", "target_id": lease_id},
                 operator=True)
    calls = []
    monkeypatch.setattr("shine.comfy.client.ComfyUIClient.free_models", lambda _self: calls.append("unload"))
    response = client.post(f"/api/v1/comfyops/resource-reservations/{lease_id}/release", headers=HEADERS, json={
        "owner": owner, "proof_decision": {"grant_id": grant["grant_id"], "snapshot": grant["snapshot"],
                                             "evidence": "Synthetic operator decision"},
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "COMFY_PHYSICAL_RELEASE_PROOF_REQUIRED"
    assert not calls
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert not db.get(ApprovalGrant, grant["grant_id"]).used
