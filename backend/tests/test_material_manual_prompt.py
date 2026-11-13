"""A hand-written material prompt replaces the producer's brief."""

from __future__ import annotations

import pytest

from shine.comfyops.models import PLAN_PURPOSE_ASSET_PREPARATION, AssetRequirement, PreProductionPlan
from shine.materialprep.execution import _compose_prompt
from tests.conftest import create_project

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}

PROMPT = (
    "A character reference photograph for a cinematic narrative production: one person, "
    "framed from the waist up, standing against a plain neutral grey-white studio background. "
    "The subject is a woman in her thirties wearing a deep navy wool coat. "
    "The lighting is soft and diffused from the front. "
    "The overall composition is calm and symmetrical, holding the face as the single focus."
)


def _requirement(factory, project_id: str) -> str:
    with factory() as db:
        plan = PreProductionPlan(
            project_id=project_id, purpose=PLAN_PURPOSE_ASSET_PREPARATION,
            payload={"visual_direction": {}}, sha256="a" * 64, status="CONFIRMED",
        )
        db.add(plan)
        db.flush()
        row = AssetRequirement(
            plan_id=plan.id, project_id=project_id,
            payload={"family": "character", "entity_id": "Lin",
                     "description": "主角：三十多岁的女人", "critical": True},
            sha256="b" * 64, status="DRAFT",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_empty_requirement_can_be_written_and_frozen(studio):
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "素材提示词")
    requirement_id = _requirement(factory, project_id)
    base = f"/api/v1/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt"

    empty = client.get(base)
    assert empty.status_code == 200, empty.text
    assert empty.json()["source"] == "empty"
    assert empty.json()["blocked"] == "MATERIAL_BRIEF_NOT_GENERATED"

    frozen = client.put(base, headers=LOCAL, json={"prompt": PROMPT, "negative_prompt": ""})
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["frozen"]["sha256"]

    read_back = client.get(base)
    assert read_back.json()["source"] == "frozen"
    assert read_back.json()["prompt"] == PROMPT
    assert read_back.json()["warnings"] == []


def test_worker_uses_the_frozen_prompt_without_asking_for_a_brief(studio):
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "素材提示词执行")
    requirement_id = _requirement(factory, project_id)
    base = f"/api/v1/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt"
    assert client.put(base, headers=LOCAL, json={"prompt": PROMPT}).status_code == 200

    with factory() as db:
        row = db.get(AssetRequirement, requirement_id)
        context = {
            "item_id": "item-1",
            "requirement_payload": dict(row.payload),
            "item_payload": {},
        }
        prompt = _compose_prompt(context)
    assert prompt.template_version == "manual-frozen-v1"
    assert prompt.prompt == PROMPT
    assert prompt.sha256 == row.payload["manual_prompt"]["sha256"]


def test_unfreezing_returns_the_requirement_to_the_brief(studio):
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "素材提示词解冻")
    requirement_id = _requirement(factory, project_id)
    base = f"/api/v1/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt"
    assert client.put(base, headers=LOCAL, json={"prompt": PROMPT}).status_code == 200

    removed = client.delete(base, headers=LOCAL)
    assert removed.status_code == 200, removed.text
    assert removed.json()["unfrozen"] is True
    assert client.get(base).json()["source"] == "empty"


def test_quality_slogans_are_advisory_only(studio):
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "素材提示词口号")
    requirement_id = _requirement(factory, project_id)
    base = f"/api/v1/projects/{project_id}/director/materials/requirements/{requirement_id}/prompt"
    body = {"prompt": "masterpiece, 8k, highly detailed, a woman in a navy coat walking.",
            "negative_prompt": ""}
    response = client.put(base, headers=LOCAL, json=body)
    assert response.status_code == 200, response.text
    assert any("质量口号" in warning for warning in response.json()["warnings"])
