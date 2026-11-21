"""每集秒数的唯一裁决：视频计划（落镜时长之和），不是场景覆盖秒数。

用户报障（2026-11-21）：② 分集书把「场景覆盖 593 秒（与目标差 +533）」当成每集
计划展示，⑥ 集卡沿用同一份错误事实显示「约 593 秒」。修复后每集展示视频计划：
未拆镜时等于目标秒数，拆镜后等于落镜时长之和；覆盖超出只进头部覆盖标识与确认门禁。
"""

from __future__ import annotations

from shine.director import streamline
from shine.director.contracts import planned_seconds
from shine.models import Shot
from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)


def test_episode_seconds_are_the_video_plan_not_scene_coverage(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4, section_seconds=30)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)

    # 未拆镜：每集计划 = 目标；覆盖（2 场 × 30s = 60/集）只进头部覆盖标识。
    for episode in book["preview"]["episodes"]:
        assert episode["planned_seconds"] == 60
        assert episode["deviation_seconds"] == 0
    assert book["preview"]["covered_scope"]["covered_seconds"] == 120

    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    with factory() as db:
        plan = streamline._latest_plan(db, project)
        expected = {}
        coverage = {}
        for episode in plan.payload["episodes"]:
            shots = [db.get(Shot, sid) for sid in episode["shot_ids"]]
            expected[episode["episode_id"]] = round(sum(planned_seconds(s) for s in shots), 3)
            coverage[episode["episode_id"]] = float(episode["planned_seconds"])

    overview = client.get(f"/api/v1/projects/{project}/director/overview").json()
    assert overview["episodes"], "overview must expose the episodes"
    for episode in overview["episodes"]:
        eid = episode["episode_id"]
        assert episode["planned_seconds"] == expected[eid], (eid, episode["planned_seconds"], expected)
        # 落镜后的视频计划与场景覆盖是两份事实；展示必须读前者。
        assert expected[eid] != coverage[eid] or expected[eid] == 60

    refreshed = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 2, "target_seconds": 60, "tolerance_seconds": 30},
        headers=LOCAL).json()
    for episode in refreshed["preview"]["episodes"]:
        assert episode["planned_seconds"] == expected[episode["episode_id"]]