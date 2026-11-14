from __future__ import annotations

from shine.providers import MockH3Provider
from shine.security import sanitize_payload
from shine.worker import run_one
from tests.conftest import (
    PNG,
    approve_lock,
    create_job,
    create_project,
    create_shot,
    freeze_style,
    shot_payload,
    upload_asset,
)


def test_project_audit_is_complete_filterable_and_paginated(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client, "审计项目")
    asset = upload_asset(client, project_id, PNG + b"audit", "审计.png")
    approve_lock(client, asset["id"])
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    run_one(factory, MockH3Provider(), "worker-audit")
    client.post(
        f"/api/v1/shots/{shot['id']}/revise",
        json=shot_payload(style["id"], title=shot["name"], beat="审计后修改")["spec"],
    )

    page = client.get(f"/api/v1/projects/{project_id}/events?limit=4").json()
    assert page["total"] >= 10
    assert page["limit"] == 4 and page["offset"] == 0
    assert len(page["items"]) == 4
    assert all(item["project_id"] == project_id for item in page["items"])
    second = client.get(f"/api/v1/projects/{project_id}/events?limit=4&offset=4").json()
    assert {item["id"] for item in page["items"]}.isdisjoint({item["id"] for item in second["items"]})

    asset_events = client.get(
        f"/api/v1/projects/{project_id}/events?entity_type=asset&entity_id={asset['id']}"
    ).json()
    assert asset_events["total"] >= 2
    assert all(item["entity_id"] == asset["id"] for item in asset_events["items"])
    types = {item["event_type"] for item in asset_events["items"]}
    assert {"ASSET_UPLOADED", "ASSET_APPROVED", "ASSET_LOCKED"}.issubset(types)

    job_events = client.get(f"/api/v1/projects/{project_id}/events?entity_type=job").json()
    assert any(item["entity_id"] == job["id"] for item in job_events["items"])
    assert any(item["event_type"] == "MOCK_WORKFLOW_PASSED" for item in job_events["items"])
    shot_events = client.get(f"/api/v1/projects/{project_id}/events?entity_type=shot").json()
    assert any(item["event_type"] == "SHOT_REVISED" for item in shot_events["items"])




def test_audit_rows_name_the_thing_they_are_about(studio):
    """D-R4-03：实体列曾只显示 8 位 id 片段，谁也读不懂那一串是谁。

    审计记录要能回答「对什么做了什么」：项目名、素材文件名这类能读的名字才有意义。
    拿不到名字的类型（这里是没有名字字段的生成任务）保留 id 供排查，不编造名字。
    """
    client, _factory, _root, _settings = studio
    project_id = create_project(client, "雾港")
    asset = upload_asset(client, project_id, PNG + b"named", "雾港夜景.png")
    approve_lock(client, asset["id"])

    asset_items = client.get(
        f"/api/v1/projects/{project_id}/events?entity_type=asset"
    ).json()["items"]
    assert asset_items, "素材应该留下审计记录"
    assert "雾港夜景.png" in {item["entity_label"] for item in asset_items}

    project_items = client.get(
        f"/api/v1/projects/{project_id}/events?entity_type=project"
    ).json()["items"]
    assert "雾港" in {item["entity_label"] for item in project_items}

    job_items = client.get(
        f"/api/v1/projects/{project_id}/events?entity_type=job"
    ).json()["items"]
    assert all(item["entity_label"] is None for item in job_items)
def test_audit_payloads_never_store_absolute_paths_or_credentials(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    upload_asset(client, project_id, PNG + b"private", "私有素材.png")
    events = client.get(f"/api/v1/projects/{project_id}/events?limit=500").json()["items"]
    serialized = str(events)
    assert "C:\\" not in serialized and "E:\\" not in serialized
    assert ".local-data" not in serialized
    assert "storage_path" not in serialized


def test_sanitize_payload_redacts_keys_and_secret_values():
    cleaned = sanitize_payload(
        {
            "api_key": "sk-abcdefgh12345678",
            "nested": {"authorization": "Bearer abcdefgh123", "path": "C:\\Users\\person\\secret.png"},
            "message": "use sk-abcdefgh12345678 now",
            "safe": 1,
        }
    )
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["nested"]["authorization"] == "[redacted]"
    assert cleaned["nested"]["path"] == "[local-path]"
    assert "sk-" not in cleaned["message"]
    assert cleaned["safe"] == 1


def test_events_for_another_project_cannot_be_read(studio):
    client, _factory, _root, _settings = studio
    first = create_project(client, "甲")
    second = create_project(client, "乙")
    create_project(client, "丙")
    response = client.get(f"/api/v1/projects/{first}/events")
    assert response.status_code == 200
    assert all(item["project_id"] == first for item in response.json()["items"])
    assert client.get("/api/v1/projects/missing/events").status_code == 404
    del second
