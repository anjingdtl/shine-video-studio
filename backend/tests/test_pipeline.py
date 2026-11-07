"""The four-stage pipeline: volume planning, bible, scene scripts.

Every stage persists a versioned artifact, is confirmed by a person, and
records provenance.  Unknown provider outcomes stay blocked; an edited
script starts unconfirmed; the volume plan covers the master completely.
"""
from __future__ import annotations

from shine.director import pipeline
from shine.models import AgentArtifact, AppSetting, ProjectDocument
from tests.conftest import create_project

LOCAL = {"x-shine-local-action": "1"}


def _make_doc(factory, project_id, text):
    """Insert a master document row directly."""
    import hashlib

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with factory.begin() as db:
        doc = ProjectDocument(project_id=project_id, filename="母本.txt", content_type="text/plain",
                              sha256=sha, source_format="txt", text=text)
        db.add(doc)
        db.flush()
        return doc


def _outline_artifact(db, project_id, doc, sections):
    """A confirmed StoryOutline artifact for the pipeline contract tests."""
    payload = {
        "schema_version": "1.0",
        "document_id": doc.id, "document_sha256": doc.sha256,
        "original_text_sha256": doc.sha256,
        "offset_unit": "unicode_codepoint",
        "production_sections": sections,
        "characters": [{"name": "阿德", "stable_identity": "u1", "role": "主角"}],
        "story_line": ["德明出海", "归来", "团聚"],
        "timeline": ["第一天出海", "第三天归来"],
        "questions": [],
        "source_locked_facts": [], "locked_facts": ["德明是渔民"],
        "leaf_artifact_ids": [],
        "continuity_status": {"basis": "user_supplied_unverified"},
    }
    from shine.h3.rulebook import digest
    a = AgentArtifact(project_id=project_id, kind="StoryOutline", schema_version="1.0",
                      payload_json=payload, sha256=digest(payload),
                      producer_role="story_outline_run", approved_by="local-user",
                      approved_at="2026-11-08T00:00:00Z")
    db.add(a)
    db.flush()
    db.merge(AppSetting(key=f"current-story-outline:{project_id}",
                        value={"artifact_id": a.id, "sha256": a.sha256}))
    db.flush()
    return a
def _sections_for(doc_text):
    """Two sections over the doc text, mirroring source_sections' contract."""
    from shine.materialprep.story import source_sections
    real = source_sections("p-fix", type("D", (), {"text": doc_text})())
    return real


def test_short_novel_plans_one_volume(api):
    client, factory, settings = api
    pid = create_project(client, "短篇")
    doc = _make_doc(factory, pid, "第一章 出海\n德明出海了。\n第二章 归来\n德明回来了。")
    with factory() as db:
        plan = pipeline.plan_volumes(db, pid, doc)
        db.commit()
        assert len(plan.payload_json["volumes"]) == 1
        v = plan.payload_json["volumes"][0]
        assert v["start_offset"] == 0
        assert v["end_offset"] == len(doc.text)
        assert plan.payload_json["coverage"]["complete"] is True


def test_long_novel_groups_by_budget_at_section_boundaries(api):
    """Sections are grouped at boundaries; coverage is complete and ordered."""
    client, factory, settings = api
    pid = create_project(client, "长篇")
    ch = "第一章 出海\n" + "海很深。" * 4000 + "\n"
    doc = _make_doc(factory, pid, ch + ch + ch)
    with factory() as db:
        plan = pipeline.plan_volumes(db, pid, doc)
        db.commit()
        volumes = plan.payload_json["volumes"]
        assert len(volumes) >= 2
        assert volumes[0]["start_offset"] == 0
        for left, right in zip(volumes, volumes[1:], strict=False):
            assert left["end_offset"] == right["start_offset"]
        assert volumes[-1]["end_offset"] == len(doc.text)
        covered = sum(v["char_count"] for v in volumes)
        assert covered == len(doc.text)
