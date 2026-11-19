"""The director streamline ①–⑤ path: one editable outline, one storyboard
book, one per-episode shot table, one shot-driven material list, and an
episode-scoped package.

Every test here walks the real services the resident Worker uses — no API
shortcuts around the landing gates, no hand-built plan rows pretending to be
confirmed.  The mock provider stands only where a real text model would be.
"""

from __future__ import annotations

import copy

import pytest

from shine.director import streamline
from shine.director.contracts import derive_episode_plan_by_count
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord
from tests.conftest import create_project

LOCAL = {"x-shine-local-action": "1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sections(count: int, *, seconds: float = 30.0) -> list[dict]:
    return [
        {"production_section_id": f"sec-{index + 1:02d}", "order": index + 1,
         "title": f"第{index + 1}场", "estimated_duration_s": seconds,
         "start_offset": index * 200, "end_offset": index * 200 + 199}
        for index in range(count)
    ]


def _approved_outline(factory, project: str, document: dict, rows: list[dict],
                      *, questions: list[str] | None = None) -> dict:
    from shine.director.package import digest_of
    from shine.models import utcnow

    payload = {
        "production_sections": rows, "document_id": document["id"],
        "document_sha256": document["sha256"],
        "summary": "雨夜里，钟表匠老周修好一只停在十二点的怀表。",
        "story_line": ["老周修好怀表", "客人第二天消失"],
        "timeline": ["深夜修表", "次日发现人去楼空"],
        "relationships": ["老周与客人是旧识"],
        "questions": questions or [],
        "characters": [{"name": "老周", "entity_id": "老周",
                        "identity": "五十岁的钟表匠，沉默寡言"}],
        "locked_facts": ["主角叫老周"],
    }
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project, role_id="story_outline_run",
            request_identity=f"outline-{document['id']}-{len(rows)}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64,
            rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED", trace={},
        )
        db.add(source)
        db.flush()
        artifact = AgentArtifact(
            project_id=project, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest_of(payload),
            producer_role="scriptwriter", source_task_id=source.id,
            approved_by="local-user", approved_at=utcnow(),
        )
        db.add(artifact)
        db.flush()
        return {"id": artifact.id, "sha256": artifact.sha256, "sections": rows}


def _document(client, project: str, name: str, text: str) -> dict:
    response = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": (name, text.encode("utf-8"), "text/plain")})
    assert response.status_code == 201, response.text
    return response.json()


def _prepare_project(client, *, sections: int = 4, section_seconds: float = 30.0,
                     questions: list[str] | None = None):
    """One project with rulepack, routes, a character master, a confirmed outline.

    The document's real bytes are padded so every section really occupies the
    200-character span its outline claims — the source-range check inside the
    chain (`STORY_SOURCE_RANGE_INVALID`) is exact, not approximate.
    """
    from tests.test_film_crew import prepare

    project_id, _style, _profile = prepare(
        client, freeze=False, character_entity_id="老周", character_filename="老周.png")
    parts = []
    for index in range(sections):
        body = (f"第{index + 1}场\n老周把怀表贴近耳朵，又放回丝绒垫上。"
                f"客人留下硬币，走进雨里。")
        parts.append(body + "。" * (200 - len(body) - 1) + "\n")
    text = "".join(parts)
    document = _document(client, project_id, "雨夜钟表铺.txt", text)
    outline = _approved_outline(
        client.app.state.session_factory, project_id, document,
        _sections(sections, seconds=section_seconds), questions=questions)
    return {"project": project_id, "document": document, "outline": outline}


def _storyboard_book(client, project: str, *, episode_count: int = 2,
                     target_seconds: int = 60, tolerance_seconds: int = 30) -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": episode_count, "target_seconds": target_seconds,
              "tolerance_seconds": tolerance_seconds},
        headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def _confirm_book(client, project: str, plan_version: int, plan_sha: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={"plan_version": plan_version, "expected_sha256": plan_sha,
              "style": {"profile_id": "wong-kar-wai", "film_type": "live_action",
                        "dialogue_ratio_percent": 80}},
        headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _chain_responder(registry: dict, *, fail_section: str | None = None):
    """The five-role chain answer, optionally broken for one section."""
    character = "老周"

    def respond(messages, schema):
        import re

        title = schema.get("title")
        blob = "".join(message.content for message in messages)
        match = re.search(r'"production_section_id"\s*:\s*"([^"]+)"', blob)
        section = match.group(1) if match else ""
        if fail_section and section == fail_section and title == "ScriptBreakdown":
            # A decided, answered refusal with genuinely missing content: the
            # salvage step cannot invent the required prose, so the refusal
            # is honest and the episode pauses.
            return {"beats": [{"id": "B1", "characters": [character], "dialogue": []}]}
        if title == "ScriptBreakdown":
            return copy.deepcopy({
                "locked_story_facts": ["主角叫老周"],
                "prompt_story_facts": ["The protagonist is a watchmaker called Lao Zhou."],
                # The frozen template style is Chinese; the contract demands one
                # faithful English rendering per frozen field.
                "prompt_style": {
                    "photography_language": "Stable composed frames with clear sightlines.",
                    "shot_blocking": "Readable blocking between people and story objects.",
                    "color_palette": "Restrained palette with one clear emphasis.",
                    "lighting_tone": "Directional lamp light with layered shadow.",
                    "lens_texture": "Clean, stable optical texture.",
                    "editing_rhythm": "Deliberate reveals after reactions.",
                    "sound_performance": "Original dialogue with restrained ambience.",
                },
                "beats": [
                    {"id": "B1", "description": "老周把怀表贴近耳朵，又放回丝绒垫上。",
                     "characters": [character], "dialogue": [
                         {"speaker_id": character, "text": "又停在十二点。", "start_s": 1, "end_s": 2.4}]},
                    {"id": "B2", "description": "客人留下硬币，走进雨里。",
                     "characters": [character], "dialogue": []},
                ],
            })
        if title == "DirectorialPlan":
            return copy.deepcopy({
                "shots": [
                    {"id": "I1", "beat_id": "B1",
                     "visual_description": "A watchmaker holds a pocket watch to his ear, then lays it on velvet.",
                     "duration_s": 7, "asset_ids": [registry["asset"]]},
                    {"id": "I2", "beat_id": "B2",
                     "visual_description": "A guest leaves a coin on the counter and walks into the rain.",
                     "duration_s": 6, "asset_ids": [registry["asset"]]},
                ],
            })
        if title == "CameraPlan":
            return copy.deepcopy({
                "shots": [
                    {"intent_id": "I1", "composition": "Medium close-up, subject right third",
                     "movement": "Static", "lighting": "Warm lamp key", "axis": "Preserve axis"},
                    {"intent_id": "I2", "composition": "Wide of the shop doorway",
                     "movement": "Static", "lighting": "Rain-blue exterior", "axis": "Same axis"},
                ],
            })
        if title == "ContinuityReport":
            return {"issues": [], "recommendation": "proceed_to_preflight"}
        if title == "EditIntentPlan":
            return copy.deepcopy({
                "schema_version": "1.2", "intent_id": "D1",
                "clips": [
                    {"intent_id": "I1", "order": 1, "planned_duration_s": 7.0,
                     "transition": "CUT", "continuity_note": "establishing"},
                    {"intent_id": "I2", "order": 2, "planned_duration_s": 6.0,
                     "transition": "CUT", "continuity_note": "reaction"},
                ],
                "rhythm": "steady", "dialogue_pace": "natural Mandarin pace",
            })
        raise AssertionError(f"unexpected schema {title}")

    return respond


def _run_storyboard_to_rest(client, settings, project: str, *, fail_section: str | None = None,
                            max_ticks: int = 40, responder=None) -> None:
    """Drive the resident worker's director-storyboard step until it rests."""
    factory = client.app.state.session_factory
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    responder = responder or _chain_responder({"asset": master}, fail_section=fail_section)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)

    for _ in range(max_ticks):
        advanced = streamline.run_pending_director_storyboard(
            factory, settings, "worker-test", gateway=gateway)
        if not advanced:
            break


# ---------------------------------------------------------------------------
# ① editable outline
# ---------------------------------------------------------------------------


def test_outline_markdown_round_trip_is_lossless():
    payload = {"summary": "一夜之间。", "story_line": ["a", "b"], "timeline": [],
               "relationships": ["x"], "questions": ["客人去哪了？"], "other": {"keep": 1}}
    markdown = streamline.outline_to_markdown(payload)
    parsed = streamline.markdown_to_outline(markdown, payload)
    assert parsed["summary"] == "一夜之间。"
    assert parsed["story_line"] == ["a", "b"]
    assert parsed["relationships"] == ["x"]
    assert parsed["questions"] == ["客人去哪了？"]
    assert parsed["other"] == {"keep": 1}  # untouched non-anchored content


def test_outline_character_edit_keeps_identity_and_updates_introduction():
    payload = {
        "summary": "雨夜。",
        "characters": [
            {"name": "老周", "entity_id": "entity-001", "stable_identity": "钟表匠",
             "introduction": "五十岁的钟表匠，沉默寡言。"},
            {"name": "客人", "entity_id": "entity-002",
             "introduction": "雨夜来访的陌生人。"},
        ],
        "story_line": [], "timeline": [], "relationships": [], "questions": [],
    }
    markdown = streamline.outline_to_markdown(payload)
    assert "## 登场人物" in markdown
    edited = markdown.replace("五十岁的钟表匠，沉默寡言。", "五十岁的钟表匠，雨夜修好一只怀表。")
    parsed = streamline.markdown_to_outline(edited, payload)
    zhou = next(c for c in parsed["characters"] if c["name"] == "老周")
    assert zhou["introduction"] == "五十岁的钟表匠，雨夜修好一只怀表。"
    assert zhou["entity_id"] == "entity-001"
    assert zhou["stable_identity"] == "钟表匠"
    guest = next(c for c in parsed["characters"] if c["name"] == "客人")
    assert guest["entity_id"] == "entity-002"
    assert guest["introduction"] == "雨夜来访的陌生人。"


def test_outline_edit_revision_and_one_click_confirm(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2, questions=["客人到底是谁？"])
    project, outline = prepared["project"], prepared["outline"]

    current = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert current["artifact_id"] == outline["id"]
    assert "客人到底是谁？" in current["markdown"]

    edited = current["markdown"].replace("一夜之间。", "雨夜，钟表匠老周修好怀表。")
    revision = client.post(
        f"/api/v1/projects/{project}/director/outline/revision",
        json={"expected_sha256": current["sha256"], "markdown": edited}, headers=LOCAL)
    assert revision.status_code == 201, revision.text
    body = revision.json()
    assert body["artifact_id"] != outline["id"]
    assert body["questions"] == ["客人到底是谁？"]

    confirmed = client.post(
        f"/api/v1/projects/{project}/director/outline/confirm",
        json={"artifact_id": body["artifact_id"], "expected_sha256": body["sha256"]},
        headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    with factory() as db:
        row = db.get(AgentArtifact, body["artifact_id"])
        assert row.approved_at is not None


def test_outline_edit_conflict_refuses_stale_sha(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    current = client.get(f"/api/v1/projects/{project}/director/outline").json()
    stale = "0" * 64
    response = client.post(
        f"/api/v1/projects/{project}/director/outline/revision",
        json={"expected_sha256": stale, "markdown": current["markdown"]}, headers=LOCAL)
    assert response.status_code == 409


def test_outline_version_chain_tracks_draft_and_confirmed(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project, outline = prepared["project"], prepared["outline"]

    current = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert [v["artifact_id"] for v in current["versions"]] == [outline["id"]]
    assert current["versions"][0]["confirmed"] is True

    revision = client.post(
        f"/api/v1/projects/{project}/director/outline/revision",
        json={"expected_sha256": current["sha256"],
              "markdown": current["markdown"].replace("一夜之间。", "雨夜改稿。")},
        headers=LOCAL)
    assert revision.status_code == 201, revision.text
    body = revision.json()

    view = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert view["artifact_id"] == body["artifact_id"]
    assert view["approved"] is False
    assert view["confirmed"] is False
    assert view["user_edited"] is True
    assert view["confirmed_artifact_id"] == outline["id"]
    chain = view["versions"]
    assert [v["artifact_id"] for v in chain] == [body["artifact_id"], outline["id"]]
    assert chain[0]["revision_of"] == outline["id"]
    assert chain[0]["user_edited"] is True
    assert chain[1]["confirmed"] is True

    old = client.get(
        f"/api/v1/projects/{project}/director/outline?artifact_id={outline['id']}").json()
    assert old["artifact_id"] == outline["id"]
    assert old["markdown"] == current["markdown"]

    # A pending draft must not displace the confirmed basis downstream.
    book = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 1, "target_seconds": 60, "tolerance_seconds": 15},
        headers=LOCAL)
    assert book.status_code == 201, book.text

    missing = client.get(
        f"/api/v1/projects/{project}/director/outline"
        "?artifact_id=00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404

    # Confirming the draft switches the single basis to v2; v1 stays history.
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/outline/confirm",
        json={"artifact_id": body["artifact_id"], "expected_sha256": body["sha256"]},
        headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    after = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert after["artifact_id"] == body["artifact_id"]
    assert after["approved"] is True and after["confirmed"] is True
    assert after["confirmed_artifact_id"] == body["artifact_id"]
    chain = {v["artifact_id"]: v for v in after["versions"]}
    assert chain[body["artifact_id"]]["confirmed"] is True
    assert chain[outline["id"]]["confirmed"] is False
    assert chain[outline["id"]]["approved"] is True  # history is never rewritten
    from shine.director import basis
    from shine.models import ProjectDocument
    with factory() as db:
        document = db.get(ProjectDocument, prepared["document"]["id"])
        resolved = basis.outline_for_document(db, project, document)
        assert resolved is not None and resolved.id == body["artifact_id"]


# ---------------------------------------------------------------------------
# ② storyboard book
# ---------------------------------------------------------------------------


def test_episode_plan_by_count_target_governs_and_count_caps_scope():
    # 2026-11-19 用户裁定：每集秒数决定范围，集数是上限；超范围显式声明，
    # 不得把整剧塞进第一集再报一个偏差数字。
    from shine.director.contracts import validate_section_coverage

    payload = derive_episode_plan_by_count(
        _sections(5, seconds=30), episode_count=1, target_seconds=60, tolerance_seconds=15)
    assert len(payload.episodes) == 1
    assert payload.episodes[0].section_ids == ["sec-01", "sec-02"]
    assert payload.out_of_scope_section_ids == ["sec-03", "sec-04", "sec-05"]
    two = derive_episode_plan_by_count(
        _sections(5, seconds=30), episode_count=2, target_seconds=60, tolerance_seconds=15)
    assert [e.section_ids for e in two.episodes] == [["sec-01", "sec-02"], ["sec-03", "sec-04"]]
    assert two.out_of_scope_section_ids == ["sec-05"]
    # 覆盖门读同一份声明：declared out 不是遗漏。
    all_ids = [f"sec-{index:02d}" for index in range(1, 6)]
    assert validate_section_coverage(payload, all_ids) == []
    undeclared = payload.model_copy(update={"out_of_scope_section_ids": []})
    assert any(p.startswith("SECTIONS_NOT_COVERED") for p in validate_section_coverage(undeclared, all_ids))


def test_episode_plan_by_count_refuses_more_episodes_than_sections():
    with pytest.raises(ValueError):
        derive_episode_plan_by_count(_sections(2), episode_count=3)


def test_storyboard_book_confirm_pairs_plan_and_style(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]

    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    preview = book["preview"]
    assert preview["episode_count"] == 2
    assert preview["episodes"][0]["sections"], "the book must read sections"
    assert "deviation_seconds" in preview["episodes"][0]

    result = _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    assert result["plan_version"] == book["plan_version"]
    assert result["style"]["style_version_id"]

    from sqlalchemy import select as sa_select

    from shine.director.models import DirectorEpisodePlan
    from shine.models import Project, StyleVersion
    with factory() as db:
        confirmed = db.scalars(sa_select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == project,
            DirectorEpisodePlan.version == book["plan_version"])).one()
        assert confirmed.status == "CONFIRMED"
        project_row = db.get(Project, project)
        frozen = db.get(StyleVersion, project_row.frozen_style_version_id)
        assert frozen is not None
        assert (frozen.snapshot or {}).get("film_type") == "live_action"


def test_storyboard_book_refuses_without_confirmed_outline(api):
    client, factory, settings = api
    project = create_project(client, "无大纲")
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 2, "target_seconds": 60}, headers=LOCAL)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# ③ storyboard run, per-episode isolation and confirmation
# ---------------------------------------------------------------------------


def test_storyboard_run_lands_shots_per_episode(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                         json={}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["task_id"]
    # Idempotent: the same contract reuses the live run.
    again = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                        json={}, headers=LOCAL)
    assert again.json()["task_id"] == task_id

    _run_storyboard_to_rest(client, settings, project)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    episodes = {row["episode_id"]: row for row in state["episodes"]}
    assert set(episodes) == {"ep-01", "ep-02"}
    for row in episodes.values():
        assert row["status"] == "APPLIED", row
        assert row["shot_ids"], "each section lands its shots"

    # The per-episode table reads the landed shots with editorial facts.
    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    assert table["shot_count"] >= 2
    first = table["shots"][0]
    assert {"duration_target_s", "editorial_seconds", "h3_generation_seconds",
            "internal_cuts", "continuity_mode"} <= set(first)
    assert first["characters"]


def test_confirm_one_episode_while_the_other_is_pending(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    by_id = {row["episode_id"]: row for row in state["episodes"]}
    assert by_id["ep-01"]["status"] == "APPLIED"
    assert by_id["ep-02"]["status"] == "BLOCKED"
    assert state["status"] == "NEEDS_REVIEW"

    # The ready episode lands even while the other is blocked.
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    shot_ids = confirmed.json()["shot_ids"]
    assert shot_ids
    # A repeated confirmation reuses the same shots, never duplicates them.
    repeat = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert repeat.status_code == 200
    assert repeat.json()["shot_ids"] == shot_ids


def test_blocked_episode_retry_resumes_and_finishes(api):
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

    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text

    # The responder no longer fails the section: the run finishes.
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_a_structured_landing_refusal_is_named_not_HTTPException(api, monkeypatch):
    """A gate that refuses with ``{"code": ...}`` must not surface as "HTTPException".

    Live evidence (2026-11-15, scene 2): all five crew roles succeeded, then the
    landing gate refused with a structured detail.  The run reported
    ``error_code=HTTPException`` and the episode kept a blocker with an empty
    rejection list, so the panel showed a blocked scene with no reason and no
    named next step - the exact "状态未知" shape this work must not produce.
    """
    from fastapi import HTTPException

    client, _factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    def refuse(*_args, **_kwargs):
        raise HTTPException(409, {"code": "FILM_HANDOFF_REFUSED_BY_TEST",
                                  "message": "landing gate refused"})

    monkeypatch.setattr("shine.api_film.apply_camera", refuse)
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "NEEDS_REVIEW"
    assert state["error_code"] == "FILM_HANDOFF_REFUSED_BY_TEST"
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocker"]["rejections"][0]["code"] == "FILM_HANDOFF_REFUSED_BY_TEST"


def test_a_landing_fault_names_itself_and_retries_without_the_chain_again(api, monkeypatch):
    """A landing gate refusal is this unit's reason, not a crew refusal to replay.

    Live evidence (2026-11-15, scene 2): all five crew roles succeeded and the
    landing gate refused.  The run kept the *previous* attempt's crew refusal as
    ``failed_task_id``; its retry then hit the replay gate
    (STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION) against a task the retry itself
    had superseded, so the unit could never advance.  The landing fault has to
    name itself and the retry has to reuse the five finished artifacts - no
    model call and no replay authorisation is involved in re-landing.
    """
    from fastapi import HTTPException

    import shine.api_film as api_film

    client, _factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    def refuse(*_args, **_kwargs):
        raise HTTPException(409, {"code": "FILM_HANDOFF_REFUSED_BY_TEST",
                                  "message": "landing gate refused"})

    real_apply = api_film.apply_camera
    monkeypatch.setattr("shine.api_film.apply_camera", refuse)
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["error_code"] == "FILM_HANDOFF_REFUSED_BY_TEST"
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    assert blocked["blocker"]["rejections"][0]["code"] == "FILM_HANDOFF_REFUSED_BY_TEST"
    with _factory() as db:
        run = db.get(AgentTaskRecord, state["task_id"])
        assert run.trace.get("failed_task_id") is None, (
            "a landing fault must not leave a crew refusal as the retry's reason")
        assert set(run.trace.get("resume_steps") or {}) >= {
            "scriptwriter", "director", "cinematographer", "continuity_supervisor",
            "editor_edit_intent"}, "the finished chain must travel with the recovery state"

    monkeypatch.setattr("shine.api_film.apply_camera", real_apply)
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text

    calls: list[str] = []
    base = _chain_responder({"asset": next(
        a["id"] for a in client.get(f"/api/v1/projects/{project}/assets").json()
        if a["role"] == "character" and a["entity_id"])})

    def counted(messages, schema):
        calls.append(schema.get("title"))
        return base(messages, schema)

    factory = client.app.state.session_factory
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=counted)
    assert streamline.run_pending_director_storyboard(
        factory, settings, "worker-test", gateway=gateway)
    assert calls == [], "re-landing must reuse the five finished crew artifacts"
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    first_unit = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")["units"][0]
    assert first_unit["state"] == "APPLIED", state
    # The rest of the episode continues normally after the recovered landing.
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_a_superseded_failure_pointer_does_not_gate_the_retry(api, monkeypatch):
    """A pointer to a request an earlier retry replaced is history, not a refusal.

    Live evidence (2026-11-15, scene 2): after the landing gate refused, the run
    still carried the *previous* attempt's director refusal.  That record had
    already been superseded by the retry's own new call, so the replay gate read
    a record that no longer represented an open refusal and refused every retry
    with STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION - a dead end.
    """
    from fastapi import HTTPException

    import shine.api_film as api_film

    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    def refuse(*_args, **_kwargs):
        raise HTTPException(409, {"code": "FILM_HANDOFF_REFUSED_BY_TEST"})

    real_apply = api_film.apply_camera
    monkeypatch.setattr("shine.api_film.apply_camera", refuse)
    _run_storyboard_to_rest(client, settings, project)
    monkeypatch.setattr("shine.api_film.apply_camera", real_apply)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()

    # Rebuild the stranded shape the old code left: the pointer names a director
    # request that a later retry already replaced, and no landing facts exist.
    with factory.begin() as db:
        run = db.get(AgentTaskRecord, state["task_id"])
        predecessor = AgentTaskRecord(
            project_id=project, role_id="director", request_identity="predecessor",
            input_hash="0" * 64, asset_manifest_hash="0" * 64, rulepack_id="test",
            rulepack_hash="0" * 64, model_profile_id="test", model_id="test",
            status="SUPERSEDED",
            trace={"rejection_reason_code": "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE",
                   "is_mock": False},
        )
        db.add(predecessor)
        db.flush()
        run.trace = {**run.trace, "failed_task_id": predecessor.id, "failed_role": "director",
                     "failed_unit_index": 0, "pending_landing": None, "resume_steps": {}}
        run.status, run.error_code = "NEEDS_REVIEW", "HTTPException"

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_legacy_unknown_refusal_recovery_path_names_and_resumes(api):
    """A failed record that says UNKNOWN stays behind the reconciliation gate:
    the direct episode retry refuses with a named code (never a silent
    nothing), and the person's decision — reconcile + resend-unknown —
    unlocks exactly one new call so the episode finishes.

    The 2026-11-15 journey hit this shape: a streamed answer cut at
    finish_reason=length was mis-classified UNKNOWN, so the panel had to
    offer this decision path instead of a dead "go check the panel" hint.
    """
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
    rejection = blocked["blocker"]["rejections"][0]
    failed_task_id = rejection["task_id"]
    # Rewrite the failed record into the legacy mis-classified shape: UNKNOWN,
    # no received-result digest, no reconciliation decision yet.
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, failed_task_id)
        assert task is not None
        task.status = "NEEDS_REVIEW"
        task.trace = {**task.trace, "external_outcome": "UNKNOWN",
                      "diagnostic_category": "provider_format",
                      "provider_finish_reason": "length", "provider_text_chars": 3140}
        task.trace.pop("received_result_sha256", None)
        task_version = task.state_version

    # 1) A blind retry cannot silently replay the unknown call: the run stays
    #    blocked, the failed episode keeps its blocked marker, and the refusal
    #    is named on the run — never a silent nothing.
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "NEEDS_REVIEW"
    assert state["error_code"], "the refusal must be named, never status-unknown"
    still = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert still["status"] == "BLOCKED", still
    assert still["blocker"] is not None, "the episode must keep its blocked marker"

    # 2) The person checks the unknown call, then explicitly authorises one
    #    new request — the decision path the panel must expose.
    checked = client.post(f"/api/v1/projects/{project}/film-runs/{failed_task_id}/reconcile",
                          json={"expected_state_version": task_version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    assert checked.json()["outcome"] == "UNKNOWN_REQUIRES_NEW_ATTEMPT"
    resent = client.post(f"/api/v1/projects/{project}/film-runs/{failed_task_id}/resend-unknown",
                         json={"expected_state_version": checked.json()["state_version"]}, headers=LOCAL)
    assert resent.status_code == 200, resent.text
    assert resent.json()["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"

    # 3) Now the episode retry is lawful and the run finishes.
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_blocked_state_names_the_failed_scene(api):
    """A blocked episode must tell the person which scene is stuck, not only
    that the episode failed: the state names each unit's script section."""
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
    unit = next(u for u in blocked["units"] if u["index"] == blocked["blocker"]["unit_index"])
    assert unit["state"] == "BLOCKED"
    assert unit["section_order"] == 3
    assert unit["section_title"] == "第3场"
    # The applied episode names its scenes too, so the person sees the full map.
    applied = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    assert applied["units"][0]["section_title"] == "第1场"


def test_a_retried_scene_is_told_what_was_refused_before(api):
    """A retry must carry the last named refusal into its first call.

    The 2026-11-15 journey found the hole: scene 2 was refused twice for
    Chinese visual prose, and each retry spent its first call re-buying the
    same refusal.  Worse, when that first call was truncated by an unrelated
    rule, the earlier lesson was dropped entirely and the retry answered the
    old mistake again.  The retry names the record it replaces, so that
    record's trace is the lesson the first call must already carry.
    """
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def chinese_prose(messages, schema):
        plan = base(messages, schema)
        if schema.get("title") == "DirectorialPlan" and pending_section in "".join(
                message.content for message in messages):
            plan["shots"][0]["visual_description"] = "老周把怀表贴近耳朵，又放回丝绒垫上。"
        return plan

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, responder=chinese_prose)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocker"]["rejections"][0]["rejection_reason_code"] == (
        "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")

    calls: list[str] = []

    def healthy(messages, schema):
        calls.append(" ".join(message.content for message in messages
                              if message.role == "system"))
        return base(messages, schema)

    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project, responder=healthy)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert calls, "the retry must actually call the model"
    assert "failed the visual language check" in calls[0], (
        "the first call of a retry must already carry the last refusal's lesson: "
        + calls[0][-400:])


def test_a_truncated_retry_does_not_drop_the_lesson_it_was_told(api, monkeypatch):
    """A retry's second call must still carry the lesson its first call earned.

    The live retry on 2026-11-15 exposed this: the retry started already
    knowing the visual-language refusal, its first call was truncated by an
    unrelated budget miss (LLM_FORMAT), and the second call overwrote the
    standing lesson with the truncation's own note.  The episode was then
    refused for the exact reason the retry had been told to repair.  Lessons
    accumulate inside one request; a later, unrelated failure never erases one.
    """
    from shine.llm import mock as mock_module
    from shine.llm.base import LLMFormatError

    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def chinese_prose(messages, schema):
        plan = base(messages, schema)
        if schema.get("title") == "DirectorialPlan" and pending_section in "".join(
                message.content for message in messages):
            plan["shots"][0]["visual_description"] = "老周把怀表贴近耳朵，又放回丝绒垫上。"
        return plan

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, responder=chinese_prose)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocker"]["rejections"][0]["rejection_reason_code"] == (
        "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")

    plan_calls: list[str] = []
    original = mock_module.MockLLMProvider.complete

    def truncated_first_answer(self, messages, *, schema=None, json_mode=True):
        system = " ".join(message.content for message in messages
                          if message.role == "system")
        if (schema or {}).get("title") == "DirectorialPlan" and pending_section in "".join(
                message.content for message in messages):
            plan_calls.append(system)
            if len(plan_calls) == 1:
                # A 200 answer that ended mid-object: the same LLM_FORMAT the
                # live truncated retry produced, and still a received result.
                raise LLMFormatError("Mock answer truncated", details={"http_status": 200})
        return original(self, messages, schema=schema, json_mode=json_mode)

    monkeypatch.setattr(mock_module.MockLLMProvider, "complete", truncated_first_answer)
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project, responder=chinese_prose)
    assert len(plan_calls) >= 2, f"a bounded retry must spend its second call: {plan_calls}"
    assert "failed the visual language check" in plan_calls[1], (
        "the second call of a retry must still carry the refusal the retry was told to "
        "repair, even when the first call failed for another reason: " + plan_calls[1][-400:])


def test_shot_row_edit_changes_duration_with_revision_fence(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    shot = table["shots"][0]
    patched = client.patch(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}",
        json={"expected_revision": shot["revision"], "duration_target_s": 9.5}, headers=LOCAL)
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == shot["revision"] + 1

    stale = client.patch(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}",
        json={"expected_revision": shot["revision"], "duration_target_s": 8.0}, headers=LOCAL)
    assert stale.status_code == 409

    # The continuity relation is an editorial fact on the same row.
    flipped = client.patch(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}",
        json={"expected_revision": patched.json()["revision"], "continuity_mode": "last_frame"},
        headers=LOCAL)
    assert flipped.status_code == 200, flipped.text
    table2 = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    edited = next(row for row in table2["shots"] if row["shot_id"] == shot["shot_id"])
    assert edited["continuity_mode"] == "last_frame"
    assert edited["duration_target_s"] == 9.5


# ---------------------------------------------------------------------------
# ④ shot-driven material list
# ---------------------------------------------------------------------------


def test_derive_materials_matches_masters_and_marks_usage(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)
    client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
                json={"acknowledge_duration_deviation": True}, headers=LOCAL)

    derived = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                          json={"episode_ids": ["ep-01"]}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    body = derived.json()
    assert body["requirements"], "the confirmed shots ask for their cast"
    character = next(row for row in body["requirements"] if row["family"] == "character")
    assert character["status"] == "matched"
    assert character["matched_master"]["asset_id"]
    assert character["used_shot_ids"], "the row names the shots that use it"
    assert body["replayed"] is False
    # Deriving again reuses the plan instead of stacking revisions.
    twice = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                        json={"episode_ids": ["ep-01"]}, headers=LOCAL)
    assert twice.json()["plan_revision_id"] == body["plan_revision_id"]
    assert twice.json()["replayed"] is True


# ---------------------------------------------------------------------------
# ⑤ episode-scoped package
# ---------------------------------------------------------------------------


def _confirmed_scoped_package(client, factory, project: str, profile: dict,
                              episode_ids: list[str]) -> dict:
    built = client.post(
        f"/api/v1/projects/{project}/director-packages",
        json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA",
              "episode_ids": episode_ids},
        headers=LOCAL)
    assert built.status_code == 201, built.text
    confirmed = client.post(f"/api/v1/director-packages/{built.json()['id']}/confirm",
                            json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return built.json()


def test_episode_scoped_package_freezes_only_ready_episodes(api):
    client, factory, settings = api
    from tests.conftest import register_workflow_profile

    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)
    confirmed_ep = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert confirmed_ep.status_code == 200, confirmed_ep.text

    profile = register_workflow_profile(client, mode="Ref2VA")
    package = _confirmed_scoped_package(client, factory, project, profile, ["ep-01"])
    assert package["episode_count"] == 1
    assert package["episode_plan"].get("selected_episode_ids") == ["ep-01"]

    # The whole-plan build still refuses while an episode is unmaterialised.
    whole = client.post(
        f"/api/v1/projects/{project}/director-packages",
        json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA"},
        headers=LOCAL)
    assert whole.status_code == 409
    assert whole.json()["detail"]["code"] == "EPISODE_PLAN_NOT_MATERIALIZED"

    # An unknown episode scope is a named refusal.
    unknown = client.post(
        f"/api/v1/projects/{project}/director-packages",
        json={"workflow_profile_id": profile["id"], "episode_ids": ["ep-99"]},
        headers=LOCAL)
    assert unknown.status_code in (404, 409, 422)


def test_journey_reports_per_episode_storyboard_progress(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)
    client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
                json={"acknowledge_duration_deviation": True}, headers=LOCAL)

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    facts = stage["facts"]
    assert facts["episode_total"] == 2
    assert facts["episode_confirmed_count"] == 1
    assert facts["ready_episode_ids"] == ["ep-01"]
    assert "1/2 集分镜已确认" in stage["summary"]


def test_journey_follows_the_current_plan_beside_an_older_scoped_package(api):
    """A package frozen for ep-01 must not keep the journey on its old plan.

    The panel and the auto-confirm write landed shots into the newest confirmed
    plan version; the journey's storyboard gate used to re-judge the plan the
    package pinned, so a later episode that landed and auto-confirmed still left
    the stage blocked with EPISODE_PLAN_NOT_MATERIALIZED.
    """
    client, factory, settings = api
    from tests.conftest import register_workflow_profile

    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    profile = register_workflow_profile(client, mode="Ref2VA")
    package = _confirmed_scoped_package(client, factory, project, profile, ["ep-01"])

    # The package covers ep-01. The project journey reports the remaining
    # storyboard work across the current plan while ep-01 stays ready.
    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    pending = next(b for b in stage["blockers"] if b["code"] == "EPISODE_PLAN_NOT_MATERIALIZED")
    assert pending["pending_section_count"] > 0
    assert stage["done"] is False
    assert stage["facts"]["ready_episode_ids"] == ["ep-01"]

    # Retry the blocked episode; landing auto-confirms it into a new plan version.
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    retried = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retried.status_code == 202, retried.text
    _run_storyboard_to_rest(client, settings, project)

    plans = client.get(f"/api/v1/projects/{project}/episode-plans").json()
    confirmed = next(row for row in plans if row["status"] == "CONFIRMED")
    assert confirmed["version"] > package["episode_plan"]["version"]
    assert all(ep["shot_ids"] for ep in confirmed["payload"]["episodes"])

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    assert not any(b["code"] == "EPISODE_PLAN_NOT_MATERIALIZED" for b in stage["blockers"])
    assert stage["done"] is True, stage["blockers"]
    assert stage["facts"]["episode_confirmed_count"] == 2
    assert stage["facts"]["storyboard_plan_version"] == confirmed["version"]


# ---------------------------------------------------------------------------
# crew salvage and budget
# ---------------------------------------------------------------------------


def test_crew_salvage_keeps_usable_content_and_refuses_missing_content():
    from shine.agents.crew_salvage import parse_with_salvage
    from shine.agents.film_contracts import ScriptBreakdown

    noisy = {
        "beats": [{"id": "B1", "description": "雨夜。", "characters": ["老周"],
                   "source_note": "trust me",
                   "dialogue": [{"speaker": "老周", "line": "来了。", "start_s": 0.5}]}],
    }
    proposal, notes = parse_with_salvage(noisy, ScriptBreakdown)
    assert proposal.beats[0].dialogue[0].speaker_id == "老周"
    assert any("aliased_to_speaker_id" in note for note in notes)
    assert any("unknown_field_dropped" in note for note in notes)

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_with_salvage({"beats": [{"id": "B1"}]}, ScriptBreakdown)


def test_crew_chain_records_the_literary_budget(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    with factory() as db:
        from sqlalchemy import select as sa_select

        rows = db.scalars(sa_select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id.in_(["scriptwriter", "director", "cinematographer",
                                         "continuity_supervisor", "editor"]),
            AgentTaskRecord.status == "SUCCEEDED")).all()
        budgeted = [row for row in rows if (row.trace or {}).get("call_budget")]
        assert budgeted, "every crew role records its approved call budget"
        sample = budgeted[0].trace["call_budget"]
        assert sample["output_request_tokens"] > 0
        assert "window_source" in sample
