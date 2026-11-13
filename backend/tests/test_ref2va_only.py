"""The project renders Ref2VA only; text-only T2VA is refused by name."""

from __future__ import annotations

import pytest

from shine.h3.mode_policy import REQUIRED_GENERATION_MODE, ModePolicyError, require_project_mode
from shine.schemas import CreateJob, GenerationMode
from shine.services import make_render_request
from tests.conftest import create_project, create_shot, freeze_style

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}


def _shot_without_references(client):
    project_id = create_project(client, "只走 Ref2VA")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"], beat="A man waits by a window.")
    return project_id, shot


def test_policy_accepts_only_ref2va():
    require_project_mode(REQUIRED_GENERATION_MODE)
    for other in (GenerationMode.T2VA, GenerationMode.I2VA,
                  GenerationMode.FL2VA, GenerationMode.L2VA):
        with pytest.raises(ModePolicyError) as excinfo:
            require_project_mode(other)
        assert excinfo.value.code == "PROJECT_REQUIRES_REF2VA"


def test_a_real_render_without_references_is_refused(studio):
    client, factory, _tmp, _settings = studio
    project_id, shot = _shot_without_references(client)

    with factory() as db:
        from shine.models import Shot

        shot_row = db.get(Shot, shot["id"])
        with pytest.raises(Exception) as excinfo:
            make_render_request(
                db, shot_row.project, shot_row,
                CreateJob(shot_id=shot["id"]),
                require_ref2va=True,
            )
        detail = getattr(excinfo.value, "detail", None)
        assert isinstance(detail, dict)
        assert detail["code"] == "PROJECT_REQUIRES_REF2VA"
        assert detail["inferred_mode"] == "T2VA"
        assert detail["required_mode"] == "Ref2VA"


def test_a_frozen_text_only_prompt_cannot_be_saved(studio):
    client, _factory, _tmp, _settings = studio
    project_id, shot = _shot_without_references(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt",
        headers=LOCAL,
        json={"prompt": "integrated_multimodal_description:\nA man waits.",
              "mode": "T2VA", "freeze": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PROJECT_REQUIRES_REF2VA"


def test_the_prompt_preview_says_why_it_is_blocked(studio):
    client, _factory, _tmp, _settings = studio
    project_id, shot = _shot_without_references(client)
    preview = client.get(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["prompt"] == ""
    assert body["blocked"]["code"] == "PROJECT_REQUIRES_REF2VA"
