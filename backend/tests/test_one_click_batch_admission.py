"""R04: the whole batch is judged, not only its first shot.

The one-click entry wrote the bindings of every shot and then created a Job
for the first one only.  A second shot that named a character without a
subject reference was therefore queued into a run that could never finish,
and the bindings of the first shot were already persisted when the run
started.

The batch is now checked against the exact revision and the binding snapshot
the person acted on, every shot is judged with the same static facts the
single-shot entry uses, and a refusal leaves nothing behind.
"""

from __future__ import annotations

from sqlalchemy import select

from shine.models import Job
from tests.conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)

LOCAL = {"X-Shine-Local-Action": "1"}


def _studio(client):
    """Two shots that both carry their character."""
    project = create_project(client, "全批准入")
    style = freeze_style(client, project)
    first = upload_asset(client, project, content=PNG + b"-a", filename="甲.png",
                         role="character", entity_id="Lin")
    approve_lock(client, first["id"])
    shots = [create_shot(
        client, project, style["id"], title=f"镜头{index + 1}", characters=["Lin"],
        asset_ids=[first["id"]],
        bindings=[{"asset_id": first["id"], "use_as": "subject",
                   "role": "CHAR_A", "ordinal": 0}],
    ) for index in range(2)]
    profile = register_workflow_profile(client, mode="Ref2VA")
    return {"project": project, "style": style, "shots": shots,
            "subject": first, "profile": profile}


def _series(client, studio, shot_ids=None, **body):
    payload = {
        "shot_ids": shot_ids or [shot["id"] for shot in studio["shots"]],
        "provider": "comfyui",
        "workflow_profile_id": studio["profile"]["id"],
        **body,
    }
    return client.post(f"/api/v1/projects/{studio['project']}/production-series",
                        headers=LOCAL, json=payload)


def _bindings(client, shot_id: str) -> list[str]:
    rows = client.get(f"/api/v1/shots/{shot_id}/bindings").json()
    return [row["asset_id"] for row in rows]


def test_the_second_shot_is_judged_too(api):
    """The counterexample: the first shot is fine, the second one is not."""
    client, factory, _settings = api
    studio = _studio(client)
    bound = studio["shots"][0]
    # A shot that names a character and carries no subject reference at all.
    bare = create_shot(client, studio["project"], studio["style"]["id"],
                       title="镜头二", characters=["Lin"])
    picture = upload_asset(client, studio["project"], content=PNG + b"-p", filename="场景.png",
                           role="scene", entity_id="码头")
    approve_lock(client, picture["id"])

    response = _series(client, studio, shot_ids=[bound["id"], bare["id"]], bindings={
        bound["id"]: [
            {"asset_id": studio["subject"]["id"], "use_as": "subject",
             "role": "CHAR_A", "ordinal": 0},
            {"asset_id": picture["id"], "use_as": "picture", "role": "SCENE", "ordinal": 1},
        ],
        bare["id"]: [],
    })
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "PRODUCTION_SERIES_SHOT_NOT_EXECUTABLE"
    assert [row["shot_id"] for row in detail["blocked"]] == [bare["id"]], detail
    assert detail["blocked"][0]["code"] == "H3_CHARACTER_REFERENCE_MISSING"
    assert detail["runnable_shot_ids"] == [bound["id"]], detail

    # Nothing was written: the extra picture is not on the first shot, and
    # no job was queued for either shot.
    assert _bindings(client, bound["id"]) == [studio["subject"]["id"]]
    with factory() as db:
        assert db.scalars(select(Job).where(Job.project_id == studio["project"])).all() == []


def test_two_legal_shots_are_queued_normally(api):
    client, factory, _settings = api
    studio = _studio(client)
    response = _series(client, studio, bindings={
        studio["shots"][0]["id"]: [{"asset_id": studio["subject"]["id"], "use_as": "subject",
                                   "role": "CHAR_A", "ordinal": 0}],
    })
    assert response.status_code == 201, response.text
    assert response.json()["shot_count"] == 2


def test_a_shot_that_moved_since_the_workbench_showed_it_is_refused(api):
    client, factory, _settings = api
    studio = _studio(client)
    bound, second = studio["shots"]
    response = _series(client, studio, expected_shot_revisions={
        bound["id"]: bound["revision"],
        second["id"]: second["revision"] + 1,
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "SHOT_REVISION_CHANGED"
    with factory() as db:
        assert db.scalars(select(Job).where(Job.project_id == studio["project"])).all() == []


def test_a_start_request_does_not_rewrite_what_it_did_not_change(api):
    """Only the existing bindings are used: the submission changes nothing."""
    client, factory, _settings = api
    studio = _studio(client)
    bound = studio["shots"][0]
    before = _bindings(client, bound["id"])
    response = _series(client, studio, bindings={
        bound["id"]: [{"asset_id": studio["subject"]["id"], "use_as": "subject",
                       "role": "CHAR_A", "ordinal": 0}],
    })
    assert response.status_code == 201, response.text
    assert _bindings(client, bound["id"]) == before


def test_late_character_master_is_bound_on_start_and_replay_keeps_the_same_series(api):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import PLAN_PURPOSE_ASSET_PREPARATION, AssetRequirement, PreProductionPlan

    client, factory, _settings = api
    studio = _studio(client)
    project = studio["project"]
    lin = studio["subject"]["id"]
    shot = create_shot(client, project, studio["style"]["id"], characters=["Mei", "Lin"],
                       asset_ids=[lin], bindings=[{"asset_id": lin, "use_as": "subject"}])
    master = upload_asset(client, project, content=PNG + b"-mei", filename="梅.png",
                          role="character", entity_id="Mei Chen")
    other_version = upload_asset(client, project, content=PNG + b"-new-mei", filename="梅-新版.png",
                                 role="character", entity_id="Mei Chen")
    approve_lock(client, master["id"])
    approve_lock(client, other_version["id"])
    with factory.begin() as db:
        plan = PreProductionPlan(project_id=project, purpose=PLAN_PURPOSE_ASSET_PREPARATION,
                                 status="CONFIRMED", payload={}, sha256=graph_hash({}))
        db.add(plan)
        db.flush()
        requirement = {"family": "character", "entity_id": "Mei", "canonical_entity_id": "Mei Chen",
                       "bound_asset_id": master["id"], "bound_asset_sha256": master["sha256"]}
        db.add(AssetRequirement(project_id=project, plan_id=plan.id, status="CONFIRMED",
                                payload=requirement, sha256=graph_hash(requirement)))
    options = {
        "shot_ids": [shot["id"]],
        "fill_missing_character_bindings": True,
        "expected_shot_revisions": {shot["id"]: shot["revision"]},
        "request_key": "late-master",
    }
    first = _series(client, studio, **options)
    assert first.status_code == 201, first.text
    assert set(_bindings(client, shot["id"])) == {lin, master["id"]}
    with factory() as db:
        job = db.scalar(select(Job).where(Job.shot_id == shot["id"]))
        manifest = job.request["reference_manifest"]
        assert [(item["label"], item["asset_id"], item["role"]) for item in manifest] == [
            ("<Subject 1>", master["id"], "Mei"),
            ("<Subject 2>", lin, "Lin"),
        ]
    replay = _series(client, studio, **options)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]


def test_auto_binding_rolls_back_when_the_batch_contains_an_ambiguous_cast(api):
    client, _factory, _settings = api
    studio = _studio(client)
    project = studio["project"]
    first = create_shot(client, project, studio["style"]["id"], characters=["Lin"])
    second = create_shot(client, project, studio["style"]["id"], characters=["Mei"])
    for index in range(2):
        master = upload_asset(client, project, content=PNG + f"-mei-{index}".encode(),
                              filename=f"mei-{index}.png", role="character", entity_id="Mei")
        approve_lock(client, master["id"])
    response = _series(client, studio, shot_ids=[first["id"], second["id"]],
                       fill_missing_character_bindings=True)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["blocked"][0]["shot_id"] == second["id"]
    assert _bindings(client, first["id"]) == []
    assert _bindings(client, second["id"]) == []
