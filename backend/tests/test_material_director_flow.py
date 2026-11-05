"""Adversarial regression tests for the director AI material-preparation flow.

The ledger tests in test_material_preparation.py build their own rows and never
drive the HTTP surface, so they cannot see whether a user can actually walk the
director journey.  These tests do exactly that: project -> document -> analyse
-> read the list -> confirm -> generate.

Every assertion here is a property the product plan states in prose:

* the user must be able to read a requirement list that carries stable ids and
  the snapshot hash, because the confirmation body references both;
* the confirmation must be accepted when it quotes what the server published;
* a generation batch must consume an APPROVED rights record, exactly like the
  existing candidate path does;
* a scene answer that breaks the published contract is refused at the boundary,
  not carried into the plan payload.
"""

from __future__ import annotations

from shine.comfyops.models import RightsRecord

LOCAL_ACTION = {"x-shine-local-action": "1"}
"""The personal-desktop CSRF marker the mutating routes require.

A same-origin loopback UI action carries it and is recorded as local-user; no
operator password is involved. The routes refuse a request without it.
"""

SCENE = {
    "schema_version": "1.0",
    "scene_key": "S1",
    "summary": "林知远在雨夜的码头等待。",
    "locked_facts": ["时代：1930年代"],
    "requirements": [
        {
            "family": "character",
            "name": "林知远",
            "description": "三十岁上下，长风衣。",
            "source_span_ids": ["S1#p1"],
            "questions": [],
        },
        {
            "family": "prop",
            "name": "铜怀表",
            "description": "表盖刻字。",
            "source_span_ids": ["S1#p2"],
            "questions": [],
        },
    ],
    "questions": [],
}


def _project(client) -> str:
    response = client.post("/api/v1/projects", json={"title": "雾港"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _document(client, project_id: str, content: str = "场景一：\n林知远：你来了。\n") -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("剧本.txt", content.encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _analyze(client, project_id: str, document: dict, *, scenes=None, key="k1"):
    response = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/analyze",
        json={
            "document_id": document["id"],
            "expected_document_hash": document["sha256"],
            "idempotency_key": key,
            "scene_preparations": scenes if scenes is not None else [SCENE],
        },
        headers=LOCAL_ACTION,
    )
    return response


def _approved_rights(factory, project_id: str) -> str:
    from shine.comfy.workflow import graph_hash

    with factory() as db:
        payload = {"scope": "asset", "license_id": "local-research", "purpose": "material preparation"}
        row = RightsRecord(
            project_id=project_id,
            status="APPROVED",
            payload=payload,
            sha256=graph_hash(payload),
        )
        db.add(row)
        db.flush()
        db.commit()
        return row.id


def test_the_list_the_user_must_confirm_carries_ids_and_a_snapshot(api):
    """A confirmation body references a requirement id and a snapshot hash.

    Both have to be published by the server, or the director page can only
    guess and every confirmation is refused.
    """
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)

    response = _analyze(client, project_id, document)
    assert response.status_code == 202, response.text
    body = response.json()

    requirements = body["requirements"]
    assert len(requirements) == 2, body
    for requirement in requirements:
        assert requirement.get("requirement_id"), requirement
        assert requirement["family"] in {"character", "scene", "prop"}, requirement
    assert body.get("expected_snapshot"), body

    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation")
    assert progress.status_code == 200, progress.text
    page = progress.json()
    assert page["stage"] == "confirm_requirements", page
    assert len(page["requirements"]) == 2, page
    assert page.get("expected_snapshot") == body["expected_snapshot"], page


def test_confirm_accepts_the_snapshot_the_server_published(api):
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)
    analyzed = _analyze(client, project_id, document).json()

    decisions = [
        {
            "requirement_id": requirement["requirement_id"],
            "strategy": "generate",
            "entity_id": requirement["entity_id"],
        }
        for requirement in analyzed["requirements"]
    ]
    response = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": decisions,
            "visual_direction": {"era": "1930年代", "palette": "青灰"},
        },
        headers=LOCAL_ACTION,
    )
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed["confirmation_id"], confirmed
    assert confirmed["confirmation_hash"], confirmed


def test_generate_requires_an_approved_rights_record(api):
    """A generation batch consumes a rights record, like every other asset path."""
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)
    analyzed = _analyze(client, project_id, document).json()

    confirmed = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": [
                {
                    "requirement_id": requirement["requirement_id"],
                    "strategy": "generate",
                    "entity_id": requirement["entity_id"],
                }
                for requirement in analyzed["requirements"]
            ],
            "visual_direction": {"era": "1930年代"},
        },
        headers=LOCAL_ACTION,
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmation = confirmed.json()

    refused = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/generate",
        json={
            "confirmation_id": confirmation["confirmation_id"],
            "confirmation_hash": confirmation["confirmation_hash"],
            "requirement_ids": [r["requirement_id"] for r in analyzed["requirements"]],
            "idempotency_key": "no-rights",
            "rights_id": "00000000-0000-0000-0000-000000000000",
            "mode": "real",
        },
        headers=LOCAL_ACTION,
    )
    assert refused.status_code == 409, refused.text

    rights_id = _approved_rights(factory, project_id)
    accepted = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/generate",
        json={
            "confirmation_id": confirmation["confirmation_id"],
            "confirmation_hash": confirmation["confirmation_hash"],
            "requirement_ids": [r["requirement_id"] for r in analyzed["requirements"]],
            "idempotency_key": "with-rights",
            "rights_id": rights_id,
            "mode": "real",
        },
        headers=LOCAL_ACTION,
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["run_id"], accepted.text


def test_a_scene_answer_that_breaks_the_contract_is_refused(api):
    """The published contract is enforced at the boundary, not stored anyway."""
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)

    broken_family = {
        **SCENE,
        "requirements": [{"family": "voice", "name": "林知远", "description": "", "questions": []}],
    }
    refused = _analyze(client, project_id, document, scenes=[broken_family], key="bad-family")
    assert refused.status_code == 422, refused.text

    unknown_field = {**SCENE, "shots": []}
    refused = _analyze(client, project_id, document, scenes=[unknown_field], key="unknown-field")
    assert refused.status_code == 422, refused.text


def test_the_same_analysis_does_not_open_a_second_revision(api):
    """A repeated analysis must not drop the decisions the user confirmed.

    The newest material plan is the current one. A second draft for the same
    script would become current and silently reset the confirmed state, even
    when the second call carries a different idempotency key.
    """
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)

    first = _analyze(client, project_id, document, key="k1")
    assert first.status_code == 202, first.text
    analyzed = first.json()

    again = _analyze(client, project_id, document, key="k2")
    assert again.status_code == 202, again.text
    assert again.json()["plan_revision_id"] == analyzed["plan_revision_id"]
    assert again.json()["replayed"] is True

    confirmed = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": [
                {
                    "requirement_id": requirement["requirement_id"],
                    "strategy": "generate",
                    "entity_id": requirement["entity_id"],
                }
                for requirement in analyzed["requirements"]
            ],
            "visual_direction": {"era": "1930年代"},
        },
        headers=LOCAL_ACTION,
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_revision = confirmed.json()["plan_revision_id"]

    third = _analyze(client, project_id, document, key="k3")
    assert third.json()["replayed"] is True
    # The confirmed revision stays current: the repeat does not open a new
    # draft on top of it and reset the decisions the user already made.
    assert third.json()["plan_revision_id"] == confirmed_revision

    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    assert progress["stage"] == "select_materials", progress
    assert progress["plan_status"] == "CONFIRMED", progress


def test_an_invented_source_span_is_refused(api):
    """The server owns the citation catalogue; an invented fragment is a refusal."""
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)

    invented = {
        **SCENE,
        "requirements": [
            {
                "family": "character",
                "name": "林知远",
                "description": "三十岁上下，长风衣。",
                "source_span_ids": ["S1#p99"],
                "questions": [],
            }
        ],
    }
    refused = _analyze(client, project_id, document, scenes=[invented], key="invented-span")
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "MATERIAL_SOURCE_SPAN_UNKNOWN"

    unknown_scene = {**SCENE, "scene_key": "S9"}
    refused = _analyze(client, project_id, document, scenes=[unknown_scene], key="unknown-scene")
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "MATERIAL_SCENE_KEY_UNKNOWN"


def test_the_scenes_route_publishes_the_server_segmentation(api):
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)

    response = client.get(
        f"/api/v1/projects/{project_id}/material-preparation/scenes",
        params={"document_id": document["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_sha256"] == document["sha256"]
    assert body["scenes"], body
    assert body["scenes"][0]["scene_key"] == "S1"
    assert body["span_ids"], body

def test_an_ambiguous_identity_is_reported_and_blocks_the_confirmation(api):
    """The same name under two descriptions is a question for the user, not a merge."""
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id, "场景一：\n林知远：你来了。\n\n场景二：\n林知远转身。\n")

    ambiguous = {
        **SCENE,
        "scene_key": "S2",
        "requirements": [
            {
                "family": "character",
                "name": "林知远",
                "description": "四十岁上下，灰袍。",
                "source_span_ids": ["S2#p2"],
                "questions": [],
            }
        ],
    }
    analyzed = _analyze(client, project_id, document, scenes=[SCENE, ambiguous], key="ambiguous").json()
    assert analyzed["conflicts"], analyzed
    assert analyzed["conflicts"][0]["code"] == "MATERIAL_IDENTITY_AMBIGUOUS"

    refused = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": [
                {
                    "requirement_id": requirement["requirement_id"],
                    "strategy": "generate",
                    "entity_id": requirement["entity_id"],
                }
                for requirement in analyzed["requirements"]
            ],
            "visual_direction": {"era": "1930年代"},
        },
        headers=LOCAL_ACTION,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "MATERIAL_IDENTITY_AMBIGUOUS"


def test_a_requirement_from_another_project_is_refused(api):
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)
    analyzed = _analyze(client, project_id, document).json()

    other = _project(client)
    other_document = _document(client, other)
    other_analyzed = _analyze(client, other, other_document, key="other").json()
    foreign = other_analyzed["requirements"][0]["requirement_id"]

    refused = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": [{"requirement_id": foreign, "strategy": "generate", "entity_id": "林知远"}],
            "visual_direction": {},
        },
        headers=LOCAL_ACTION,
    )
    assert refused.status_code == 404, refused.text


def test_an_empty_analysis_is_not_a_completed_material_list(api):
    """A script that yielded nothing must not pass as "no material needed"."""
    client, factory, _ = api
    project_id = _project(client)
    document = _document(client, project_id)

    analyzed = _analyze(client, project_id, document, scenes=[], key="empty").json()
    assert analyzed["requirements"] == []
    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    assert progress["stage"] != "materials_ready", progress
    assert progress["requirements"] == [], progress

    refused = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": [{"requirement_id": "x" * 36, "strategy": "generate", "entity_id": "林知远"}],
            "visual_direction": {},
        },
        headers=LOCAL_ACTION,
    )
    assert refused.status_code in (404, 422), refused.text


def _confirm_material(client, project_id: str, analyzed: dict) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/confirm",
        json={
            "plan_revision_id": analyzed["plan_revision_id"],
            "expected_snapshot": analyzed["expected_snapshot"],
            "decisions": [
                {
                    "requirement_id": requirement["requirement_id"],
                    "strategy": "generate",
                    "entity_id": requirement["entity_id"],
                }
                for requirement in analyzed["requirements"]
            ],
            "visual_direction": {"era": "1930年代"},
        },
        headers=LOCAL_ACTION,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_material_list_is_only_ready_when_every_requirement_is_bound(api):
    """The completion condition is judged, not assumed."""
    from tests.conftest import PNG, approve_lock, upload_asset

    client, factory, settings = api
    project_id = _project(client)
    document = _document(client, project_id)
    analyzed = _analyze(client, project_id, document).json()
    _confirm_material(client, project_id, analyzed)

    refused = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/complete", headers=LOCAL_ACTION
    )
    assert refused.status_code == 200, refused.text
    judged = refused.json()
    assert judged["materials_ready"] is False, judged
    assert judged["missing"], judged
    assert judged["missing"][0]["reason"] == "ASSET_OR_VOICE_MISSING", judged

    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    assert progress["stage"] != "materials_ready", progress

    asset = upload_asset(client, project_id, PNG + b"master", "林知远.png",
                         role="character", entity_id="林知远")
    approve_lock(client, asset["id"])
    prop = upload_asset(client, project_id, PNG + b"prop", "铜怀表.png",
                        role="prop", entity_id="铜怀表")
    approve_lock(client, prop["id"])

    _bind_uploaded(client, project_id, [asset, prop])
    ready = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/complete", headers=LOCAL_ACTION
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["materials_ready"] is True, ready.text
    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    assert progress["stage"] == "materials_ready", progress
    assert progress["plan_status"] == "MATERIALS_READY", progress


def test_an_offline_only_mock_master_never_counts_as_ready(api):
    """A real project may not bind a Fake master."""
    from tests.conftest import PNG, upload_asset

    client, factory, settings = api
    project_id = _project(client)
    document = _document(client, project_id)
    analyzed = _analyze(client, project_id, document).json()
    _confirm_material(client, project_id, analyzed)

    asset = upload_asset(client, project_id, PNG + b"mock", "林知远.png",
                         role="character", entity_id="林知远")
    prop = upload_asset(client, project_id, PNG + b"mockprop", "铜怀表.png",
                        role="prop", entity_id="铜怀表")
    with factory() as db:
        from shine.models import Asset

        for identity in (asset["id"], prop["id"]):
            row = db.get(Asset, identity)
            row.approved = True
            row.locked = True
            row.provenance = {"offline_only": True, "is_mock": True}
        db.commit()

    _bind_uploaded(client, project_id, [asset, prop])
    judged = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/complete", headers=LOCAL_ACTION
    ).json()
    assert judged["materials_ready"] is False, judged
    assert all(entry["reason"] == "MOCK_ASSET_NOT_PRODUCTION" for entry in judged["missing"]), judged


def test_a_tampered_master_file_is_not_ready(api):
    """The bytes on disk are re-checked, not remembered."""
    from tests.conftest import PNG, approve_lock, upload_asset

    client, factory, settings = api
    project_id = _project(client)
    document = _document(client, project_id)
    analyzed = _analyze(client, project_id, document).json()
    _confirm_material(client, project_id, analyzed)

    asset = upload_asset(client, project_id, PNG + b"master", "林知远.png",
                         role="character", entity_id="林知远")
    approve_lock(client, asset["id"])
    prop = upload_asset(client, project_id, PNG + b"prop", "铜怀表.png",
                        role="prop", entity_id="铜怀表")
    approve_lock(client, prop["id"])
    with factory() as db:
        from shine.models import Asset

        row = db.get(Asset, asset["id"])
        storage_path = row.storage_path
    from shine.media.artifacts import safe_asset_path

    target = safe_asset_path(settings.data_dir, storage_path)
    target.write_bytes(b"tampered")

    _bind_uploaded(client, project_id, [asset, prop])
    judged = client.post(
        f"/api/v1/projects/{project_id}/material-preparation/complete", headers=LOCAL_ACTION
    ).json()
    assert judged["materials_ready"] is False, judged
    assert any(entry["reason"] == "ASSET_HASH_CHANGED" for entry in judged["missing"]), judged


def _bind_uploaded(client, project_id, assets):
    state = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    response = client.post(f"/api/v1/projects/{project_id}/material-preparation/confirm", json={
        "plan_revision_id": state["plan_revision_id"], "expected_snapshot": state["expected_snapshot"],
        "decisions": [{"requirement_id": r["requirement_id"], "entity_id": r["entity_id"],
                       "strategy": "reuse_master", "asset_id": next(
                           a["id"] for a in assets if a["entity_id"] == r["entity_id"])}
                      for r in state["requirements"]], "visual_direction": {},
    }, headers=LOCAL_ACTION)
    assert response.status_code == 200, response.text


def test_explicit_version_wins_and_missing_or_changed_version_is_never_substituted(api):
    from shine.models import Asset
    from tests.conftest import PNG, approve_lock, upload_asset

    client, factory, _ = api
    project_id = _project(client)
    doc = _document(client, project_id)
    analyzed = _analyze(client, project_id, doc).json()
    _confirm_material(client, project_id, analyzed)
    first = upload_asset(client, project_id, PNG + b"first", "old.png", role="character", entity_id="林知远")
    chosen = upload_asset(client, project_id, PNG + b"chosen", "chosen.png", role="character", entity_id="林知远")
    prop = upload_asset(client, project_id, PNG + b"prop", "prop.png", role="prop", entity_id="铜怀表")
    for asset in (first, chosen, prop):
        approve_lock(client, asset["id"])
    _bind_uploaded(client, project_id, [chosen, prop])
    path = f"/api/v1/projects/{project_id}/material-preparation/complete"
    judged = client.post(path, headers=LOCAL_ACTION).json()
    assert judged["materials_ready"]
    assert next(r for r in judged["requirements"] if r["family"] == "character")["asset_id"] == chosen["id"]
    with factory.begin() as db:
        db.get(Asset, chosen["id"]).sha256 = "f" * 64
    judged = client.post(path, headers=LOCAL_ACTION).json()
    assert not judged["materials_ready"]
    assert any(r["reason"] == "ASSET_HASH_CHANGED" for r in judged["missing"])
    with factory.begin() as db:
        db.get(Asset, chosen["id"]).locked = False
    judged = client.post(path, headers=LOCAL_ACTION).json()
    assert not judged["materials_ready"]
    assert next(r for r in judged["requirements"] if r["family"] == "character")["asset_id"] is None
