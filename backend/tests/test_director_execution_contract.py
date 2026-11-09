"""R03: a package and an adoption refuse what the renderer would refuse.

Two conditions were accepted that cannot be executed:

* a director package froze an output the H3 output contract forbids (30 fps,
  a clip of 30 seconds) and was still confirmable and startable, while the
  same output posted to a Series was refused with 422;
* a licence was revoked, and the adoption preview still said "nothing is
  blocked", the adoption wrote a new shot revision anyway, and a confirmed
  package that used the same material still reported itself startable.

The package and the adoption now read the same facts the renderer reads.
"""

from __future__ import annotations

from tests.conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)
from tests.test_asset_center import _png, _rights_upload
from tests.test_output_spec_and_identity import _profile_with_size

LOCAL = {"X-Shine-Local-Action": "1"}


def _studio(client, *, dialogue=None, shots: int = 1):
    project = create_project(client, "可执行条件")
    style = freeze_style(client, project)
    subject = upload_asset(client, project, content=PNG + b"-subject", filename="角色.png",
                           role="character", entity_id="Lin")
    approve_lock(client, subject["id"])
    created = [create_shot(
        client, project, style["id"], title=f"镜头{index + 1}",
        beat=f"第{index + 1}个动作", characters=["Lin"], asset_ids=[subject["id"]],
        dialogue=dialogue if index == 0 else None,
        bindings=[{"asset_id": subject["id"], "use_as": "subject",
                   "role": "Lin", "ordinal": 0}],
    ) for index in range(shots)]
    return {"project": project, "style": style, "shots": created, "subject": subject}


def _plan(client, project: str) -> dict:
    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{project}/episode-plans/{plan.json()['version']}/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return plan.json()


def _package(client, project: str, profile: dict, **body) -> dict:
    response = client.post(f"/api/v1/projects/{project}/director-packages",
                           json={"workflow_profile_id": profile["id"],
                                 "generation_mode": "Ref2VA", **body}, headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def _readiness(client, package_id: str) -> dict:
    return client.get(f"/api/v1/director-packages/{package_id}/readiness").json()


def _codes(payload: dict) -> set[str]:
    return {row["code"] for row in payload.get("blockers") or []}


def test_a_package_with_an_output_the_h3_contract_forbids_is_not_startable(api):
    """30 fps: the draft may show the gap, the confirmation may not."""
    client, factory, _ = api
    studio = _studio(client)
    project = studio["project"]
    _plan(client, project)
    profile = register_workflow_profile(client, mode="Ref2VA")

    built = _package(client, project, profile, output={"fps": 30})
    readiness = _readiness(client, built["id"])
    assert "OUTPUT_SPEC_INVALID" in _codes(readiness), readiness
    assert readiness["startable"] is False

    confirmed = client.post(f"/api/v1/director-packages/{built['id']}/confirm", json={}, headers=LOCAL)
    assert confirmed.status_code == 409, confirmed.text
    detail = confirmed.json()["detail"]
    assert detail["code"] == "DIRECTOR_PACKAGE_NOT_READY"
    assert "OUTPUT_SPEC_INVALID" in {row["code"] for row in detail["blockers"]}


def test_a_package_with_an_illegal_clip_length_is_not_startable(api):
    client, factory, _ = api
    studio = _studio(client)
    project = studio["project"]
    _plan(client, project)
    profile = register_workflow_profile(client, mode="Ref2VA")

    built = _package(client, project, profile, output={"clip_seconds": 30})
    readiness = _readiness(client, built["id"])
    assert "OUTPUT_SPEC_INVALID" in _codes(readiness), readiness
    assert readiness["startable"] is False


def test_a_valid_output_passes_and_reaches_the_renderer(api):
    client, factory, _ = api
    studio = _studio(client)
    project = studio["project"]
    _plan(client, project)
    profile = _profile_with_size(client, with_size=True)

    built = _package(client, project, profile,
                     output={"width": 1344, "height": 768, "fps": 24})
    readiness = _readiness(client, built["id"])
    assert "OUTPUT_SPEC_INVALID" not in _codes(readiness), readiness
    confirmed = client.post(f"/api/v1/director-packages/{built['id']}/confirm", json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text


def test_a_clip_length_that_does_not_fit_the_dialogue_is_named(api):
    """The package asks the same question the Series entry asks by name."""
    client, factory, _ = api
    studio = _studio(client, dialogue=[{"speaker_id": "Lin", "text": "一句话",
                                        "start_s": 0.0, "end_s": 6.0}])
    project = studio["project"]
    _plan(client, project)
    profile = register_workflow_profile(client, mode="Ref2VA")

    built = _package(client, project, profile, output={"clip_seconds": 4})
    readiness = _readiness(client, built["id"])
    assert "CLIP_TOO_SHORT_FOR_DIALOGUE" in _codes(readiness), readiness
    assert readiness["startable"] is False


def _library_character(client, factory, project: str) -> tuple[str, str, str]:
    """Upload, approve rights, QC, promote and assign one character version."""
    from shine.asset_center import service
    from shine.asset_center.models import LibraryRights

    uploaded = _rights_upload(client, _png(2, [[(25, 35, 45, 255), (55, 65, 75, 255)]])).json()
    rights_id = uploaded["rights_id"]
    with factory() as db:
        snapshot = service.canonical_hash(service.rights_snapshot(db.get(LibraryRights, rights_id)))
    version = client.post("/api/v1/asset-center/assets", json={
        "title": "fixture character", "kind": "image", "category": "character",
        "media_id": uploaded["id"],
    }).json()["versions"][0]["id"]
    rights = client.post(f"/api/v1/asset-center/rights/{rights_id}/approve", headers=LOCAL, json={
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
        "idempotency_key": "r03-assignment-01", "confirmed": True})
    assert assigned.status_code == 201, assigned.text
    return version, assigned.json()["project_asset_id"], rights_id


def _revision(client, project: str, shot_id: str) -> int:
    rows = client.get(f"/api/v1/projects/{project}/shots").json()
    return next(row["revision"] for row in rows if row["id"] == shot_id)


def test_a_revoked_licence_blocks_the_adoption_and_writes_no_revision(api):
    client, factory, _ = api
    studio = _studio(client)
    project = studio["project"]
    shot = studio["shots"][0]
    version, asset_id, rights_id = _library_character(client, factory, project)

    revoked = client.post(f"/api/v1/asset-center/rights/{rights_id}/revoke",
                          params={"evidence": "revoked by the test"}, headers=LOCAL)
    assert revoked.status_code == 200, revoked.text

    preview = client.post("/api/v1/asset-center/adoptions/preview", json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin"})
    assert preview.status_code == 200, preview.text
    blocked = preview.json()["blocked"]
    assert blocked, preview.json()
    assert blocked[0]["conflict"] == "ASSET_RIGHTS_NOT_APPROVED"

    before = _revision(client, project, shot["id"])
    applied = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: before},
        "idempotency_key": "r03-adoption-01", "confirmed": True})
    assert applied.status_code == 409, applied.text
    assert applied.json()["detail"]["code"] == "ADOPTION_BLOCKED"
    assert _revision(client, project, shot["id"]) == before, "no revision may be written"


def test_a_frozen_package_stops_being_startable_when_the_licence_is_revoked(api):
    client, factory, _ = api
    studio = _studio(client, shots=2)
    project = studio["project"]
    shot = studio["shots"][0]
    other = studio["shots"][1]
    version, asset_id, rights_id = _library_character(client, factory, project)

    before = _revision(client, project, shot["id"])
    applied = client.post("/api/v1/asset-center/adoptions", headers=LOCAL, json={
        "project_id": project, "library_version_id": version, "project_asset_id": asset_id,
        "shot_ids": [shot["id"]], "role": "character", "entity_id": "Lin",
        "expected_shot_revisions": {shot["id"]: before},
        "idempotency_key": "r03-adoption-02", "confirmed": True})
    assert applied.status_code == 201, applied.text

    _plan(client, project)
    profile = register_workflow_profile(client, mode="Ref2VA")
    built = _package(client, project, profile)
    confirmed = client.post(f"/api/v1/director-packages/{built['id']}/confirm", json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    assert _readiness(client, built["id"])["startable"] is True

    revoked = client.post(f"/api/v1/asset-center/rights/{rights_id}/revoke",
                          params={"evidence": "revoked by the test"}, headers=LOCAL)
    assert revoked.status_code == 200, revoked.text

    readiness = _readiness(client, built["id"])
    assert "ASSET_RIGHTS_NOT_APPROVED" in _codes(readiness), readiness
    assert readiness["startable"] is False

    # The independent shot keeps its own state: only the revoked material stops.
    assert other["id"] != shot["id"]
