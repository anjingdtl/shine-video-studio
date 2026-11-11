"""P5: two-level literary review that never takes the pen away from the writer.

L0 proves what the server already owns; L1 answers one named doubt.  A review is
evidence stored beside the text - it can refuse a *delivery*, never rewrite a
scene, and a broken review is a note rather than a failed task.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from shine.director import pipeline
from shine.h3.rulebook import digest
from shine.literary import qa, triage
from shine.literary.models import StoryMemoryFact
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord
from tests.test_literary_low_blocking import _prepared

pytestmark = pytest.mark.integration


SOURCE = "0123456789" + "德明出海了，风浪很大。" + "尾"
SECTION = {"section_id": "s1", "scene_key": "s1", "title": "出海",
           "start_offset": 10, "end_offset": 21}
EXCERPT = SOURCE[10:21]


# --------------------------------------------------------------------------- L0


def test_a_range_outside_the_document_is_a_named_block_not_a_failure():
    report = qa.check_scene_script(
        {"text": "德明：起风了。", "continuity_out": "出海"},
        unit_id="u1", section=SECTION, source_text=SOURCE,
        source_length=5)  # the document is shorter than the range
    assert report.state == triage.HARD_BLOCK
    assert [check.code for check in report.checks] == ["SCENE_SOURCE_RANGE_INVALID"]
    assert report.checks[0].evidence["document_length"] == 5


def test_a_range_is_measured_against_the_whole_document_not_the_excerpt():
    """The excerpt is exactly ``end - start`` long; comparing would block all."""
    report = qa.check_scene_script(
        {"text": "德明：起风了。", "continuity_out": "出海"},
        unit_id="u1", section=SECTION, source_text=EXCERPT,
        source_length=len(SOURCE))
    assert report.state == triage.INFO, report.as_dict()


def test_an_unknown_character_is_review_not_block():
    report = qa.check_scene_script(
        {"text": "德明：起风了。", "continuity_out": "出海", "characters": ["德明", "陌生客"]},
        unit_id="u1", section=SECTION, source_text=SOURCE, source_length=len(SOURCE),
        known_characters=("德明",))
    assert report.state == triage.LOCAL_REVIEW
    assert [check.code for check in report.checks] == ["SCENE_CHARACTER_UNKNOWN"]
    assert report.needs_semantic_review is True


def test_a_secret_from_the_ledger_is_flagged_but_never_an_auto_rewrite():
    report = qa.check_scene_script(
        {"text": "阿娘：真凶是管家。", "continuity_out": "出海"},
        unit_id="u1", section=SECTION, source_text=SOURCE, source_length=len(SOURCE),
        secret_statements=("真凶是管家",))
    assert report.state == triage.LOCAL_REVIEW
    assert report.checks[0].code == "SCENE_SECRET_LEAK_RISK"
    # Review is advice: the payload the caller passed is untouched.
    assert report.checks[0].severity != triage.HARD_BLOCK


def test_a_missing_continuity_is_only_a_warning():
    report = qa.check_scene_script(
        {"text": "德明：起风了。", "continuity_out": ""},
        unit_id="u1", section=SECTION, source_text=SOURCE, source_length=len(SOURCE))
    assert report.state == triage.WARNING
    assert report.needs_semantic_review is False
    assert qa.semantic_question(report) is None


def test_the_semantic_contract_cannot_declare_a_hard_block():
    """A model opinion may not escalate itself into a delivery blocker."""
    with pytest.raises(ValidationError):
        qa.SemanticReview.model_validate({"findings": [
            {"code": "X", "severity": triage.HARD_BLOCK, "message": "不行"}]})
    review = qa.SemanticReview.model_validate({"findings": [
        {"code": "TONE_OFF", "severity": "LOCAL_REVIEW", "message": "语气偏硬"}]})
    assert review.findings[0].severity == "LOCAL_REVIEW"


def test_beat_coverage_is_proved_in_both_directions():
    breakdown = {"beats": [{"id": "b1"}, {"id": "b2"}]}
    partial = qa.check_beat_coverage(breakdown, {"shots": [{"beat_id": "b1"}]})
    assert partial.state == triage.HARD_BLOCK
    assert partial.checks[0].code == "STORYBOARD_BEAT_UNCOVERED"
    assert partial.checks[0].evidence["beat_ids"] == ["b2"]

    ghost = qa.check_beat_coverage(breakdown, {"shots": [{"beat_id": "b1"},
                                                         {"beat_id": "b2"},
                                                         {"beat_id": "b9"}]})
    assert ghost.state == triage.LOCAL_REVIEW
    assert ghost.checks[0].code == "STORYBOARD_BEAT_UNKNOWN"

    covered = qa.check_beat_coverage(breakdown, {"shots": [
        {"beat_id": "b1"}, {"beat_id": "b2", "cuts": [{"beat_id": "b1"}]}]})
    assert covered.state == triage.INFO, covered.as_dict()


def test_a_semantic_request_is_none_when_nothing_is_doubted():
    quiet = qa.check_scene_script(
        {"text": "德明：起风了。", "continuity_out": "出海"},
        unit_id="u1", section=SECTION, source_text=SOURCE, source_length=len(SOURCE))
    assert qa.semantic_request(quiet, text="德明：起风了。", section=SECTION) is None


# ------------------------------------------------------------------- pipeline


def _scene_responder(text: str, *, review=None, calls: list | None = None):
    def responder(messages, schema):
        if calls is not None:
            calls.append(schema.get("title"))
        if schema.get("title") == "SemanticReview":
            if isinstance(review, Exception):
                raise review
            return review if review is not None else {"findings": []}
        return {"text": text, "continuity_out": "出海"}

    return responder


def _plant_secret(factory, project_id: str) -> None:
    """One confirmed fact the audience must not learn early."""
    with factory.begin() as db:
        db.add(StoryMemoryFact(
            project_id=project_id, kind="clue", dimensions=["clue", "foreshadow"],
            subjects=["管家"], statement="真凶是管家",
            evidence={"fact_key": "fixture-secret"}, origin="source_fact",
            authority="approved", visibility={"scope": "production_only"}))


def _run_scenes(api, responder, *, runs: int = 6, plant_secret: bool = False):
    """Queue and run every scene of the fixture volume with one responder."""
    _client, factory, settings = api
    pid, plan, volume, sections, _ = _prepared(api)
    if plant_secret:
        _plant_secret(factory, pid)
    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        tasks = pipeline.queue_scene_scripts(db, pid, volume["volume_id"], gateway)
        db.commit()
        assert len(tasks) == len(sections)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    for _ in range(runs):
        if not pipeline.run_pending_pipeline_task(factory, settings, "qa-worker", gateway):
            break
    return pid, plan, volume, sections


def test_shadow_records_a_verdict_without_a_second_call(api):
    """Default mode: L0 is computed and stored, and nothing extra is bought."""
    calls: list[str] = []
    text = "德明：起风了，收帆！\n阿娘：早去早回。"
    pid, _plan, _volume, sections = _run_scenes(api, _scene_responder(text, calls=calls))
    _client, factory, _ = api

    assert calls.count("SemanticReview") == 0, calls
    assert len(calls) == len(sections), "exactly one writer call per scene"
    with factory() as db:
        reports = db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == pid,
            AgentArtifact.kind == qa.REPORT_KIND)).all()
        assert len(reports) == len(sections)
        payload = reports[0].payload_json
        assert payload["mode"] == "shadow"
        assert payload["level"] == qa.L0
        assert payload["state"] in {triage.INFO, triage.WARNING}
        # The verdict names the exact text it judged.
        script = db.get(AgentArtifact, payload["script_artifact_id"])
        assert script.sha256 == payload["script_sha256"]
        assert script.approved_at is None, "a review never confirms a script"


def test_a_named_doubt_buys_exactly_one_targeted_review(api, monkeypatch):
    monkeypatch.setenv("SHINE_LITERARY_QA", "on")
    calls: list[str] = []
    text = "德明：真凶是管家。\n阿娘：早去早回。"
    responder = _scene_responder(
        text, calls=calls,
        review={"findings": [{"code": "SECRET_EARLY", "severity": "LOCAL_REVIEW",
                              "message": "这一场提前说出了尚未揭晓的真凶。"}]})
    pid, _plan, _volume, sections = _run_scenes(api, responder, plant_secret=True)
    _client, factory, _ = api

    assert calls.count("SemanticReview") == len(sections), calls
    with factory() as db:
        reports = db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == pid,
            AgentArtifact.kind == qa.REPORT_KIND)).all()
        assert len(reports) == len(sections)
        payload = next(item.payload_json for item in reports
                       if item.payload_json.get("level") == f"{qa.L0}+{qa.L1}")
        assert payload["state"] == triage.LOCAL_REVIEW
        assert any(check["code"] == "SECRET_EARLY" for check in payload["checks"])
        assert payload["semantic_cost"]["excerpt_chars"] > 0
        # The text the person reads is byte-identical to what the writer sent.
        script = db.get(AgentArtifact, payload["script_artifact_id"])
        assert script.payload_json["text"] == text
        assert script.approved_at is None
        tasks = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        assert all(task.status == "SUCCEEDED" for task in tasks)


def test_one_doubt_also_needs_a_production_only_fact(api, monkeypatch):
    """Without a confirmed secret there is no leak doubt, so no review is bought."""
    monkeypatch.setenv("SHINE_LITERARY_QA", "on")
    calls: list[str] = []
    text = "德明：真凶是管家。\n阿娘：早去早回。"
    _run_scenes(api, _scene_responder(text, calls=calls))
    assert calls.count("SemanticReview") == 0, calls


def test_a_broken_review_never_fails_the_scene(api, monkeypatch):
    monkeypatch.setenv("SHINE_LITERARY_QA", "on")
    text = "德明：真凶是管家。\n阿娘：早去早回。"
    calls: list[str] = []

    def responder(messages, schema):
        calls.append(schema.get("title"))
        if schema.get("title") == "SemanticReview":
            # A complete answer that cannot satisfy the contract, including
            # after its one correction: the reviewer is unavailable, the scene
            # is not.
            return {"findings": [{"code": "", "message": ""}]}
        return {"text": text, "continuity_out": "出海"}

    pid, _plan, _volume, _sections = _run_scenes(api, responder, plant_secret=True)
    _client, factory, _ = api
    with factory() as db:
        reports = db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == pid,
            AgentArtifact.kind == qa.REPORT_KIND)).all()
        payload = reports[0].payload_json
        assert any(check["code"] == "QA_PENDING" for check in payload["checks"]), payload
        # The doubt itself is still unresolved, so the scene stays in review -
        # but as advice: nothing blocked the task, nothing rewrote the text.
        assert payload["state"] == triage.LOCAL_REVIEW, payload
        tasks = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).all()
        assert all(task.status == "SUCCEEDED" for task in tasks), [t.trace for t in tasks]
        script = db.get(AgentArtifact, payload["script_artifact_id"])
        assert script.payload_json["text"] == text


def test_a_hard_verdict_blocks_confirmation_but_keeps_the_text(api):
    pid, _plan, _volume, _sections = _run_scenes(
        api, _scene_responder("德明：起风了。\n阿娘：早去早回。"))
    _client, factory, _ = api
    with factory() as db:
        task = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == pid,
            AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE)).first()
        script = db.get(AgentArtifact, task.trace["artifact_id"])
        blocking = {"mode": "on", "level": qa.L0, "state": triage.HARD_BLOCK,
                    "script_artifact_id": script.id, "script_sha256": script.sha256,
                    "section_id": script.payload_json["section_id"],
                    "checks": [{"code": "SCENE_SOURCE_RANGE_INVALID",
                                "severity": triage.HARD_BLOCK, "message": "范围无效",
                                "evidence": {}, "unit_id": "u"}]}
        report = AgentArtifact(project_id=pid, kind=qa.REPORT_KIND, schema_version="1.0",
                               payload_json=blocking, sha256=digest(blocking),
                               producer_role=pipeline.SCENE_SCRIPT_RUN_ROLE,
                               source_task_id=task.id, input_revision_ids=[script.id])
        db.add(report)
        db.commit()
        sha, artifact_id = script.sha256, script.id
        expected_text = script.payload_json["text"]

    with factory.begin() as db:
        with pytest.raises(ValueError) as caught:
            pipeline.confirm_scene_script(db, pid, artifact_id, sha)
        assert "PIPELINE_SCENE_SCRIPT_QA_BLOCKED" in str(caught.value)
        assert "SCENE_SOURCE_RANGE_INVALID" in str(caught.value)
    with factory() as db:
        kept = db.get(AgentArtifact, artifact_id)
        assert kept.payload_json["text"] == expected_text
        assert kept.approved_at is None


def test_the_section_qa_endpoint_reads_the_verdict_it_stored(api):
    client, factory, _ = api
    pid, plan, volume, sections = _run_scenes(
        api, _scene_responder("德明：起风了。\n阿娘：早去早回。"))
    section_id = sections[0]["production_section_id"]
    response = client.get(
        f"/api/v1/projects/{pid}/pipeline/volumes/{volume['volume_id']}/sections/{section_id}/qa")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] in {triage.INFO, triage.WARNING}
    assert body["section_id"] == section_id
    assert body["script_sha256"]

    empty = client.get(
        f"/api/v1/projects/{pid}/pipeline/volumes/{volume['volume_id']}/sections/nope/qa")
    assert empty.json() == {"state": "NOT_RUN", "checks": []}


def test_the_storyboard_view_is_withheld_in_shadow_and_compact_in_on(api, monkeypatch):
    """S4 consumes identities and clues, not the mainline prose or dossiers."""
    from shine.agents.film import _storyboard_memory_view
    from tests.conftest import create_project

    client, factory, _ = api
    pid = create_project(client, "分镜记忆")
    with factory.begin() as db:
        db.add(StoryMemoryFact(
            project_id=pid, kind="character", dimensions=["character"], subjects=["德明"],
            statement="德明左手有旧伤，握桨时会疼", evidence={"fact_key": "c1"},
            origin="source_fact", authority="approved",
            visibility={"scope": "audience_known"}))
        db.add(StoryMemoryFact(
            project_id=pid, kind="mainline", dimensions=["mainline"], subjects=["德明"],
            statement="主线全文" * 200, evidence={"fact_key": "m1"},
            origin="source_fact", authority="approved",
            visibility={"scope": "audience_known"}))

    monkeypatch.setenv("SHINE_LITERARY_MEMORY", "shadow")
    with factory() as db:
        assert _storyboard_memory_view(db, pid, role="scriptwriter") is None

    monkeypatch.setenv("SHINE_LITERARY_MEMORY", "on")
    with factory() as db:
        view = _storyboard_memory_view(db, pid, role="scriptwriter")
    assert view is not None
    assert "德明左手有旧伤" in view["facts"]
    assert "主线全文" not in view["facts"], "mainline prose stays out of S4"
    assert view["fact_ids"]
