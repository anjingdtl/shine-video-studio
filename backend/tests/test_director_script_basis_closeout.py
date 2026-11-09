"""J06: a package can never pair one script's outline with another's plan."""

from __future__ import annotations

from shine.director.package import digest_of
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from tests.conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)
from tests.test_director_long_form import LOCAL


def _studio(client, *, shots: int = 1):
    project_id = create_project(client, "剧本血缘")
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="角色.png")
    approve_lock(client, subject["id"])
    created = [create_shot(
        client, project_id, style["id"], title=f"镜头{index + 1}",
        beat=f"第{index + 1}个动作", asset_ids=[subject["id"]],
        bindings=[{"asset_id": subject["id"], "use_as": "subject",
                   "role": "CHAR_A", "ordinal": 0}],
    ) for index in range(shots)]
    return {"project": project_id, "style": style, "shots": created,
            "profile": register_workflow_profile(client, mode="Ref2VA")}


def _document(client, project: str, name: str, text: bytes) -> dict:
    response = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": (name, text, "text/plain")})
    assert response.status_code == 201, response.text
    return response.json()


def _approved_outline(factory, project: str, document: dict) -> dict:
    sections = [
        {"production_section_id": f"{document['id']}-s{index}", "order": index + 1,
         "title": f"第{index + 1}场", "estimated_duration_s": 6,
         "start_offset": index * 100, "end_offset": index * 100 + 99}
        for index in range(2)
    ]
    payload = {"production_sections": sections, "document_id": document["id"],
               "document_sha256": document["sha256"]}
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project, role_id="story_outline_run",
            request_identity=f"outline-{document['id']}", input_hash="0" * 64,
            asset_manifest_hash="0" * 64, rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED", trace={},
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
        return {"id": artifact.id, "sha256": artifact.sha256, "sections": sections}


def _section_plan(client, studio, outline, shot_ids):
    episodes = [{
        "episode_id": "ep-1", "order": 1, "title": "第一集",
        "section_ids": [row["production_section_id"] for row in outline["sections"]],
        "shot_ids": list(shot_ids), "planned_seconds": 12,
    }]
    response = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans",
        json={"target_seconds": 60, "tolerance_seconds": 0, "mode": "sections",
              "episodes": episodes}, headers=LOCAL)
    assert response.status_code == 201, response.text
    version = response.json()["version"]
    confirmed = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans/{version}/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _package(client, studio):
    return client.post(
        f"/api/v1/projects/{studio['project']}/director-packages",
        json={"workflow_profile_id": studio["profile"]["id"], "generation_mode": "Ref2VA"},
        headers=LOCAL)


def test_a_new_script_basis_cannot_package_the_old_plan(api):
    client, factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    first = _document(client, project, "a.txt", b"Scene one. Original basis.")
    outline_a = _approved_outline(factory, project, first)
    _section_plan(client, studio, outline_a, [studio["shots"][0]["id"]])

    built_a = _package(client, studio)
    assert built_a.status_code == 201, built_a.text
    detail_a = client.get(f"/api/v1/director-packages/{built_a.json()['id']}").json()
    assert detail_a["script"]["outline_artifact_id"] == outline_a["id"]
    assert detail_a["episode_plan"]["source"]["outline_artifact_id"] == outline_a["id"]

    second = _document(client, project, "b.txt", b"Scene one. Different script.")
    _approved_outline(factory, project, second)
    switched = client.post(f"/api/v1/projects/{project}/script-basis",
                           json={"document_id": second["id"]}, headers=LOCAL)
    assert switched.status_code == 200, switched.text

    refused = _package(client, studio)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "EPISODE_PLAN_SCRIPT_CHANGED"

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    plan_stage = next(row for row in journey["stages"] if row["id"] == "plan")
    assert plan_stage["done"] is False
    assert "EPISODE_PLAN_SCRIPT_CHANGED" in {row["code"] for row in plan_stage["blockers"]}

    # The historical package of A is still readable and still says A.
    detail_a_again = client.get(f"/api/v1/director-packages/{built_a.json()['id']}").json()
    assert detail_a_again["script"]["document_id"] == first["id"]


def test_the_frozen_manifest_carries_the_plan_source(api):
    """The mixed pair is detectable after the fact, not only at build time."""
    client, factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    doc = _document(client, project, "a.txt", b"Scene one.")
    outline = _approved_outline(factory, project, doc)
    _section_plan(client, studio, outline, [studio["shots"][0]["id"]])
    built = _package(client, studio)
    assert built.status_code == 201, built.text
    detail = client.get(f"/api/v1/director-packages/{built.json()['id']}").json()
    source = detail["episode_plan"]["source"]
    assert source["outline_artifact_id"] == outline["id"]
    assert source["outline_sha256"] == outline["sha256"]
