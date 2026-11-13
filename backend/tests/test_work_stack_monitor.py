"""工作栈监控与「关掉某一个栈」的最小契约。

监控是只读投影：它只回答「这台机器现在有哪些后台单元、各自什么状态」，
不会启动、停止或加载任何东西。定向关闭仍走同一条证据链：只有本工作台
拥有、且没有在出片的进程才会被关闭。
"""

from __future__ import annotations

from shine.comfyops import renderer_release
from shine.models import AppSetting


def _record(instance_id: str, *, prefix: str = "renderer-startup:", **extra) -> dict:
    return {
        "key": f"{prefix}{instance_id}",
        "prefix": prefix,
        "instance_id": instance_id,
        "stage": "STOPPED",
        "port": 8188,
        "recorded_at": "2026-11-01T00:00:00",
        "installation": {"id": f"install-{instance_id}", "sha256": "a" * 64, "attempt_id": "attempt-1"},
        **extra,
    }


def test_work_stack_snapshot_is_read_only(api):
    client, _factory, _settings = api
    response = client.get("/api/v1/setup/work-stacks")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stacks"] == []
    assert body["counts"]["stacks"] == 0
    assert body["counts"]["working_stacks"] == 0
    assert body["counts"]["historical_stacks"] == 0
    # 没有任何进程时也要如实给出 0，而不是编一个「在跑」。
    assert body["counts"]["running_jobs"] == 0


def test_stopped_records_are_history_not_working_stacks(api):
    client, factory, _settings = api
    with factory.begin() as db:
        db.add(AppSetting(key="renderer-startup:instance-1", value={
            "instance_id": "instance-1", "stage": "STOPPED", "port": 8188,
            "recorded_at": "2026-11-01T00:00:00",
        }))
    body = client.get("/api/v1/setup/work-stacks").json()
    assert len(body["stacks"]) == 1
    row = body["stacks"][0]
    assert row["historical"] is True
    assert row["working"] is False
    assert row["state"] == "STOPPED"
    assert body["counts"]["historical_stacks"] == 1


def test_targeted_release_only_names_the_requested_stack(monkeypatch, api):
    _client, factory, _settings = api
    records = [
        _record("instance-a"),
        _record("vlm-service", prefix="review-startup:", service_id="vlm-service"),
    ]
    monkeypatch.setattr(renderer_release, "managed_processes", lambda db: records)
    with factory() as db:
        assert renderer_release.installation_ids_of_managed_processes(db) == [
            "install-instance-a", "install-vlm-service",
        ]
        assert renderer_release.installation_ids_of_managed_processes(
            db, only={"review:vlm-service"},
        ) == ["install-vlm-service"]


def test_record_key_matches_the_monitor_row_id():
    assert renderer_release._record_key(_record("instance-a")) == "renderer:instance-a"
    assert renderer_release._record_key(
        _record("vlm-service", prefix="review-startup:", service_id="vlm-service"),
    ) == "review:vlm-service"
