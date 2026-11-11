"""P6 acceptance: the four stages on real shapes of input, and old work still reads.

Three things are proven here without buying a single model call:

* an untitled long manuscript is cut at paragraph boundaries, with the cut
  recorded, and not one character is lost;
* a project that predates V2.1 (no review, no memory, no attempt rows) still
  reads and still exposes the same panel contract;
* the four-stage chain that the existing suite drives really records a review
  verdict for every scene it wrote.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from shine.director import pipeline
from shine.literary import qa, triage
from shine.models import AgentArtifact, AgentTaskRecord
from tests.conftest import create_project
from tests.test_literary_low_blocking import _document

pytestmark = pytest.mark.integration


def _untitled_manuscript(paragraphs: int = 3, chars_each: int = 30_000) -> str:
    """Prose with no heading of any kind - the hardest shape to split."""
    block = "德明把缆绳绕在桩上，风从海面压过来。" * (chars_each // 19 + 1)
    return "\n\n".join(block[:chars_each] for _ in range(paragraphs))


def test_an_untitled_long_manuscript_is_cut_at_paragraphs_without_losing_a_character(api):
    client, factory, _ = api
    pid = create_project(client, "无标题长篇")
    text = _untitled_manuscript()
    doc = _document(factory, pid, text)

    with factory.begin() as db:
        plan = pipeline.plan_volumes(db, pid, db.get(type(doc), doc.id))
        payload = plan.payload_json

    volumes = payload["volumes"]
    assert len(volumes) >= 3, [(v["start_offset"], v["end_offset"]) for v in volumes]
    assert volumes[0]["start_offset"] == 0
    assert volumes[-1]["end_offset"] == len(text)
    for left, right in zip(volumes, volumes[1:], strict=False):
        assert left["end_offset"] == right["start_offset"], "a cut never leaves a gap"
        assert text[left["end_offset"] - 1] == "\n", "every cut lands after a paragraph break"
    assert sum(volume["char_count"] for volume in volumes) == len(text)
    assert payload["coverage"]["complete"] is True
    assert all(volume["char_count"] <= pipeline.TARGET_VOLUME_CHARS for volume in volumes)
    assert all(volume["boundary_reason"] == "hard_split" for volume in volumes)
    # The cut is named, with the exact range, so a person can check it.
    assert all("没有删字" in (volume.get("split_note") or "") for volume in volumes)
    assert [w["code"] for w in payload["warnings"]].count("VOLUME_HARD_SPLIT") == len(volumes)
    assert "VOLUME_MAY_EXCEED_STORYBOARD_INLINE_LIMIT" not in [w["code"] for w in payload["warnings"]]


def test_a_titled_manuscript_is_still_split_by_its_own_chapters(api):
    """The hard cut must not replace real chapter boundaries."""
    client, factory, _ = api
    pid = create_project(client, "有标题长篇")
    chapter = "德明把缆绳绕在桩上。" * 3_000
    text = f"第一章 出海\n{chapter}\n第二章 归来\n{chapter}\n"
    doc = _document(factory, pid, text)
    with factory.begin() as db:
        plan = pipeline.plan_volumes(db, pid, db.get(type(doc), doc.id))
    volumes = plan.payload_json["volumes"]
    assert len(volumes) == 2, [(v["title"], v["char_count"]) for v in volumes]
    assert all(volume["boundary_reason"] != "hard_split" for volume in volumes)
    assert all("split_note" not in volume for volume in volumes)


def test_a_project_without_any_v2_records_still_reads_and_reports_not_run(api):
    """Old work keeps its read path: no review, no memory, no attempt rows."""
    client, factory, _ = api
    pid = create_project(client, "旧项目")
    text = "第一章 出海\n德明出海了。\n第二章 归来\n德明回来了。"
    doc = _document(factory, pid, text)
    with factory.begin() as db:
        plan = pipeline.plan_volumes(db, pid, db.get(type(doc), doc.id))
        pipeline.confirm_volume_plan(db, pid, plan.id, plan.sha256)
        pipeline.materialize_volume_documents(db, pid, plan.id)
    volume_id = plan.payload_json["volumes"][0]["volume_id"]

    overview = client.get(f"/api/v1/projects/{pid}/pipeline")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["volume_plan_confirmed"] is True
    assert body["volumes"], body
    assert body["volumes"][0]["scripts"] == [], "no outline yet means no script entries"

    # A pre-V2.1 task trace has none of the new keys; the summary must read it.
    with factory.begin() as db:
        legacy_id = pipeline._authoring_task(
            db, pid, pipeline.SCENE_SCRIPT_RUN_ROLE,
            {"stage": "NEEDS_REVIEW", "blocked_reason": "LLM_HTTP",
             "external_outcome": "UNKNOWN", "attempt": 1})
        row = db.get(AgentTaskRecord, legacy_id)
        row.status = "NEEDS_REVIEW"
        row.error_code = "LLM_HTTP"
    summary = client.get(f"/api/v1/projects/{pid}/pipeline").json()["active_tasks"]
    assert any(item["status"] == "NEEDS_REVIEW" for item in summary), summary

    # No review exists for this project, and the honest answer says so.
    sections = client.get(f"/api/v1/projects/{pid}/pipeline").json()["volumes"][0]
    assert sections["scripts"] == []
    missing = client.get(
        f"/api/v1/projects/{pid}/pipeline/volumes/{volume_id}/sections/anything/qa")
    assert missing.json() == {"state": "NOT_RUN", "checks": []}


def test_the_four_stage_chain_records_a_review_for_every_scene_it_wrote(api):
    """The review runs inside the real chain, and never confirms a script."""
    from tests.test_pipeline import _prepare_handoff_fixture

    _client, factory, _settings, pid, _plan, volume_id, units = _prepare_handoff_fixture(api)
    with factory() as db:
        reports = db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == pid,
            AgentArtifact.kind == qa.REPORT_KIND)).all()
        scene_reports = [row for row in reports
                         if (row.payload_json or {}).get("source") != "storyboard"]
        assert len(scene_reports) == len(units), [(row.payload_json or {}).get("section_id")
                                                  for row in scene_reports]
        for row in scene_reports:
            payload = row.payload_json
            # A review is evidence beside the text, not a second script.
            assert payload["script_sha256"]
            script = db.get(AgentArtifact, payload["script_artifact_id"])
            assert script.kind == pipeline.SCENE_SCRIPT_KIND
            assert script.sha256 == payload["script_sha256"]
            assert script.approved_by == "local-user", "the fixture confirmed it, not the review"
            assert payload["state"] in {triage.INFO, triage.WARNING,
                                        triage.LOCAL_REVIEW, triage.HARD_BLOCK}

    overview = _client.get(f"/api/v1/projects/{pid}/pipeline").json()
    entries = next(v for v in overview["volumes"] if v["volume_id"] == volume_id)["scripts"]
    assert entries and all("qa" in entry for entry in entries), entries
