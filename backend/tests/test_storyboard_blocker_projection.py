"""③ 阻塞投影收敛回归（2026-11-19 穿测报障）。

用户实障：第 3 场场记的旧请求进程死在 WAITING_PROVIDER，整集被
AGENT_REQUEST_RECONCILIATION_REQUIRED 挡住；页面折叠行没有镜数、阻塞通知与
下方场次表对不上、右轨只说「还有 9 个场次没有拆成镜头」。三处必须读同一份
投影：逐场表/折叠镜数/阻塞摘要/核对目标（占用身份的旧任务，活版本），右轨
消息包含同一摘要。核对→授权一次重发→重试必须能真解阻。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from shine.agents.execution import AgentRequestBlocked, guard_request
from shine.director import streamline
from shine.models import AgentTaskRecord
from tests.conftest import create_project
from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)


def _dead_holder_and_legacy_rejection(factory, project, episode_id):
    """Rewrite the failed record into the live incident shape: a RUNNING row
    whose process died after the request left (WAITING_PROVIDER), and a legacy
    rejection that names no task at all."""
    with factory.begin() as db:
        run = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id == streamline.DIRECTOR_STORYBOARD_RUN,
        ).order_by(AgentTaskRecord.created_at.desc())).first()
        entry = run.trace["blocked_episodes"][episode_id]
        holder_id = entry["failed_task_id"]
        holder = db.get(AgentTaskRecord, holder_id)
        holder.status = "RUNNING"
        holder.lease_owner = None
        holder.lease_expires_at = None
        holder.trace = {**holder.trace, "phase": "WAITING_PROVIDER",
                        "phase_started_at": "2026-01-01T00:00:00",
                        "network_timeout_s": 600.0,
                        "external_outcome": None, "reconciliation": None}
        # Fresh nested dict: mutating the loaded JSON in place would make the
        # attribute history see old == new and skip the UPDATE entirely.
        entry = {**entry, "rejections": [{"role": holder.role_id,
                                         "code": "AGENT_REQUEST_RECONCILIATION_REQUIRED"}]}
        run.trace = {**run.trace, "blocked_episodes": {
            **run.trace["blocked_episodes"], episode_id: entry}}
        return holder_id, holder.role_id, holder.state_version


def test_blocked_projection_one_fact_for_table_rail_and_reconcile(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    fail_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=fail_section)

    holder_id, holder_role, holder_version = _dead_holder_and_legacy_rejection(
        factory, project, "ep-02")

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    applied = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    blocker = blocked["blocker"]

    # 摘要：镜粒度（已落 0 镜 → 第 1 镜）+场次上下文+工种人话+核对语义，服务端一次算好。
    assert "第 1 镜" in blocker["summary"]
    assert "第 3 场" in blocker["summary"]
    assert "核对" in blocker["summary"]
    # 集→场→镜 同一 join：state 的镜列表与镜头表逐一同序。
    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    assert applied["shot_ids"] == [row["shot_id"] for row in table["shots"]]
    # ③ 继承 ② 的目标秒数：同一裁决函数，偏差具名，不报裸总数。
    assert table["target_seconds"] == 60
    assert table["duration_verdict"] is not None
    assert blocker["uncertain"] is True
    # 核对目标是占用身份的旧任务（legacy 拒绝没带 task_id 时按开放记录命名），
    # 版本必须是活版本，按钮才不会被版本门再拒。
    assert blocker["reconcile_task_id"] == holder_id
    assert blocker["reconcile_state_version"] == holder_version

    # 只有真正失败的单元是 BLOCKED；同集未尝试的单元是排队，不是失败。
    failed_index = blocker["unit_index"]
    for unit in blocked["units"]:
        if unit["index"] == failed_index:
            assert unit["state"] == "BLOCKED"
        else:
            assert unit["state"] == "PENDING", unit
    # 逐场表与折叠镜数读同一份 units：第 3 场被阻塞，第 4 场待生成。
    assert [sec["section_order"] for sec in blocked["sections"]] == [3, 4]
    assert blocked["sections"][0]["state"] == "BLOCKED"
    assert blocked["sections"][1]["state"] == "PENDING"
    assert blocked["pending_section_count"] == 2
    assert blocked["landed_shot_count"] == 0
    assert applied["pending_section_count"] == 0
    assert all(sec["state"] == "APPLIED" for sec in applied["sections"])
    assert applied["landed_shot_count"] == len(applied["shot_ids"]) > 0

    # 右轨与面板同故事：旅程阻塞消息包含同一摘要与同一覆盖数。
    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    message = stage["blockers"][0]["message"]
    assert blocker["summary"] in message
    assert "还有 2 个场次没有拆成镜头" in message

    # 核对 → 授权一次重发 → 重试：真解阻，跑完。
    checked = client.post(f"/api/v1/projects/{project}/film-runs/{holder_id}/reconcile",
                          json={"expected_state_version": holder_version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    assert checked.json()["outcome"] == "UNKNOWN_REQUIRES_NEW_ATTEMPT"
    resent = client.post(f"/api/v1/projects/{project}/film-runs/{holder_id}/resend-unknown",
                         json={"expected_state_version": checked.json()["state_version"]},
                         headers=LOCAL)
    assert resent.status_code == 200, resent.text
    retry = client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
                        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    final = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert final["status"] == "SUCCEEDED", final
    assert all(row["status"] == "APPLIED" for row in final["episodes"])


def _unknown_provider_holder_and_rejection(factory, project, episode_id):
    """The live 2026-11-21 incident shape: a settled NEEDS_REVIEW crew call whose
    stream broke mid-answer (LLM_CONNECTION, unknown outcome), and a rejection
    that names the task but is NOT a pre-check reconciliation code."""
    with factory.begin() as db:
        run = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id == streamline.DIRECTOR_STORYBOARD_RUN,
        ).order_by(AgentTaskRecord.created_at.desc())).first()
        entry = run.trace["blocked_episodes"][episode_id]
        holder_id = entry["failed_task_id"]
        holder = db.get(AgentTaskRecord, holder_id)
        holder.status = "NEEDS_REVIEW"
        holder.error_code = "LLM_CONNECTION"
        holder.lease_owner = None
        holder.lease_expires_at = None
        holder.trace = {**holder.trace, "phase": "WAITING_PROVIDER",
                        "external_outcome": "UNKNOWN", "reconciliation": None,
                        "network_timeout_s": 300.0}
        entry = {**entry, "rejections": [{
            "role": holder.role_id, "code": "LLM_CONNECTION",
            "rejection_reason_code": "LLM_CONNECTION", "task_id": holder_id,
            "state_version": holder.state_version, "external_outcome": "UNKNOWN",
            "network_timeout_s": 300.0, "phase": "WAITING_PROVIDER"}]}
        run.trace = {**run.trace, "blocked_episodes": {
            **run.trace["blocked_episodes"], episode_id: entry}}
        return holder_id, holder.state_version


def test_a_plain_unknown_stream_block_names_its_reconcile_target(api):
    """穿测活缺陷（2026-11-21）：普通断流 UNKNOWN（LLM_CONNECTION，非预检核对码）
    的阻塞集，投影曾把 reconcile_task_id 留空、版本留 0；顶栏「核对并重试被阻塞
    的集」拿 failed_task_id 凑目标却配版本 0，被 reconcile 的版本门拒成
    FILM_AGENT_TASK_STATE_CHANGED，按钮看着能点实则必拒。修复落在唯一投影口：
    凡 uncertain 的拒绝都解析核对目标（rejection.task_id + 数据库活版本）。"""
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    fail_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=fail_section)

    holder_id, holder_version = _unknown_provider_holder_and_rejection(factory, project, "ep-02")
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    blocker = blocked["blocker"]
    assert blocker["uncertain"] is True
    # 核对目标与活版本必须被解析出来，顶栏按钮才不会被版本门再拒。
    assert blocker["reconcile_task_id"] == holder_id
    assert blocker["reconcile_state_version"] == holder_version

    # 核对 → 授权一次重发 → 重试：真解阻。
    checked = client.post(f"/api/v1/projects/{project}/film-runs/{holder_id}/reconcile",
                          json={"expected_state_version": blocker["reconcile_state_version"]},
                          headers=LOCAL)
    assert checked.status_code == 200, checked.text
    assert checked.json()["outcome"] == "UNKNOWN_REQUIRES_NEW_ATTEMPT"
    resent = client.post(f"/api/v1/projects/{project}/film-runs/{holder_id}/resend-unknown",
                         json={"expected_state_version": checked.json()["state_version"]},
                         headers=LOCAL)
    assert resent.status_code == 200, resent.text
    retry = client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
                        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    final = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert final["status"] == "SUCCEEDED", final


def test_book_scope_is_governed_by_the_confirmed_target(api):
    """2026-11-19 用户裁定：1 集 60 秒只覆盖前几场；超范围显式声明，run 不越界。"""
    client, factory, settings = api
    prepared = _prepare_project(client, sections=6)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    preview = book["preview"]
    assert preview["episode_count"] == 1
    assert preview["episodes"][0]["section_count"] == 2
    assert preview["out_of_scope"]["count"] == 4
    assert "不在本次 1 集范围内" in preview["out_of_scope"]["note"]
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["total_units"] == 2
    assert [sec["section_order"] for sec in state["episodes"][0]["sections"]] == [1, 2]
    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    assert "还有 2 个场次没有拆成镜头" in stage["blockers"][0]["message"]


def test_an_overrunning_episode_is_confirmed_whole_with_its_deviation(api):
    """V4 废止「取最接近目标的前缀」：② 确认的秒数由戏剧层在写作时控制体量，
    落镜超出容差窗时整集确认并如实记录偏差，不砍尾、不把本集的场次改成超范围。
    ② 阶段定下的范围（sec3-4 不在本次 1 集内）原样保留。"""
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4, section_seconds=10)
    project = prepared["project"]
    book = client.post(f"/api/v1/projects/{project}/director/storyboard-book",
                       json={"episode_count": 1, "target_seconds": 20, "tolerance_seconds": 5},
                       headers=LOCAL).json()
    # 装箱：sec1+sec2=20s 进第一集；sec3-4 超上限声明超范围。
    assert book["preview"]["episodes"][0]["section_count"] == 2
    assert book["preview"]["out_of_scope"]["count"] == 2
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)
    # 每单元落 2 镜(7+6=13s)，两单元 26s > 窗[15,25] → 超窗，偏差如实可见。
    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    assert table["duration_verdict"]["deviation_seconds"] == 6.0
    assert "target_fit" not in table
    plans = client.get(f"/api/v1/projects/{project}/episode-plans").json()
    latest = next(p for p in plans if p["status"] == "CONFIRMED")
    episode = latest["payload"]["episodes"][0]
    # The whole episode is in the confirmed plan: both scenes, all four shots.
    assert len(episode["shot_ids"]) == len(table["shots"]) == 4
    assert episode["section_ids"] == ["sec-01", "sec-02"]
    # ② 阶段的范围声明原样保留：确认不会把本集自己的场次改成超范围。
    assert sorted(latest["payload"]["out_of_scope_section_ids"]) == ["sec-03", "sec-04"]

    # The person re-confirming by hand is still told the deviation by name.
    refused = client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
                          json={}, headers=LOCAL)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "EPISODE_DURATION_OUT_OF_TARGET"
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    assert len(confirmed.json()["shot_ids"]) == 4


def test_guard_refusal_carries_the_blocking_row(api):
    client, factory, settings = api
    project = create_project(client, "门禁携带旧任务")
    with factory.begin() as db:
        row = AgentTaskRecord(
            project_id=project, role_id="director", request_identity="ident-1",
            input_hash="hash-1", asset_manifest_hash="0" * 64,
            rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="RUNNING",
            trace={"phase": "WAITING_PROVIDER"},
        )
        db.add(row)
        db.flush()
        profile = SimpleNamespace(kind="mock", model="m", base_url="http://127.0.0.1:9")
        with pytest.raises(AgentRequestBlocked) as exc:
            guard_request(db, project, "director", "hash-1", profile)
        assert str(exc.value) == "AGENT_REQUEST_RECONCILIATION_REQUIRED"
        assert exc.value.blocking["task_id"] == row.id
        assert exc.value.blocking["status"] == "RUNNING"
        assert exc.value.blocking["phase"] == "WAITING_PROVIDER"
