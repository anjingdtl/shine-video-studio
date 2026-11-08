"""Director-journey closeout: the ten counterexamples become contracts.

Every test here was first reproduced on `main@12dd99b` (see
``docs/ADVERSARIAL_REVIEW_DIRECTOR_JOURNEY_12DD99B_20261109.md``) and now
asserts the fixed behaviour.  They are software contracts only: no H3 render,
no model weight and no real GPU is claimed.
"""

import copy

import pytest

from shine.director.package import digest_of
from shine.models import AgentArtifact, AgentTaskRecord, Job, JobAttempt, MediaArtifact, utcnow
from tests.conftest import (
    COMFY_GRAPH,
    COMFY_PARAM_MAP,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)
from tests.test_director_entries_and_post import _delivered_run, _has_ffmpeg
from tests.test_director_long_form import LOCAL, _confirm_package, _plan_and_package, _seed_passed_job, _studio


def stages(client, project, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    suffix = f"?{query}" if query else ""
    body = client.get(f"/api/v1/projects/{project}/journey{suffix}")
    assert body.status_code == 200, body.text
    return {row["id"]: row for row in body.json()["stages"]}, body.json()


def _document(client, project, name: str, text: bytes) -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/documents",
        files={"file": (name, text, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approved_outline(factory, project, document, seconds=(6, 6)) -> str:
    """A confirmed outline that really names the document it came from."""
    sections = [
        {
            "production_section_id": f"sec-{index + 1}",
            "order": index + 1,
            "title": f"第{index + 1}场",
            "estimated_duration_s": value,
            "start_offset": index * 100,
            "end_offset": index * 100 + 99,
        }
        for index, value in enumerate(seconds)
    ]
    payload = {
        "production_sections": sections,
        "document_id": document["id"],
        "document_sha256": document["sha256"],
    }
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project, role_id="story_outline_run",
            request_identity=f"outline-{project}-{document['id']}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64, rulepack_id="test",
            rulepack_hash="0" * 64, model_profile_id="test", model_id="test",
            status="SUCCEEDED", trace={},
        )
        db.add(source)
        db.flush()
        artifact = AgentArtifact(
            project_id=project, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest_of(payload), producer_role="scriptwriter",
            source_task_id=source.id, approved_by="local-user", approved_at=utcnow(),
        )
        db.add(artifact)
        db.flush()
        return artifact.id


# ---------------------------------------------------------------------------
# U04 · an upload is a candidate; the package pins the chosen basis
# ---------------------------------------------------------------------------


def test_importing_a_second_script_does_not_replace_the_package_basis(api):
    client, factory, _ = api
    data = _studio(client, shots=1)
    project = data["project"]
    first = _document(client, project, "a.txt", b"Scene one. Original basis.")
    _approved_outline(factory, project, first)

    basis = client.get(f"/api/v1/projects/{project}/script-basis").json()
    assert basis["document_id"] == first["id"] and basis["explicit"] is True

    _, package_a = _plan_and_package(client, data, target_seconds=60)
    detail_a = client.get(f"/api/v1/director-packages/{package_a['id']}").json()
    assert detail_a["script"]["document_id"] == first["id"]
    assert detail_a["script"]["outline_artifact_id"]

    second = _document(client, project, "b.txt", b"Scene one. Different script.")
    after_upload = client.get(f"/api/v1/projects/{project}/script-basis").json()
    assert after_upload["document_id"] == first["id"], "an upload never switches the basis"
    assert {row["document_id"] for row in after_upload["candidates"]} == {first["id"], second["id"]}

    _, package_b = _plan_and_package(client, data, target_seconds=60)
    detail_b = client.get(f"/api/v1/director-packages/{package_b['id']}").json()
    assert detail_b["script"]["document_id"] == first["id"], (
        "the newest upload must not be attached to an outline built from another script")
    assert detail_b["script"]["outline_artifact_id"] == detail_a["script"]["outline_artifact_id"]


def test_an_explicit_basis_switch_invalidates_the_old_package_readiness(api):
    client, factory, _ = api
    data = _studio(client, shots=1)
    project = data["project"]
    first = _document(client, project, "a.txt", b"Scene one. Original basis.")
    _approved_outline(factory, project, first)
    _, package = _plan_and_package(client, data, target_seconds=60)
    _confirm_package(client, package["id"])

    second = _document(client, project, "b.txt", b"Scene one. Different script.")
    switched = client.post(f"/api/v1/projects/{project}/script-basis",
                           json={"document_id": second["id"]}, headers=LOCAL)
    assert switched.status_code == 200, switched.text
    assert switched.json()["document_id"] == second["id"] and switched.json()["changed"] is True

    readiness = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert readiness["startable"] is False
    assert any(row["code"] == "SCRIPT_BASIS_CHANGED" for row in readiness["blockers"])
    # The old package is still readable by its own id; it is not silently rewritten.
    assert client.get(f"/api/v1/director-packages/{package['id']}").json()["script"]["document_id"] == first["id"]


# ---------------------------------------------------------------------------
# U07 · readiness and job admission are the same statement
# ---------------------------------------------------------------------------


def test_ref2va_package_without_any_reference_is_not_startable(api):
    client, _, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], characters=["Lin"])
    profile = register_workflow_profile(client, mode="Ref2VA")
    _, package = _plan_and_package(client, {"project": project, "profile": profile}, target_seconds=60)

    readiness = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert readiness["startable"] is False
    mismatch = [row for row in readiness["blockers"] if row["code"] == "H3_MODE_REFERENCE_MISMATCH"]
    assert mismatch and mismatch[0]["shot_id"] == shot["id"]
    assert mismatch[0]["mode"] == "Ref2VA"

    # The same shot through the single-shot entry is refused for the same reason.
    job = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": profile["id"], "generation_mode": "Ref2VA",
    })
    assert job.status_code in {409, 422}, job.text
    assert "H3_MODE_REFERENCE_MISMATCH" in job.text or "WORKFLOW_PROFILE" in job.text


def test_a_profile_without_a_reference_slot_is_named_before_a_run_starts(api):
    client, _, _ = api
    data = _studio(client, shots=1)
    profile = register_workflow_profile(
        client, mode="Ref2VA",
        parameter_map={"positive_prompt": "1.inputs.positive_prompt", "seed": "1.inputs.seed",
                       "duration": "1.inputs.duration"},
    )
    data["profile"] = profile
    _, package = _plan_and_package(client, data, target_seconds=60)
    readiness = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert readiness["startable"] is False
    mapping = [row for row in readiness["blockers"] if row["code"] == "PENDING_WORKFLOW_MAPPING"]
    assert mapping and mapping[0]["shot_id"]
    assert "reference_subject_1" in mapping[0]["details"]["missing_reference_slots"]


# ---------------------------------------------------------------------------
# U08 · an in-place mapping edit changes the execution fingerprint
# ---------------------------------------------------------------------------


def test_editing_the_parameter_map_in_place_invalidates_the_frozen_package(api):
    client, _, _ = api
    data = _studio(client, shots=1)
    graph = copy.deepcopy(COMFY_GRAPH)
    graph["1"]["class_type"] = "MiniMaxH3FixtureSampler"
    graph["1"]["inputs"].update({"width": 320, "height": 240})
    profile = register_workflow_profile(
        client, mode="Ref2VA", name="mutable-mapping-fixture", graph=graph,
        parameter_map=COMFY_PARAM_MAP, required_nodes=["MiniMaxH3FixtureSampler"])
    data["profile"] = profile
    _, package = _plan_and_package(client, data, target_seconds=60)
    _confirm_package(client, package["id"])
    before = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert before["startable"] is True

    changed = client.post(f"/api/v1/workflow-profiles/{profile['id']}/standard-parameters")
    assert changed.status_code == 200 and "width" in changed.json()["parameter_map"], changed.text

    after = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert after["startable"] is False
    drift = [row for row in after["blockers"] if row["code"] == "WORKFLOW_PROFILE_CHANGED"]
    assert drift and "parameter_map" in drift[0]["changed_fields"]
    assert after["config_fingerprint"] != before["config_fingerprint"]


def test_the_execution_fingerprint_covers_the_shot_reference_set(api):
    client, factory, _ = api
    data = _studio(client, shots=1)
    _, package = _plan_and_package(client, data, target_seconds=60)
    _confirm_package(client, package["id"])
    before = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()

    extra = upload_asset(client, data["project"], content=b"\x89PNG\r\n\x1a\n-extra", filename="extra.png")
    approve_lock(client, extra["id"])
    shot = data["shots"][0]
    current = client.get(f"/api/v1/shots/{shot['id']}/bindings").json()
    bindings = [
        {"asset_id": row["asset_id"], "use_as": row["use_as"], "role": row.get("role"), "ordinal": index}
        for index, row in enumerate(current)
    ] + [{"asset_id": extra["id"], "use_as": "picture", "role": "prop", "ordinal": len(current)}]
    replaced = client.put(f"/api/v1/shots/{shot['id']}/bindings", json={"bindings": bindings}, headers=LOCAL)
    assert replaced.status_code == 200, replaced.text

    after = client.get(f"/api/v1/director-packages/{package['id']}/readiness").json()
    assert after["config_fingerprint"] != before["config_fingerprint"]
    assert any(row["code"] == "SHOT_BINDINGS_CHANGED" for row in after["blockers"])


# ---------------------------------------------------------------------------
# U09 · one request, one receipt; bindings are an explicit decision
# ---------------------------------------------------------------------------


def test_a_job_may_not_create_shot_bindings_as_a_side_effect(api):
    client, factory, _ = api
    data = _studio(client, shots=1)
    project = data["project"]
    shot = create_shot(client, project, data["style"]["id"], title="no saved bindings")
    assert client.get(f"/api/v1/shots/{shot['id']}/bindings").json() == []
    body = {
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": data["profile"]["id"], "generation_mode": "Ref2VA",
        "references": [{"asset_id": data["subject"]["id"], "use_as": "subject", "role": "CHAR_A"}],
    }
    refused = client.post(f"/api/v1/projects/{project}/jobs", json=body)
    assert refused.status_code == 409, refused.text
    assert "SHOT_BINDINGS_REQUIRED" in refused.text
    assert client.get(f"/api/v1/shots/{shot['id']}/bindings").json() == [], (
        "a refused job must not have written a binding")

    saved = client.put(f"/api/v1/shots/{shot['id']}/bindings", headers=LOCAL, json={"bindings": [
        {"asset_id": data["subject"]["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0}]})
    assert saved.status_code == 200, saved.text
    created = client.post(f"/api/v1/projects/{project}/jobs", json={**body, "references": None})
    assert created.status_code == 201, created.text


def test_the_same_request_key_replays_one_receipt_and_a_different_request_conflicts(api):
    client, _, _ = api
    data = _studio(client, shots=1)
    project = data["project"]
    shot = data["shots"][0]
    body = {
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": data["profile"]["id"], "generation_mode": "Ref2VA",
        "request_key": "double-click-01",
    }
    first = client.post(f"/api/v1/projects/{project}/jobs", json=body)
    second = client.post(f"/api/v1/projects/{project}/jobs", json=body)
    assert first.status_code == 201 and second.status_code == 201, (first.text, second.text)
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["replayed"] is True

    conflict = client.post(f"/api/v1/projects/{project}/jobs", json={
        **body, "generation_mode": "T2VA"})
    assert conflict.status_code == 409, conflict.text
    assert "JOB_REQUEST_CONFLICT" in conflict.text


def test_an_unreconciled_unknown_submission_blocks_a_new_attempt(api):
    client, factory, _ = api
    data = _studio(client, shots=1)
    project = data["project"]
    shot = data["shots"][0]
    first = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": data["profile"]["id"], "generation_mode": "Ref2VA",
    })
    assert first.status_code == 201, first.text
    with factory.begin() as db:
        row = db.get(Job, first.json()["id"])
        row.status = "NEEDS_REVIEW"
        row.failure_code = "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
        db.add(JobAttempt(job_id=row.id, attempt_no=1, status="NEEDS_REVIEW",
                          lease_owner="audit-fixture", lease_expires_at=utcnow(),
                          request_fingerprint="a" * 64, prompt_graph_sha256="b" * 64))
    blocked = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": data["profile"]["id"], "generation_mode": "Ref2VA",
    })
    assert blocked.status_code == 409, blocked.text
    assert "SHOT_SUBMISSION_OUTCOME_UNKNOWN" in blocked.text
    # Changing the request key does not launder the uncertain submission.
    also_blocked = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": data["profile"]["id"], "generation_mode": "Ref2VA",
        "request_key": "pretend-this-is-new",
    })
    assert also_blocked.status_code == 409, also_blocked.text


# ---------------------------------------------------------------------------
# U10 · one scope decides production, delivery and progress
# ---------------------------------------------------------------------------


def test_an_old_delivered_run_never_lights_up_a_new_plan(api):
    client, factory, settings = api
    if not _has_ffmpeg(settings):
        pytest.skip("ffmpeg unavailable")
    data, old_run = _delivered_run(api)
    project = data["project"]
    assert old_run["status"] == "COMPLETED", old_run
    create_shot(client, project, data["style"]["id"], title="new required shot",
                asset_ids=[data["subject"]["id"]], bindings=[{
                    "asset_id": data["subject"]["id"], "use_as": "subject", "role": "CHAR_A", "ordinal": 0}])
    _, package = _plan_and_package(client, data, target_seconds=60)

    scoped, body = stages(client, project, package_id=package["id"])
    assert scoped["production"]["facts"]["scope_shot_count"] == 3
    assert scoped["production"]["done"] is False
    assert scoped["post"]["done"] is False, (
        "a 1/1 run of an older package must not mark this package's delivery complete")
    assert scoped["post"]["facts"]["run_id"] is None
    assert body["scope"]["package_id"] == package["id"]

    # The old run is still reachable by its own scope, and only there.
    old_scoped, _ = stages(client, project, package_id=old_run["package_id"])
    assert old_scoped["post"]["done"] is True
    assert old_scoped["post"]["facts"]["run_id"] == old_run["id"]


def test_a_mock_take_is_counted_as_demo_never_as_production(api):
    client, factory, settings = api
    data = _studio(client, shots=1)
    project = data["project"]
    _, package = _plan_and_package(client, data, target_seconds=60)
    job_id = _seed_passed_job(factory, settings, project, data["shots"][0]["id"], data["style"]["id"])
    with factory.begin() as db:
        job = db.get(Job, job_id)
        job.provider = "mock"
        job.quality_verification = "mock"
        db.get(MediaArtifact, job.artifact_id).is_mock = True

    scoped, _ = stages(client, project, package_id=package["id"])
    assert scoped["production"]["done"] is False
    assert scoped["production"]["facts"]["passed_shot_count"] == 0
    assert scoped["production"]["facts"]["demo_shot_count"] == 1


def test_an_unknown_package_deep_link_is_a_named_error(api):
    client, _, _ = api
    data = _studio(client, shots=1)
    _, package = _plan_and_package(client, data, target_seconds=60)
    response = client.get(f"/api/v1/projects/{data['project']}/journey?package_id=does-not-exist")
    assert response.status_code == 404, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "DIRECTOR_PACKAGE_NOT_FOUND"
    assert package["id"] in detail["available_package_ids"]
