"""Novel → multi-episode autopilot: the P0 closure regressions.

Covers the three production blockers fixed this round:
* a bare volume/chapter title is never a five-role creation unit (it is merged
  into an adjacent narrative fragment, coverage preserved, no double-count);
* a truncated answer (finish_reason=length) gets one bounded budget escalation
  toward the verified ceiling, never an unbounded max_tokens;
* a schema refusal records the failing field paths (no provider text) and an
  episode materialises itself once all its units landed.
"""

from __future__ import annotations

from shine.literary.budget import plan_call_budget
from shine.literary.requests import compile_request
from shine.llm.base import ProviderConfig
from shine.materialprep.source_spans import (
    is_narrative_fragment,
    merge_non_narrative_fragments,
    narrative_body,
)
from tests.test_director_streamline import (
    LOCAL,
    _approved_outline,
    _confirm_book,
    _document,
    _run_storyboard_to_rest,
    _storyboard_book,
)

# ---------------------------------------------------------------------------
# WP1 · narrative validity and deterministic merge (pure)
# ---------------------------------------------------------------------------


def test_narrative_body_strips_headings_and_markers():
    assert narrative_body("第一卷 邪月之歌\n") == ""
    assert narrative_body("第0001章 从今天开始做王子\n") == ""
    assert narrative_body("第1场 夜 钟表铺\n老周修好怀表。") == "老周修好怀表。"
    # A preamble title is not a recognised marker, but it is below the narrative
    # threshold, so it still merges as a non-narrative fragment.
    assert narrative_body("前言与人物设定\n") == "前言与人物设定"
    assert not is_narrative_fragment("前言与人物设定\n")


def test_is_narrative_fragment_threshold():
    assert not is_narrative_fragment("第一卷 邪月之歌\n")
    assert not is_narrative_fragment("第0001章 标题\n\n")
    assert is_narrative_fragment("第0001章 标题\n老周把怀表贴近耳朵，又放回丝绒垫上。客人走进雨里。")


def _frag(section, start, end, text, *, episode="ep-01", order=1):
    return {"episode_id": episode, "production_section_id": section,
            "section_order": order, "start_offset": start, "end_offset": end,
            "text": text}


def test_merge_absorbs_a_title_only_section_forward():
    title = "第一卷 邪月之歌\n"
    body = "第0001章 王子\n老周把怀表贴近耳朵，又放回丝绒垫上。客人留下硬币，走进雨里。"
    fragments = [
        _frag("sec-01", 0, len(title), title, order=1),
        _frag("sec-02", len(title), len(title) + len(body), body, order=2),
    ]
    units = merge_non_narrative_fragments(fragments)
    assert len(units) == 1
    unit = units[0]
    assert unit["production_section_id"] == "sec-02"
    assert unit["merged_section_ids"] == ["sec-01"]
    assert unit["start_offset"] == 0
    assert unit["end_offset"] == len(title) + len(body)
    assert unit["text"] == title + body  # coverage exact, no dropped char


def test_merge_absorbs_a_trailing_title_backward():
    body = "第0001章 王子\n老周把怀表贴近耳朵，又放回丝绒垫上。客人留下硬币，走进雨里。"
    tail = "第0002章 标题\n"
    fragments = [
        _frag("sec-01", 0, len(body), body, order=1),
        _frag("sec-02", len(body), len(body) + len(tail), tail, order=2),
    ]
    units = merge_non_narrative_fragments(fragments)
    assert len(units) == 1
    assert units[0]["production_section_id"] == "sec-01"
    assert units[0]["merged_section_ids"] == ["sec-02"]
    assert units[0]["text"] == body + tail


def test_merge_keeps_intra_section_title_with_its_body():
    # A scene heading followed by its prose is one narrative fragment already;
    # a standalone heading fragment inside the same section merges without
    # claiming a different section.
    head = "第2场 日 码头\n"
    body = "老周登上船，回头望了一眼钟表铺，雨还在下。"
    fragments = [
        _frag("sec-01", 0, len(head), head),
        _frag("sec-01", len(head), len(head) + len(body), body),
    ]
    units = merge_non_narrative_fragments(fragments)
    assert len(units) == 1
    assert units[0]["merged_section_ids"] == []  # same section, no cross-merge
    assert units[0]["text"] == head + body


def test_merge_refuses_an_isolated_title_by_flag():
    # No adjacent narrative: the fragment stays its own unit, flagged, so the
    # caller refuses it by name instead of inventing plot.
    fragments = [_frag("sec-01", 0, 12, "第一卷 邪月之歌\n")]
    units = merge_non_narrative_fragments(fragments)
    assert len(units) == 1
    assert units[0]["non_narrative"] is True


def test_merge_preserves_coverage_across_many_fragments():
    title = "第一卷\n"
    b1 = "第0001章\n老周修好怀表，客人走进雨里。"
    b2 = "第0002章\n第二天客人消失了，桌上留下一枚硬币。"
    fragments = [
        _frag("s0", 0, len(title), title, order=1),
        _frag("s1", len(title), len(title) + len(b1), b1, order=2),
        _frag("s2", len(title) + len(b1), len(title) + len(b1) + len(b2), b2, order=3),
    ]
    units = merge_non_narrative_fragments(fragments)
    assert units[0]["start_offset"] == 0
    assert units[-1]["end_offset"] == len(title) + len(b1) + len(b2)
    assert "".join(u["text"] for u in units) == title + b1 + b2


# ---------------------------------------------------------------------------
# WP1/WP2 · bounded output-budget escalation
# ---------------------------------------------------------------------------


def _config(max_output=131_072, window=1_000_000, configured=32_768):
    return ProviderConfig(
        profile_id="p", name="p",
        kind="openai_compatible", base_url="https://api.minimax.cn/v1",
        model="MiniMax-M3.1-Flash", api_key="x",
        context_window_tokens=window, max_output_tokens=max_output,
        generation_options={"max_completion_tokens": configured},
    )


def test_output_floor_raises_budget_toward_verified_cap_not_beyond():
    request = compile_request(system="s", user="u" * 3000, schema={"type": "object"})
    base = plan_call_budget(_config(), request, purpose="structured", output_ceiling=32_768)
    escalated = plan_call_budget(_config(), request, purpose="structured",
                                 output_ceiling=32_768, output_floor_tokens=32_768)
    assert escalated.output_request_tokens > base.output_request_tokens
    # The hard cap is 60% of the configured 32768 = 19660; the floor never exceeds it.
    assert escalated.output_request_tokens == base.output_cap_tokens
    assert escalated.output_request_tokens <= 19_660
    assert escalated.fits


def test_output_floor_cannot_exceed_a_small_verified_cap():
    # A model that only declares 8192 output: the floor is clamped to its hard cap.
    request = compile_request(system="s", user="u" * 1000, schema={"type": "object"})
    escalated = plan_call_budget(_config(max_output=8_192, configured=8_192), request,
                                 purpose="structured", output_ceiling=8_192,
                                 output_floor_tokens=1_000_000)
    assert escalated.output_request_tokens <= escalated.output_cap_tokens
    assert escalated.output_cap_tokens == int(8_192 * 0.60)


# ---------------------------------------------------------------------------
# WP1/WP3 · integration: a title-only section merges and the episode self-confirms
# ---------------------------------------------------------------------------


def _prepare_with_title_section(client):
    from tests.test_film_crew import prepare

    project_id, _style, _profile = prepare(
        client, freeze=False, character_entity_id="老周", character_filename="老周.png")
    title = "第一卷 邪月之歌\n"
    body = ("第0001章 从今天开始做王子\n老周把怀表贴近耳朵，又放回丝绒垫上。"
            "客人留下硬币，走进雨里。")
    body = body + "。" * (300 - len(body) - 1) + "\n"
    text = title + body
    document = _document(client, project_id, "邪月之歌.txt", text)
    sections = [
        {"production_section_id": "sec-title", "order": 1, "title": "第一卷 邪月之歌",
         "estimated_duration_s": 30.0, "start_offset": 0, "end_offset": len(title)},
        {"production_section_id": "sec-body", "order": 2, "title": "第0001章 从今天开始做王子",
         "estimated_duration_s": 30.0, "start_offset": len(title), "end_offset": len(text)},
    ]
    outline = _approved_outline(client.app.state.session_factory, project_id, document, sections)
    return {"project": project_id, "document": document, "outline": outline}


def test_title_only_section_merges_and_episode_self_confirms(api):
    client, factory, settings = api
    prepared = _prepare_with_title_section(client)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                         json={}, headers=LOCAL)
    assert queued.status_code == 202, queued.text

    # The run holds ONE merged unit: the bare volume title joined its chapter.
    from shine.models import AgentTaskRecord
    with factory() as db:
        run = db.get(AgentTaskRecord, queued.json()["task_id"])
        units = run.trace["units"]
    assert len(units) == 1, units
    assert units[0]["production_section_id"] == "sec-body"
    assert units[0]["merged_section_ids"] == ["sec-title"]
    assert units[0]["start_offset"] == 0

    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state

    # WP3: the episode materialised itself — no manual per-episode confirm.
    from shine.director import streamline
    with factory() as db:
        plan = streamline._latest_plan(db, project)
        episode = plan.payload["episodes"][0]
    assert episode["shot_ids"], "the episode auto-confirms once its units land"

    # The merged title section is satisfied, not pending, and adds no duplicate.
    from shine.materialprep.story import section_storyboard_shot_ids
    with factory() as db:
        merged = section_storyboard_shot_ids(db, project, "sec-title")
        owner = section_storyboard_shot_ids(db, project, "sec-body")
    assert merged["merged_into"] == "sec-body"
    assert merged["shot_ids"] == []
    assert owner["shot_ids"] == episode["shot_ids"]

    # An explicit confirm reuses the same shots (idempotent, no double-count).
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["shot_ids"] == episode["shot_ids"]


def test_episode_self_confirms_without_manual_click(api):
    from tests.test_director_streamline import _prepare_project

    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    from shine.director import streamline
    with factory() as db:
        plan = streamline._latest_plan(db, project)
    by_id = {e["episode_id"]: e for e in plan.payload["episodes"]}
    assert by_id["ep-01"]["shot_ids"], "ep-01 self-confirms"
    assert by_id["ep-02"]["shot_ids"], "ep-02 self-confirms"


def test_retry_accepts_a_post_response_fault_without_explicit_outcome(api):
    """A FAILED task whose answer arrived (received_result_sha256) but whose
    external_outcome was never stamped (a post-processing fault) is a decided
    refusal, not an UNKNOWN: the episode retry must authorize one new call
    instead of demanding reconciliation."""
    from tests.test_director_streamline import _prepare_project
    from shine.models import AgentTaskRecord

    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"
    failed_task_id = blocked["blocker"]["failed_task_id"]

    # Simulate the generic post-response fault path: the answer arrived, but the
    # task is FAILED with no explicit external_outcome (only the result hash).
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, failed_task_id)
        task.status = "FAILED"
        task.trace = {**task.trace, "external_outcome": None,
                      "received_result_sha256": "f" * 64, "error_type": "TypeError"}

    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


# ---------------------------------------------------------------------------
# WP2 · a schema refusal records the failing field paths, never provider text
# ---------------------------------------------------------------------------


def test_schema_refusal_records_field_paths_without_values(api):
    from shine.director import streamline
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.models import AgentTaskRecord
    from tests.test_director_streamline import _chain_responder, _prepare_project

    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def broken(messages, schema):
        if schema.get("title") == "DirectorialPlan":
            # A required creative field is missing: salvage cannot invent it, so
            # the refusal is honest and must name the field path.
            return {"shots": [{"id": "I1", "beat_id": "B1", "duration_s": 7,
                               "asset_ids": [master]}]}
        return base(messages, schema)

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=broken)
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    for _ in range(20):
        if not streamline.run_pending_director_storyboard(factory, settings, "w", gateway=gateway):
            break

    with factory() as db:
        rows = db.query(AgentTaskRecord).filter(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id == "director",
            AgentTaskRecord.status == "REJECTED",
        ).all()
        rejected = [r for r in rows if (r.trace or {}).get("schema_errors")]
    assert rejected, "a schema refusal must record its failing field paths"
    errors = rejected[0].trace["schema_errors"]
    assert any("visual_description" in (e.get("path") or "") for e in errors), errors
    # No rejected value or provider text is persisted.
    for entry in errors:
        assert set(entry) <= {"path", "type"}