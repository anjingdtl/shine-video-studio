"""The four-stage creative pipeline: import, outline, script, storyboard.

The four stages turn one imported master text into per-volume, per-episode,
per-scene confirmed scripts and then into storyboard drafts.  Every stage
persists its own versioned artifact, is confirmed by a person, and records
its provenance.  Nothing here replaces the existing outline, crew chain,
episode plans, packages or runs: the pipeline prepares their inputs.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from shine.h3.rulebook import digest
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument, utcnow
from shine.services import add_audit

SOURCE_MASTER_KIND = "FourStageSourceMaster"
VOLUME_PLAN_KIND = "FourStageVolumePlan"
BIBLE_KIND = "FourStageGlobalStoryBible"
SCENE_SCRIPT_KIND = "FourStageSceneScript"
BIBLE_RUN_ROLE = "four_stage_bible_run"
SCENE_SCRIPT_RUN_ROLE = "four_stage_scene_script_run"

#: A single volume keeps the whole master when the master fits the budget.
SINGLE_VOLUME_MAX_CHARS = 45_000
#: The working budget of one volume: sections are grouped at narrative
#: boundaries within this budget, never mid-section.
TARGET_VOLUME_CHARS = 45_000
#: The pipeline's own claims follow the story tasks' convention.
PIPELINE_CLAIM_SECONDS = 120
PIPELINE_VERSION = "four-stage-pipeline-v1"
POINTER_PREFIX = "four-stage-pointer:"

HEADING_RE = re.compile(
    r"^\s*(第[0-9一二三四五六七八九十百千零两]+[章节回卷幕]|CHAPTER\s+\d+|Chapter\s+\d+|SCENE\s+\d+|Scene\s+\d+|INT\.|EXT\.|场景[0-9一二三四五六七八九十]+)\s*$"
)
VOLUME_MARKER_RE = re.compile(r"^\s*(第[0-9一二三四五六七八九十百千零两]+卷)\s*$")


def _pointer_key(project_id: str, name: str) -> str:
    return f"{POINTER_PREFIX}{project_id}:{name}"


def _read_pointer(db, key: str) -> dict | None:
    from shine.models import AppSetting

    row = db.get(AppSetting, key)
    if row is None:
        return None
    value = row.value
    return value if isinstance(value, dict) else None


def _write_pointer(db, key: str, value: dict) -> None:
    from shine.models import AppSetting

    row = AppSetting(key=key, value=value)
    db.merge(row)


REUSABLE_TASK_STATUSES = ("QUEUED", "RUNNING", "PAUSED", "NEEDS_REVIEW", "SUCCEEDED")


def current_volume_plan(db, project_id: str) -> AgentArtifact | None:
    """The confirmed volume plan artifact, or None when none is confirmed."""
    from shine.models import AgentArtifact

    pointer = _read_pointer(db, _pointer_key(project_id, "volume-plan"))
    if not pointer:
        return None
    artifact = db.get(AgentArtifact, pointer.get("artifact_id", ""))
    if artifact is None or artifact.kind != VOLUME_PLAN_KIND:
        return None
    return artifact


def current_bible(db, project_id: str) -> AgentArtifact | None:
    """The confirmed GlobalStoryBible artifact, or None."""
    from shine.models import AgentArtifact

    pointer = _read_pointer(db, _pointer_key(project_id, "story-bible"))
    if not pointer:
        return None
    artifact = db.get(AgentArtifact, pointer.get("artifact_id", ""))
    if artifact is None or artifact.kind != BIBLE_KIND:
        return None
    return artifact


def current_scene_script(db, project_id: str, volume_id: str, section_id: str) -> AgentArtifact | None:
    """The confirmed SceneScript for one section, or None."""
    from shine.models import AgentArtifact

    key = _pointer_key(project_id, f"scene-script:{volume_id}:{section_id}")
    pointer = _read_pointer(db, key)
    if not pointer:
        return None
    artifact = db.get(AgentArtifact, pointer.get("artifact_id", ""))
    if artifact is None or artifact.kind != SCENE_SCRIPT_KIND:
        return None
    return artifact


def _authoring_task(db, project_id: str, role_id: str, trace: dict) -> str:
    """The human authoring task every imported artifact must point at."""
    from shine.models import AgentTaskRecord

    row = AgentTaskRecord(
        project_id=project_id, role_id=role_id,
        request_identity=digest({"role": role_id, "project": project_id, "trace": trace}),
        input_hash=digest({"role": role_id, "project": project_id, "trace": trace}),
        asset_manifest_hash=digest({"role": role_id, "project": project_id, "trace": trace}),
        rulepack_id=PIPELINE_VERSION, rulepack_hash=digest(PIPELINE_VERSION),
        model_profile_id="human", model_id="human", status="SUCCEEDED",
        trace={"external_outcome": "NOT_SENT", **trace},
    )
    db.add(row)
    db.flush()
    return row.id


def build_source_master(db, project_id: str, document) -> AgentArtifact:
    """The immutable identity snapshot of the imported master text.

    One SourceMaster per document identity: re-importing the same bytes
    returns the existing master instead of a new one, so downstream pointers
    survive a repeated import.
    """
    from shine.models import AgentArtifact

    text = document.text or ""
    existing = None
    for row in db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == SOURCE_MASTER_KIND)).all():
        if row.payload_json.get("document_id") == document.id:
            existing = row
            break
    if existing and existing.payload_json.get("sha256") == document.sha256:
        return existing
    task_id = _authoring_task(db, project_id, "four_stage_import", {"document_id": document.id})
    artifact = AgentArtifact(
        project_id=project_id,
        kind=SOURCE_MASTER_KIND,
        schema_version="1.0",
        payload_json={
            "document_id": document.id,
            "document_sha256": document.sha256,
            "char_count": len(text),
            "line_count": text.count("\n") + 1,
            "warnings": [],
            "coverage": {"start_offset": 0, "end_offset": len(text), "complete": True},
        },
        sha256=document.sha256,
        producer_role="import",
        input_revision_ids=[document.id],
        source_task_id=task_id,
    )
    db.add(artifact)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_SOURCE_MASTER_CREATED", "agent_artifact",
              artifact.id, {"document_sha256": document.sha256}, project_id=project_id)
    return artifact


def detect_volume_boundaries(text: str, sections: list[dict]) -> list[dict]:
    """Group the master's sections into volume candidates.

    Boundaries only fall on section boundaries: a volume is a run of whole
    sections.  Explicit 第X卷 markers split volumes; otherwise sections are
    grouped by the target character budget.  A master that fits one volume
    stays one volume - the plan never splits a chapter in half.
    """
    if not sections:
        return [{"order": 1, "title": "全本", "start_offset": 0,
                 "end_offset": len(text), "section_ids": [],
                 "boundary_reason": "no_sections"}]
    hard = [s["start_offset"] for s in sections if VOLUME_MARKER_RE.match(s.get("title", ""))]
    groups: list[list[dict]] = [[]]
    current_size = 0
    for section in sections:
        size = section["end_offset"] - section["start_offset"]
        if hard and section["start_offset"] in hard and groups[-1]:
            groups.append([])
            current_size = 0
        elif current_size + size > TARGET_VOLUME_CHARS and groups[-1]:
            groups.append([])
            current_size = 0
        groups[-1].append(section)
        current_size += size
    return [
        {
            "order": index + 1,
            "title": f"第{index + 1}卷",
            "start_offset": group[0]["start_offset"],
            "end_offset": group[-1]["end_offset"],
            "section_ids": [s["production_section_id"] for s in group],
            "char_count": sum(s["end_offset"] - s["start_offset"] for s in group),
            "boundary_reason": "volume_marker" if group[0]["start_offset"] in hard else "budget_grouping",
        }
        for index, group in enumerate(groups) if group
    ]


def source_sections_safe(project_id: str, document) -> list[dict]:
    """The master's section boundaries (the story module's own computation)."""
    from shine.materialprep.story import source_sections

    return source_sections(project_id, document)


def plan_volumes(db, project_id: str, document) -> AgentArtifact:
    """One draft VolumePlan for the master, covering the text completely."""
    from shine.models import AgentArtifact

    master = build_source_master(db, project_id, document)
    sections = source_sections_safe(project_id, document)
    boundaries = detect_volume_boundaries(document.text or "", sections)
    total = len(document.text or "")
    volumes = []
    for index, boundary in enumerate(boundaries):
        volume_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"shine:{project_id}:volume:{index + 1}:{total}"))
        volumes.append({
            "volume_id": volume_id, "order": index + 1,
            "title": boundary["title"],
            "start_offset": boundary["start_offset"],
            "end_offset": boundary["end_offset"],
            "section_ids": boundary["section_ids"],
            "char_count": boundary.get("char_count", boundary["end_offset"] - boundary["start_offset"]),
            "boundary_reason": boundary["boundary_reason"],
        })
    payload = {
        "source_master": {"document_id": document.id, "sha256": document.sha256},
        "master_char_count": total,
        "volumes": volumes,
        "coverage": {"start_offset": 0, "end_offset": total,
                     "complete": sum(v["char_count"] for v in volumes) == total},
        "status": "DRAFT",
    }
    task_id = _authoring_task(db, project_id, "four_stage_import", {"master": document.id})
    artifact = AgentArtifact(
        project_id=project_id,
        kind=VOLUME_PLAN_KIND,
        schema_version="1.0",
        payload_json=payload,
        sha256=digest(payload),
        producer_role="import",
        input_revision_ids=[master.id],
        source_task_id=task_id,
    )
    db.add(artifact)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_VOLUME_PLAN_DRAFTED", "agent_artifact",
              artifact.id, {"volume_count": len(volumes)}, project_id=project_id)
    return artifact


def confirm_volume_plan(db, project_id: str, artifact_id: str, expected_sha256: str) -> AgentArtifact:
    """A person confirms the volume plan; the pointer names the confirmed revision."""
    from shine.models import AgentArtifact, AppSetting

    artifact = db.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id or artifact.kind != VOLUME_PLAN_KIND:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_FOUND")
    if artifact.sha256 != expected_sha256:
        raise ValueError("PIPELINE_VOLUME_PLAN_SHA_MISMATCH")
    row = AppSetting(key=_pointer_key(project_id, "volume-plan"),
                     value={"artifact_id": artifact.id, "sha256": artifact.sha256})
    db.merge(row)
    artifact.approved_by = "local-user"
    artifact.approved_at = utcnow()
    db.flush()
    add_audit(db, "local-user", "PIPELINE_VOLUME_PLAN_CONFIRMED", "agent_artifact",
              artifact.id, {"sha256": artifact.sha256}, project_id=project_id)
    return artifact


def materialize_volume_documents(db, project_id: str, plan_artifact_id: str) -> list:
    """Create one derived ProjectDocument per volume from the confirmed plan."""
    from shine.models import ProjectDocument

    plan = db.get(AgentArtifact, plan_artifact_id)
    if plan is None or plan.kind != VOLUME_PLAN_KIND:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_FOUND")
    created = []
    master_doc_id = plan.payload_json["source_master"]["document_id"]
    master = db.get(ProjectDocument, master_doc_id)
    if master is None or master.project_id != project_id:
        raise ValueError("PIPELINE_MASTER_DOCUMENT_NOT_FOUND")
    for volume in plan.payload_json["volumes"]:
        start = volume["start_offset"]
        end = volume["end_offset"]
        text = (master.text or "")[start:end]
        provenance = {
            "kind": "four_stage_volume", "master_document_id": master.id,
            "master_sha256": master.sha256, "volume_id": volume["volume_id"],
            "volume_order": volume["order"], "volume_title": volume["title"],
            "start_offset": start, "end_offset": end,
        }
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = None
        for row in db.scalars(select(ProjectDocument).where(
                ProjectDocument.project_id == project_id,
                ProjectDocument.source_format == "volume")).all():
            parsed = row.parsed if isinstance(row.parsed, dict) else {}
            if parsed.get("kind") == "four_stage_volume" and parsed.get("volume_id") == volume["volume_id"] \
                    and parsed.get("master_sha256") == master.sha256 \
                    and parsed.get("start_offset") == start and parsed.get("end_offset") == end:
                existing = row
                break
        if existing:
            created.append(existing)
            continue
        doc = ProjectDocument(
            project_id=project_id,
            filename=f"第{volume['order']}卷-{volume['title']}.md",
            content_type="text/markdown",
            sha256=sha,
            source_format="volume",
            text=text,
            parsed=provenance,
        )
        db.add(doc)
        db.flush()
        created.append(doc)
    if created:
        add_audit(db, "local-user", "PIPELINE_VOLUME_DOCUMENTS_MATERIALIZED",
                  "agent_artifact", plan_artifact_id,
                  {"volume_count": len(created)}, project_id=project_id)
    return created


class GlobalStoryBiblePayload(BaseModel):
    """The cross-volume blueprint: who, where, what is promised, what is open."""
    schema_version: str = "1.0"
    mainline: str = Field(min_length=1, max_length=2000)
    characters: list[dict] = Field(default_factory=list, max_length=64)
    world: str = Field(default="", max_length=2000)
    timeline: list[str] = Field(default_factory=list, max_length=48)
    foreshadowing: list[str] = Field(default_factory=list, max_length=48)
    locked_facts: list[str] = Field(default_factory=list, max_length=48)
    open_questions: list[dict] = Field(default_factory=list, max_length=48)
    ending_direction: str = Field(default="", max_length=400)


class SceneScriptPayload(BaseModel):
    """One scene's confirmed script text with its provenance."""
    scene_key: str = Field(min_length=1, max_length=64)
    section_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=24000)
    characters: list[str] = Field(default_factory=list, max_length=24)
    dialogue: list[dict] = Field(default_factory=list, max_length=64)
    estimated_seconds: int = Field(default=0, ge=0, le=3600)
    source_refs: dict = Field(default_factory=dict)
    continuity_in: str = Field(default="", max_length=400)
    continuity_out: str = Field(default="", max_length=400)
    questions: list[str] = Field(default_factory=list, max_length=8)


def queue_bible(db, project_id: str, gateway) -> AgentTaskRecord:
    """Queue one bible synthesis task for the project's current master."""
    from shine.materialprep import text_consent
    from shine.models import AgentArtifact, AgentTaskRecord

    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    master_doc = db.get(ProjectDocument, plan.payload_json["source_master"]["document_id"])
    if master_doc is None:
        raise ValueError("PIPELINE_MASTER_DOCUMENT_NOT_FOUND")
    outlines = []
    for row in db.scalars(select(AgentArtifact).where(
            AgentArtifact.kind == "StoryOutline",
            AgentArtifact.approved_at.is_not(None))).all():
        outlines.append(row)
    if not outlines:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    profile = gateway.profile_for_role(db, BIBLE_RUN_ROLE)
    fingerprint = text_consent.fingerprint(profile)
    base = {
        "project": project_id, "master": master_doc.id, "master_sha": master_doc.sha256,
        "outlines": [{"id": o.id, "sha": o.sha256} for o in outlines],
        "facts": [], "profile": fingerprint, "tool": BIBLE_RUN_ROLE, "version": PIPELINE_VERSION,
    }
    identity = digest(base)
    attempts = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == BIBLE_RUN_ROLE,
        AgentTaskRecord.input_hash == identity)).all()
    for old in attempts:
        if old.status in REUSABLE_TASK_STATUSES:
            return old
    row = AgentTaskRecord(
        project_id=project_id,
        role_id=BIBLE_RUN_ROLE,
        request_identity=identity,
        input_hash=identity,
        asset_manifest_hash=identity,
        rulepack_id=PIPELINE_VERSION,
        rulepack_hash=digest(PIPELINE_VERSION),
        model_profile_id=profile.id,
        model_id=profile.model,
        schema_version="1.0",
        status="QUEUED",
        trace={
            "stage": "DRAFT", "cursor": 0, "automatic_replay": False,
            "document_id": master_doc.id, "document_sha256": master_doc.sha256,
            "outline_refs": [{"id": o.id, "sha256": o.sha256} for o in outlines],
            "provider_fingerprint": fingerprint, "base_identity": identity,
        },
    )
    db.add(row)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_BIBLE_QUEUED", "agent_task", row.id,
              {"master_sha256": master_doc.sha256}, project_id=project_id)
    return row


def _claim_pipeline_task(session_factory, worker_id: str, role: str):
    """Take one queued or expired pipeline task; the claim is a fenced UPDATE."""
    from sqlalchemy import update

    from shine.models import AgentTaskRecord

    now = utcnow()
    for _ in range(3):
        with session_factory() as db:
            task = db.scalars(
                select(AgentTaskRecord)
                .where(
                    AgentTaskRecord.role_id == role,
                    or_(
                        AgentTaskRecord.status == "QUEUED",
                        (AgentTaskRecord.status == "RUNNING") & (
                            AgentTaskRecord.lease_expires_at.is_(None)
                            | (AgentTaskRecord.lease_expires_at <= now)
                        ),
                    ),
                )
                .order_by(AgentTaskRecord.created_at)
                .limit(1)
            ).first()
            if task is None:
                return None
            task_id, version = task.id, task.state_version
            trace = dict(task.trace or {})
        with session_factory.begin() as db:
            claimed = db.execute(
                update(AgentTaskRecord)
                .where(
                    AgentTaskRecord.id == task_id,
                    AgentTaskRecord.state_version == version,
                    or_(
                        AgentTaskRecord.status == "QUEUED",
                        (AgentTaskRecord.status == "RUNNING") & (
                            AgentTaskRecord.lease_expires_at.is_(None)
                            | (AgentTaskRecord.lease_expires_at <= now)
                        ),
                    ),
                )
                .values(status="RUNNING", lease_owner=worker_id,
                        lease_expires_at=now + timedelta(seconds=PIPELINE_CLAIM_SECONDS * 15),
                        state_version=AgentTaskRecord.state_version + 1)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount == 1:
                return task_id, version + 1, trace
    return None


def _finish_pipeline_task(session_factory, task_id, version, worker_id, trace,
                          status: str = "QUEUED", error: str | None = None) -> bool:
    """Close a task under the full fence: status, owner and version must match."""
    from sqlalchemy import update

    from shine.models import AgentTaskRecord

    with session_factory.begin() as db:
        changed = db.execute(update(AgentTaskRecord).where(
            AgentTaskRecord.id == task_id,
            AgentTaskRecord.state_version == version,
            AgentTaskRecord.lease_owner == worker_id,
            AgentTaskRecord.status == "RUNNING",
        ).values(trace=trace, status=status, error_code=error,
                 lease_owner=None, lease_expires_at=None,
                 state_version=version + 1)).rowcount
        return bool(changed)


def _persist_pipeline_sending(session_factory, task_id, version, worker_id, trace) -> dict | None:
    """Record the intent to send before the external call happens."""
    from sqlalchemy import update

    from shine.models import AgentTaskRecord

    updated = {**trace, "stage": "SENDING", "external_outcome": "SENDING",
               "sent_at": utcnow().isoformat() + "Z"}
    with session_factory.begin() as db:
        changed = db.execute(update(AgentTaskRecord).where(
            AgentTaskRecord.id == task_id,
            AgentTaskRecord.state_version == version,
            AgentTaskRecord.lease_owner == worker_id,
            AgentTaskRecord.status == "RUNNING",
        ).values(trace=updated)).rowcount
    return updated if changed == 1 else None


def run_bible_task(factory, settings, gateway, task_id: str, version: int, worker_id: str) -> bool:
    """Execute one bible synthesis task under the fenced claim."""
    from shine.materialprep.agent_call import call_material_agent
    from shine.models import AgentArtifact, AgentTaskRecord

    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        if task is None:
            return False
        trace = dict(task.trace or {})
        project_id = task.project_id
        master_doc = db.get(ProjectDocument, trace["document_id"])
        if master_doc is None or master_doc.sha256 != trace["document_sha256"]:
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_MASTER_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_MASTER_CHANGED")
            return True
        outlines = []
        for ref in trace["outline_refs"]:
            outline = db.get(AgentArtifact, ref["id"])
            if (outline is None or outline.stale or outline.sha256 != ref["sha256"]
                    or not outline.approved_at):
                _finish_pipeline_task(factory, task_id, version, worker_id,
                                      {**trace, "blocked_reason": "PIPELINE_OUTLINE_CHANGED"},
                                      "NEEDS_REVIEW", "PIPELINE_OUTLINE_CHANGED")
                return True
            outlines.append(outline)
        context = {
            "master": {"char_count": len(master_doc.text or ""), "outline_count": len(outlines)},
            "outline_summaries": [
                {"order": index + 1,
                 "story_line": o.payload_json.get("story_line", []),
                 "timeline": o.payload_json.get("timeline", []),
                 "characters": o.payload_json.get("characters", []),
                 "questions": o.payload_json.get("questions", []),
                 "sections": [
                     {"scene_key": s.get("scene_key", s.get("production_section_id")),
                      "title": s.get("title"), "summary": s.get("summary", "")}
                     for s in (o.payload_json.get("production_sections") or [])],
                 }
                for index, o in enumerate(outlines)],
        }
        schema = GlobalStoryBiblePayload.model_json_schema()
        sent = _persist_pipeline_sending(factory, task_id, version, worker_id, trace)
        if sent is None:
            return True
        trace = sent
        outcome = call_material_agent(
            gateway, factory, BIBLE_RUN_ROLE,
            system=(
                "你是总编剧与连续性总管。基于给出的逐卷大纲摘要，提炼全书级故事蓝图："
                "主线、人物（姓名/身份/弧线）、世界观、时间线、跨卷伏笔、必须保持的锁定事实、"
                "开放问题与结局方向。只输出 JSON。"
            ),
            context=context,
            schema=schema,
            contract=GlobalStoryBiblePayload,
            task_category="creative",
        )
        if not outcome.ok:
            blocked = {**trace, "stage": "NEEDS_REVIEW",
                       "blocked_reason": outcome.error_code,
                       "external_outcome": outcome.details.get("external_outcome"),
                       "response_status": outcome.details.get("response_status"),
                       "truncated": outcome.details.get("truncated")}
            _finish_pipeline_task(factory, task_id, version, worker_id, blocked, "NEEDS_REVIEW", outcome.error_code)
            return True
        payload = dict(outcome.parsed)
        with factory.begin() as db:
            artifact = AgentArtifact(
                project_id=project_id,
                kind=BIBLE_KIND,
                schema_version="1.0",
                payload_json=payload,
                sha256=digest(payload),
                producer_role=BIBLE_RUN_ROLE,
                source_task_id=task_id,
                input_revision_ids=[ref["id"] for ref in trace["outline_refs"]],
            )
            db.add(artifact)
            db.flush()
            updated = {**trace, "stage": "DOCUMENT", "artifact_id": artifact.id,
                       "artifact_sha256": artifact.sha256,
                       "usage": outcome.details.get("usage"),
                       "output_budget": outcome.details.get("output_budget")}
        _finish_pipeline_task(factory, task_id, version, worker_id, updated, "SUCCEEDED")
        return True


def confirm_bible(db, project_id: str, artifact_id: str, expected_sha256: str) -> AgentArtifact:
    """A person confirms the global story bible; the pointer names it."""
    from shine.models import AgentArtifact, AppSetting

    artifact = db.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id or artifact.kind != BIBLE_KIND:
        raise ValueError("PIPELINE_BIBLE_NOT_FOUND")
    if artifact.sha256 != expected_sha256:
        raise ValueError("PIPELINE_BIBLE_SHA_MISMATCH")
    row = AppSetting(key=_pointer_key(project_id, "story-bible"),
                     value={"artifact_id": artifact.id, "sha256": artifact.sha256})
    db.merge(row)
    artifact.approved_by = "local-user"
    artifact.approved_at = utcnow()
    db.flush()
    add_audit(db, "local-user", "PIPELINE_BIBLE_CONFIRMED", "agent_artifact", artifact.id,
              {"sha256": artifact.sha256}, project_id=project_id)
    return artifact


def _volume_document(db, project_id: str, volume_id: str):
    """The derived volume document row for one volume id."""
    from shine.models import ProjectDocument

    for row in db.scalars(select(ProjectDocument).where(
            ProjectDocument.project_id == project_id,
            ProjectDocument.source_format == "volume")).all():
        parsed = row.parsed if isinstance(row.parsed, dict) else {}
        if parsed.get("kind") == "four_stage_volume" and parsed.get("volume_id") == volume_id:
            return row
    return None


def _volume_outline(db, project_id: str, volume_id: str):
    """The confirmed StoryOutline artifact of one volume's document."""
    from shine.models import AgentArtifact

    doc = _volume_document(db, project_id, volume_id)
    if doc is None:
        return None
    pointer = _read_pointer(db, f"current-story-outline:{project_id}:{doc.id}")
    if pointer is None:
        pointer = _read_pointer(db, f"current-story-outline:{project_id}")
    if pointer is None:
        return None
    artifact = db.get(AgentArtifact, pointer.get("artifact_id", ""))
    if artifact is None or artifact.stale or not artifact.approved_at:
        return None
    return artifact


def queue_scene_scripts(db, project_id: str, volume_id: str, gateway) -> list:
    """Queue one script task per section that has no confirmed script yet."""
    from shine.materialprep import text_consent
    from shine.models import AgentTaskRecord

    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    volume = next((v for v in plan.payload_json["volumes"] if v["volume_id"] == volume_id), None)
    if volume is None:
        raise ValueError("PIPELINE_VOLUME_NOT_IN_PLAN")
    outline = _volume_outline(db, project_id, volume_id)
    if outline is None:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    bible = current_bible(db, project_id)
    if bible is None:
        raise ValueError("PIPELINE_BIBLE_NOT_CONFIRMED")
    sections = outline.payload_json.get("production_sections") or []
    volume_doc = _volume_document(db, project_id, volume_id)
    if volume_doc is None:
        raise ValueError("PIPELINE_VOLUME_DOCUMENT_MISSING")
    profile = gateway.profile_for_role(db, SCENE_SCRIPT_RUN_ROLE)
    fingerprint = text_consent.fingerprint(profile)
    created = []
    for section in sections:
        section_id = section["production_section_id"]
        if current_scene_script(db, project_id, volume_id, section_id):
            continue
        section_ctx = {
            "section_id": section_id,
            "scene_key": section.get("scene_key", section_id),
            "title": section.get("title", ""),
            "summary": section.get("summary", ""),
            "order": section.get("order"),
            "start_offset": section.get("start_offset"),
            "end_offset": section.get("end_offset"),
        }
        base = {
            "project": project_id, "volume": volume_id, "section": section_id,
            "master": plan.payload_json["source_master"]["sha256"],
            "outline": outline.sha256, "bible": bible.sha256,
            "profile": fingerprint, "tool": SCENE_SCRIPT_RUN_ROLE, "version": PIPELINE_VERSION,
        }
        identity = digest(base)
        attempts = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id == SCENE_SCRIPT_RUN_ROLE,
            AgentTaskRecord.input_hash == identity)).all()
        existing_task = next((a for a in attempts if a.status in REUSABLE_TASK_STATUSES), None)
        if existing_task:
            created.append(existing_task)
            continue
        task = AgentTaskRecord(
            project_id=project_id,
            role_id=SCENE_SCRIPT_RUN_ROLE,
            request_identity=identity,
            input_hash=identity,
            asset_manifest_hash=identity,
            rulepack_id=PIPELINE_VERSION,
            rulepack_hash=digest(PIPELINE_VERSION),
            model_profile_id=profile.id,
            model_id=profile.model,
            schema_version="1.0",
            status="QUEUED",
            trace={
                "stage": "DRAFT", "automatic_replay": False,
                "volume_id": volume_id,
                "volume_document_id": volume_doc.id,
                "volume_document_sha256": volume_doc.sha256,
                "section": section_ctx,
                "outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                "bible_artifact_id": bible.id, "bible_sha256": bible.sha256,
                "provider_fingerprint": fingerprint, "base_identity": identity,
            },
        )
        db.add(task)
        created.append(task)
    if created:
        db.flush()
        add_audit(db, "local-user", "PIPELINE_SCENE_SCRIPTS_QUEUED", "agent_task",
                  created[0].id, {"queued": len(created)}, project_id=project_id)
    return created


def run_scene_script_task(factory, settings, gateway, task_id: str, version: int, worker_id: str) -> bool:
    """Execute one scene-script task under the fenced claim."""
    from shine.materialprep.agent_call import call_material_agent
    from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument

    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        if task is None:
            return False
        trace = dict(task.trace or {})
        project_id = task.project_id
        volume_doc = db.get(ProjectDocument, trace["volume_document_id"])
        if volume_doc is None or volume_doc.sha256 != trace["volume_document_sha256"]:
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_VOLUME_DOCUMENT_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_VOLUME_DOCUMENT_CHANGED")
            return True
        bible = db.get(AgentArtifact, trace["bible_artifact_id"])
        if (bible is None or bible.stale or bible.sha256 != trace["bible_sha256"]
                or not bible.approved_at):
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_BIBLE_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_BIBLE_CHANGED")
            return True
        outline = db.get(AgentArtifact, trace["outline_artifact_id"])
        if (outline is None or outline.stale or outline.sha256 != trace["outline_sha256"]
                or not outline.approved_at):
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_OUTLINE_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_OUTLINE_CHANGED")
            return True
        section = trace["section"]
        start = section.get("start_offset")
        end = section.get("end_offset")
        source_text = ""
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(volume_doc.text or ""):
            source_text = volume_doc.text[start:end]
        section_order = section.get("order")
        previous_script = None
        if isinstance(section_order, int) and section_order > 1:
            prior_section = next((s for s in (outline.payload_json.get("production_sections") or [])
                                  if s.get("order") == section_order - 1), None)
            if prior_section:
                previous_script = current_scene_script(
                    db, project_id, trace["volume_id"], prior_section["production_section_id"])
        context = {
            "bible": _bible_snapshot(bible.payload_json),
            "section": {"scene_key": section.get("scene_key", section["section_id"]),
                        "title": section.get("title"), "summary": section.get("summary"),
                        "order": section_order},
            "source_text": source_text,
            "previous_ending": (previous_script.payload_json.get("continuity_out", "")
                                if previous_script else None),
        }
        schema = SceneScriptPayload.model_json_schema()
        sent = _persist_pipeline_sending(factory, task_id, version, worker_id, trace)
        if sent is None:
            return True
        trace = sent
        outcome = call_material_agent(
            gateway, factory, SCENE_SCRIPT_RUN_ROLE,
            system=(
                "你是剧集编剧。基于全书故事蓝图、本段原文与上一场的收尾状态，写出这一场的完整剧本："
                "对白、动作、场景与时间空间，并把收尾状态写入 continuity_out。只输出 JSON。"
            ),
            context=context,
            schema=schema,
            contract=SceneScriptPayload,
            task_category="creative",
        )
        if not outcome.ok:
            blocked = {**trace, "stage": "NEEDS_REVIEW",
                       "blocked_reason": outcome.error_code,
                       "external_outcome": outcome.details.get("external_outcome"),
                       "response_status": outcome.details.get("response_status"),
                       "truncated": outcome.details.get("truncated")}
            _finish_pipeline_task(factory, task_id, version, worker_id, blocked, "NEEDS_REVIEW", outcome.error_code)
            return True
        payload = dict(outcome.parsed)
        payload["scene_key"] = section.get("scene_key", section["section_id"])
        payload["section_id"] = section["section_id"]
        payload["source_refs"] = {"volume_id": trace["volume_id"],
                                  "volume_document_id": trace["volume_document_id"],
                                  "start_offset": section.get("start_offset"),
                                  "end_offset": section.get("end_offset"),
                                  "outline_sha256": trace["outline_sha256"]}
        with factory.begin() as db:
            artifact = AgentArtifact(
                project_id=project_id,
                kind=SCENE_SCRIPT_KIND,
                schema_version="1.0",
                payload_json=payload,
                sha256=digest(payload),
                producer_role=SCENE_SCRIPT_RUN_ROLE,
                source_task_id=task_id,
                input_revision_ids=[trace["bible_artifact_id"], trace["outline_artifact_id"]],
            )
            db.add(artifact)
            db.flush()
            updated = {**trace, "stage": "DOCUMENT", "artifact_id": artifact.id,
                       "artifact_sha256": artifact.sha256,
                       "usage": outcome.details.get("usage"),
                       "output_budget": outcome.details.get("output_budget")}
        _finish_pipeline_task(factory, task_id, version, worker_id, updated, "SUCCEEDED")
        return True


def _bible_snapshot(payload: dict) -> dict:
    """The compact bible facts the scene writer sees (bounded for the window)."""
    return {
        "mainline": payload.get("mainline", "")[:2000],
        "characters": [
            {"name": c.get("name"), "identity": c.get("identity", "")[:200],
             "arc_summary": c.get("arc_summary", "")[:200]}
            for c in (payload.get("characters") or [])[:32]],
        "timeline": [t[:300] for t in (payload.get("timeline") or [])[:24]],
        "foreshadowing": [f[:300] for f in (payload.get("foreshadowing") or [])[:24]],
        "locked_facts": [f[:300] for f in (payload.get("locked_facts") or [])[:24]],
        "ending_direction": payload.get("ending_direction", "")[:400],
    }


def confirm_scene_script(db, project_id: str, artifact_id: str, expected_sha256: str) -> AgentArtifact:
    """A person confirms one scene script; the pointer names the confirmed revision."""
    from shine.models import AgentArtifact, AppSetting

    artifact = db.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id or artifact.kind != SCENE_SCRIPT_KIND:
        raise ValueError("PIPELINE_SCENE_SCRIPT_NOT_FOUND")
    if artifact.sha256 != expected_sha256:
        raise ValueError("PIPELINE_SCENE_SCRIPT_SHA_MISMATCH")
    volume_id = str(artifact.payload_json.get("volume_id"))
    section_id = str(artifact.payload_json.get("section_id"))
    key = _pointer_key(project_id, f"scene-script:{volume_id}:{section_id}")
    row = AppSetting(key=key, value={"artifact_id": artifact.id, "sha256": artifact.sha256})
    db.merge(row)
    artifact.approved_by = "local-user"
    artifact.approved_at = utcnow()
    db.flush()
    add_audit(db, "local-user", "PIPELINE_SCENE_SCRIPT_CONFIRMED", "agent_artifact", artifact.id,
              {"sha256": artifact.sha256}, project_id=project_id)
    return artifact


def edit_scene_script(db, project_id: str, artifact_id: str, new_text: str, note: str = "") -> AgentArtifact:
    """A person's edit creates a NEW version; the old artifact stays history.

    An edited scene script starts unconfirmed again: the person re-confirms it
    after reading, and the old revision stays in the ledger.  The pointer is
    cleared when the edited artifact was the confirmed one, so a stale
    confirmation can never silently cover an unconfirmed text.
    """
    from shine.models import AgentArtifact, AppSetting

    old = db.get(AgentArtifact, artifact_id)
    if not old or old.project_id != project_id or old.kind != SCENE_SCRIPT_KIND:
        raise ValueError("PIPELINE_SCENE_SCRIPT_NOT_FOUND")
    payload = {**old.payload_json, "text": new_text[:24000], "edited": True,
               "edited_from": old.sha256, "edit_note": note[:400]}
    artifact = AgentArtifact(
        project_id=project_id,
        kind=SCENE_SCRIPT_KIND,
        schema_version=old.schema_version,
        payload_json=payload,
        sha256=digest(payload),
        producer_role="user_edit",
        source_task_id=old.source_task_id,
        input_revision_ids=[old.id],
    )
    db.add(artifact)
    db.flush()
    volume_id = str(old.payload_json.get("volume_id"))
    section_id = str(old.payload_json.get("section_id"))
    key = _pointer_key(project_id, f"scene-script:{volume_id}:{section_id}")
    pointer = db.get(AppSetting, key)
    if pointer and pointer.value.get("artifact_id") == old.id:
        db.delete(pointer)
    add_audit(db, "local-user", "PIPELINE_SCENE_SCRIPT_EDITED", "agent_artifact", artifact.id,
              {"from_sha256": old.sha256, "note": note[:400]}, project_id=project_id)
    return artifact


def run_pending_pipeline_task(factory, settings, worker_id: str, gateway=None) -> bool:
    """The worker entry: claim and run one pipeline task (bible or scene script).

    Bible tasks wait for confirmed outlines; scene-script tasks check the
    confirmed bible at execution time.  Both keep the same fencing and the
    same DRAFT->SENDING->RESPONSE->DOCUMENT stages as the creative tasks.
    """
    for role in (BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE):
        claimed = _claim_pipeline_task(factory, worker_id, role)
        if claimed is None:
            continue
        task_id, version, _trace = claimed
        try:
            if role == BIBLE_RUN_ROLE:
                return run_bible_task(factory, settings, gateway, task_id, version, worker_id)
            return run_scene_script_task(factory, settings, gateway, task_id, version, worker_id)
        except HTTPException as exc:
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {"stage": "NEEDS_REVIEW", "blocked_reason": str(exc.detail)},
                                  "NEEDS_REVIEW", str(exc.detail))
            return True
    return False
