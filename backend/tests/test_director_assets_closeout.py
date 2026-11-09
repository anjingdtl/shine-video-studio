"""Script entry, material adoption and live rights: the journey's first half.

These are the fixed contracts for U03, U05 and U06.  They use the real FastAPI
routes, an isolated SQLite database and synthetic media; nothing here claims a
real H3 render or a real model review.
"""

from sqlalchemy import select

from shine.models import Job
from tests.conftest import (
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)
from tests.test_asset_center import _png, _rights_upload
from tests.test_storyboard import _responder, _storyboard_request, _studio_with_chain

LOCAL = {"X-Shine-Local-Action": "1"}


def _revision(client, project: str, shot_id: str) -> int:
    rows = client.get(f"/api/v1/projects/{project}/shots").json()
    return next(row["revision"] for row in rows if row["id"] == shot_id)


def stages(client, project, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    suffix = f"?{query}" if query else ""
    body = client.get(f"/api/v1/projects/{project}/journey{suffix}")
    assert body.status_code == 200, body.text
    return {row["id"]: row for row in body.json()["stages"]}


# ---------------------------------------------------------------------------
# U03 · the next step after an import is on the same page
# ---------------------------------------------------------------------------


def test_an_imported_script_reports_the_outline_step_on_stage_one(api):
    client, _, _ = api
    project = create_project(client)
    document = client.post(
        f"/api/v1/projects/{project}/documents",
        files={"file": ("audit-script.txt", b"Scene one.\nLin: hello.\n", "text/plain")},
    ).json()

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    script = {row["id"]: row for row in journey["stages"]}["script"]
    assert script["done"] is False
    assert script["facts"]["document_id"] == document["id"]
    assert script["facts"]["outline_confirmed"] is False
    assert script["facts"]["basis_explicit"] is True
    assert journey["next_stage"] == "script", "the outline step belongs to stage 1"

    # The outline endpoint the stage-one panel calls is the real one.
    assert client.get(f"/api/v1/projects/{project}/story-preparation?document_id={document['id']}").status_code == 200
    basis = client.get(f"/api/v1/projects/{project}/script-basis").json()
    assert basis["document_id"] == document["id"]
    assert [row["document_id"] for row in basis["candidates"]] == [document["id"]]


def test_the_storyboard_no_longer_stops_for_an_unprepared_character(studio):
    client, _, _, _ = studio
    project, _, registry = _studio_with_chain(client)
    client.app.state.mock_llm_responder = _responder(registry, character_name="Unprepared")
    response = _storyboard_request(client, project, confirm_characters=False)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["status"] != "AWAITING_CHARACTER_CONFIRMATION", (
        "a missing character master is a material requirement, not a stop")
    assert "director" in row["steps"]
    # The explicit matching action is still available as its own decision.
    proposal = client.post(
        f"/api/v1/projects/{project}/storyboard/character-binding/proposal",
        json={"breakdown_artifact_id": row["steps"]["scriptwriter"]["artifact_id"]},
    )
    assert proposal.status_code == 201, proposal.text
    assert proposal.json()["characters"][0]["script_name"] == "Unprepared"


# ---------------------------------------------------------------------------
# U05 · assignment reaches the shot through an explicit adoption
# ---------------------------------------------------------------------------


def _library_character(client, factory, project: str) -> tuple[str, str]:
    """Upload, approve rights, QC, promote and assign one character version."""
    from shine.asset_center import service
    from shine.asset_center.models import LibraryRights

    uploaded = _rights_upload(client, _png(2, [[(25, 35, 45, 255), (55, 65, 75, 255)]])).json()
    with factory() as db:
        snapshot = service.canonical_hash(service.rights_snapshot(db.get(LibraryRights, uploaded["rights_id"])))
    version = client.post("/api/v1/asset-center/assets", json={
        "title": "fixture character", "kind": "image", "category": "character",
        "media_id": uploaded["id"],
    }).json()["versions"][0]["id"]
    rights = client.post(f"/api/v1/asset-center/rights/{uploaded['rights_id']}/approve", headers=LOCAL, json={
        "expected_snapshot": snapshot, "evidence": "local fixture", "allowed_project_ids": [project],
        "permitted_uses": ["research"], "commercial_allowed": False})
    assert rights.status_code == 200, rights.text
    assert client.post(f"/api/v1/asset-center/versions/{version}/qc").status_code == 200
    grant = client.post(f"/api/v1/asset-center/versions/{version}/grant", headers=LOCAL).json()
    promoted = client.post(f"/api/v1/asset-center/versions/{version}/promote", headers=LOCAL, json={
        "grant_id": grant["grant_id"], "snapshot": grant["snapshot"], "evidence": "reviewed fixture"})
    assert promoted.status_code == 200, promoted.text
    assigned = client.post("/api/v1/asset-center/assignments", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "role": "character",
        "entity_id": "Lin", "usage_scope": "research",
        "idempotency_key": "closeout-assignment-01", "confirmed": True})
    assert assigned.status_code == 201, assigned.text
    return version, assigned.json()["project_asset_id"]


def test_an_assignment_only_reaches_a_shot_through_an_explicit_adoption(api):
    client, factory, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], title="pre-existing storyboard", characters=["Lin"])
    version, asset_id = _library_character(client, factory, project)

    # The assignment created a project master; it did not silently rewrite the shot.
    assert client.get(f"/api/v1/shots/{shot['id']}/bindings").json() == []

    preview = client.post("/api/v1/asset-center/adoptions/preview", json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["shot_count"] == 1 and body["blocked"] == []
    assert body["shots"][0]["revision"] == shot["revision"]
    assert body["shots"][0]["use_as"] == "subject"

    refused = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-01", "confirmed": False})
    assert refused.status_code == 409 and "ADOPTION_CONFIRMATION_REQUIRED" in refused.text

    adopted = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-01", "confirmed": True})
    assert adopted.status_code == 201, adopted.text
    receipt = adopted.json()
    assert receipt["adopted_count"] == 1 and receipt["shots"][0]["status"] == "ADOPTED"
    bindings = client.get(f"/api/v1/shots/{shot['id']}/bindings").json()
    assert [row["asset_id"] for row in bindings] == [asset_id]
    assert _revision(client, project, shot["id"]) == shot["revision"] + 1

    replay = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-01", "confirmed": True})
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert _revision(client, project, shot["id"]) == shot["revision"] + 1

    stale = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-02", "confirmed": True})
    assert stale.status_code == 409 and "SHOT_REVISION_CHANGED" in stale.text


def test_project_master_can_be_collected_and_bound_without_a_source_declaration(api):
    client, _, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    asset = upload_asset(client, project, role="prop", entity_id="Lantern")
    approve_lock(client, asset["id"])

    collected = client.post(
        f"/api/v1/projects/{project}/assets/{asset['id']}/save-to-library",
        headers=LOCAL, json={"title": "Lantern", "category": "prop"},
    )
    assert collected.status_code == 201, collected.text
    assert collected.json()["versions"][0]["status"] == "READY_FOR_REVIEW"

    request = {"project_id": project, "project_asset_id": asset["id"], "shot_ids": [shot["id"]]}
    preview = client.post("/api/v1/asset-center/adoptions/preview", json=request)
    assert preview.status_code == 200 and preview.json()["shots"][0]["use_as"] == "picture"
    adopted = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        **request,
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "project-master-adoption",
        "confirmed": True,
    })
    assert adopted.status_code == 201, adopted.text
    assert adopted.json()["adopted_count"] == 1


def test_adoption_refuses_a_shot_inside_a_confirmed_package(api):
    client, factory, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    subject = upload_asset(client, project, content=b"\x89PNG\r\n\x1a\n-subject", filename="角色.png",
                           role="character", entity_id="Lin")
    approve_lock(client, subject["id"])
    shot = create_shot(client, project, style["id"], characters=["Lin"], asset_ids=[subject["id"]],
                       bindings=[{"asset_id": subject["id"], "use_as": "subject",
                                  "role": "CHAR_A", "ordinal": 0}])
    profile = register_workflow_profile(client, mode="Ref2VA")
    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 10}, headers=LOCAL).json()
    client.post(f"/api/v1/projects/{project}/episode-plans/{plan['version']}/confirm",
                json={"expected_sha256": plan["sha256"]}, headers=LOCAL)
    package = client.post(f"/api/v1/projects/{project}/director-packages",
                          json={"workflow_profile_id": profile["id"]}, headers=LOCAL).json()
    confirmed = client.post(f"/api/v1/director-packages/{package['id']}/confirm",
                            json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text

    version, asset_id = _library_character(client, factory, project)
    preview = client.post("/api/v1/asset-center/adoptions/preview", json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin"})
    assert preview.json()["blocked"][0]["conflict"] == "SHOT_IN_CONFIRMED_PACKAGE"
    refused = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-confirmed-package", "confirmed": True})
    assert refused.status_code == 409 and "ADOPTION_BLOCKED" in refused.text


def test_the_same_adoption_key_cannot_describe_another_shot(api):
    """One key, one canonical request: a different payload is a named conflict."""
    client, factory, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    first = create_shot(client, project, style["id"], title="第一镜", characters=["Lin"])
    second = create_shot(client, project, style["id"], title="第二镜", characters=["Lin"])
    version, asset_id = _library_character(client, factory, project)

    base = {
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "role": "character", "entity_id": "Lin", "idempotency_key": "one-key-one-request",
        "confirmed": True,
    }
    adopted = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        **base, "shot_ids": [first["id"]],
        "expected_shot_revisions": {first["id"]: first["revision"]},
    })
    assert adopted.status_code == 201, adopted.text

    conflicting = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        **base, "shot_ids": [second["id"]],
        "expected_shot_revisions": {second["id"]: second["revision"]},
    })
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()["detail"]["code"] == "ADOPTION_IDEMPOTENCY_CONFLICT"
    assert client.get(f"/api/v1/shots/{second['id']}/bindings").json() == []


def test_a_newer_package_revision_unlocks_material_adoption(api):
    """A confirmed package is history once a newer revision exists."""
    client, factory, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    subject = upload_asset(client, project, content=b"\x89PNG\r\n\x1a\n-subject",
                           filename="角色.png", role="character", entity_id="Lin")
    approve_lock(client, subject["id"])
    shot = create_shot(client, project, style["id"], characters=["Lin"], asset_ids=[subject["id"]],
                       bindings=[{"asset_id": subject["id"], "use_as": "subject",
                                  "role": "CHAR_A", "ordinal": 0}])
    profile = register_workflow_profile(client, mode="Ref2VA")
    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 10}, headers=LOCAL).json()
    client.post(f"/api/v1/projects/{project}/episode-plans/{plan['version']}/confirm",
                json={"expected_sha256": plan["sha256"]}, headers=LOCAL)
    first = client.post(f"/api/v1/projects/{project}/director-packages",
                        json={"workflow_profile_id": profile["id"]}, headers=LOCAL).json()
    assert client.post(f"/api/v1/director-packages/{first['id']}/confirm", json={},
                       headers=LOCAL).status_code == 200
    # The person started the replacement path: a newer draft package exists.
    second = client.post(f"/api/v1/projects/{project}/director-packages",
                         json={"workflow_profile_id": profile["id"]}, headers=LOCAL)
    assert second.status_code == 201, second.text

    version, asset_id = _library_character(client, factory, project)
    preview = client.post("/api/v1/asset-center/adoptions/preview", json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin"})
    assert preview.json()["blocked"] == [], preview.text
    adopted = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-after-new-revision", "confirmed": True})
    assert adopted.status_code == 201, adopted.text
    assert adopted.json()["adopted_count"] == 1


def test_a_live_take_blocks_adoption_by_name(api):
    client, factory, _ = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"], title="在拍镜头", characters=["Lin"])
    queued = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "mock", "mock_scenario": "success"})
    assert queued.status_code == 201, queued.text
    version, asset_id = _library_character(client, factory, project)

    preview = client.post("/api/v1/asset-center/adoptions/preview", json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin"})
    blocked = preview.json()["blocked"][0]
    assert blocked["conflict"] == "SHOT_PRODUCTION_IN_FLIGHT"
    assert blocked["action"] == "RECONCILE_OR_FINISH_CURRENT_TAKE"
    assert blocked["production_job_id"]

    refused = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "idempotency_key": "adopt-while-running", "confirmed": True})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "ADOPTION_BLOCKED"


# ---------------------------------------------------------------------------
# U06 · a revoked global licence stops every real consumer
# ---------------------------------------------------------------------------


def test_a_revoked_library_version_blocks_the_job_and_the_worker_gate(api):
    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    version, asset_id = _library_character(client, factory, project)
    shot = create_shot(client, project, style["id"], characters=["Lin"], asset_ids=[asset_id],
                       bindings=[{"asset_id": asset_id, "use_as": "subject",
                                  "role": "character", "ordinal": 0}])
    profile = register_workflow_profile(client, mode="Ref2VA")

    revoked = client.post(f"/api/v1/asset-center/rights/{_rights_id(factory, version)}/revoke",
                          headers=LOCAL, params={"evidence": "revoked fixture"})
    assert revoked.status_code == 200, revoked.text

    response = client.post(f"/api/v1/projects/{project}/jobs", json={
        "shot_id": shot["id"], "provider": "comfyui",
        "workflow_profile_id": profile["id"], "generation_mode": "Ref2VA"})
    assert response.status_code == 409, response.text
    assert "LIBRARY_RIGHTS" in response.text or "RIGHTS" in response.text
    with factory() as db:
        assert db.scalar(select(Job).where(Job.shot_id == shot["id"])) is None, (
            "a refused admission must not leave a queued job behind")


def _rights_id(factory, version_id: str) -> str:
    """The global rights row behind one promoted version."""
    from shine.asset_center.models import LibraryVersion

    with factory() as db:
        return db.get(LibraryVersion, version_id).rights_id
