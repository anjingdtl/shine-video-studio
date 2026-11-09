"""R01: the compatible entry groups shots, but it never re-attributes them.

A plan that groups the shots a project already has used to record no source
at all.  Re-grouping the shots of script A after the person switched to script
B therefore produced a package whose outline was B while its episodes and
shots were A's.  The plan now records the outline its shots really came from,
and every entry resolves the basis in one place.
"""

from __future__ import annotations

from shine.director.package import digest_of
from shine.models import AgentArtifact, AgentTaskRecord, AppSetting, utcnow
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
    project = create_project(client, "分集依据")
    style = freeze_style(client, project)
    subject = upload_asset(client, project, content=PNG + b"-subject", filename="角色.png")
    approve_lock(client, subject["id"])
    created = [create_shot(
        client, project, style["id"], title=f"镜头{index + 1}",
        beat=f"第{index + 1}个动作", asset_ids=[subject["id"]],
        bindings=[{"asset_id": subject["id"], "use_as": "subject",
                   "role": "CHAR_A", "ordinal": 0}],
    ) for index in range(shots)]
    return {"project": project, "style": style, "shots": created, "subject": subject,
            "profile": register_workflow_profile(client, mode="Ref2VA")}


def _document(client, project: str, name: str, text: bytes) -> dict:
    response = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": (name, text, "text/plain")})
    assert response.status_code == 201, response.text
    return response.json()


def _approved_outline(factory, project: str, document: dict, tag: str,
                      sections: int = 1) -> dict:
    rows = [
        {"production_section_id": f"{tag}-s{index + 1}", "order": index + 1,
         "title": f"第{index + 1}场", "estimated_duration_s": 6,
         "start_offset": index * 100, "end_offset": index * 100 + 99}
        for index in range(sections)
    ]
    payload = {"production_sections": rows, "document_id": document["id"],
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
        return {"id": artifact.id, "sha256": artifact.sha256, "sections": rows}


def _apply_storyboard(factory, project: str, outline: dict, shot_ids: list[str],
                      tag: str, section_index: int = 0) -> None:
    """Record the section run and the apply receipt the storyboard apply writes."""
    artifact_id = f"camera-{tag}"
    section_id = outline["sections"][section_index]["production_section_id"]
    with factory.begin() as db:
        db.add(AgentTaskRecord(
            project_id=project, role_id="story_section_run", request_identity=f"section-{tag}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64, rulepack_id="test",
            rulepack_hash="0" * 64, model_profile_id="test", model_id="test", status="SUCCEEDED",
            trace={"outline_artifact_id": outline["id"],
                   "units": [{"production_section_id": section_id, "start_offset": 0}],
                   "results": [{"unit": {"start_offset": 0},
                                "storyboard": {"steps": {"cinematographer":
                                                {"artifact_id": artifact_id}}}}]},
        ))
        db.add(AppSetting(key=f"film-apply:{artifact_id}",
                           value={"shot_ids": list(shot_ids)}))


def _plan(client, project: str, **body) -> dict:
    response = client.post(f"/api/v1/projects/{project}/episode-plans",
                           json={"target_seconds": 60, "tolerance_seconds": 0, **body},
                           headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def _confirm_plan(client, project: str, version: int) -> dict:
    response = client.post(f"/api/v1/projects/{project}/episode-plans/{version}/confirm",
                           json={}, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _package(client, project: str, profile: dict):
    return client.post(f"/api/v1/projects/{project}/director-packages",
                       json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA"},
                       headers=LOCAL)


def _confirmed_package(client, project: str, profile: dict) -> dict:
    built = _package(client, project, profile)
    assert built.status_code == 201, built.text
    confirmed = client.post(f"/api/v1/director-packages/{built.json()['id']}/confirm",
                            json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return built.json()


def _readiness(client, package_id: str) -> dict:
    return client.get(f"/api/v1/director-packages/{package_id}/readiness").json()


def test_regrouping_the_old_scripts_shots_is_not_the_new_scripts_plan(api):
    """A 分组 -> 切到 B -> 重新分组: A 的镜头不能被无声认作 B。"""
    client, factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    first = _document(client, project, "a.txt", b"Scene one. Original basis.")
    outline_a = _approved_outline(factory, project, first, "a")
    _apply_storyboard(factory, project, outline_a, [studio["shots"][0]["id"]], "a")
    chosen = client.post(f"/api/v1/projects/{project}/script-basis",
                         json={"document_id": first["id"]}, headers=LOCAL)
    assert chosen.status_code == 200, chosen.text

    plan = _plan(client, project)
    source = plan["payload"]["source"]
    assert source["basis"] == "shot_apply_receipts", source
    assert source["outline_artifact_id"] == outline_a["id"], source
    _confirm_plan(client, project, plan["version"])
    built = _confirmed_package(client, project, studio["profile"])
    readiness = _readiness(client, built["id"])
    assert readiness["startable"] is True, readiness["blockers"]

    # A second script becomes the basis.
    second = _document(client, project, "b.txt", b"Scene one. Different script.")
    _approved_outline(factory, project, second, "b")
    chosen = client.post(f"/api/v1/projects/{project}/script-basis",
                         json={"document_id": second["id"]}, headers=LOCAL)
    assert chosen.status_code == 200, chosen.text

    regrouped = _plan(client, project)
    assert regrouped["payload"]["source"]["outline_artifact_id"] == outline_a["id"], (
        "the plan must keep the outline its shots really came from")
    _confirm_plan(client, project, regrouped["version"])
    refused = _package(client, project, studio["profile"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "EPISODE_PLAN_SCRIPT_CHANGED"

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    plan_stage = next(row for row in journey["stages"] if row["id"] == "plan")
    assert plan_stage["done"] is False, plan_stage
    assert "EPISODE_PLAN_SCRIPT_CHANGED" in {row["code"] for row in plan_stage["blockers"]}

    # The historical package of A stays readable and still says A.
    again = client.get(f"/api/v1/director-packages/{built['id']}").json()
    assert again["script"]["document_id"] == first["id"]


def test_the_script_first_entry_materialises_the_new_scripts_shots(api):
    """按场次物化: 切到 B 之后，B 的分集仍然能物化并开拍。"""
    client, factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    first = _document(client, project, "a.txt", b"Scene one. Original basis.")
    outline_a = _approved_outline(factory, project, first, "a")
    _apply_storyboard(factory, project, outline_a, [studio["shots"][0]["id"]], "a")
    client.post(f"/api/v1/projects/{project}/script-basis",
                json={"document_id": first["id"]}, headers=LOCAL)

    plan = _plan(client, project, mode="sections")
    assert plan["payload"]["mode"] == "sections"
    assert plan["payload"]["source"]["outline_artifact_id"] == outline_a["id"]
    materialized = client.post(
        f"/api/v1/projects/{project}/episode-plans/{plan['version']}/materialize", headers=LOCAL)
    assert materialized.status_code == 201, materialized.text
    assert materialized.json()["payload"]["episodes"][0]["shot_ids"] == [studio["shots"][0]["id"]]
    _confirm_plan(client, project, materialized.json()["version"])
    built = _confirmed_package(client, project, studio["profile"])
    assert _readiness(client, built["id"])["startable"] is True

    # The second script, its own outline and its own shots.
    second = _document(client, project, "b.txt", b"Scene one. Different script.")
    outline_b = _approved_outline(factory, project, second, "b")
    shot_b = create_shot(client, project, studio["style"]["id"], title="B的镜头",
                         beat="另一个动作", asset_ids=[studio["subject"]["id"]],
                         bindings=[{"asset_id": studio["subject"]["id"], "use_as": "subject",
                                    "role": "CHAR_A", "ordinal": 0}])
    _apply_storyboard(factory, project, outline_b, [shot_b["id"]], "b")
    client.post(f"/api/v1/projects/{project}/script-basis",
                json={"document_id": second["id"]}, headers=LOCAL)

    plan_b = _plan(client, project, mode="sections")
    assert plan_b["payload"]["source"]["outline_artifact_id"] == outline_b["id"]
    materialized_b = client.post(
        f"/api/v1/projects/{project}/episode-plans/{plan_b['version']}/materialize", headers=LOCAL)
    assert materialized_b.status_code == 201, materialized_b.text
    assert materialized_b.json()["payload"]["episodes"][0]["shot_ids"] == [shot_b["id"]]
    _confirm_plan(client, project, materialized_b.json()["version"])
    built_b = _confirmed_package(client, project, studio["profile"])
    assert _readiness(client, built_b["id"])["startable"] is True
    detail_b = client.get(f"/api/v1/director-packages/{built_b['id']}").json()
    assert detail_b["script"]["document_id"] == second["id"]


def test_a_legacy_grouping_row_bridges_on_the_apply_receipts(api):
    """旧行有可信证据则自动衔接: an old row without a source is bridged."""
    client, factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    first = _document(client, project, "a.txt", b"Scene one.")
    outline_a = _approved_outline(factory, project, first, "a")
    _apply_storyboard(factory, project, outline_a, [studio["shots"][0]["id"]], "a")
    client.post(f"/api/v1/projects/{project}/script-basis",
                json={"document_id": first["id"]}, headers=LOCAL)
    plan = _plan(client, project)
    _confirm_plan(client, project, plan["version"])

    # A row written before the source existed: the receipts still prove it.
    from shine.director.models import DirectorEpisodePlan

    with factory.begin() as db:
        row = db.get(DirectorEpisodePlan, plan["id"])
        payload = {**row.payload, "source": {}}
        row.payload = payload
        row.sha256 = digest_of(payload)

    built = _confirmed_package(client, project, studio["profile"])
    readiness = _readiness(client, built["id"])
    assert readiness["startable"] is True, readiness["blockers"]


def test_a_hand_built_shot_list_still_groups_without_a_script(api):
    """无剧本的手工项目保留直接按镜头。"""
    client, factory, _settings = api
    studio = _studio(client, shots=2)
    project = studio["project"]
    plan = _plan(client, project)
    assert plan["payload"]["mode"] == "shots"
    assert plan["payload"]["source"] == {}
    _confirm_plan(client, project, plan["version"])
    built = _confirmed_package(client, project, studio["profile"])
    assert _readiness(client, built["id"])["startable"] is True
