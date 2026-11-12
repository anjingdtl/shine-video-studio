"""The material list must reuse the finished four-stage analysis, not re-buy it.

The crew already read every contracted scene once.  Deriving the requirement
list from those durable artifacts makes zero model calls; a project without
confirmed episode contracts keeps the explicit analysis path.
"""
from __future__ import annotations

from tests.test_four_stage_reacceptance import _confirm_episode, _volume_sections
from tests.test_pipeline import LOCAL, _prepare_handoff_fixture


def test_derive_reuses_the_four_stage_analysis_without_model_calls(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections)

    def forbidden(messages, schema):
        raise AssertionError("deriving the material list must not call the model")

    client.app.state.mock_llm_responder = forbidden
    response = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["model_calls"] == 0
    assert body["source"] == "four_stage_artifacts"
    families = {row["family"] for row in body["requirements"]}
    assert "character" in families and "scene" in families, body["requirements"]
    assert body["expected_snapshot"]

    state = client.get(f"/api/v1/projects/{pid}/material-preparation").json()
    assert state["plan_revision_id"] == body["plan_revision_id"]
    assert state["document_id"] == body["document_id"]
    assert state["requirements"], "the page reads the derived list from the plan"

    # Same input twice reuses the revision instead of opening a second draft.
    again = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert again.status_code == 202
    assert again.json()["replayed"] is True
    assert again.json()["plan_revision_id"] == body["plan_revision_id"]


def test_derive_without_confirmed_episodes_is_refused_by_name(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    response = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert response.status_code == 409
    assert response.json()["detail"] == "MATERIAL_NO_CONFIRMED_EPISODES"