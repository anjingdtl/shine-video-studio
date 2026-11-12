"""The material list derives from one project-wide entity consolidation.

The crew already read every contracted scene; a single catalog call merges
aliases into one entry per real character (with per-scene variants) and picks
key scenes/props.  An unchanged basis reuses the catalog; an unknown outcome
is reconciled or explicitly resent, never replayed automatically.
"""
from __future__ import annotations

from tests.test_four_stage_reacceptance import _confirm_episode, _volume_sections
from tests.test_pipeline import LOCAL, _prepare_handoff_fixture, _route_mock


def _catalog(sections):
    return {
        "schema_version": "1.0",
        "characters": [{
            "name": "德明", "aliases": ["阿明"], "identity": "出海的男人，短发。",
            "variants": [{"section_id": sections[0], "appearance": "披着旧雨衣。"}],
            "section_ids": list(sections),
        }],
        "scenes": [{"name": "码头", "description": "石砌码头，夜灯。", "section_ids": list(sections[:1])}],
        "props": [{"name": "铜怀表", "description": "刻字怀表。", "carrier": "object",
                   "core_prop_reason": "表盖刻字是线索。", "section_ids": list(sections[:1])}],
    }


def test_derive_consolidates_identities_and_reuses_the_catalog(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections)
    _route_mock(client, "producer")
    calls = []

    def catalog_responder(messages, schema):
        calls.append(1)
        return _catalog(sections)

    client.app.state.mock_llm_responder = catalog_responder
    response = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert response.status_code == 202, response.text
    body = response.json()
    assert len(calls) == 1, "one project-wide consolidation call"
    by_name = {row["entity_id"]: row for row in body["requirements"]}
    assert by_name["德明"]["family"] == "character"
    assert by_name["码头"]["family"] == "scene"
    assert by_name["铜怀表"]["family"] == "prop"
    assert by_name["铜怀表"]["core_prop_reason"] == "表盖刻字是线索。"
    assert "阿明" not in by_name, "aliases never become a second master"

    again = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert again.status_code == 202
    assert again.json()["replayed"] is True
    assert len(calls) == 1, "an unchanged basis buys no second call"


def test_derive_without_confirmed_episodes_is_refused_by_name(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    response = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert response.status_code == 409
    assert response.json()["detail"] == "MATERIAL_NO_CONFIRMED_EPISODES"


def test_an_unknown_catalog_is_reconciled_or_resent_once_never_replayed(api):
    client, factory, settings, pid, plan, volume_id, units = _prepare_handoff_fixture(api)
    sections = _volume_sections(factory, pid, volume_id)
    _confirm_episode(client, pid, volume_id, "第一集", sections)
    _route_mock(client, "producer")

    def lost(messages, schema):
        raise TimeoutError("response lost")

    client.app.state.mock_llm_responder = lost
    refused = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "MATERIAL_CATALOG_OUTCOME_UNKNOWN"
    task_id, version = detail["task_id"], detail["state_version"]

    # A second derive must not replay the unknown request.
    replay = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "MATERIAL_CATALOG_OUTCOME_UNKNOWN"

    checked = client.post(
        f"/api/v1/projects/{pid}/material-preparation/scenes/{task_id}/reconcile",
        json={"expected_state_version": version}, headers=LOCAL)
    assert checked.status_code == 200
    assert checked.json()["outcome"] == "UNCONFIRMABLE"

    resent = client.post(
        f"/api/v1/projects/{pid}/material-preparation/scenes/{task_id}/resend-unknown",
        json={"expected_state_version": checked.json()["state_version"],
              "evidence": "用户确认对统揽任务发起一次新请求"}, headers=LOCAL)
    assert resent.status_code == 200, resent.text
    assert resent.json()["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"

    client.app.state.mock_llm_responder = lambda m, s: _catalog(sections)
    ok = client.post(f"/api/v1/projects/{pid}/material-preparation/derive", headers=LOCAL)
    assert ok.status_code == 202, ok.text