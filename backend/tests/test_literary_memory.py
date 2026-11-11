"""P3: one fact ledger, five views, a bounded and safe retrieval."""

from __future__ import annotations

import pytest

from shine.literary import flags, memory
from shine.literary.models import StoryMemoryFact
from shine.models import AppSetting
from tests.conftest import create_project

pytestmark = pytest.mark.integration

GOOD_OBSERVATIONS = [
    {"kind": "character", "subjects": ["德明"], "statement": "德明是渔民之子，右手有旧伤",
     "evidence_span_ids": ["S1#p1"]},
    {"kind": "clue", "subjects": ["德明", "账册"], "statement": "德明在酒馆发现账册残页",
     "evidence_span_ids": ["S12#p3"],
     "dimensions": ["mainline", "foreshadow"]},
    {"kind": "foreshadow", "subjects": ["罗盘"], "statement": "祖传罗盘在风暴前裂了一道缝",
     "evidence_span_ids": ["S3#p2"], "visibility": {"scope": "production_only"}},
]


def _project(api, title: str = "记忆") -> tuple[str, object]:
    client, factory, _settings = api
    return create_project(client, title), factory


def test_one_event_serves_several_dimensions_from_a_single_row(api):
    pid, factory = _project(api)
    with factory.begin() as db:
        result = memory.record_observations(
            db, project_id=pid,
            observations=memory.parse_observations({"observations": GOOD_OBSERVATIONS},
                                                   unit_id="bible")[0],
            document_sha256="a" * 64)
    assert len(result.facts) == 3
    with factory() as db:
        rows = db.query(StoryMemoryFact).filter(StoryMemoryFact.project_id == pid).all()
        clue = next(row for row in rows if row.kind == "clue")
        assert set(clue.dimensions) == {"clue", "mainline", "foreshadow"}
        assert clue.authority == "candidate", "a model observation is never authoritative"
        assert clue.evidence["verifiable"] is True


def test_a_repeated_observation_is_stored_once(api):
    pid, factory = _project(api)
    observations = memory.parse_observations({"observations": GOOD_OBSERVATIONS},
                                             unit_id="bible")[0]
    with factory.begin() as db:
        memory.record_observations(db, project_id=pid, observations=observations,
                                   document_sha256="a" * 64)
    with factory.begin() as db:
        again = memory.record_observations(db, project_id=pid, observations=observations,
                                           document_sha256="a" * 64)
    assert again.facts == []
    assert again.skipped == len(observations)


def test_an_empty_observation_list_is_legal(api):
    pid, factory = _project(api)
    observations, findings = memory.parse_observations({"observations": []}, unit_id="s1")
    assert observations == [] and findings == []
    with factory.begin() as db:
        result = memory.record_observations(db, project_id=pid, observations=[])
    assert result.facts == []


def test_one_bad_observation_is_a_warning_about_that_observation(api):
    pid, factory = _project(api)
    payload = {"observations": [
        {"kind": "character", "subjects": ["德明"], "statement": "德明出海"},
        {"kind": "weather", "subjects": ["天"], "statement": "起风"},
        {"kind": "clue", "statement": ""},
        "not an object",
    ]}
    observations, findings = memory.parse_observations(payload, unit_id="bible")
    assert len(observations) == 1
    assert len(findings) == 3
    assert all(item.severity == "WARNING" for item in findings)
    assert all(item.reason_code == "MEMORY_OBSERVATION_INVALID" for item in findings)


def test_a_citation_that_cannot_be_found_is_kept_but_not_called_verifiable(api):
    pid, factory = _project(api)
    observations, _ = memory.parse_observations({"observations": [
        {"kind": "clue", "subjects": ["甲"], "statement": "甲捡到一封信",
         "evidence_span_ids": ["不存在#9"]},
    ]}, unit_id="bible")
    with factory.begin() as db:
        result = memory.record_observations(
            db, project_id=pid, observations=observations,
            document_sha256="b" * 64, known_span_ids={"S1#p1"})
    assert len(result.facts) == 1
    assert result.facts[0].evidence["verifiable"] is False
    assert result.facts[0].evidence["unverified_span_ids"] == ["不存在#9"]
    assert result.findings and result.findings[0].severity == "WARNING"


def test_retrieval_shows_authoritative_facts_and_withholds_secrets(api):
    pid, factory = _project(api)
    observations, _ = memory.parse_observations({"observations": GOOD_OBSERVATIONS},
                                                unit_id="bible")
    with factory.begin() as db:
        result = memory.record_observations(db, project_id=pid, observations=observations,
                                            document_sha256="c" * 64)
        for fact in result.facts:
            memory.promote(db, fact.id)
        memory.snapshot(db, project_id=pid)
    with factory() as db:
        view = memory.retrieve(db, pid, token_budget=500, subject_names=("德明",))
    assert view.snapshot_id
    assert view.fact_ids, "authoritative facts must reach the writer"
    assert "德明" in view.context_text
    assert "罗盘" not in view.context_text, "a production-only secret must not leak"
    assert view.omitted >= 1
    with factory() as db:
        reviewer = memory.retrieve(db, pid, token_budget=2_000, include_candidates=True,
                                   max_secret_lines=2)
    assert "罗盘" in reviewer.context_text, "the reviewer may ask for the secret explicitly"


def test_retrieval_respects_the_token_budget_without_cutting_a_fact(api):
    pid, factory = _project(api)
    observations, _ = memory.parse_observations({"observations": [
        {"kind": "mainline", "subjects": ["甲"], "statement": "事件" + str(index) + "：" + "很长" * 40}
        for index in range(12)
    ]}, unit_id="bible")
    with factory.begin() as db:
        result = memory.record_observations(db, project_id=pid, observations=observations)
        for fact in result.facts:
            memory.promote(db, fact.id)
    with factory() as db:
        view = memory.retrieve(db, pid, token_budget=120)
    lines = [line for line in view.context_text.splitlines() if line.strip()]
    assert 0 < len(lines) < 12
    statements = {f"事件{index}：{'很长' * 40}" for index in range(12)}
    assert all(line.split("甲：", 1)[1] in statements for line in lines), \
        "a fact is included whole or not at all"
    assert view.omitted >= 1


def test_an_empty_retrieval_is_a_named_empty_view_not_a_failure(api):
    pid, factory = _project(api)
    with factory() as db:
        view = memory.retrieve(db, pid, token_budget=500)
    assert view.context_text == ""
    assert "MEMORY_EMPTY" in view.warnings


def test_shadow_records_facts_but_never_changes_the_request(api):
    _client, factory, _settings = api
    pid, _ = _project(api, "灰度")
    with factory() as db:
        assert flags.mode("memory", db, pid) == "shadow"
        assert flags.affects_request("memory", db, pid) is False
    with factory.begin() as db:
        flags.set_mode(db, pid, "memory", "on")
    with factory() as db:
        assert flags.affects_request("memory", db, pid) is True
        assert db.get(AppSetting, f"literary-feature:{pid}:memory").value["mode"] == "on"


def test_a_bad_feature_value_is_treated_as_off(api, monkeypatch):
    _client, factory, _settings = api
    pid, _ = _project(api, "开关")
    monkeypatch.setenv("SHINE_LITERARY_MEMORY", "yes-please")
    with factory() as db:
        assert flags.mode("memory", db, pid) == "off"


def test_bad_observations_never_rebuild_the_outline(api):
    """The outline succeeded; one unusable observation is a warning beside it."""
    from shine.director import pipeline
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.models import AgentArtifact, AgentTaskRecord
    from tests.test_literary_low_blocking import _route_mock

    client, factory, settings = api
    from tests.test_literary_low_blocking import _confirmed_outline, _document
    pid = create_project(client, "大纲记忆")
    _route_mock(client, pipeline.BIBLE_RUN_ROLE)
    text = "第一章 出海\n德明出海了。\n第二章 归来\n德明回来了。"
    doc = _document(factory, pid, text)
    with factory.begin() as db:
        plan = pipeline.plan_volumes(db, pid, db.get(type(doc), doc.id))
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    volume = plan.payload_json["volumes"][0]
    with factory.begin() as db:
        volume_doc = pipeline._volume_document(db, pid, volume["volume_id"])
        from shine.materialprep.story import source_sections
        _confirmed_outline(db, pid, volume_doc, source_sections(pid, volume_doc))

    def responder(messages, schema):
        return {
            "mainline": "渔村少年的成长", "characters": [{"name": "德明", "identity": "渔民"}],
            "world": "渔村", "timeline": ["出海"], "foreshadowing": ["罗盘"],
            "locked_facts": ["Deming is a fisherman"], "open_questions": [],
            "ending_direction": "Homecoming",
            "observations": [
                {"kind": "character", "subjects": ["德明"], "statement": "德明右手有旧伤"},
                {"kind": "clue", "subjects": ["德明", "账册"], "statement": "德明发现账册残页",
                 "dimensions": ["foreshadow"]},
                {"kind": "气象", "subjects": ["天"], "statement": "起风"},
            ],
        }

    with factory() as db:
        gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
        task = pipeline.queue_bible(db, pid, gateway)
        db.commit()
        task_id = task.id
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    assert pipeline.run_pending_pipeline_task(factory, settings, "mem-1", gateway) is True

    with factory() as db:
        row = db.get(AgentTaskRecord, task_id)
        assert row.status == "SUCCEEDED", row.trace
        artifact = db.get(AgentArtifact, row.trace["artifact_id"])
        summary = artifact.payload_json["memory_ingest"]
        assert summary["saved"] == 2, summary
        assert summary["mode"] == "shadow"
        assert any(item["reason_code"] == "MEMORY_OBSERVATION_INVALID"
                   for item in summary["findings"])
        facts = db.query(StoryMemoryFact).filter(StoryMemoryFact.project_id == pid).all()
        assert len(facts) == 2
        assert all(fact.authority == "candidate" for fact in facts), \
            "a model observation is never promoted by the task that produced it"
