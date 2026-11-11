"""The four-stage creative pipeline: import, outline, script, storyboard.

The four stages turn one imported master text into per-volume, per-episode,
per-scene confirmed scripts and then into storyboard drafts.  Every stage
persists its own versioned artifact, is confirmed by a person, and records
its provenance.  Nothing here replaces the existing outline, crew chain,
episode plans, packages or runs: the pipeline prepares their inputs.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from shine.h3.rulebook import digest
from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument, utcnow
from shine.services import add_audit

logger = logging.getLogger(__name__)

SOURCE_MASTER_KIND = "FourStageSourceMaster"
VOLUME_PLAN_KIND = "FourStageVolumePlan"
BIBLE_KIND = "FourStageGlobalStoryBible"
SCENE_SCRIPT_KIND = "FourStageSceneScript"
SCENE_BEAT_KIND = "FourStageSceneBeat"
BIBLE_RUN_ROLE = "four_stage_bible_run"
SCENE_SCRIPT_RUN_ROLE = "four_stage_scene_script_run"

#: One scene's script text limit.  The writer contract and the person's editor
#: share this number, so an edit is refused by name instead of being truncated
#: into a scene that silently lost its ending.
SCENE_SCRIPT_TEXT_LIMIT = 24_000
"""One scene's script text limit, shared by the writer contract and the editor."""

BIBLE_SYSTEM_PROMPT = (
    "你是总编剧与连续性总管。基于给出的逐卷大纲摘要，提炼全书级故事蓝图："
    "主线、人物（姓名/身份/弧线）、世界观、时间线、跨卷伏笔、必须保持的锁定事实、"
    "开放问题与结局方向。锁定事实（locked_facts）与结局方向（ending_direction）必须用英文写，"
    "其余字段用中文。characters 是含 name、identity、arc_summary 字符串的对象数组；"
    "open_questions 是含 question 字符串的对象数组。timeline、foreshadowing 与 locked_facts "
    "均为字符串数组。mainline 和 world 各用一小段，最多 1000 字符；"
    "ending_direction 只写一句英文，最多 200 字符。依据未覆盖结局时，请注明待定。只输出 JSON。"
)
"""The one blueprint prompt.  The dry-run planner measures this exact text."""

SCENE_SYSTEM_PROMPT = (
    "你是剧集编剧。基于全书故事蓝图、本段原文与上一场的收尾状态，写出这一场的完整剧本："
    "对白、动作、场景与时间空间。text 写完整正文；continuity_out 用一两句写本场结束后的关键状态。"
    "不要输出场次编号、来源范围或版本，这些由系统填写。只输出 JSON。"
)
"""The one scene prompt.  Shared by the worker and the dry-run planner."""

#: A single volume keeps the whole master when the master fits the budget.
SINGLE_VOLUME_MAX_CHARS = 45_000
#: The working budget of one volume: sections are grouped at narrative
#: boundaries within this budget, never mid-section.
TARGET_VOLUME_CHARS = 45_000
HARD_SPLIT_MIN_CHARS = 55_000
"""A single section bigger than this cannot be one volume.

It is the same limit the storyboard lane names as its inline input ceiling, so a
chapter below it keeps the old plan - one volume, no cut."""
#: The pipeline's own claims follow the story tasks' convention.
PIPELINE_CLAIM_SECONDS = 120
PIPELINE_VERSION = "four-stage-pipeline-v1"
POINTER_PREFIX = "four-stage-pointer:"

HEADING_RE = re.compile(
    r"^\s*(第[0-9一二三四五六七八九十百千零两]+[章节回卷幕]"
    r"|CHAPTER\s+\d+|Chapter\s+\d+|SCENE\s+\d+|Scene\s+\d+"
    r"|INT\.|EXT\.|场景[0-9一二三四五六七八九十]+)\s*$"
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


class _FenceLost(Exception):
    """The claimed task moved on: this worker may not publish a result."""


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

    unique = {"role": role_id, "project": project_id, "trace": trace,
              "at": utcnow().isoformat()}
    identity = digest(unique)
    row = AgentTaskRecord(
        project_id=project_id, role_id=role_id,
        request_identity=identity,
        input_hash=identity,
        asset_manifest_hash=identity,
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
        if (row.payload_json.get("document_id") == document.id
                and row.payload_json.get("document_sha256") == document.sha256):
            existing = row
            break
    if existing is not None:
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


HARD_SPLIT_PREFERRED_ENDINGS = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?")
"""Where a forced cut may land, best first.  A cut is only forced when one
section alone is bigger than a volume target, which happens for manuscripts with
no headings at all."""


def _hard_split_ranges(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Cut one oversized run at the best boundary, never mid-sentence if avoidable.

    The ranges are contiguous and cover ``[start, end)`` exactly: a hard split
    moves a boundary, it never drops or duplicates a character.
    """
    ranges: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > target:
        window = text[cursor:cursor + target]
        cut = 0
        for marker in HARD_SPLIT_PREFERRED_ENDINGS:
            at = window.rfind(marker)
            if at >= target // 2:
                cut = cursor + at + len(marker)
                break
        if cut <= cursor:
            cut = cursor + target
        ranges.append((cursor, cut))
        cursor = cut
    if cursor < end:
        ranges.append((cursor, end))
    return ranges


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
    boundaries: list[dict] = []
    for group in (item for item in groups if item):
        size = sum(s["end_offset"] - s["start_offset"] for s in group)
        reason = "volume_marker" if group[0]["start_offset"] in hard else "budget_grouping"
        if len(group) == 1 and size > HARD_SPLIT_MIN_CHARS:
            # A manuscript without headings is one long section.  The plan may
            # not pretend it fits: it is cut, and the cut is recorded so the
            # person can see exactly where and why.
            section = group[0]
            for start, end in _hard_split_ranges(text, section["start_offset"],
                                                 section["end_offset"], TARGET_VOLUME_CHARS):
                boundaries.append({
                    "start_offset": start, "end_offset": end,
                    "section_ids": [section["production_section_id"]],
                    "char_count": end - start,
                    "boundary_reason": "hard_split",
                    "split_note": (
                        f"原文《{section.get('title') or '未命名段落'}》超过单卷可承载的"
                        f"{HARD_SPLIT_MIN_CHARS} 字，且没有可用的章节边界，"
                        f"已按 {TARGET_VOLUME_CHARS} 字目标在段落处硬切；"
                        f"原文范围 [{start}, {end}) 与前一段连续，没有删字。"),
                })
            continue
        boundaries.append({
            "start_offset": group[0]["start_offset"],
            "end_offset": group[-1]["end_offset"],
            "section_ids": [s["production_section_id"] for s in group],
            "char_count": size,
            "boundary_reason": reason,
        })
    return [
        {"order": index + 1, "title": f"第{index + 1}卷", **boundary}
        for index, boundary in enumerate(boundaries)
    ]


def source_sections_safe(project_id: str, document) -> list[dict]:
    """The master's section boundaries (the story module's own computation)."""
    from shine.materialprep.story import source_sections

    return source_sections(project_id, document)


def plan_volumes(db, project_id: str, document) -> AgentArtifact:
    """One draft VolumePlan for the master, covering the text completely."""
    from shine.models import AgentArtifact

    if document.source_format == "volume":
        raise ValueError("PIPELINE_SOURCE_MUST_BE_MASTER")
    pointer = _read_pointer(db, _pointer_key(project_id, "volume-plan"))
    if pointer is not None:
        confirmed = db.get(AgentArtifact, pointer.get("artifact_id", ""))
        if (confirmed is not None and confirmed.approved_at is not None
                and confirmed.payload_json.get("source_master", {}).get("document_id") != document.id):
            # Master isolation: one confirmed pipeline per project.  A
            # different master requires an explicit supersede, never a
            # silent mix of two masters' volumes.
            raise ValueError("PIPELINE_MASTER_CONFLICT_ALREADY_CONFIRMED")
    master = build_source_master(db, project_id, document)
    sections = source_sections_safe(project_id, document)
    boundaries = detect_volume_boundaries(document.text or "", sections)
    total = len(document.text or "")
    volumes = []
    for index, boundary in enumerate(boundaries):
        volume_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"shine:{project_id}:volume:{index + 1}:{document.sha256}"))
        volumes.append({
            "volume_id": volume_id, "order": index + 1,
            "title": boundary["title"],
            "start_offset": boundary["start_offset"],
            "end_offset": boundary["end_offset"],
            "volume_revision": volume_revision(document.sha256, boundary["start_offset"],
                                               boundary["end_offset"]),
            "section_ids": boundary["section_ids"],
            "char_count": boundary.get("char_count", boundary["end_offset"] - boundary["start_offset"]),
            "boundary_reason": boundary["boundary_reason"],
            # A forced cut travels with the plan: the person who confirms the
            # plan must be able to read where it was cut and why.
            **({"split_note": boundary["split_note"]} if boundary.get("split_note") else {}),
        })
    payload = {
        "source_master": {"document_id": document.id, "sha256": document.sha256},
        "master_char_count": total,
        "volumes": volumes,
        "coverage": {"start_offset": 0, "end_offset": total,
                     "complete": sum(v["char_count"] for v in volumes) == total},
        "warnings": [
            {"volume_id": v["volume_id"],
             "code": "VOLUME_MAY_EXCEED_STORYBOARD_INLINE_LIMIT",
             "detail": "该卷超过 55,000 字，分镜草案可能超出链路单次输入上限；"
                       "请在源文拆分该章或手动调整分卷边界"}
            for v in volumes if v["char_count"] > 55_000
        ] + [
            {"volume_id": v["volume_id"], "code": "VOLUME_HARD_SPLIT",
             "detail": v.get("split_note") or "这一卷是硬切出来的，请确认切口。"}
            for v in volumes if v.get("boundary_reason") == "hard_split"
        ],
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
    if artifact.sha256 != expected_sha256 or digest(artifact.payload_json) != artifact.sha256:
        raise ValueError("PIPELINE_VOLUME_PLAN_SHA_MISMATCH")
    current = current_volume_plan(db, project_id)
    if current is not None:
        if current.id == artifact.id:
            return current
        if current.payload_json["source_master"] != artifact.payload_json["source_master"]:
            raise ValueError("PIPELINE_MASTER_CONFLICT_ALREADY_CONFIRMED")
        if artifact.approved_at or artifact.input_revision_ids != [current.id]:
            raise ValueError("PIPELINE_VOLUME_PLAN_CHANGED")
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
    if plan is None or plan.project_id != project_id or plan.kind != VOLUME_PLAN_KIND:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_FOUND")
    created = []
    master_doc_id = plan.payload_json["source_master"]["document_id"]
    master = db.get(ProjectDocument, master_doc_id)
    if master is None or master.project_id != project_id:
        raise ValueError("PIPELINE_MASTER_DOCUMENT_NOT_FOUND")
    existing_documents = db.scalars(select(ProjectDocument).where(
        ProjectDocument.project_id == project_id, ProjectDocument.source_format == "volume"
    ).order_by(ProjectDocument.created_at, ProjectDocument.id)).all()
    existing_by_range = {
        (row.parsed.get("volume_id"), row.parsed.get("start_offset"), row.parsed.get("end_offset")): row
        for row in existing_documents
        if isinstance(row.parsed, dict) and row.parsed.get("kind") == "four_stage_volume"
        and row.parsed.get("master_document_id") == master.id
        and row.parsed.get("master_sha256") == master.sha256
    }
    for volume in plan.payload_json["volumes"]:
        start = volume["start_offset"]
        end = volume["end_offset"]
        text = (master.text or "")[start:end]
        provenance = {
            "kind": "four_stage_volume", "master_document_id": master.id,
            "master_sha256": master.sha256, "volume_id": volume["volume_id"],
            "volume_order": volume["order"], "volume_title": volume["title"],
            "volume_revision": volume.get("volume_revision"),
            "start_offset": start, "end_offset": end,
        }
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = existing_by_range.get((volume["volume_id"], start, end))
        if existing is not None:
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
    text: str = Field(min_length=1, max_length=SCENE_SCRIPT_TEXT_LIMIT)
    characters: list[str] = Field(default_factory=list, max_length=24)
    dialogue: list[dict] = Field(default_factory=list, max_length=64)
    estimated_seconds: int = Field(default=0, ge=0, le=3600)
    source_refs: dict = Field(default_factory=dict)
    continuity_in: str = Field(default="", max_length=400)
    continuity_out: str = Field(default="", max_length=400)
    questions: list[str] = Field(default_factory=list, max_length=8)


def resolve_plan_outlines(db, project_id: str, plan) -> tuple[list[dict], list[dict]]:
    """The current plan's volumes in order, each with its own confirmed outline.

    The blueprint is a cross-volume document, so its input must be the plan's
    volumes in plan order - never "every confirmed outline the project has".
    A project that holds an older document's outline would otherwise mix two
    ranges into one blueprint and silently lose a volume.  Returns the
    resolved pairs and the volumes that still lack a confirmed outline.
    """
    resolved: list[dict] = []
    missing: list[dict] = []
    for volume in plan.payload_json.get("volumes") or []:
        outline = _volume_outline(db, project_id, volume["volume_id"])
        entry = {"volume_id": volume["volume_id"], "order": volume["order"],
                 "title": volume.get("title", ""),
                 "start_offset": volume.get("start_offset"),
                 "end_offset": volume.get("end_offset"),
                 "volume_revision": volume.get("volume_revision")}
        if outline is None:
            missing.append(entry)
        else:
            resolved.append({**entry, "outline": outline})
    return resolved, missing


def queue_bible(db, project_id: str, gateway) -> AgentTaskRecord:
    """Queue one bible synthesis task over the plan's volumes in order.

    A partial set is allowed for *draft* synthesis and is recorded as such;
    confirming or publishing that blueprint later requires the full set, so a
    missing volume cannot be mistaken for a finished blueprint.
    """
    from shine.materialprep import text_consent
    from shine.models import AgentTaskRecord

    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    master_doc = db.get(ProjectDocument, plan.payload_json["source_master"]["document_id"])
    if master_doc is None or master_doc.project_id != project_id:
        raise ValueError("PIPELINE_MASTER_DOCUMENT_NOT_FOUND")
    resolved, missing = resolve_plan_outlines(db, project_id, plan)
    if not resolved:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    coverage = {
        "complete": not missing,
        "volume_count": len(resolved) + len(missing),
        "covered_volume_ids": [entry["volume_id"] for entry in resolved],
        "missing_volumes": [{"volume_id": entry["volume_id"], "order": entry["order"]}
                            for entry in missing],
        "plan_sha256": plan.sha256,
    }
    profile = gateway.profile_for_role(db, BIBLE_RUN_ROLE)
    fingerprint = text_consent.fingerprint(profile)
    base = {
        "project": project_id, "master": master_doc.id, "master_sha": master_doc.sha256,
        "plan": plan.sha256,
        "outlines": [{"volume": entry["volume_id"], "id": entry["outline"].id,
                      "sha": entry["outline"].sha256} for entry in resolved],
        "facts": [], "profile": fingerprint, "tool": BIBLE_RUN_ROLE, "version": PIPELINE_VERSION,
    }
    identity = digest(base)
    existing = _existing_attempt(db, project_id, BIBLE_RUN_ROLE, identity)
    if existing is not None:
        return existing
    row = AgentTaskRecord(
        project_id=project_id,
        role_id=BIBLE_RUN_ROLE,
        request_identity=attempt_identity(identity, 1),
        input_hash=identity,
        asset_manifest_hash=identity,
        rulepack_id=PIPELINE_VERSION,
        rulepack_hash=digest(PIPELINE_VERSION),
        model_profile_id=profile.id,
        model_id=profile.model,
        schema_version="1.0",
        status="QUEUED",
        trace={
            "stage": "DRAFT", "cursor": 0, "automatic_replay": False, "attempt": 1,
            "result_contract": _result_contract(BIBLE_KIND),
            "input_revision_ids": [entry["outline"].id for entry in resolved],
            "document_id": master_doc.id, "document_sha256": master_doc.sha256,
            "plan_artifact_id": plan.id, "plan_sha256": plan.sha256,
            "outline_coverage": coverage,
            "outline_refs": [{"id": entry["outline"].id, "sha256": entry["outline"].sha256,
                              "volume_id": entry["volume_id"], "order": entry["order"]}
                             for entry in resolved],
            "provider_fingerprint": fingerprint, "base_identity": identity,
        },
    )
    db.add(row)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_BIBLE_QUEUED", "agent_task", row.id,
              {"master_sha256": master_doc.sha256, "volumes": len(resolved),
               "missing_volumes": len(missing)}, project_id=project_id)
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


#: Stages that prove a provider request was opened and may still own an answer.
UNCERTAIN_SEND_STAGES = ("SENDING", "WAITING_PROVIDER", "PREPARING_PROVIDER")

#: Trace keys that describe one finished attempt; a new attempt clears them.
_ATTEMPT_RESULT_KEYS = (
    "artifact_id", "artifact_sha256", "blocked_reason", "sent_at", "adopted",
    "received_result_sha256", "response_status", "truncated", "usage", "output_budget",
)


def _send_outcome_unknown(trace: dict) -> bool:
    """True when the durable ledger cannot rule out that the provider answered.

    The decision reads the persisted outcome, never the error text: a provider
    timeout keeps its own error code (``LLM_HTTP``, ``LLM_PROVIDER_FAULT``) yet
    the answer may exist upstream, so re-queueing it would buy the same call
    twice and lose one of the two answers.
    """
    if trace.get("artifact_id"):
        return False
    if trace.get("external_outcome") == "UNKNOWN":
        return True
    return trace.get("stage") in UNCERTAIN_SEND_STAGES


def _result_contract(kind: str) -> str:
    """The contract identity one artifact of a kind must satisfy."""
    return f"{kind}:{PIPELINE_VERSION}"


def _orphan_candidates(db, task_id: str, project_id: str, kind: str, role: str,
                       trace: dict) -> tuple[list, str | None]:
    """Artifacts a crashed attempt already wrote, judged by their own evidence.

    An artifact only counts as this task's answer when everything a replay
    cannot fake agrees: the source task, the project, the artifact kind, the
    producing role, the contract version, the pinned input chain and a digest
    recomputed from the stored payload.  Two matching rows are ambiguous and
    are reported, never picked arbitrarily.
    """
    from shine.models import AgentArtifact

    contract = trace.get("result_contract")
    if contract:
        expected_kind, _, expected_version = contract.partition(":")
        if expected_kind != kind or expected_version != PIPELINE_VERSION:
            # The task was queued under a different result contract, so its
            # artifacts cannot be judged as this contract's answer.  Say so by
            # name instead of adopting them or silently buying a second call.
            return [], "PIPELINE_RESULT_CONTRACT_MISMATCH"
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.source_task_id == task_id,
        AgentArtifact.project_id == project_id)).all()
    candidates = []
    for row in rows:
        if row.kind != kind or row.producer_role != role or row.stale:
            continue
        if digest(row.payload_json) != row.sha256:
            continue
        pinned = trace.get("input_revision_ids")
        if isinstance(pinned, list) and pinned and list(row.input_revision_ids) != pinned:
            continue
        candidates.append(row)
    if len(candidates) > 1:
        return candidates, "PIPELINE_AMBIGUOUS_RESULT_CANDIDATES"
    return candidates, None


def _adopt_orphan_result(factory, task_id: str, version: int, worker_id: str, trace: dict,
                         project_id: str, kind: str, role: str) -> bool:
    """Adopt a uniquely matching orphan artifact a crashed attempt left behind."""
    with factory() as db:
        candidates, ambiguous = _orphan_candidates(db, task_id, project_id, kind, role, trace)
        if ambiguous:
            _finish_pipeline_task(
                factory, task_id, version, worker_id,
                {**trace, "stage": "NEEDS_REVIEW", "blocked_reason": ambiguous,
                 "orphan_candidate_ids": [row.id for row in candidates][:8]},
                "NEEDS_REVIEW", ambiguous)
            return True
        if not candidates:
            return False
        artifact = candidates[0]
        artifact_id, artifact_sha = artifact.id, artifact.sha256
    _finish_pipeline_task(
        factory, task_id, version, worker_id,
        {**trace, "stage": "DOCUMENT", "adopted": True, "external_outcome": "RESPONSE_RECEIVED",
         "artifact_id": artifact_id, "artifact_sha256": artifact_sha},
        "SUCCEEDED")
    return True


def _pipeline_entry_guard(factory, task_id: str, version: int, worker_id: str, trace: dict,
                          *, project_id: str, kind: str, role: str) -> str:
    """Dispatch on the durable stage BEFORE any provider contact.

    * a persisted pointer: the answer exists - adopt it without a second call.
    * a unique orphan artifact from this task: also adopt, because the answer
      really was written and only the task's own pointer was lost.
    * an uncertain send with no result: the outcome is unknown - stop for
      reconciliation and never replay.
    * DRAFT: safe to proceed to a fresh send.
    Returns "proceed" when the caller may send.
    """
    if trace.get("artifact_id"):
        return "adopt"
    if _adopt_orphan_result(factory, task_id, version, worker_id, trace,
                            project_id, kind, role):
        # A unique orphan is this task's real answer, and an ambiguous set is
        # reported: either way the ledger already proves what happened, so the
        # caller must not open another provider request.
        return "adopted"
    if _send_outcome_unknown(trace):
        _finish_pipeline_task(
            factory, task_id, version, worker_id,
            {**trace, "stage": "NEEDS_REVIEW", "external_outcome": "UNKNOWN",
             "blocked_reason": "PIPELINE_SEND_OUTCOME_UNKNOWN"},
            "NEEDS_REVIEW", "PIPELINE_SEND_OUTCOME_UNKNOWN")
        return "blocked"
    return "proceed"


def _adopt_persisted_artifact(factory, task_id: str, version: int, worker_id: str, trace: dict,
                              project_id: str, kind: str, role: str) -> bool:
    """Finish a task whose answer artifact is already in the ledger."""
    from shine.models import AgentArtifact

    with factory() as db:
        artifact = db.get(AgentArtifact, trace["artifact_id"])
        invalid = (
            artifact is None or artifact.project_id != project_id
            or artifact.sha256 != trace.get("artifact_sha256")
            or artifact.kind != kind or artifact.producer_role != role
            or artifact.source_task_id != task_id
            or digest(artifact.payload_json) != artifact.sha256)
        if invalid:
            _finish_pipeline_task(
                factory, task_id, version, worker_id,
                {**trace, "stage": "NEEDS_REVIEW", "blocked_reason": "PIPELINE_PERSISTED_ARTIFACT_INVALID"},
                "NEEDS_REVIEW", "PIPELINE_PERSISTED_ARTIFACT_INVALID")
            return True
    _finish_pipeline_task(factory, task_id, version, worker_id,
                          {**trace, "stage": "DOCUMENT", "adopted": True,
                           "external_outcome": "RESPONSE_RECEIVED"}, "SUCCEEDED")
    return True


def _commit_pipeline_result(factory, *, task_id: str, version: int, worker_id: str,
                            project_id: str, kind: str, role: str, payload: dict,
                            input_revision_ids: list[str], trace: dict) -> tuple[str, str] | None:
    """Write the answer artifact and close the task in ONE fenced transaction.

    Both rows must commit together or not at all.  A crash after the artifact
    but before the task state used to leave a paid answer nobody could adopt;
    a worker whose lease was taken over must not be able to publish at all,
    so the artifact insert is undone when the fenced task update matches no
    row.  The transaction never wraps a provider call.
    """
    from sqlalchemy import update

    from shine.models import AgentArtifact, AgentTaskRecord

    try:
        with factory.begin() as db:
            artifact = AgentArtifact(
                project_id=project_id,
                kind=kind,
                schema_version="1.0",
                payload_json=payload,
                sha256=digest(payload),
                producer_role=role,
                source_task_id=task_id,
                input_revision_ids=list(input_revision_ids),
            )
            db.add(artifact)
            db.flush()
            updated = {**trace, "stage": "DOCUMENT", "artifact_id": artifact.id,
                       "artifact_sha256": artifact.sha256,
                       "external_outcome": "RESPONSE_RECEIVED",
                       "input_revision_ids": list(input_revision_ids)}
            changed = db.execute(update(AgentTaskRecord).where(
                AgentTaskRecord.id == task_id,
                AgentTaskRecord.state_version == version,
                AgentTaskRecord.lease_owner == worker_id,
                AgentTaskRecord.status == "RUNNING",
            ).values(trace=updated, status="SUCCEEDED", error_code=None,
                     lease_owner=None, lease_expires_at=None,
                     state_version=version + 1)).rowcount
            if changed != 1:
                raise _FenceLost(task_id)
            return artifact.id, artifact.sha256
    except _FenceLost:
        return None


def _scene_qa(*, factory, gateway, project_id: str, task_id: str, trace: dict,
               section: dict, source_text: str, payload: dict,
               unit_budget=None) -> dict | None:
    """L0 first; L1 only for a doubt L0 could not settle.

    Nothing here can rewrite the scene and nothing here can fail it: a QA fault
    is recorded as ``QA_PENDING`` and the script keeps the state it earned.  The
    verdict is a separate artifact, so a later re-review never touches the text.
    """
    from shine.literary import flags, qa
    from shine.literary.governor import UnitBudget
    from shine.materialprep.agent_call import call_material_agent

    unit_id = f"scene:{trace.get('volume_id')}:{section.get('section_id')}"
    # Imported here (not at module scope) so the module keeps its existing import
    # graph; the session factory is the caller's, never a new engine.
    from shine.models import ProjectDocument
    try:
        with factory() as db:
            mode = flags.mode("qa", db, project_id)
            if mode == "off":
                # The switch means what it says: no local report either.
                return None
            guards = qa.memory_guards(db, project_id)
            # The authoritative length of the document the offsets point into.
            document = db.get(ProjectDocument, trace.get("volume_document_id") or "")
            document_length = len(document.text or "") if document is not None else None
        report = qa.check_scene_script(
            payload, unit_id=unit_id, section=section, source_text=source_text,
            source_length=document_length,
            known_characters=guards["known_characters"],
            secret_statements=guards["secret_statements"])
        summary = {"mode": mode, **report.as_dict()}
        if mode != "on" or not report.needs_semantic_review:
            return summary
        request = qa.semantic_request(report, text=str(payload.get("text") or ""),
                                      section=section, guards=guards)
        if request is None:
            return summary
        context, question = request
        outcome = call_material_agent(
            gateway, factory, SCENE_SCRIPT_RUN_ROLE,
            system=qa.SEMANTIC_SYSTEM_PROMPT,
            context=context,
            schema=qa.SemanticReview.model_json_schema(),
            contract=qa.SemanticReview,
            purpose="qa",
            unit_id=f"qa:{trace.get('volume_id')}:{section.get('section_id')}",
            unit_budget=unit_budget or UnitBudget(max_calls=1, max_repair_rounds=0),
            record_attempt=_attempt_recorder(factory, project_id, task_id))
        report = qa.merge_semantic_report(report, outcome)
        return {"mode": mode, **report.as_dict(),
                "semantic_question": question,
                "semantic_cost": qa.estimate_semantic_cost(question, context["excerpt"]),
                "semantic_error": outcome.error_code}
    except Exception:  # noqa: BLE001 - a review never fails a written scene
        logger.exception("literary_qa_failed task=%s unit=%s", task_id, unit_id)
        return {"mode": "error", "state": "WARNING", "level": "L0",
                "checks": [{"code": "QA_PENDING", "severity": "WARNING",
                            "message": "终校未能完成，已记录；不影响本场正文。",
                            "evidence": {}, "unit_id": unit_id}]}


def _persist_qa_report(*, factory, project_id: str, task_id: str, script_artifact_id: str,
                       script_sha256: str, section: dict, summary: dict,
                       volume_id: str | None = None) -> str | None:
    """Store one QA verdict as a versioned artifact bound to the text it judged."""
    from shine.literary import qa
    from shine.models import AgentArtifact

    payload = {
        "volume_id": volume_id,
        "section_id": section.get("section_id"),
        "script_artifact_id": script_artifact_id,
        "script_sha256": script_sha256,
        **summary,
    }
    try:
        with factory.begin() as db:
            artifact = AgentArtifact(
                project_id=project_id, kind=qa.REPORT_KIND, schema_version="1.0",
                payload_json=payload, sha256=digest(payload),
                producer_role=SCENE_SCRIPT_RUN_ROLE, source_task_id=task_id,
                input_revision_ids=[script_artifact_id])
            db.add(artifact)
            db.flush()
            return artifact.id
    except Exception:  # noqa: BLE001 - the verdict is a side record
        logger.exception("literary_qa_report_failed task=%s", task_id)
        return None


QA_MESSAGES = {
    "SCENE_TEXT_MISSING": "这一场没有正文，不能确认。",
    "SCENE_SOURCE_RANGE_INVALID": "这一场引用的原文范围无效，不能确认。",
    "SCENE_CHARACTER_UNKNOWN": "出现了蓝图里没有的人物，请确认是否为新角色。",
    "SCENE_SECRET_LEAK_RISK": "可能提前说出了尚未揭晓的秘密，请复核。",
    "SCENE_CONTINUITY_OUT_MISSING": "没有写下收尾状态，下一场会缺少接续信息。",
    "STORYBOARD_BEAT_UNCOVERED": "有剧情节拍没有被镜头覆盖。",
    "STORYBOARD_BEAT_UNKNOWN": "有镜头引用了剧本里不存在的节拍。",
    "QA_PENDING": "终校未完成，已记录；不影响正文。",
}
"""One Chinese sentence per check, so no raw code reaches the normal UI."""


def qa_states_by_section(db, project_id: str) -> dict[str, dict]:
    """The newest verdict per section, as the panel needs it: state + one line."""
    from shine.literary import qa
    from shine.models import AgentArtifact

    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == qa.REPORT_KIND,
    ).order_by(AgentArtifact.created_at.desc()).limit(200)).all()
    result: dict[str, dict] = {}
    for row in rows:
        payload = row.payload_json or {}
        section_id = payload.get("section_id")
        if not section_id or payload.get("source") == "storyboard" or section_id in result:
            continue
        blocking = [str(check.get("code")) for check in payload.get("checks") or []
                    if check.get("severity") in {"HARD_BLOCK", "LOCAL_REVIEW"}]
        result[section_id] = {
            "state": payload.get("state"),
            "level": payload.get("level"),
            "codes": blocking[:4],
            "message": "；".join(QA_MESSAGES.get(code, "请复核这一场。")
                                for code in blocking[:2]) or None,
            "artifact_id": row.id,
            "script_sha256": payload.get("script_sha256"),
            "script_artifact_id": payload.get("script_artifact_id"),
        }
    return result


def latest_section_qa_report(db, project_id: str, volume_id: str, section_id: str):
    """The newest verdict on one section, whichever revision it judged."""
    from shine.literary import qa
    from shine.models import AgentArtifact

    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == qa.REPORT_KIND,
    ).order_by(AgentArtifact.created_at.desc()).limit(40)).all()
    return next((row for row in rows
                 if (row.payload_json or {}).get("section_id") == section_id
                 and (row.payload_json or {}).get("source") != "storyboard"
                 and ((row.payload_json or {}).get("volume_id") in (None, volume_id))), None)


def latest_qa_report(db, project_id: str, script_sha256: str):
    """The newest verdict on exactly this text, or None."""
    from shine.literary import qa
    from shine.models import AgentArtifact

    if not script_sha256:
        return None
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == qa.REPORT_KIND,
    ).order_by(AgentArtifact.created_at.desc()).limit(12)).all()
    return next((row for row in rows
                 if (row.payload_json or {}).get("script_sha256") == script_sha256), None)


def _scene_memory_view(factory, project_id: str, bible_payload: dict) -> dict | None:
    """The bounded five-dimension view a scene writer may receive.

    Returns ``None`` in ``off``/``shadow``: shadow computes and records new facts
    but never changes the request the user pays for.  A memory fault returns
    ``None`` as well - the scene is written from the bible and the source, which
    is exactly what happened before V2.1.
    """
    from shine.literary import flags, memory

    try:
        with factory() as db:
            if not flags.affects_request("memory", db, project_id):
                return None
            names = tuple(
                str(character.get("name")).strip()
                for character in (bible_payload.get("characters") or [])[:8]
                if isinstance(character, dict) and str(character.get("name") or "").strip())
            retrieval = memory.retrieve(db, project_id=project_id, token_budget=1_200,
                                        subject_names=names)
        if not retrieval.fact_ids:
            # A legitimate empty view: no authoritative fact touches this scene.
            return None
        return {"facts": retrieval.context_text, "fact_ids": retrieval.fact_ids,
                "warnings": retrieval.warnings}
    except Exception:  # noqa: BLE001 - guidance never blocks the writer
        logger.exception("literary_memory_retrieval_failed project=%s", project_id)
        return None


def _ingest_memory(factory, *, project_id: str, task_id: str, payload: dict,
                   document_sha256: str | None) -> dict:
    """Archive the optional five-dimension observations of a blueprint answer.

    This is a side ledger, never a gate: an invalid observation is a warning
    about that observation, and a memory fault cannot fail the outline that was
    successfully written.  Nothing here is promoted to authoritative.
    """
    from shine.literary import flags, memory

    try:
        with factory() as db:
            if not flags.enabled("memory", db, project_id):
                return {"mode": "off"}
            mode = flags.mode("memory", db, project_id)
        observations, findings = memory.parse_observations(payload, unit_id="bible")
        if not observations:
            return {"mode": mode, "saved": 0, "findings": [item.as_dict() for item in findings]}
        with factory.begin() as db:
            result = memory.record_observations(
                db, project_id=project_id, observations=observations,
                unit_id="bible", source_task_id=task_id, document_sha256=document_sha256)
            snapshot = memory.snapshot(db, project_id, upstream={"task_id": task_id})
            summary = result.as_dict()
            summary["snapshot_sha256"] = snapshot.sha256
            summary["mode"] = mode
            summary["findings"] = [item.as_dict() for item in [*findings, *result.findings]]
            return summary
    except Exception:  # noqa: BLE001 - a side ledger never fails the outline
        logger.exception("literary_memory_ingest_failed task=%s", task_id)
        return {"mode": "error"}


def _attempt_recorder(factory, project_id: str, task_id: str):
    """A durable physical-call recorder for one task.

    The governor calls it twice per request: once before the request leaves and
    once with the real outcome.  A ledger fault is logged and swallowed - the
    literary work must not fail because bookkeeping did.
    """
    from shine.literary import ledger

    def record(attempt: dict) -> None:
        try:
            with factory.begin() as db:
                ledger.record(db, project_id=project_id, attempt=attempt, task_id=task_id)
        except Exception:  # noqa: BLE001 - bookkeeping never fails a scene
            logger.exception("literary_attempt_ledger_failed task=%s unit=%s",
                             task_id, attempt.get("unit_id"))

    return record


def _compact_attempts(attempts) -> list[dict]:
    """The bounded physical-call ledger one task keeps in its trace."""
    if not isinstance(attempts, list):
        return []
    keep = ("attempt_no", "request_fingerprint", "input_tokens_estimated",
            "output_request_tokens", "completion_tokens", "reasoning_tokens",
            "latency_ms", "finish_reason", "external_outcome", "error_code",
            "http_status", "corrective", "purpose", "role")
    return [{key: item.get(key) for key in keep if key in item} for item in attempts[-6:]
            if isinstance(item, dict)]


def _persist_candidate(factory, *, task_id: str, project_id: str, target_kind: str,
                       unit_id: str, text: str, payload: dict | None,
                       findings, notes: list[str] | None = None,
                       input_revision_ids: list[str] | None = None) -> str | None:
    """Save the literary text a rejected answer still carried.

    This is the "收到有效内容先持久化" rule in code: the task may end in
    review, but the chapter it produced is not deleted.  The draft is its own
    artifact kind, so no confirmed-pointer or orphan-adoption path can promote
    it into an accepted stage result.
    """
    from shine.literary.drafts import save_candidate
    from shine.models import AgentTaskRecord

    if not (text or "").strip() and not isinstance(payload, dict):
        return None
    try:
        with factory.begin() as db:
            task = db.get(AgentTaskRecord, task_id)
            if task is None or task.project_id != project_id:
                return None
            candidate = save_candidate(
                db, project_id=project_id, task=task, target_kind=target_kind,
                unit_id=unit_id, text=text, payload=payload, findings=list(findings or []),
                notes=list(notes or []), input_revision_ids=input_revision_ids)
            return candidate.artifact.id
    except Exception:  # noqa: BLE001 - losing a draft must never fail the task path
        logger.exception("literary_candidate_write_failed task=%s unit=%s", task_id, unit_id)
        return None


def _blueprint_request_context(db, project_id: str, trace: dict, outlines: list) -> dict:
    """The merged digest input one blueprint call receives.

    The merge keeps every event id, compresses prose when the budget is tight and
    reports a volume whose outline does not cover its own source range.  A
    missing coverage is a named warning for the person who confirms the
    blueprint, never a silent gap.
    """
    from shine.literary.blueprint import blueprint_context, digest_from_outline, merge_digests

    plan = db.get(AgentArtifact, trace.get("plan_artifact_id") or "")
    volumes = {}
    if plan is not None and plan.project_id == project_id:
        volumes = {volume.get("volume_id"): volume
                   for volume in (plan.payload_json.get("volumes") or [])}
    digests = []
    for ref, outline in zip(trace.get("outline_refs") or [], outlines, strict=False):
        volume = volumes.get(ref.get("volume_id")) or {
            "volume_id": ref.get("volume_id"), "order": ref.get("order")}
        digests.append(digest_from_outline({**outline.payload_json, "_sha256": outline.sha256},
                                           volume=volume))
    merged = merge_digests(digests, token_budget=3_000)
    return blueprint_context(merged)


def run_bible_task(factory, settings, gateway, task_id: str, version: int, worker_id: str) -> bool:
    """Execute one bible synthesis task under the fenced claim."""
    from shine.literary.governor import UnitBudget
    from shine.materialprep import text_consent
    from shine.materialprep.agent_call import call_material_agent
    from shine.models import AgentTaskRecord

    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        if task is None:
            return False
        trace = dict(task.trace or {})
        project_id = task.project_id
        verdict = _pipeline_entry_guard(factory, task_id, version, worker_id, trace,
                                        project_id=project_id, kind=BIBLE_KIND, role=BIBLE_RUN_ROLE)
        if verdict == "adopt":
            return _adopt_persisted_artifact(factory, task_id, version, worker_id, trace,
                                             project_id, BIBLE_KIND, BIBLE_RUN_ROLE)
        if verdict in ("blocked", "adopted"):
            return True
        master_doc = db.get(ProjectDocument, trace["document_id"])
        if (master_doc is None or master_doc.project_id != project_id
                or master_doc.sha256 != trace["document_sha256"]):
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_MASTER_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_MASTER_CHANGED")
            return True
        outlines = []
        for ref in trace["outline_refs"]:
            outline = db.get(AgentArtifact, ref["id"])
            if (outline is None or outline.project_id != project_id
                    or outline.stale or outline.sha256 != ref["sha256"]
                    or not outline.approved_at):
                _finish_pipeline_task(factory, task_id, version, worker_id,
                                      {**trace, "blocked_reason": "PIPELINE_OUTLINE_CHANGED"},
                                      "NEEDS_REVIEW", "PIPELINE_OUTLINE_CHANGED")
                return True
            volume_id = ref.get("volume_id")
            if volume_id:
                # The reference must still be the current outline of that
                # volume: a re-extracted or re-adjusted volume makes this
                # blueprint's input range obsolete rather than silently
                # mixing two revisions of the same volume.
                current = _volume_outline(db, project_id, volume_id)
                if current is None or current.id != outline.id:
                    _finish_pipeline_task(factory, task_id, version, worker_id,
                                          {**trace, "blocked_reason": "PIPELINE_VOLUME_OUTLINE_SUPERSEDED"},
                                          "NEEDS_REVIEW", "PIPELINE_VOLUME_OUTLINE_SUPERSEDED")
                    return True
            outlines.append(outline)
        context = {
            "master": {"char_count": len(master_doc.text or ""), "outline_count": len(outlines)},
            **_blueprint_request_context(db, project_id, trace, outlines),
        }
        schema = GlobalStoryBiblePayload.model_json_schema()
        sent = _persist_pipeline_sending(factory, task_id, version, worker_id, trace)
        if sent is None:
            return True
        trace = sent
        outcome = call_material_agent(
            gateway, factory, BIBLE_RUN_ROLE,
            system=BIBLE_SYSTEM_PROMPT,
            context=context,
            schema=schema,
            contract=GlobalStoryBiblePayload,
            task_category="creative",
            purpose="outline",
            unit_id=f"bible:{project_id}",
            unit_budget=UnitBudget(max_calls=3, max_repair_rounds=1),
            record_attempt=_attempt_recorder(factory, project_id, task_id),
            before_call=text_consent.guard(project_id, trace["document_id"], trace["document_sha256"],
                                           BIBLE_RUN_ROLE, trace["provider_fingerprint"], gateway),
        )
        if not outcome.ok:
            from shine.literary.adapters import adapt_bible
            adapted = adapt_bible(outcome.candidate_payload or {})
            candidate_id = _persist_candidate(
                factory, task_id=task_id, project_id=project_id, target_kind=BIBLE_KIND,
                unit_id="bible", text=outcome.candidate_text or adapted.payload.get("text", ""),
                payload=adapted.payload,
                findings=outcome.findings,
                notes=[*adapted.notes, *adapted.gaps],
                input_revision_ids=trace.get("input_revision_ids") or [])
            blocked = {**trace, "stage": "NEEDS_REVIEW",
                       "blocked_reason": outcome.error_code,
                       "external_outcome": outcome.details.get("external_outcome"),
                       "response_status": outcome.details.get("response_status"),
                       "schema_errors": outcome.schema_errors,
                       "truncated": outcome.details.get("truncated"),
                       "review": outcome.review,
                       "candidate_artifact_id": candidate_id,
                       "output_budget": outcome.details.get("output_budget")}
            _finish_pipeline_task(factory, task_id, version, worker_id, blocked, "NEEDS_REVIEW", outcome.error_code)
            return True
        # The blueprint carries the ordered outline identity it was written
        # from.  Without it a blueprint could be confirmed against a set of
        # outlines that is no longer the current one.
        payload = {**dict(outcome.parsed), "basis": {
            "outline_refs": [{"id": ref.get("id"), "sha256": ref.get("sha256"),
                              "volume_id": ref.get("volume_id"), "order": ref.get("order")}
                             for ref in (trace.get("outline_refs") or [])],
            "plan_artifact_id": trace.get("plan_artifact_id"),
            "plan_sha256": trace.get("plan_sha256"),
        }}
        payload["memory_ingest"] = _ingest_memory(
            factory, project_id=project_id, task_id=task_id, payload=payload,
            document_sha256=trace.get("document_sha256"))
        _commit_pipeline_result(
            factory, task_id=task_id, version=version, worker_id=worker_id,
            project_id=project_id, kind=BIBLE_KIND, role=BIBLE_RUN_ROLE, payload=payload,
            input_revision_ids=trace.get("input_revision_ids")
            or [ref["id"] for ref in trace["outline_refs"]],
            trace={**trace, "usage": outcome.details.get("usage"),
                   "output_budget": outcome.details.get("output_budget")})
        return True


def scene_script_bible_basis(db, project_id: str, script) -> dict:
    """The blueprint one confirmed scene script was drafted on.

    A scene may be written against a draft blueprint.  The basis travels with
    the scene, so the publication path can check the confirmed blueprint
    against the one the scene really used.
    """
    from shine.models import AgentTaskRecord

    if script is None:
        return {}
    task = db.get(AgentTaskRecord, script.source_task_id) if script.source_task_id else None
    trace = (task.trace or {}) if task is not None else {}
    return {
        "bible_artifact_id": trace.get("bible_artifact_id"),
        "bible_sha256": trace.get("bible_sha256"),
        "bible_basis": trace.get("bible_basis"),
    }


def bible_basis_blockers(db, project_id: str, artifact) -> list[str]:
    """Why a blueprint no longer matches the work it was written from.

    The comparison is the ordered outline identity and the plan revision: a
    blueprint was synthesised from one specific, ordered list of volume
    outlines, so when that list moved on the blueprint describes a different
    work.  Confirmation, the read path, the volume publication and the
    package admission all read this one function, so they can never disagree
    about whether the blueprint is still current.
    """
    if artifact is None:
        return ["PIPELINE_BIBLE_NOT_FOUND"]
    basis = artifact.payload_json.get("basis") or {}
    refs = basis.get("outline_refs") or []
    if not refs:
        # A blueprint the person wrote themselves has no provider input to
        # compare; it is held to the plan and its volumes instead.
        return []
    plan = current_volume_plan(db, project_id)
    if plan is None:
        return ["PIPELINE_VOLUME_PLAN_NOT_CONFIRMED"]
    if basis.get("plan_sha256") and basis["plan_sha256"] != plan.sha256:
        return ["PIPELINE_BIBLE_PLAN_SUPERSEDED"]
    resolved, _missing = resolve_plan_outlines(db, project_id, plan)
    current = [{"id": entry["outline"].id, "sha256": entry["outline"].sha256}
               for entry in resolved]
    written = [{"id": ref.get("id"), "sha256": ref.get("sha256")} for ref in refs]
    if current != written:
        return ["PIPELINE_BIBLE_OUTLINE_SUPERSEDED"]
    return []


def bible_confirmation_blockers(db, project_id: str, artifact) -> list[str]:
    """Why a blueprint may not yet be confirmed, decided from durable evidence.

    Confirming a blueprint is the cross-volume decision, so it needs the full
    ordered set of the *current* plan's volumes.  A blueprint synthesized from
    a partial set stays a usable draft and is refused here by name instead of
    being confirmed as if every volume had been read.  A blueprint a person
    wrote themselves needs no provider coverage evidence, but it is held to
    the same rule about the plan's volumes.
    """
    from shine.models import AgentTaskRecord

    blockers: list[str] = []
    task = db.get(AgentTaskRecord, artifact.source_task_id) if artifact.source_task_id else None
    trace = (task.trace or {}) if task is not None else {}
    authored_by_person = trace.get("external_outcome") == "NOT_SENT"
    coverage = trace.get("outline_coverage") or {}
    if task is None and not authored_by_person:
        return ["PIPELINE_BIBLE_SOURCE_TASK_MISSING"]
    if not authored_by_person and not coverage.get("complete"):
        missing = coverage.get("missing_volumes") or []
        blockers.append("PIPELINE_BIBLE_OUTLINE_COVERAGE_INCOMPLETE:"
                        + ",".join(str(item.get("order")) for item in missing)[:200])
    plan = current_volume_plan(db, project_id)
    if plan is None:
        blockers.append("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
        return blockers
    if coverage.get("plan_sha256") and coverage["plan_sha256"] != plan.sha256:
        blockers.append("PIPELINE_BIBLE_PLAN_SUPERSEDED")
    _, missing_now = resolve_plan_outlines(db, project_id, plan)
    if missing_now:
        blockers.append("PIPELINE_BIBLE_OUTLINE_COVERAGE_INCOMPLETE:"
                        + ",".join(str(item["order"]) for item in missing_now)[:200])
    # The blueprint must still describe the current work: the ordered outline
    # identity it was written from is compared with the current one.
    blockers.extend(bible_basis_blockers(db, project_id, artifact))
    return blockers


def confirm_bible(db, project_id: str, artifact_id: str, expected_sha256: str) -> AgentArtifact:
    """A person confirms the global story bible; the pointer names it."""
    from shine.models import AgentArtifact, AppSetting

    artifact = db.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id or artifact.kind != BIBLE_KIND:
        raise ValueError("PIPELINE_BIBLE_NOT_FOUND")
    if artifact.sha256 != expected_sha256:
        raise ValueError("PIPELINE_BIBLE_SHA_MISMATCH")
    blockers = bible_confirmation_blockers(db, project_id, artifact)
    if blockers:
        raise ValueError(blockers[0])
    open_questions = list(artifact.payload_json.get("open_questions") or [])
    row = AppSetting(key=_pointer_key(project_id, "story-bible"),
                     value={"artifact_id": artifact.id, "sha256": artifact.sha256,
                            "open_questions": open_questions[:48]})
    db.merge(row)
    artifact.approved_by = "local-user"
    artifact.approved_at = utcnow()
    db.flush()
    add_audit(db, "local-user", "PIPELINE_BIBLE_CONFIRMED", "agent_artifact", artifact.id,
              {"sha256": artifact.sha256, "open_questions": open_questions[:48]},
              project_id=project_id)
    return artifact


def _planned_volume(db, project_id: str, volume_id: str) -> tuple[dict | None, dict]:
    """The current plan's entry for one stable volume key, plus its master ref."""
    plan = current_volume_plan(db, project_id)
    if plan is None:
        return None, {}
    master = plan.payload_json.get("source_master") or {}
    volume = next((v for v in (plan.payload_json.get("volumes") or [])
                   if v.get("volume_id") == volume_id), None)
    return volume, master


def volume_revision(master_sha256: str, start: int, end: int) -> str:
    """The content revision of one volume: its exact range in one master.

    The volume *key* is stable across boundary adjustments so the same
    business volume keeps its identity; this revision is what actually pins
    the text, and a document is only reusable when both agree.
    """
    return digest({"master_sha256": master_sha256, "start_offset": start, "end_offset": end})


def _volume_document(db, project_id: str, volume_id: str):
    """The derived volume document of one volume, matched on its exact span.

    Reading by key alone used to return the first document an earlier
    adjustment had created, so a re-adjusted plan could silently read the old
    range.  The match therefore pins the master identity and the planned
    offsets, and the newest exact match wins.
    """
    from shine.models import ProjectDocument

    planned, master = _planned_volume(db, project_id, volume_id)
    parsed = ProjectDocument.parsed
    query = select(ProjectDocument).where(
        ProjectDocument.project_id == project_id,
        ProjectDocument.source_format == "volume",
        parsed["kind"].as_string() == "four_stage_volume",
        parsed["volume_id"].as_string() == volume_id,
    )
    if planned is not None:
        query = query.where(
            parsed["master_document_id"].as_string() == master["document_id"],
            parsed["master_sha256"].as_string() == master["sha256"],
            parsed["start_offset"].as_integer() == planned["start_offset"],
            parsed["end_offset"].as_integer() == planned["end_offset"],
        )
    return db.scalar(query.order_by(ProjectDocument.created_at.desc(), ProjectDocument.id.desc()).limit(1))


def _volume_outline(db, project_id: str, volume_id: str):
    """The confirmed StoryOutline of exactly this volume's document.

    A project-level pointer is only a hint: an outline is accepted only when
    its own payload names this volume's document and digest, so an unrelated
    or superseded outline can never stand in for the current volume.
    """
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
    if (artifact is None or artifact.project_id != project_id or artifact.stale
            or not artifact.approved_at
            or artifact.payload_json.get("document_id") != doc.id
            or artifact.payload_json.get("document_sha256") != doc.sha256
            or digest(artifact.payload_json) != artifact.sha256):
        return None
    return artifact


def bible_eligibility(db, project_id: str, artifact) -> tuple[bool, str]:
    """Whether a blueprint may be the basis of drafting, and on what grounds.

    The queue and the Worker call this same function, so a draft blueprint
    that let a task be enqueued can never be refused at execution time for the
    same reason.  A draft is a real basis for *draft* writing; only a
    confirmed blueprint may publish a volume.
    """
    if (artifact is None or artifact.project_id != project_id or artifact.stale
            or artifact.kind != BIBLE_KIND
            or digest(artifact.payload_json) != artifact.sha256):
        return False, "PIPELINE_BIBLE_CHANGED"
    return True, "confirmed" if artifact.approved_at else "draft_unconfirmed"


def bible_basis_equivalent(a: dict, b: dict) -> bool:
    """True when two blueprints agree on every decisive, user-visible fact.

    A script drafted on a draft blueprint may be published without rewriting
    when the confirmed blueprint says the same things; any difference in the
    mainline, the locked facts or the ending direction needs a re-review.
    """
    def normalized(payload: dict) -> dict:
        characters = []
        for entry in payload.get("characters") or []:
            if isinstance(entry, dict):
                characters.append(str(entry.get("name") or "").strip())
            else:
                characters.append(str(entry).strip())
        open_items = []
        for entry in payload.get("open_questions") or []:
            if isinstance(entry, dict):
                open_items.append(str(entry.get("question") or entry.get("text")
                                       or entry.get("summary") or "").strip())
            else:
                open_items.append(str(entry).strip())
        return {
            "mainline": str(payload.get("mainline") or "").strip(),
            "characters": sorted(name for name in characters if name),
            "world": str(payload.get("world") or "").strip(),
            "timeline": sorted(str(item).strip() for item in (payload.get("timeline") or [])),
            "open_questions": sorted(item for item in open_items if item),
            "locked_facts": sorted(str(item).strip() for item in (payload.get("locked_facts") or [])),
            "ending_direction": str(payload.get("ending_direction") or "").strip(),
        }
    return normalized(a) == normalized(b)


def _existing_attempt(db, project_id: str, role: str, base_identity: str):
    """The newest reusable attempt of one logical input identity.

    Attempts of one logical unit share ``base_identity`` and differ only in
    their attempt number, so re-queueing must look them up by that identity -
    otherwise an explicitly authorised new attempt would spawn a third task.
    """
    from shine.models import AgentTaskRecord

    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == role).order_by(
        AgentTaskRecord.created_at.desc())).all()
    matching = [row for row in rows
                if (row.trace or {}).get("base_identity") == base_identity
                or row.input_hash == base_identity]
    return next((row for row in matching if row.status in REUSABLE_TASK_STATUSES), None)


def attempt_identity(base_identity: str, attempt: int) -> str:
    """The request identity of one physical attempt of one logical unit."""
    return base_identity if attempt <= 1 else digest({"base": base_identity, "attempt": attempt})


def _attempt_count(db, project_id: str, role: str, base_identity: str) -> int:
    from shine.models import AgentTaskRecord

    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == role)).all()
    return sum(1 for row in rows
               if (row.trace or {}).get("base_identity") == base_identity)


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
    bible = current_bible(db, project_id) or latest_bible_artifact(db, project_id)
    eligible, bible_basis = bible_eligibility(db, project_id, bible)
    if not eligible:
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
        existing_task = _existing_attempt(db, project_id, SCENE_SCRIPT_RUN_ROLE, identity)
        if existing_task:
            created.append(existing_task)
            continue
        task = AgentTaskRecord(
            project_id=project_id,
            role_id=SCENE_SCRIPT_RUN_ROLE,
            request_identity=attempt_identity(identity, 1),
            input_hash=identity,
            asset_manifest_hash=identity,
            rulepack_id=PIPELINE_VERSION,
            rulepack_hash=digest(PIPELINE_VERSION),
            model_profile_id=profile.id,
            model_id=profile.model,
            schema_version="1.0",
            status="QUEUED",
            trace={
                "stage": "DRAFT", "automatic_replay": False, "attempt": 1,
                "result_contract": _result_contract(SCENE_SCRIPT_KIND),
                "input_revision_ids": [bible.id, outline.id],
                "volume_id": volume_id,
                "volume_document_id": volume_doc.id,
                "volume_document_sha256": volume_doc.sha256,
                "master_document_id": plan.payload_json["source_master"]["document_id"],
                "master_document_sha256": plan.payload_json["source_master"]["sha256"],
                "section": section_ctx,
                "outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                "bible_artifact_id": bible.id, "bible_sha256": bible.sha256,
                "bible_basis": bible_basis,
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


def _scene_beat_plan(db, gateway, section: dict, source_text: str, schema: dict) -> list:
    """How the section's source is split, measured against the real budget.

    A section that fits is one beat, so the caller has a single code path.  This
    is a *decision*, not a model call: the target size comes from the same
    approved budget the send path uses.
    """
    from shine.literary import beats as beat_lib
    from shine.literary.budget import plan_call_budget
    from shine.literary.dryrun import SCENE_CONTEXT_OVERHEAD_TOKENS
    from shine.literary.requests import compile_request

    if not (source_text or "").strip():
        return []
    profile = gateway.profile_for_role(db, SCENE_SCRIPT_RUN_ROLE)
    config = gateway.config_for(profile)
    probe = compile_request(system=SCENE_SYSTEM_PROMPT,
                            user='{"section":{}, "source_text":""}', schema=schema)
    budget = plan_call_budget(config, probe, purpose="scene")
    target = max(2_000, budget.burst_input_limit - SCENE_CONTEXT_OVERHEAD_TOKENS)
    return beat_lib.plan_beats(source_text, section_id=str(section.get("section_id") or "section"),
                               target_chars=target)


def _beat_artifacts(db, task_id: str, project_id: str) -> dict[str, AgentArtifact]:
    """Beat answers this task already paid for, by beat id."""
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.source_task_id == task_id,
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == SCENE_BEAT_KIND)).all()
    return {row.payload_json.get("beat_id"): row for row in rows if row.payload_json.get("beat_id")}


def _save_beat(factory, *, project_id: str, task_id: str, beat, text: str,
               continuity_out: str) -> None:
    """Persist one beat's text the moment it arrives, in its own transaction."""
    from shine.models import AgentArtifact

    payload = {"beat_id": beat.beat_id, "ordinal": beat.ordinal,
               "section_id": beat.section_id, "text": text,
               "continuity_out": continuity_out[:400],
               "source_sha256": beat.source_sha256,
               "start": beat.start, "end": beat.end}
    with factory.begin() as db:
        db.add(AgentArtifact(project_id=project_id, kind=SCENE_BEAT_KIND, schema_version="1.0",
                             payload_json=payload, sha256=digest(payload),
                             producer_role=SCENE_SCRIPT_RUN_ROLE, source_task_id=task_id,
                             input_revision_ids=[]))
        add_audit(db, "local-user", "PIPELINE_SCENE_BEAT_SAVED", "agent_task", task_id,
                  {"beat_id": beat.beat_id, "ordinal": beat.ordinal,
                   "chars": len(text)}, project_id=project_id)


def _run_scene_beats(*, factory, gateway, task_id: str, version: int, worker_id: str,
                     project_id: str, trace: dict, section: dict, source_text: str,
                     context: dict, schema: dict, plan: list, unit_budget=None) -> bool:
    from shine.literary.models import LiteraryUnit

    """Write one long scene beat by beat, then assemble it verbatim.

    Each beat is paid for once and persisted immediately, so a later beat's
    failure keeps the earlier beats' text.  A failed beat marks only its
    dependants as ``WAITING_DEPENDENCY``; the scene becomes a candidate draft,
    never a confirmed script.
    """
    from shine.literary import beats as beat_lib
    from shine.literary.adapters import SceneDraft, adapt_scene_script
    from shine.literary.governor import UnitBudget
    from shine.materialprep import text_consent
    from shine.materialprep.agent_call import call_material_agent

    before_call = text_consent.guard(
        project_id, trace["master_document_id"], trace["master_document_sha256"],
        SCENE_SCRIPT_RUN_ROLE, trace["provider_fingerprint"], gateway)
    if unit_budget is None:
        unit_budget = UnitBudget(max_calls=len(plan) + 1, max_repair_rounds=len(plan),
                                 max_completion_tokens=600_000)
    with factory.begin() as db:
        units = beat_lib.save_units(db, project_id=project_id,
                                    section_id=str(section.get("section_id") or "section"),
                                    beats=plan, task_id=task_id)
        # Keep ids, not detached rows: every later write opens its own session.
        unit_ids = {unit.ordinal: unit.id for unit in units}
    with factory() as db:
        done = _beat_artifacts(db, task_id, project_id)

    failure = None
    for beat in plan:
        existing = done.get(beat.beat_id)
        if existing is not None and (existing.payload_json.get("text") or "").strip():
            continue
        beat_context = {**context,
                        "source_text": source_text[beat.start:beat.end],
                        "beat": {"ordinal": beat.ordinal, "total": len(plan),
                                 "section_id": beat.section_id,
                                 "note": "只写这一段，结尾写 continuity_out"}}
        outcome = call_material_agent(
            gateway, factory, SCENE_SCRIPT_RUN_ROLE,
            system=SCENE_SYSTEM_PROMPT,
            context=beat_context, schema=schema, contract=SceneDraft,
            task_category="creative", purpose="scene",
            unit_id=f"scene:{trace['volume_id']}:{section['section_id']}:b{beat.ordinal}",
            unit_budget=unit_budget,
            record_attempt=_attempt_recorder(factory, project_id, task_id),
            before_call=before_call)
        if not outcome.ok:
            failure = outcome
            with factory.begin() as db:
                unit = db.get(LiteraryUnit, unit_ids.get(beat.ordinal, ""))
                if unit is not None:
                    beat_lib.mark(db, unit, "FAILED", error_code=outcome.error_code,
                                  external_outcome=outcome.details.get("external_outcome"))
                rows = [db.get(LiteraryUnit, unit_id) for unit_id in unit_ids.values()]
                beat_lib.propagate([row for row in rows if row is not None],
                                   failed_ordinal=beat.ordinal)
            break
        piece = adapt_scene_script(outcome.parsed, section=section)
        _save_beat(factory, project_id=project_id, task_id=task_id, beat=beat,
                   text=piece.payload["text"], continuity_out=piece.payload["continuity_out"])
        with factory.begin() as db:
            unit = db.get(LiteraryUnit, unit_ids.get(beat.ordinal, ""))
            if unit is not None:
                beat_lib.mark(db, unit, "PASSED", chars=len(piece.payload["text"]))
            for unit_id in unit_ids.values():
                other = db.get(LiteraryUnit, unit_id)
                if (other is not None and other.ordinal > beat.ordinal
                        and other.status == "WAITING_DEPENDENCY"):
                    other.status = "PENDING"
            db.flush()

    with factory() as db:
        done = _beat_artifacts(db, task_id, project_id)
        pieces = [{"beat": beat,
                   "text": (done.get(beat.beat_id).payload_json.get("text") if beat.beat_id in done else ""),
                   "continuity_out": (done.get(beat.beat_id).payload_json.get("continuity_out")
                                      if beat.beat_id in done else "")}
                  for beat in plan]
    assembly = beat_lib.assemble(str(section.get("section_id") or "section"), pieces)
    coverage = beat_lib.coverage(plan, len(source_text))

    if failure is not None or not assembly.complete:
        candidate_id = _persist_candidate(
            factory, task_id=task_id, project_id=project_id, target_kind=SCENE_SCRIPT_KIND,
            unit_id=f"scene:{trace['volume_id']}:{section['section_id']}",
            text=assembly.text, payload={"section_id": section.get("section_id"),
                                         "partial_beats": assembly.as_dict(),
                                         "coverage": coverage},
            findings=(failure.findings if failure is not None else []),
            notes=[*assembly.notes, f"missing_beats={assembly.missing_ordinals}"],
            input_revision_ids=[trace["bible_artifact_id"], trace["outline_artifact_id"]])
        blocked = {**trace, "stage": "NEEDS_REVIEW",
                   "blocked_reason": (failure.error_code if failure is not None
                                      else "PIPELINE_SCENE_BEATS_INCOMPLETE"),
                   "external_outcome": (failure.details.get("external_outcome")
                                        if failure is not None else "RESPONSE_RECEIVED"),
                   "schema_errors": (failure.schema_errors if failure is not None else []),
                   "truncated": (failure.details.get("truncated")
                                 if failure is not None else False),
                   "review": (failure.review if failure is not None
                              else {"state": "LOCAL_REVIEW",
                                    "message": "部分节拍未完成，已保存其余正文。",
                                    "next_action": "retry_failed_beat"}),
                   "candidate_artifact_id": candidate_id,
                   "beats": [beat.as_dict() for beat in plan],
                   "beat_assembly": assembly.as_dict(), "beat_coverage": coverage,
                   "output_budget": (failure.details.get("output_budget")
                                     if failure is not None else None)}
        _finish_pipeline_task(factory, task_id, version, worker_id, blocked, "NEEDS_REVIEW",
                              blocked["blocked_reason"])
        return True

    adapted = adapt_scene_script({"text": assembly.text,
                                  "continuity_out": assembly.continuity[-1] if assembly.continuity else ""},
                                 section=section)
    payload = dict(adapted.payload)
    payload["source_refs"] = {"volume_id": trace["volume_id"],
                              "volume_document_id": trace["volume_document_id"],
                              "start_offset": section.get("start_offset"),
                              "end_offset": section.get("end_offset"),
                              "outline_sha256": trace["outline_sha256"],
                              "bible_sha256": trace["bible_sha256"]}
    committed = _commit_pipeline_result(
        factory, task_id=task_id, version=version, worker_id=worker_id,
        project_id=project_id, kind=SCENE_SCRIPT_KIND, role=SCENE_SCRIPT_RUN_ROLE,
        payload={**payload, "beats": [beat.as_dict() for beat in plan],
                 "beat_coverage": coverage},
        input_revision_ids=[trace["bible_artifact_id"], trace["outline_artifact_id"]],
        trace={**trace, "beats": [beat.as_dict() for beat in plan],
               "beat_assembly": assembly.as_dict(), "beat_coverage": coverage, "review": {}})
    if committed is not None:
        artifact_id, sha256 = committed
        # The assembled scene is judged as one text: the beats were already
        # reviewed against their own bounds, this is the whole-scene verdict.
        summary = _scene_qa(factory=factory, gateway=gateway, project_id=project_id,
                            task_id=task_id, trace=trace, section=section,
                            source_text=source_text, payload=payload,
                            unit_budget=unit_budget)
        if summary is not None:
            _persist_qa_report(factory=factory, project_id=project_id, task_id=task_id,
                               script_artifact_id=artifact_id, script_sha256=sha256,
                               section=section, summary=summary,
                               volume_id=trace.get("volume_id"))
    return True


def run_scene_script_task(factory, settings, gateway, task_id: str, version: int, worker_id: str) -> bool:
    """Execute one scene-script task under the fenced claim."""
    from shine.literary.adapters import SceneDraft
    from shine.literary.governor import UnitBudget
    from shine.materialprep import text_consent
    from shine.materialprep.agent_call import call_material_agent
    from shine.models import AgentArtifact, AgentTaskRecord, ProjectDocument

    with factory() as db:
        task = db.get(AgentTaskRecord, task_id)
        if task is None:
            return False
        trace = dict(task.trace or {})
        project_id = task.project_id
        verdict = _pipeline_entry_guard(factory, task_id, version, worker_id, trace,
                                        project_id=project_id, kind=SCENE_SCRIPT_KIND,
                                        role=SCENE_SCRIPT_RUN_ROLE)
        if verdict == "adopt":
            return _adopt_persisted_artifact(factory, task_id, version, worker_id, trace,
                                             project_id, SCENE_SCRIPT_KIND, SCENE_SCRIPT_RUN_ROLE)
        if verdict in ("blocked", "adopted"):
            return True
        volume_doc = db.get(ProjectDocument, trace["volume_document_id"])
        if (volume_doc is None or volume_doc.project_id != project_id
                or volume_doc.sha256 != trace["volume_document_sha256"]):
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_VOLUME_DOCUMENT_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_VOLUME_DOCUMENT_CHANGED")
            return True
        bible = db.get(AgentArtifact, trace["bible_artifact_id"])
        # The queue and the Worker answer the same question with the same
        # function: a draft blueprint may drive draft scripts, and only a
        # confirmed one may publish them.
        eligible, basis = bible_eligibility(db, project_id, bible)
        if not eligible or bible.sha256 != trace["bible_sha256"]:
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {**trace, "blocked_reason": "PIPELINE_BIBLE_CHANGED"},
                                  "NEEDS_REVIEW", "PIPELINE_BIBLE_CHANGED")
            return True
        trace = {**trace, "bible_basis": basis}
        outline = db.get(AgentArtifact, trace["outline_artifact_id"])
        if (outline is None or outline.project_id != project_id
                or outline.stale or outline.sha256 != trace["outline_sha256"]
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
            "section": {"section_id": section["section_id"],
                        "scene_key": section.get("scene_key", section["section_id"]),
                        "title": section.get("title"), "summary": section.get("summary"),
                        "order": section_order},
            "source_text": source_text,
            "previous_ending": (previous_script.payload_json.get("continuity_out", "")
                                if previous_script else None),
        }
        memory_view = _scene_memory_view(factory, project_id, bible.payload_json)
        if memory_view is not None:
            context["memory"] = memory_view
        schema = SceneDraft.model_json_schema()
        beat_plan = _scene_beat_plan(db, gateway, section, source_text, schema)
        sent = _persist_pipeline_sending(factory, task_id, version, worker_id, trace)
        if sent is None:
            return True
        trace = sent
        # One logical unit, one budget: the writer, its single correction and the
        # QA reviewer all spend from the same three calls.
        unit_budget = UnitBudget(max_calls=3, max_repair_rounds=1)
        if len(beat_plan) > 1:
            return _run_scene_beats(
                factory=factory, gateway=gateway, task_id=task_id, version=version,
                worker_id=worker_id, project_id=project_id, trace=trace,
                section=section, source_text=source_text, context=context,
                schema=schema, plan=beat_plan, unit_budget=unit_budget)
        outcome = call_material_agent(
            gateway, factory, SCENE_SCRIPT_RUN_ROLE,
            system=SCENE_SYSTEM_PROMPT,
            context=context,
            schema=schema,
            contract=SceneDraft,
            task_category="creative",
            purpose="scene",
            unit_id=f"scene:{trace['volume_id']}:{section['section_id']}",
            unit_budget=unit_budget,
            record_attempt=_attempt_recorder(factory, project_id, task_id),
            before_call=text_consent.guard(
                project_id, trace["master_document_id"], trace["master_document_sha256"],
                SCENE_SCRIPT_RUN_ROLE, trace["provider_fingerprint"], gateway),
        )
        if not outcome.ok:
            from shine.literary.adapters import adapt_scene_script
            adapted = adapt_scene_script(
                outcome.candidate_payload or {}, section=section,
                fallback_text="" if (outcome.candidate_payload or {}).get("text")
                else outcome.candidate_text)
            candidate_id = _persist_candidate(
                factory, task_id=task_id, project_id=project_id, target_kind=SCENE_SCRIPT_KIND,
                unit_id=f"scene:{trace['volume_id']}:{section['section_id']}",
                text=adapted.payload.get("text", "") or outcome.candidate_text,
                payload=adapted.payload,
                findings=outcome.findings,
                notes=[*adapted.notes, *adapted.gaps],
                input_revision_ids=[trace["bible_artifact_id"], trace["outline_artifact_id"]])
            blocked = {**trace, "stage": "NEEDS_REVIEW",
                       "blocked_reason": outcome.error_code,
                       "external_outcome": outcome.details.get("external_outcome"),
                       "response_status": outcome.details.get("response_status"),
                       "schema_errors": outcome.schema_errors,
                       "truncated": outcome.details.get("truncated"),
                       "review": outcome.review,
                       "candidate_artifact_id": candidate_id,
                       "output_budget": outcome.details.get("output_budget"),
                       "attempts": _compact_attempts(outcome.details.get("attempts"))}
            _finish_pipeline_task(factory, task_id, version, worker_id, blocked, "NEEDS_REVIEW", outcome.error_code)
            return True
        from shine.literary.adapters import adapt_scene_script
        adapted = adapt_scene_script(outcome.parsed, section=section,
                                     fallback_text=outcome.candidate_text)
        payload = dict(adapted.payload)
        payload["source_refs"] = {"volume_id": trace["volume_id"],
                                  "volume_document_id": trace["volume_document_id"],
                                  "start_offset": section.get("start_offset"),
                                  "end_offset": section.get("end_offset"),
                                  "outline_sha256": trace["outline_sha256"],
                                  "bible_sha256": trace["bible_sha256"]}
        committed = _commit_pipeline_result(
            factory, task_id=task_id, version=version, worker_id=worker_id,
            project_id=project_id, kind=SCENE_SCRIPT_KIND, role=SCENE_SCRIPT_RUN_ROLE,
            payload=payload,
            input_revision_ids=[trace["bible_artifact_id"], trace["outline_artifact_id"]],
            trace={**trace, "usage": outcome.details.get("usage"),
                   "output_budget": outcome.details.get("output_budget"),
                   "review": outcome.review, "adaptation": adapted.as_dict(),
                   "attempts": _compact_attempts(outcome.details.get("attempts"))})
        if committed is not None:
            # The text is durable first; the review judges the stored revision,
            # never a copy that could differ from it.
            artifact_id, sha256 = committed
            summary = _scene_qa(factory=factory, gateway=gateway, project_id=project_id,
                                task_id=task_id, trace=trace, section=section,
                                source_text=source_text, payload=payload,
                                unit_budget=unit_budget)
            if summary is not None:
                _persist_qa_report(factory=factory, project_id=project_id, task_id=task_id,
                                   script_artifact_id=artifact_id, script_sha256=sha256,
                                   section=section, summary=summary,
                                   volume_id=trace.get("volume_id"))
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
    # A hard QA verdict blocks delivery, never the text: the scene stays in the
    # ledger, the editor can fix it, and the refusal names the exact reasons.
    verdict = latest_qa_report(db, project_id, artifact.sha256)
    if verdict is not None and (verdict.payload_json or {}).get("state") == "HARD_BLOCK":
        codes = [str(check.get("code")) for check in (verdict.payload_json or {}).get("checks") or []
                 if check.get("severity") == "HARD_BLOCK"]
        raise ValueError("PIPELINE_SCENE_SCRIPT_QA_BLOCKED:" + ",".join(codes[:3]))
    refs = artifact.payload_json.get("source_refs") or {}
    volume_id = str(refs.get("volume_id"))
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

    Text past the scene contract's limit is refused by name with its real
    length.  Silently truncating an edit would drop the ending of a scene
    while still writing a new artifact that looks complete.
    """
    from shine.models import AgentArtifact, AppSetting

    old = db.get(AgentArtifact, artifact_id)
    if not old or old.project_id != project_id or old.kind != SCENE_SCRIPT_KIND:
        raise ValueError("PIPELINE_SCENE_SCRIPT_NOT_FOUND")
    limit = SCENE_SCRIPT_TEXT_LIMIT
    if len(new_text) > limit:
        raise ValueError(f"PIPELINE_SCENE_SCRIPT_TEXT_TOO_LONG:{len(new_text)}:{limit}"
                         ":edit within the limit or split the scene in the source")
    payload = {**old.payload_json, "text": new_text, "edited": True,
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
    refs = old.payload_json.get("source_refs") or {}
    volume_id = str(refs.get("volume_id"))
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
        if gateway is None:
            from shine.director.creative import _gateway
            gateway = _gateway(settings)
        from shine.models import AgentTaskRecord, ProviderProfile

        with factory() as db:
            task = db.get(AgentTaskRecord, task_id)
            pinned = db.get(ProviderProfile, task.model_profile_id) if task and task.model_profile_id else None
            pinned_model = task.model_id if task else None
        if pinned is None or not pinned.enabled or not pinned.model.strip() or (
                pinned_model and pinned.model != pinned_model):
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {"stage": "NEEDS_REVIEW", "blocked_reason": "PIPELINE_PROVIDER_PIN_INVALID"},
                                  "NEEDS_REVIEW", "PIPELINE_PROVIDER_PIN_INVALID")
            return True
        gateway.pin_role(role, pinned)
        try:
            if role == BIBLE_RUN_ROLE:
                return run_bible_task(factory, settings, gateway, task_id, version, worker_id)
            return run_scene_script_task(factory, settings, gateway, task_id, version, worker_id)
        except HTTPException as exc:
            _finish_pipeline_task(factory, task_id, version, worker_id,
                                  {"stage": "NEEDS_REVIEW", "blocked_reason": str(exc.detail)},
                                  "NEEDS_REVIEW", str(exc.detail))
            return True
        finally:
            gateway.pin_role(role, None)
    return False


def latest_unconfirmed_by_section(db, project_id: str, volume_id: str, section_id: str) -> AgentArtifact | None:
    """The newest unconfirmed SceneScript of one section."""
    payload = AgentArtifact.payload_json
    return db.scalar(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == SCENE_SCRIPT_KIND,
        AgentArtifact.approved_at.is_(None),
        AgentArtifact.stale.is_(False),
        payload["section_id"].as_string() == section_id,
        payload["source_refs"]["volume_id"].as_string() == volume_id,
    ).order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc()).limit(1))


def latest_unconfirmed(db, project_id: str, kind: str) -> AgentArtifact | None:
    """The newest artifact of a kind that still waits for a person."""
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == kind).order_by(AgentArtifact.created_at.desc())).all()
    return next((r for r in rows if not r.approved_at), None)


#: The inline budget of one crew run.  The storyboard request contract caps a
#: single call at 60,000 characters; staying well below it leaves room for the
#: locked facts, the JSON schema and the answer, so a batch can never be
#: refused by the very limit it was designed to respect.
STORYBOARD_UNIT_CHARS = 15_000

#: Failures that will fail identically for every remaining unit: a missing
#: route or a refused consent is not fixed by trying the next scene.
GLOBAL_STORYBOARD_STOPS = {
    "REMOTE_AGENT_CONSENT_REQUIRED",
    "STORYBOARD_AGENT_ROUTE_MISSING",
    "PIPELINE_MASTER_DOCUMENT_NOT_FOUND",
}


def serialize_volume_script(db, project_id: str, volume_id: str) -> str:
    """The volume's confirmed scripts, serialized in section order.

    This projection is the volume's *identity*, not one crew input: the crew
    chain runs per scene (see ``storyboard_units``), so a project-length
    volume is never refused by an inline character limit.  Its sha256 is what
    the storyboard receipts and the package lineage pin.
    """
    outline = _volume_outline(db, project_id, volume_id)
    if outline is None:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    if _volume_document(db, project_id, volume_id) is None:
        raise ValueError("PIPELINE_VOLUME_DOCUMENT_MISSING")
    sections = outline.payload_json.get("production_sections") or []
    parts = []
    for section in sections:
        script = current_scene_script(db, project_id, volume_id, section["production_section_id"])
        if script is None:
            raise ValueError("PIPELINE_SCENE_SCRIPT_NOT_CONFIRMED:" + section["production_section_id"])
        title = section.get("title", "")
        parts.append(f"第{section.get('order', '?')}场 {title}\n{script.payload_json.get('text', '')}")
    return "\n\n".join(parts)


def _split_scene_text(text: str, limit: int) -> list[str]:
    """Split one oversized scene at paragraph boundaries, never mid-sentence.

    A scene is the smallest unit that carries narrative meaning, so it is only
    divided when it cannot fit at all, and then on the paragraph boundaries
    the writer already wrote.  A single paragraph longer than the budget is
    the one case that must hard-split; it is labelled by chunk index, never
    dropped or silently truncated.
    """
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for paragraph in text.split("\n"):
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) > limit and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    result: list[str] = []
    for chunk in chunks:
        while len(chunk) > limit:
            result.append(chunk[:limit])
            chunk = chunk[limit:]
        if chunk:
            result.append(chunk)
    return result


def storyboard_units(db, project_id: str, volume_id: str) -> list[dict]:
    """One bounded crew input per scene, in plan order.

    Whole scenes are the unit because the per-scene mapping is exactly what
    the review table ("集 → 场 → 片段 → 镜") and the delivery coverage check
    need: a batch that mixed two scenes into one indistinguishable block would
    lose which shot belongs to which scene.  An oversized scene becomes
    labelled chunks, each with its own receipt and its own resume boundary.
    """
    outline = _volume_outline(db, project_id, volume_id)
    if outline is None:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    if _volume_document(db, project_id, volume_id) is None:
        raise ValueError("PIPELINE_VOLUME_DOCUMENT_MISSING")
    units: list[dict] = []
    for section in outline.payload_json.get("production_sections") or []:
        section_id = section["production_section_id"]
        script = current_scene_script(db, project_id, volume_id, section_id)
        if script is None:
            raise ValueError("PIPELINE_SCENE_SCRIPT_NOT_CONFIRMED:" + section_id)
        text = f"第{section.get('order', '?')}场 {section.get('title', '')}\n" \
               f"{script.payload_json.get('text', '')}"
        chunks = _split_scene_text(text, STORYBOARD_UNIT_CHARS)
        for index, chunk in enumerate(chunks, start=1):
            units.append({
                "unit_id": section_id if len(chunks) == 1 else f"{section_id}#{index}",
                "section_id": section_id,
                "scene_key": section.get("scene_key", section_id),
                "order": section.get("order"),
                "title": section.get("title", ""),
                "chunk_index": index,
                "chunk_count": len(chunks),
                "chunk_sha256": digest(chunk),
                "script_sha256": script.sha256,
                "source_start": section.get("start_offset"),
                "source_end": section.get("end_offset"),
                "continuity_in": script.payload_json.get("continuity_in", ""),
                "continuity_out": script.payload_json.get("continuity_out", ""),
                "text": chunk,
                "char_count": len(chunk),
            })
    return units


def expected_storyboard_units(db, project_id: str, volume_id: str) -> list[dict]:
    """The unit list this volume must produce, derived from the current work.

    The list is computed, never negotiated: it comes from the current scene
    revision, the source range, the chunk ordinal and its hash, and the
    continuity baseline.  Two runs over the same work therefore expect the
    same units in the same order, and a delivery is compared against this
    list instead of against a count.
    """
    units = storyboard_units(db, project_id, volume_id)
    return [{key: value for key, value in unit.items() if key != "text"}
            for unit in units]


def _storyboard_ledger_key(project_id: str, volume_id: str) -> str:
    return f"four-stage-storyboard-units:{project_id}:{volume_id}"


def storyboard_ledger(db, project_id: str, volume_id: str) -> dict:
    """The persistent per-unit ledger of one volume.

    It is the read-back point after a refresh, a crash or a restart: every
    unit the crew chain really produced, every unit it failed on and every
    apply handle it produced are recorded here, per unit, and a partial
    redo only replaces the units it actually ran.
    """
    pointer = _read_pointer(db, _storyboard_ledger_key(project_id, volume_id))
    return dict(pointer or {})


def merge_storyboard_ledger(db, project_id: str, volume_id: str,
                             units: list[dict], status: str) -> dict:
    """Merge the units of one run into the persistent ledger.

    Only the units this run produced are replaced.  The other scenes keep
    their receipts, their applied shots and their history, so a partial redo
    can never erase the work a person already reviewed.
    """
    from shine.models import AppSetting

    ledger = storyboard_ledger(db, project_id, volume_id)
    stored = dict(ledger.get("units") or {})
    now = utcnow().isoformat() + "Z"
    for unit in units:
        unit_id = unit.get("unit_id")
        if not unit_id:
            continue
        stored[unit_id] = {**stored.get(unit_id, {}), **unit, "updated_at": now}
    value = {
        "schema_version": "1.0",
        "volume_id": volume_id,
        "status": status,
        "units": stored,
        "updated_at": now,
    }
    db.merge(AppSetting(key=_storyboard_ledger_key(project_id, volume_id), value=value))
    return value


def applied_shots_for(db, camera_artifact_id: str | None) -> dict:
    """Whether this scene's camera plan was applied, and which shots it made.

    The review table reads "scene -> shot" from this: a scene counts as
    produced only when its own apply receipt exists, and the shot ids are
    what the handoff will verify.
    """
    from shine.models import AppSetting

    if not camera_artifact_id:
        return {"applied": False, "shot_ids": []}
    receipt = db.get(AppSetting, f"film-apply:{camera_artifact_id}")
    if receipt is None:
        return {"applied": False, "shot_ids": []}
    shot_ids = [str(item) for item in (receipt.value or {}).get("shot_ids", [])]
    return {"applied": bool(shot_ids), "shot_ids": shot_ids}


def volume_storyboard_state(db, project_id: str, volume_id: str) -> dict:
    """The authoritative per-unit state of one volume's storyboard draft.

    It is read from the persistent ledger, so a refresh, a crash or a restart
    shows exactly what exists: every unit that produced a proposal, every
    unit that failed and where, and the apply handle of each one.
    """
    ledger = storyboard_ledger(db, project_id, volume_id)
    stored = ledger.get("units") or {}
    units = []
    for unit_id in sorted(stored, key=lambda key: (
            stored[key].get("order") is None, stored[key].get("order") or 0,
            stored[key].get("chunk_index") or 1)):
        entry = dict(stored[unit_id])
        applied = applied_shots_for(db, entry.get("camera_artifact_id"))
        units.append({
            "unit_id": unit_id,
            "section_id": entry.get("section_id"),
            "scene_key": entry.get("scene_key"),
            "order": entry.get("order"),
            "title": entry.get("title"),
            "chunk_index": entry.get("chunk_index", 1),
            "chunk_count": entry.get("chunk_count", 1),
            "status": entry.get("status"),
            "camera_artifact_id": entry.get("camera_artifact_id"),
            "camera_sha256": entry.get("camera_sha256"),
            "continuity_artifact_id": entry.get("continuity_artifact_id"),
            "arbitration_artifact_id": entry.get("arbitration_artifact_id"),
            "script_sha256": entry.get("script_sha256"),
            "chunk_sha256": entry.get("chunk_sha256"),
            "rejections": entry.get("rejections") or [],
            "missing_characters": entry.get("missing_characters") or [],
            "dialogue_coverage": entry.get("dialogue_coverage"),
            "updated_at": entry.get("updated_at"),
            **applied,
        })
    return {
        "status": ledger.get("status"),
        "updated_at": ledger.get("updated_at"),
        "units": units,
    }


def volume_handoff_receipt(db, project_id: str, volume_id: str) -> dict | None:
    """The latest delivery receipt of one volume, with its exact package id.

    The receipt names the package the person can open, not a truncated
    preview: a delivery that cannot be located afterwards is not a receipt.
    """
    from shine.director.models import DirectorPackageVersion
    from shine.director.package import package_readiness

    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "FourStageVolumeHandoff").order_by(
        AgentArtifact.created_at.desc())).all()
    for row in rows:
        if row.payload_json.get("volume_id") != volume_id:
            continue
        package = db.get(DirectorPackageVersion, row.payload_json.get("package_id") or "")
        return {
            "handoff_id": row.id,
            "package_id": row.payload_json.get("package_id"),
            "package_revision": row.payload_json.get("package_revision"),
            "plan_version": row.payload_json.get("episode_plan_version"),
            "episodes": row.payload_json.get("episode_count"),
            "shots": row.payload_json.get("shot_count"),
            "episode_basis": row.payload_json.get("episode_basis"),
            "lineage_sha256": row.payload_json.get("lineage_sha256"),
            "created_at": (row.created_at.isoformat() + "Z" if row.created_at else None),
            "readiness": (package_readiness(db, package) if package
                         else {"startable": False, "blockers": []}),
        }
    return None


def volume_episode_contracts(db, project_id: str, volume_id: str) -> list[dict]:
    """The confirmed episode contracts of one volume, with their currency."""
    out = []
    for row in _episode_contracts(db, project_id, volume_id):
        reason = episode_contract_currency(db, project_id, volume_id, row)
        out.append({
            "artifact_id": row.id,
            "sha256": row.sha256,
            "title": row.payload_json.get("title"),
            "sections": [entry.get("section_id")
                         for entry in row.payload_json.get("sections") or []],
            "confirmed": True,
            "current": not reason,
            "reason": reason or None,
        })
    return out


def volume_production_state(db, project_id: str, volume_id: str) -> dict:
    """Everything the workbench needs to show about one volume, read at once.

    The panel renders from this: the authoritative draft, the per-unit state,
    the pending applies, the failures with their reconciliation handles, the
    delivery receipt and the exact package id.
    """
    return {
        "volume_id": volume_id,
        "storyboard": volume_storyboard_state(db, project_id, volume_id),
        "episode_contracts": volume_episode_contracts(db, project_id, volume_id),
        "handoff": volume_handoff_receipt(db, project_id, volume_id),
    }


def volume_storyboard_draft(db, project_id: str, volume_id: str, gateway, data_dir=None,
                            only_section_ids: list[str] | None = None) -> dict:
    """Run the draft crew chain over every confirmed scene, one unit at a time.

    Each unit is one bounded crew input, so a project-length volume is written
    scene by scene instead of being refused by an inline limit, and a failed
    unit never invalidates the units that already passed: the run reports
    ``INCOMPLETE`` with the exact failed boundary and keeps the receipts of
    the units that really produced a proposal.

    These crew calls send *derived* script text to a provider, so the master
    document's persistent consent travels with every one of them:
    ``allow_remote_text`` only states that this lane may use a remote provider
    at all, while the per-role document consent guard inside the Dispatcher is
    what authorises each individual send.
    """
    from types import SimpleNamespace

    from shine.api_film import StoryboardRequest, generate_storyboard

    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    bible = current_bible(db, project_id) or latest_bible_artifact(db, project_id)
    if bible is None:
        raise ValueError("PIPELINE_BIBLE_NOT_CONFIRMED")
    master = db.get(ProjectDocument, plan.payload_json["source_master"]["document_id"])
    if master is None or master.project_id != project_id:
        raise ValueError("PIPELINE_MASTER_DOCUMENT_NOT_FOUND")
    units = storyboard_units(db, project_id, volume_id)
    if only_section_ids:
        selected = set(only_section_ids)
        units = [unit for unit in units if unit["section_id"] in selected]
    if not units:
        raise ValueError("PIPELINE_STORYBOARD_UNITS_EMPTY")
    namespace = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(data_dir=data_dir))))
    locked = list(bible.payload_json.get("locked_facts", []))[:100]
    receipts: list[dict] = []
    rejections: list[dict] = []
    incomplete = False
    previous_ending = None
    last_steps: dict = {}
    last_apply: dict = {}
    for unit in units:
        request_text = unit["text"]
        if previous_ending:
            # The previous scene's closing state is the explicit boundary a
            # later batch starts from, so two units never restart cold.
            request_text = f"【上一场收尾】{previous_ending}\n\n{request_text}"
        base = {key: value for key, value in unit.items() if key != "text"}
        try:
            result = generate_storyboard(
                project_id,
                StoryboardRequest(
                    text=request_text, draft=True, locked_story_facts=locked,
                    allow_remote_text=True,
                    consent_document_id=master.id,
                    consent_document_sha256=master.sha256,
                ),
                namespace, db, gateway,
            )
        except HTTPException as exc:
            detail = exc.detail
            code = detail if isinstance(detail, str) else str((detail or {}).get("code", "STORYBOARD_REJECTED"))
            rejection = {"unit_id": unit["unit_id"], "section_id": unit["section_id"],
                         "code": code, "external_outcome": "NOT_SENT"}
            rejections.append(rejection)
            # The failed unit is recorded like any other: a unit that was
            # never answered must stay visible, so the delivery can refuse
            # it by name instead of quietly shrinking the scope.
            receipts.append({**base, "status": "REJECTED", "steps": {},
                             "rejections": [rejection],
                             "dialogue_coverage": None,
                             "camera_artifact_id": None, "camera_sha256": None,
                             "continuity_artifact_id": None,
                             "arbitration_artifact_id": None})
            incomplete = True
            if code.split(":")[0] in GLOBAL_STORYBOARD_STOPS:
                break
            continue
        apply_info = (result.get("next") or {}).get("apply_camera") or {}
        steps = result.get("steps") or {}
        unit_rejections = [{**item, "unit_id": unit["unit_id"],
                            "section_id": unit["section_id"]}
                           for item in (result.get("rejections") or [])]
        receipt = {
            **base,
            "status": result.get("status"),
            "steps": steps,
            "rejections": unit_rejections,
            "dialogue_coverage": result.get("dialogue_coverage"),
            # What this design asks the material library for: the characters
            # it names that have no approved master yet.  The storyboard is
            # finished; these are what the material step has to answer.
            "missing_characters": [str(name) for name in (
                (result.get("material_requirements") or {}).get("characters") or [])],
            "camera_artifact_id": apply_info.get("camera_artifact_id"),
            "camera_sha256": (steps.get("cinematographer") or {}).get("sha256"),
            "continuity_artifact_id": apply_info.get("continuity_artifact_id"),
            "arbitration_artifact_id": apply_info.get("arbitration_artifact_id"),
        }
        receipts.append(receipt)
        if result.get("status") != "PROPOSED":
            incomplete = True
            rejections.extend(unit_rejections)
            if any(item.get("external_outcome") == "NOT_SENT"
                   for item in (result.get("rejections") or [])):
                # A provably unsent call will fail the same way for every
                # remaining unit: stop, keep the completed receipts, and let
                # the person fix the cause before continuing.
                break
            continue
        last_steps = steps
        last_apply = apply_info
        previous_ending = unit.get("continuity_out") or previous_ending
    proposed = [item for item in receipts if item["status"] == "PROPOSED"]
    return {
        "status": "PROPOSED" if proposed and not incomplete else "INCOMPLETE",
        "units": receipts,
        "proposed_units": len(proposed),
        "total_units": len(receipts),
        "steps": last_steps,
        "next": {"apply_camera": last_apply} if last_apply else {},
        "rejections": rejections,
        "missing_characters": [],
    }


def record_storyboard_draft(db, project_id: str, volume_id: str, result: dict,
                            script_text: str = "") -> dict:
    """Record the per-unit draft receipts the handoff verifies.

    The record is the mapping between a scene and the shots it will produce,
    so it keeps the unit order, each unit's camera artifact and hash, and the
    script digest it was built from.
    """
    from shine.models import AppSetting

    units = result.get("units") or []
    # The persistent ledger is the authority: this run replaces only the
    # units it really produced, and every other scene keeps its receipt,
    # its applied shots and its history.
    ledger = merge_storyboard_ledger(db, project_id, volume_id, units,
                                     result.get("status") or "")
    stored_units = ledger.get("units") or {}
    ordered = sorted(
        stored_units.values(),
        key=lambda unit: (unit.get("order") is None, unit.get("order") or 0,
                          unit.get("chunk_index") or 1))
    first_camera = next((unit for unit in ordered if unit.get("camera_artifact_id")), {})
    record = {
        "camera_artifact_id": first_camera.get("camera_artifact_id"),
        "camera_sha256": first_camera.get("camera_sha256"),
        "script_sha256": digest(script_text) if script_text else None,
        "status": result.get("status"),
        "units": ordered,
        "ledger_updated_at": ledger.get("updated_at"),
        "missing_characters": result.get("missing_characters", []),
        "recorded_at": utcnow().isoformat() + "Z",
    }
    key = f"four-stage-storyboard:{project_id}:{volume_id}"
    db.merge(AppSetting(key=key, value=record))
    return record


def creative_lineage(db, project_id: str, volume_id: str) -> dict:
    """The exact creative identities a volume's production was derived from.

    A package built from this volume pins this digest, so editing a scene, a
    blueprint or an episode contract is detected before a run starts or a
    batch is dispatched - instead of discovering it after paying for renders.
    """
    plan = current_volume_plan(db, project_id)
    outline = _volume_outline(db, project_id, volume_id)
    bible = current_bible(db, project_id)
    sections = (outline.payload_json.get("production_sections") or []) if outline else []
    script_parts = []
    for section in sections:
        section_id = section["production_section_id"]
        script = current_scene_script(db, project_id, volume_id, section_id)
        script_parts.append({"section_id": section_id,
                             "script_sha256": script.sha256 if script else None})
    plan_volume = None
    if plan:
        plan_volume = next((v for v in plan.payload_json.get("volumes") or []
                            if v["volume_id"] == volume_id), None)
    payload = {
        "volume_id": volume_id,
        "plan_sha256": plan.sha256 if plan else None,
        "volume_revision": (plan_volume or {}).get("volume_revision"),
        "outline_sha256": outline.sha256 if outline else None,
        "bible_sha256": bible.sha256 if bible else None,
        "sections": script_parts,
        "episode_scripts": [{"artifact_id": row.id, "sha256": row.sha256}
                            for row in confirmed_episode_scripts(db, project_id, volume_id)],
    }
    return {**payload, "sha256": digest(payload)}


def current_lineage_digest(db, project_id: str, volume_id: str) -> str | None:
    """The recomputed lineage digest, or None when the volume cannot be read."""
    try:
        return creative_lineage(db, project_id, volume_id)["sha256"]
    except ValueError:
        return None


def _section_shot_groups(db, project_id: str, record: dict,
                         expected_units: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """The ordered scene -> shots mapping the apply receipts actually prove.

    Each unit's camera artifact must still be the artifact that was applied,
    must still belong to this project and must still hash to what the record
    pinned.  A receipt that names no shot, or a shot two units both claim, is
    a coverage defect - never a number to be compared against a count.

    When the volume has an expected unit list, the mapping is verified
    against it unit by unit: a missing, duplicated, out-of-scope, stale or
    unapplied unit is a named defect.  The scope of a delivery is therefore
    the work that really exists, not the set of scenes that happen to carry
    a camera artifact.
    """
    from shine.models import AppSetting
    from shine.models import Shot as _Shot

    groups: list[dict] = []
    problems: list[str] = []
    claimed: set[str] = set()
    stored = record.get("units") or {}
    if isinstance(stored, list):
        stored = {unit.get("unit_id"): unit for unit in stored if unit.get("unit_id")}
    if expected_units:
        expected_by_id = {str(unit["unit_id"]): unit for unit in expected_units}
        current_sections = {str(unit.get("section_id")) for unit in expected_units}
        for unit_id in stored:
            if unit_id in expected_by_id:
                continue
            entry = stored[unit_id] or {}
            if str(entry.get("section_id")) in current_sections:
                # An older revision of a scene the current work still has:
                # it is history, not scope.  The current chunks of that
                # scene are verified on their own.
                continue
            # A unit the current work does not produce at all: it is out of
            # scope and may never widen the delivery.
            problems.append("PIPELINE_STORYBOARD_UNIT_OUT_OF_SCOPE:" + str(unit_id))
        for expected in expected_units:
            unit_id = str(expected["unit_id"])
            entry = stored.get(unit_id)
            if entry is None:
                problems.append("PIPELINE_STORYBOARD_UNIT_MISSING:" + unit_id)
                continue
            if entry.get("status") not in (None, "PROPOSED"):
                problems.append("PIPELINE_STORYBOARD_UNIT_REJECTED:" + unit_id)
                continue
            if (entry.get("chunk_sha256") != expected.get("chunk_sha256")
                    or entry.get("script_sha256") != expected.get("script_sha256")):
                problems.append("PIPELINE_STORYBOARD_UNIT_STALE:" + unit_id)
                continue
            camera_id = entry.get("camera_artifact_id")
            if not camera_id:
                problems.append("PIPELINE_STORYBOARD_UNIT_MISSING:" + unit_id)
                continue
            camera = db.get(AgentArtifact, camera_id)
            if (camera is None or camera.project_id != project_id or camera.stale
                    or (entry.get("camera_sha256") and camera.sha256 != entry["camera_sha256"])
                    or digest(camera.payload_json) != camera.sha256):
                problems.append("PIPELINE_STORYBOARD_CHANGED:" + unit_id)
                continue
            receipt = db.get(AppSetting, f"film-apply:{camera_id}")
            if receipt is None:
                problems.append("PIPELINE_STORYBOARD_NOT_APPLIED:" + unit_id)
                continue
            shot_ids = [str(item) for item in (receipt.value or {}).get("shot_ids", [])]
            if not shot_ids:
                problems.append("PIPELINE_STORYBOARD_APPLIED_EMPTY:" + unit_id)
                continue
            for shot_id in shot_ids:
                if shot_id in claimed:
                    problems.append("PIPELINE_SHOT_CLAIMED_TWICE:" + shot_id)
                claimed.add(shot_id)
                row = db.get(_Shot, shot_id)
                if row is None or row.project_id != project_id:
                    problems.append("PIPELINE_STORYBOARD_SHOT_MISSING:" + shot_id)
            groups.append({"unit_id": unit_id, "section_id": entry.get("section_id"),
                           "order": entry.get("order"), "chunk_index": entry.get("chunk_index", 1),
                           "camera_artifact_id": camera_id, "shot_ids": shot_ids})
        return groups, problems
    units = list(stored.values())
    if not units and record.get("camera_artifact_id"):
        # A record written by the single-chain lane: one unit over every scene.
        units = [{"unit_id": "legacy", "section_id": None,
                  "camera_artifact_id": record["camera_artifact_id"],
                  "camera_sha256": record.get("camera_sha256")}]
    for unit in units:
        camera_id = unit.get("camera_artifact_id")
        label = str(unit.get("section_id") or unit.get("unit_id") or "?")
        if unit.get("status") not in (None, "PROPOSED"):
            problems.append("PIPELINE_STORYBOARD_UNIT_REJECTED:" + label)
            continue
        if not camera_id:
            problems.append("PIPELINE_STORYBOARD_UNIT_MISSING:" + label)
            continue
        camera = db.get(AgentArtifact, camera_id)
        if (camera is None or camera.project_id != project_id or camera.stale
                or (unit.get("camera_sha256") and camera.sha256 != unit["camera_sha256"])
                or digest(camera.payload_json) != camera.sha256):
            problems.append("PIPELINE_STORYBOARD_CHANGED:" + label)
            continue
        receipt = db.get(AppSetting, f"film-apply:{camera_id}")
        if receipt is None:
            problems.append("PIPELINE_STORYBOARD_NOT_APPLIED:" + label)
            continue
        shot_ids = [str(item) for item in (receipt.value or {}).get("shot_ids", [])]
        if not shot_ids:
            problems.append("PIPELINE_STORYBOARD_APPLIED_EMPTY:" + label)
            continue
        for shot_id in shot_ids:
            if shot_id in claimed:
                problems.append("PIPELINE_SHOT_CLAIMED_TWICE:" + shot_id)
            claimed.add(shot_id)
            row = db.get(_Shot, shot_id)
            if row is None or row.project_id != project_id:
                # A receipt that names a shot this project no longer holds
                # cannot become a delivery scope: refuse by id instead of
                # failing later while building the package manifest.
                problems.append("PIPELINE_STORYBOARD_SHOT_MISSING:" + shot_id)
        groups.append({"unit_id": unit.get("unit_id"), "section_id": unit.get("section_id"),
                       "order": unit.get("order"), "chunk_index": unit.get("chunk_index", 1),
                       "camera_artifact_id": camera_id, "shot_ids": shot_ids})
    return groups, problems


def _handoff_episodes(db, project_id: str, volume_id: str, groups: list[dict]) -> tuple[list[dict], str]:
    """The delivery episodes, derived from the confirmed episode contracts.

    A confirmed EpisodeScript owns the episode boundaries, so the plan follows
    it exactly and never re-cuts the volume by a duration heuristic.  Without
    a confirmed contract the volume is delivered as one episode over its
    scenes in order, and the basis is recorded instead of being disguised.
    """
    from shine.models import Shot

    durations = {}
    for group in groups:
        for shot_id in group["shot_ids"]:
            shot = db.get(Shot, shot_id)
            durations[shot_id] = float((shot.spec or {}).get("duration_target_s") or 0) if shot else 0.0
    contracts = confirmed_episode_scripts(db, project_id, volume_id)
    if contracts:
        outline = _volume_outline(db, project_id, volume_id)
        # Delivery order follows the script's own scene order, not the order in
        # which the person happened to create the episode contracts: an episode
        # covering scene 1 is episode 1 even if it was confirmed last.
        section_order = {section["production_section_id"]: index
                         for index, section in enumerate(
                             (outline.payload_json.get("production_sections") or [])
                             if outline else [])}
        contracts = sorted(
            contracts,
            key=lambda row: min((section_order.get(entry["section_id"], 10_000)
                                 for entry in row.payload_json.get("sections") or []),
                                default=10_000))
        episodes = []
        for index, contract in enumerate(contracts, start=1):
            wanted = [entry["section_id"] for entry in contract.payload_json.get("sections") or []]
            shot_ids = [shot_id for group in groups if group["section_id"] in wanted
                        for shot_id in group["shot_ids"]]
            episodes.append({
                "episode_id": f"{volume_id}-e{index}",
                "order": index,
                "title": contract.payload_json.get("title") or f"第{index}集",
                "shot_ids": shot_ids,
                "planned_seconds": round(sum(durations.get(shot_id, 0.0) for shot_id in shot_ids), 3),
                "section_ids": wanted,
                "episode_script_id": contract.id,
                "episode_script_sha256": contract.sha256,
            })
        return episodes, "episode_script"
    ordered: list[str] = []
    for group in groups:
        for shot_id in group["shot_ids"]:
            if shot_id not in ordered:
                ordered.append(shot_id)
    return ([{
        "episode_id": f"{volume_id}-e1", "order": 1, "title": "第1集",
        "shot_ids": ordered,
        "planned_seconds": round(sum(durations.get(shot_id, 0.0) for shot_id in ordered), 3),
        "section_ids": [group["section_id"] for group in groups if group["section_id"]],
    }], "volume_scope")


def delivery_snapshot(db, project_id: str, volume_id: str, groups: list[dict],
                      workflow_profile_id: str) -> dict:
    """The full identity of one volume's delivery, recomputed every time.

    It binds the ordered units and their applies, every shot's revision and
    spec, the shot bindings, the frozen style, the rule pack and the
    workflow graph with its parameter map.  Two deliveries may share a
    package only when this whole snapshot is identical, so a changed shot
    revision produces a new package instead of silently reusing the old one.
    """
    from shine.models import (
        Project,
        ProjectRuleLock,
        StyleVersion,
        WorkflowProfile,
    )
    from shine.models import (
        Shot as _Shot,
    )

    project = db.get(Project, project_id)
    style = None
    if project is not None and project.frozen_style_version_id:
        style_row = db.get(StyleVersion, project.frozen_style_version_id)
        if style_row is not None:
            style = {"style_version_id": style_row.id, "sha256": style_row.sha256}
    rule_pack = None
    lock = db.scalars(select(ProjectRuleLock).where(
        ProjectRuleLock.project_id == project_id)).first()
    if lock is not None:
        rule_pack = {"rulepack_id": lock.rulepack_id, "rulepack_hash": lock.rulepack_hash}
    workflow = None
    profile = db.get(WorkflowProfile, workflow_profile_id)
    if profile is not None:
        workflow = {
            "workflow_profile_id": profile.id,
            "graph_sha256": profile.graph_sha256,
            "checkpoint_id": profile.checkpoint_id,
            "mode": profile.mode,
            "parameter_map": profile.parameter_map or {},
            "media_transfers": profile.media_transfers or {},
        }
    shots = []
    for group in groups:
        for shot_id in group.get("shot_ids") or []:
            shot = db.get(_Shot, shot_id)
            if shot is None:
                shots.append({"shot_id": shot_id, "missing": True})
                continue
            shots.append({
                "shot_id": shot.id,
                "revision": shot.revision,
                "spec_sha256": digest(shot.spec or {}),
                "bindings": [
                    {"asset_id": link.asset_id, "use_as": link.use_as,
                     "role": link.role, "ordinal": link.ordinal}
                    for link in sorted(shot.bindings, key=lambda row: row.ordinal)],
            })
    return {
        "volume_id": volume_id,
        "units": [
            {"unit_id": group.get("unit_id"), "section_id": group.get("section_id"),
             "order": group.get("order"), "chunk_index": group.get("chunk_index"),
             "camera_artifact_id": group.get("camera_artifact_id"),
             "shot_ids": list(group.get("shot_ids") or [])}
            for group in groups],
        "shots": shots,
        "style": style,
        "rule_pack": rule_pack,
        "workflow": workflow,
    }


def build_volume_handoff(db, project_id: str, volume_id: str, workflow_profile_id: str,
                         *, acknowledge_scene_coverage: bool = False, settings=None) -> dict:
    """Publish the volume to the existing production machinery.

    The scope is the ordered ``section -> shots`` mapping the apply receipts
    prove, and the episodes come from the confirmed episode contracts.  A
    shot count is never evidence of coverage: a volume whose scenes are not
    all mapped is refused by scene name.  Confirming a package, creating a run
    and dispatching a batch all read the same readiness, which pins this
    creative lineage, so an upstream edit stops production before it spends a
    render rather than after.

    ``acknowledge_scene_coverage`` is accepted and recorded for compatibility
    but no longer waives coverage: the mapping is verified, not counted.
    """
    from shine.director.contracts import EpisodePlanPayload
    from shine.director.models import DirectorEpisodePlan, DirectorPackageVersion
    from shine.director.package import build_package, confirm_package, package_readiness
    from shine.models import Project, Shot, WorkflowProfile
    from shine.services import apply_missing_character_bindings

    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    bible = current_bible(db, project_id)
    if bible is None:
        raise ValueError("PIPELINE_BIBLE_NOT_CONFIRMED")
    basis_problems = bible_basis_blockers(db, project_id, bible)
    if basis_problems:
        # The blueprint must still describe the current work before a volume
        # may be produced from it.
        raise ValueError(basis_problems[0])
    outline = _volume_outline(db, project_id, volume_id)
    if outline is None:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("PROJECT_NOT_FOUND")
    if not project.frozen_style_version_id:
        raise ValueError("STYLE_NOT_FROZEN")
    volume = next((v for v in plan.payload_json["volumes"] if v["volume_id"] == volume_id), None)
    if volume is None:
        raise ValueError("PIPELINE_VOLUME_NOT_IN_PLAN")
    ledger = storyboard_ledger(db, project_id, volume_id)
    legacy_record = _read_pointer(db, f"four-stage-storyboard:{project_id}:{volume_id}")
    if ledger.get("units"):
        # The persistent ledger is the delivery scope: a partial redo keeps
        # the other scenes, so the record is never a whole-volume overwrite.
        record = {"units": ledger["units"]}
        has_ledger = True
    elif legacy_record is not None and (legacy_record.get("units")
                                       or legacy_record.get("camera_artifact_id")):
        record = legacy_record
        has_ledger = False
    else:
        raise ValueError("PIPELINE_STORYBOARD_NOT_GENERATED")
    # The unit list the current work expects.  It is computed from the
    # confirmed scene revisions, so a scene that lost its confirmation is
    # refused by name before anything else is considered.
    expected_units = expected_storyboard_units(db, project_id, volume_id) if has_ledger else None
    sections = outline.payload_json.get("production_sections") or []
    # A scene written against a draft blueprint may only be published when
    # the confirmed blueprint still says the same decisive things: the
    # characters, the world, the timeline, the locked facts, the ending and
    # the cross-volume open questions.
    for section in sections:
        section_id = section["production_section_id"]
        script = current_scene_script(db, project_id, volume_id, section_id)
        scene_basis = scene_script_bible_basis(db, project_id, script)
        if scene_basis.get("bible_basis") != "draft_unconfirmed":
            continue
        draft = db.get(AgentArtifact, scene_basis.get("bible_artifact_id") or "")
        if draft is None:
            raise ValueError("PIPELINE_BIBLE_BASIS_MISSING:" + section_id)
        if not bible_basis_equivalent(draft.payload_json, bible.payload_json):
            raise ValueError("PIPELINE_BIBLE_BASIS_CHANGED:" + section_id)
    episode_contracts = confirmed_episode_scripts(db, project_id, volume_id)
    stale_contracts = stale_episode_scripts(db, project_id, volume_id)
    if stale_contracts:
        # A confirmed episode contract whose outline or scene scripts moved on
        # is history.  The person confirmed episodes, so the volume may not
        # silently fall back to one flat scope: the rebuilt contract has to be
        # confirmed first, and the reason is named.
        raise ValueError(stale_contracts[0]["reason"])
    if episode_contracts:
        contract_problems = episode_contract_problems(
            db, project_id, volume_id, episode_contracts, sections)
        if contract_problems:
            raise ValueError(contract_problems[0])
    lineage = creative_lineage(db, project_id, volume_id)
    if expected_units is None and record.get("script_sha256") and record["script_sha256"] != digest(
            serialize_volume_script(db, project_id, volume_id)):
        raise ValueError("PIPELINE_SCRIPT_CHANGED_SINCE_STORYBOARD")
    groups, problems = _section_shot_groups(db, project_id, record, expected_units)
    grouped_sections = {group["section_id"] for group in groups if group["section_id"]}
    for section in sections:
        section_id = section["production_section_id"]
        if section_id not in grouped_sections:
            problems.append("PIPELINE_SCENE_WITHOUT_SHOT:" + section_id)
    if problems:
        raise ValueError(problems[0])
    episodes, episode_basis = _handoff_episodes(db, project_id, volume_id, groups)
    payload = {
        "schema_version": "1.0",
        "mode": "shots",
        "target_seconds": 60,
        "tolerance_seconds": 15,
        "episodes": episodes,
        "allowed_repeats": [],
        "source": {"volume_id": volume_id, "kind": "four_stage_volume",
                   "outline_artifact_id": outline.id, "outline_sha256": outline.sha256,
                   "episode_basis": episode_basis,
                   "lineage_sha256": lineage["sha256"]},
    }
    payload = EpisodePlanPayload.model_validate(payload)
    planned = payload.shot_order()
    scope = {shot_id for group in groups for shot_id in group["shot_ids"]}
    problems = []
    if sorted(planned) != sorted(scope):
        problems.append("VOLUME_SHOTS_NOT_COVERED_EXACTLY")
    if len(planned) != len(set(planned)):
        problems.append("VOLUME_SHOT_COVERED_TWICE")
    if problems:
        raise ValueError("PIPELINE_VOLUME_PLAN_SCOPE_INVALID:" + ",".join(problems)[:400])
    plan_dump = payload.model_dump(mode="json")
    profile = db.get(WorkflowProfile, workflow_profile_id)
    if profile and profile.project_id in (None, project_id) and profile.mode.lower() == "ref2va":
        apply_missing_character_bindings(
            db, project, [db.get(Shot, shot_id) for shot_id in planned], actor="local-user",
        )
    handoff_digest = digest({
        "project": project_id, "volume": volume_id,
        "workflow_profile": workflow_profile_id,
        "lineage_sha256": lineage["sha256"],
        "episode_basis": episode_basis,
        "episode_plan_sha256": digest(plan_dump),
        "shot_order": planned,
        # The whole delivery snapshot: the units and their applies, every
        # shot revision and spec, the bindings, the frozen style, the rule
        # pack and the workflow graph with its parameter map.  Only an
        # identical snapshot may reuse a package.
        "snapshot": delivery_snapshot(db, project_id, volume_id, groups, workflow_profile_id),
    })
    for existing in db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "FourStageVolumeHandoff")).all():
        if existing.payload_json.get("handoff_digest") != handoff_digest:
            continue
        # The same confirmed scope was already handed off: return that exact
        # receipt instead of creating a second plan, package and handoff.
        package = db.get(DirectorPackageVersion, existing.payload_json.get("package_id") or "")
        return {
            "handoff_id": existing.id, "reused": True,
            "plan_version": existing.payload_json.get("episode_plan_version"),
            "package_revision": existing.payload_json.get("package_revision"),
            "package_id": existing.payload_json.get("package_id"),
            "readiness": package_readiness(db, package) if package else {"startable": False},
            "episodes": existing.payload_json.get("episode_count"),
            "shots": existing.payload_json.get("shot_count"),
            "episode_basis": existing.payload_json.get("episode_basis"),
        }
    previous = db.scalars(
        select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == project_id)).all()
    version = max((row.version for row in previous), default=0) + 1
    plan_row = DirectorEpisodePlan(
        project_id=project_id, version=version, status="DRAFT",
        target_seconds=payload.target_seconds, tolerance_seconds=payload.tolerance_seconds,
        payload=plan_dump,
        sha256=digest(plan_dump),
        source="four_stage_volume", created_by="local-user",
    )
    db.add(plan_row)
    db.flush()
    plan_row.status = "CONFIRMED"
    plan_row.confirmed_by = "local-user"
    plan_row.confirmed_at = utcnow()
    shot_ids = [shot_id for group in groups for shot_id in group["shot_ids"]]
    package = build_package(db, project, settings=settings, workflow_profile_id=workflow_profile_id,
                            plan=plan_row, scope_shot_ids=shot_ids,
                            creative_lineage=lineage)
    confirm_package(db, package, actor="local-user")
    handoff_payload = {
        "schema_version": "1.0",
        "volume_id": volume_id, "volume_order": volume["order"],
        "episode_plan_version": plan_row.version, "episode_plan_sha256": plan_row.sha256,
        "package_revision": package.revision, "package_id": package.id,
        "package_manifest_sha256": package.manifest_sha256,
        "camera_artifact_id": record.get("camera_artifact_id"),
        "script_sha256": digest(serialize_volume_script(db, project_id, volume_id)),
        "lineage_sha256": lineage["sha256"],
        "handoff_digest": handoff_digest,
        "episode_basis": episode_basis,
        "shot_count": len(shot_ids),
        "episode_count": len(episodes),
        "episode_script_ids": [contract.id for contract in episode_contracts],
        "scene_coverage": {
            "scenes": len(sections),
            "scenes_mapped": len(grouped_sections),
            "shots": len(shot_ids),
            "acknowledged": bool(acknowledge_scene_coverage),
        },
    }
    task_id = _authoring_task(db, project_id, "four_stage_handoff", {"volume": volume_id})
    artifact = AgentArtifact(
        project_id=project_id, kind="FourStageVolumeHandoff", schema_version="1.0",
        payload_json=handoff_payload, sha256=digest(handoff_payload),
        producer_role="four_stage_handoff", source_task_id=task_id,
        input_revision_ids=[outline.id],
    )
    db.add(artifact)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_VOLUME_HANDOFF", "agent_artifact", artifact.id,
              {"volume_id": volume_id, "package": package.revision,
               "episode_basis": episode_basis}, project_id=project_id)
    readiness = package_readiness(db, package)
    return {"handoff_id": artifact.id, "reused": False,
            "plan_version": plan_row.version, "package_revision": package.revision,
            "package_id": package.id, "readiness": readiness, "episodes": len(episodes),
            "shots": len(shot_ids), "episode_basis": episode_basis}


def adjust_volume_boundaries(db, project_id: str, base_plan_id: str,
                             volumes: list[dict]) -> AgentArtifact:
    """A person's manual boundary adjustment becomes a NEW draft plan revision.

    The adjustment is validated before it exists: volumes must cover the
    master completely, never overlap, stay ordered and keep every section
    inside exactly one volume - a chapter is never split in half.  The base
    plan stays history; the new revision needs its own confirmation.
    """
    from shine.models import AgentArtifact, ProjectDocument

    base = db.get(AgentArtifact, base_plan_id)
    if (base is None or base.project_id != project_id or base.kind != VOLUME_PLAN_KIND):
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_FOUND")
    master_doc_id = base.payload_json["source_master"]["document_id"]
    master = db.get(ProjectDocument, master_doc_id)
    if master is None or master.project_id != project_id:
        raise ValueError("PIPELINE_MASTER_DOCUMENT_NOT_FOUND")
    text = master.text or ""
    total = len(text)
    sections = source_sections_safe(project_id, master)
    if not volumes:
        raise ValueError("PIPELINE_VOLUME_EDITS_EMPTY")
    adjusted = []
    cursor = 0
    for _index, volume in enumerate(volumes):
        start = volume.get("start_offset")
        end = volume.get("end_offset")
        if not isinstance(start, int) or not isinstance(end, int) or start < cursor or end <= start:
            raise ValueError("PIPELINE_VOLUME_EDITS_INVALID_ORDER")
        adjusted.append({"volume": volume, "start": start, "end": end})
        cursor = end
    if cursor != total:
        raise ValueError("PIPELINE_VOLUME_EDITS_INCOMPLETE_COVERAGE")
    for section in sections:
        s_start, s_end = section["start_offset"], section["end_offset"]
        hosts = [v for v in adjusted if v["start"] <= s_start and s_end <= v["end"]]
        if len(hosts) != 1:
            raise ValueError("PIPELINE_VOLUME_EDITS_SPLIT_SECTION:" + section["production_section_id"])
    payload = {
        "source_master": {"document_id": master.id, "sha256": master.sha256},
        "master_char_count": total,
        "volumes": [
            {
                "volume_id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                            f"shine:{project_id}:volume:{index + 1}:{master.sha256}")),
                "order": index + 1,
                "title": str(v["volume"].get("title") or f"第{index + 1}卷")[:80],
                "start_offset": v["start"], "end_offset": v["end"],
                "volume_revision": volume_revision(master.sha256, v["start"], v["end"]),
                "section_ids": [s["production_section_id"] for s in sections
                                if v["start"] <= s["start_offset"] and s["end_offset"] <= v["end"]],
                "char_count": v["end"] - v["start"],
                "boundary_reason": "manual_adjustment",
            }
            for index, v in enumerate(adjusted)
        ],
        "coverage": {"start_offset": 0, "end_offset": total, "complete": True},
        "status": "DRAFT",
    }
    task_id = _authoring_task(db, project_id, "four_stage_import",
                              {"manual_adjustment_of": base_plan_id})
    artifact = AgentArtifact(
        project_id=project_id, kind=VOLUME_PLAN_KIND, schema_version="1.0",
        payload_json=payload, sha256=digest(payload), producer_role="user_edit",
        input_revision_ids=[base.id], source_task_id=task_id,
    )
    db.add(artifact)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_VOLUME_PLAN_ADJUSTED", "agent_artifact",
              artifact.id, {"base_plan": base_plan_id, "volume_count": len(adjusted)},
              project_id=project_id)
    return artifact


EPISODE_SCRIPT_KIND = "FourStageEpisodeScript"


def build_episode_script(db, project_id: str, volume_id: str, title: str,
                         section_ids: list[str]) -> AgentArtifact:
    """One episode's script as an independent, confirmable contract.

    The contract pins the exact confirmed SceneScript revisions it covers
    and records the continuity check across them: each scene's continuity_in
    should answer the previous scene's continuity_out.  A mismatch is an
    advisory issue for the person, never a silent rewrite of either scene.
    """
    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    outline = _volume_outline(db, project_id, volume_id)
    if outline is None:
        raise ValueError("PIPELINE_OUTLINE_NOT_CONFIRMED")
    sections = outline.payload_json.get("production_sections") or []
    by_id = {s["production_section_id"]: s for s in sections}
    if not section_ids or any(sid not in by_id for sid in section_ids):
        raise ValueError("PIPELINE_EPISODE_SECTIONS_INVALID")
    entries = []
    issues = []
    previous_out = None
    for order, sid in enumerate(section_ids, start=1):
        script = current_scene_script(db, project_id, volume_id, sid)
        if script is None:
            raise ValueError("PIPELINE_SCENE_SCRIPT_NOT_CONFIRMED:" + sid)
        payload = script.payload_json
        continuity_in = payload.get("continuity_in", "")
        if previous_out is not None and continuity_in and previous_out:
            if continuity_in.strip() != previous_out.strip():
                issues.append({
                    "after_section_id": sid,
                    "code": "CONTINUITY_IN_MISMATCH",
                    "expected_from_previous_out": previous_out,
                    "actual_continuity_in": continuity_in,
                })
        entries.append({
            "order": order,
            "section_id": sid,
            "scene_key": payload.get("scene_key", sid),
            "script_artifact_id": script.id,
            "script_sha256": script.sha256,
            "text": payload.get("text", ""),
            "estimated_seconds": payload.get("estimated_seconds", 0),
        })
        previous_out = payload.get("continuity_out", "")
    episode_payload = {
        "schema_version": "1.0",
        "volume_id": volume_id,
        "title": title[:120],
        "outline_sha256": outline.sha256,
        "sections": entries,
        "planned_seconds": sum(e["estimated_seconds"] or 0 for e in entries),
        "continuity_issues": issues,
        "continuity_status": "verified" if not issues else "needs_review",
    }
    task_id = _authoring_task(db, project_id, "four_stage_episode_script",
                              {"volume": volume_id, "sections": len(entries)})
    artifact = AgentArtifact(
        project_id=project_id, kind=EPISODE_SCRIPT_KIND, schema_version="1.0",
        payload_json=episode_payload, sha256=digest(episode_payload),
        producer_role="four_stage_episode_script", source_task_id=task_id,
        input_revision_ids=[outline.id] + [e["script_artifact_id"] for e in entries],
    )
    db.add(artifact)
    db.flush()
    add_audit(db, "local-user", "PIPELINE_EPISODE_SCRIPT_DRAFTED", "agent_artifact",
              artifact.id, {"volume_id": volume_id, "sections": len(entries),
                            "continuity": episode_payload["continuity_status"]},
              project_id=project_id)
    return artifact


def confirm_episode_script(db, project_id: str, artifact_id: str, expected_sha256: str) -> AgentArtifact:
    """A person confirms one episode's script contract; the pointer names it."""
    from shine.models import AppSetting

    artifact = db.get(AgentArtifact, artifact_id)
    if not artifact or artifact.project_id != project_id or artifact.kind != EPISODE_SCRIPT_KIND:
        raise ValueError("PIPELINE_EPISODE_SCRIPT_NOT_FOUND")
    if artifact.sha256 != expected_sha256:
        raise ValueError("PIPELINE_EPISODE_SCRIPT_SHA_MISMATCH")
    volume_id = str(artifact.payload_json.get("volume_id"))
    key = _pointer_key(project_id, f"episode-script:{volume_id}:{artifact_id}")
    row = AppSetting(key=key, value={"artifact_id": artifact.id, "sha256": artifact.sha256})
    db.merge(row)
    artifact.approved_by = "local-user"
    artifact.approved_at = utcnow()
    db.flush()
    add_audit(db, "local-user", "PIPELINE_EPISODE_SCRIPT_CONFIRMED", "agent_artifact",
              artifact.id, {"sha256": artifact.sha256,
                            "continuity": artifact.payload_json.get("continuity_status")},
              project_id=project_id)
    return artifact


def episode_contract_currency(db, project_id: str, volume_id: str, contract) -> str:
    """Why a confirmed episode contract may no longer be used, or an empty string.

    A contract pins the outline and the exact scene script revisions it was
    built from.  When either moved on, the contract is history: the person
    has to confirm a rebuilt contract before the volume may be delivered as
    episodes.  The reason is named, never silently ignored.
    """
    outline = _volume_outline(db, project_id, volume_id)
    if outline is None:
        return "PIPELINE_EPISODE_OUTLINE_MISSING"
    if contract.payload_json.get("outline_sha256") != outline.sha256:
        return "PIPELINE_EPISODE_OUTLINE_SUPERSEDED"
    for entry in contract.payload_json.get("sections") or []:
        section_id = str(entry.get("section_id") or "")
        script = current_scene_script(db, project_id, volume_id, section_id)
        if script is None:
            return "PIPELINE_EPISODE_SCENE_SCRIPT_MISSING:" + section_id
        if entry.get("script_sha256") != script.sha256:
            return "PIPELINE_EPISODE_SCRIPT_SUPERSEDED:" + section_id
    return ""


def _episode_contracts(db, project_id: str, volume_id: str) -> list:
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == EPISODE_SCRIPT_KIND).order_by(AgentArtifact.created_at)).all()
    return [r for r in rows if r.approved_at is not None and not r.stale
            and r.payload_json.get("volume_id") == volume_id]


def confirmed_episode_scripts(db, project_id: str, volume_id: str) -> list:
    """The current confirmed EpisodeScript contracts of one volume.

    A confirmed contract whose outline or scene scripts have moved on is
    history, not a basis: it is left out here and reported by
    stale_episode_scripts(), so the formal multi-episode path can only be
    built from contracts the person confirmed against the current work.
    """
    return [row for row in _episode_contracts(db, project_id, volume_id)
            if not episode_contract_currency(db, project_id, volume_id, row)]


def stale_episode_scripts(db, project_id: str, volume_id: str) -> list[dict]:
    """The confirmed episode contracts that no longer match the current work."""
    out = []
    for row in _episode_contracts(db, project_id, volume_id):
        reason = episode_contract_currency(db, project_id, volume_id, row)
        if reason:
            out.append({"artifact_id": row.id, "sha256": row.sha256, "reason": reason})
    return out


def episode_contract_problems(db, project_id: str, volume_id: str,
                              contracts: list, sections: list[dict]) -> list[str]:
    """The named defects of a set of episode contracts against the volume.

    Every problem is reported by name: a missing episode, a section two
    episodes both claim, a section the volume does not have, and an episode
    order that is not a contiguous run of the script.
    """
    problems: list[str] = []
    section_order = {str(section["production_section_id"]): index
                     for index, section in enumerate(sections)}
    contracted: list[str] = []
    for contract in contracts:
        contracted.extend(str(entry.get("section_id") or "")
                          for entry in contract.payload_json.get("sections") or [])
    duplicates = sorted({sid for sid in contracted if contracted.count(sid) > 1})
    if duplicates:
        problems.append("PIPELINE_EPISODE_SECTION_OVERLAP:" + ",".join(duplicates)[:200])
    unknown = sorted({sid for sid in contracted if sid not in section_order})
    if unknown:
        problems.append("PIPELINE_EPISODE_SECTION_UNKNOWN:" + ",".join(unknown)[:200])
    missing = sorted(set(section_order) - set(contracted))
    if missing:
        problems.append("PIPELINE_EPISODE_COVERAGE_INCOMPLETE:" + ",".join(missing)[:200])
    known = [sid for sid in dict.fromkeys(contracted) if sid in section_order]
    positions = sorted(section_order[sid] for sid in known)
    if positions and positions != list(range(positions[0], positions[0] + len(positions))):
        problems.append("PIPELINE_EPISODE_ORDER_NOT_CONTIGUOUS")
    return problems


STAGE_LABELS = {
    "DRAFT": "排队等待", "SENDING": "调用模型中", "WAITING_RESOURCE": "等待资源",
    "PREPARING_PROVIDER": "准备供应商", "WAITING_PROVIDER": "等待模型返回",
    "RESPONSE": "校验返回", "DOCUMENT": "落库中", "NEEDS_REVIEW": "待你核对",
}


def latest_bible_artifact(db, project_id: str) -> AgentArtifact | None:
    """The newest bible artifact, confirmed or draft (with its status)."""
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == BIBLE_KIND).order_by(AgentArtifact.created_at.desc())).all()
    return rows[0] if rows else None


def pipeline_task_summary(db, project_id: str) -> list[dict]:
    """The live progress of the pipeline's own tasks, for the UI progress bar.

    The outline run's cursor over source units is real progress; the bible
    and scene-script stages report their durable phase.  A blocked task
    carries the consent document identity so the panel can offer the exact
    recovery action instead of a dead end.
    """
    from shine.models import AgentTaskRecord

    roles = ["story_outline_run", BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE]
    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id.in_(roles)).order_by(
        AgentTaskRecord.created_at.desc())).all()
    summary = []
    seen = set()
    for row in rows:
        if row.role_id in seen:
            continue
        seen.add(row.role_id)
        trace = row.trace or {}
        done, total = 0, 0
        if row.role_id == "story_outline_run":
            units = trace.get("units") or trace.get("production_sections") or []
            total = len(units) if isinstance(units, list) else 0
            done = int(trace.get("cursor") or 0)
        elif row.status == "SUCCEEDED":
            # A role without a unit list still has a real end: report it as
            # one whole step so the progress bar is determinate.
            done, total = 1, 1
        stage = trace.get("stage") or trace.get("phase") or ""
        # The stage a task *reached* is not the state it is *in*.  A finished
        # task must not keep reading as if it were still running.
        if row.status == "SUCCEEDED":
            stage_label = "已完成"
        elif row.status in ("NEEDS_REVIEW", "FAILED", "PAUSED"):
            stage_label = "待你核对"
        else:
            stage_label = STAGE_LABELS.get(stage, stage or "—")
        summary.append({
            "task_id": row.id,
            "role": row.role_id,
            "state_version": row.state_version,
            "status": row.status,
            "stage": stage,
            "stage_label": stage_label,
            "progress_done": done,
            "progress_total": total,
            "error_code": row.error_code,
            "attempt": trace.get("attempt", 1),
            "external_outcome": trace.get("external_outcome"),
            "blocked_reason": trace.get("blocked_reason"),
            "available_actions": task_available_actions(row),
            "reconciliation": (row.trace or {}).get("reconciliation"),
            "superseded_by_task_id": (row.trace or {}).get("superseded_by_task_id"),
            "updated_at": (row.lease_expires_at.isoformat() if row.lease_expires_at else None),
            "consent_document_id": trace.get("document_id") or trace.get("master_document_id"),
            "consent_document_sha256": trace.get("document_sha256") or trace.get("master_document_sha256"),
            "consent_role": row.role_id if row.role_id != "story_outline_run" else "scriptwriter",
        })
    return summary


def task_available_actions(row) -> list[str]:
    """The operations the server allows on a pipeline task, decided here only.

    The UI reads this list instead of guessing from an error string: an
    unknown outcome offers reconciliation (and an explicitly authorised new
    attempt), a provably unsent request offers a plain resume.
    """
    trace = row.trace or {}
    if row.status == "SUCCEEDED":
        return []
    if trace.get("superseded_by_task_id"):
        # The authorisation was already used: this row is history and it
        # offers nothing further.
        return []
    reconciliation = trace.get("reconciliation") or {}
    if reconciliation.get("outcome") == "NOT_SENT_PROVEN":
        # Only a proven "not sent" may be continued as an ordinary resume.
        return ["resume"]
    unknown = _send_outcome_unknown(trace)
    if unknown:
        return ["reconcile", "new_attempt"]
    if row.status in ("NEEDS_REVIEW", "PAUSED", "FAILED"):
        return ["resume"]
    return []


def _next_attempt_number(db, project_id: str, role: str, base_identity: str) -> int:
    return _attempt_count(db, project_id, role, base_identity) + 1


def resume_pipeline_task(db, project_id: str, task_id: str, expected_state_version: int) -> dict:
    """A person re-queues a blocked pipeline task after fixing its cause.

    The fence matches the task's version so a stale view cannot resurrect a
    task another action already moved.  Recovery never touches the unknown
    ledger: whenever the durable evidence cannot rule out that the provider
    answered, the task is refused here - whatever error code it carries -
    because a plain resume would buy the same call twice and discard one
    answer.  Such a task needs ``start_pipeline_new_attempt`` with its own
    authorization and attempt identity.
    """
    from sqlalchemy import update

    from shine.models import AgentTaskRecord

    row = db.get(AgentTaskRecord, task_id)
    if row is None or row.project_id != project_id or row.role_id not in {
            "story_outline_run", BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE}:
        raise ValueError("PIPELINE_TASK_NOT_FOUND")
    if _send_outcome_unknown(row.trace or {}):
        raise ValueError("PIPELINE_TASK_OUTCOME_UNKNOWN_RECONCILE_REQUIRED")
    changed = db.execute(update(AgentTaskRecord).where(
        AgentTaskRecord.id == task_id,
        AgentTaskRecord.state_version == expected_state_version,
        AgentTaskRecord.status.in_(["NEEDS_REVIEW", "PAUSED", "FAILED"]),
    ).values(status="QUEUED", error_code=None,
             state_version=AgentTaskRecord.state_version + 1)).rowcount
    if not changed:
        raise ValueError("PIPELINE_TASK_STATE_CHANGED")
    add_audit(db, "local-user", "PIPELINE_TASK_RESUMED", "agent_task", task_id,
              {"expected_state_version": expected_state_version}, project_id=project_id)
    return {"task_id": task_id, "status": "QUEUED"}


def _new_attempt_receipt(db, row, authorization: dict) -> dict | None:
    """The receipt of an authorisation that was already used, if there is one."""
    from shine.models import AgentTaskRecord

    new_task_id = authorization.get("new_task_id")
    if not new_task_id:
        identity = authorization.get("identity")
        if not identity:
            return None
        existing = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.request_identity == identity)).first()
        if existing is None:
            return None
        new_task_id = existing.id
    return {"task_id": new_task_id, "attempt": authorization.get("attempt"),
            "status": "QUEUED", "supersedes": row.id, "reused": True}


def reconcile_pipeline_task(db, project_id: str, task_id: str,
                            expected_state_version: int,
                            actor: str = "local-user") -> dict:
    """The role-matched reconciliation of one four-stage task.

    It reads the durable external state, never the error text: a task whose
    send outcome is unknown stays unknown, so only a proven "not sent" may
    be continued as an ordinary resume.  The conclusion is written to its
    own audit and to its own trace key, so it never overwrites the late
    answer that may still arrive.
    """
    from shine.models import AgentTaskRecord

    row = db.get(AgentTaskRecord, task_id)
    if row is None or row.project_id != project_id or row.role_id not in {
            "story_outline_run", BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE}:
        raise ValueError("PIPELINE_TASK_NOT_FOUND")
    if row.state_version != expected_state_version:
        raise ValueError("PIPELINE_TASK_STATE_CHANGED")
    trace = dict(row.trace or {})
    decided = trace.get("reconciliation") or {}
    if decided.get("outcome"):
        # Reconciling twice must never change a conclusion.
        return {"outcome": decided["outcome"], "task_id": row.id,
                "state_version": row.state_version,
                "resubmittable": bool(decided.get("resubmittable")),
                "note": "这一项已经核对过，结论不变。"}
    if _send_outcome_unknown(trace):
        outcome, resubmittable = "UNKNOWN_REQUIRES_NEW_ATTEMPT", False
        note = "无法确认上一次是否已经发出；只有「核对后重新发送一次」可以继续这一项。"
    elif trace.get("external_outcome") == "NOT_SENT":
        outcome, resubmittable = "NOT_SENT_PROVEN", True
        note = "已确认上一次没有发出，可以继续这一项。"
    elif trace.get("external_outcome") == "RESPONSE_RECEIVED":
        outcome, resubmittable = "RESPONSE_RECEIVED_NO_ANSWER", True
        note = "服务商已经答复但没有可用结果；重新提交会创建一次新的模型请求。"
    else:
        outcome, resubmittable = "UNCONFIRMABLE", False
        note = "这一项的状态无法确认，请查看任务详情。"
    row.trace = {**trace, "reconciliation": {
        "outcome": outcome, "resubmittable": resubmittable,
        "checked_at": utcnow().isoformat()}}
    row.state_version += 1
    add_audit(db, actor, "PIPELINE_TASK_RECONCILED", "agent_task", row.id,
              {"outcome": outcome, "resubmittable": resubmittable, "external_calls": 0},
              project_id=project_id)
    return {"outcome": outcome, "task_id": row.id, "state_version": row.state_version,
            "resubmittable": resubmittable, "note": note}


def start_pipeline_new_attempt(db, project_id: str, task_id: str,
                               expected_state_version: int, actor: str = "local-user") -> dict:
    """An explicitly authorised, physically separate retry of an unknown call.

    The original row stays exactly as it is - its ``UNKNOWN`` outcome remains
    the truth about that attempt, together with the cost already paid.  The
    new attempt is a different task with a different request identity, so no
    queue, claim or dedupe rule can ever confuse the two.

    One authorisation buys one attempt.  The authorisation is consumed by the
    very request that uses it: the old row's state version is advanced and it
    is marked superseded in the same transaction that creates the new task.
    A repeated or concurrent call with the same version therefore returns the
    same receipt instead of buying a second provider call.
    """
    from sqlalchemy import update

    from shine.models import AgentTaskRecord

    row = db.get(AgentTaskRecord, task_id)
    if row is None or row.project_id != project_id or row.role_id not in {
            BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE}:
        raise ValueError("PIPELINE_TASK_NOT_FOUND")
    trace = dict(row.trace or {})
    authorization = trace.get("new_attempt_authorization")
    if authorization is not None:
        if authorization.get("expected_state_version") != expected_state_version:
            raise ValueError("PIPELINE_TASK_STATE_CHANGED")
        receipt = _new_attempt_receipt(db, row, authorization)
        if receipt is not None:
            # The same authorisation, already used: the same receipt.
            return receipt
        attempt = int(authorization.get("attempt") or 1)
        identity = str(authorization.get("identity") or "")
        base = str(authorization.get("base_identity") or row.input_hash)
        fence_version = row.state_version
    else:
        if row.state_version != expected_state_version:
            raise ValueError("PIPELINE_TASK_STATE_CHANGED")
        if not _send_outcome_unknown(trace):
            raise ValueError("PIPELINE_TASK_NOT_UNKNOWN")
        if row.status not in {"NEEDS_REVIEW", "FAILED", "PAUSED"}:
            # A task still RUNNING may really be in flight; a succeeded one has its
            # answer.  Neither may start a parallel second attempt.
            raise ValueError("PIPELINE_TASK_STATE_CHANGED")
        base = trace.get("base_identity") or row.input_hash
        if not base:
            raise ValueError("PIPELINE_TASK_IDENTITY_MISSING")
        attempt = _next_attempt_number(db, project_id, row.role_id, base)
        identity = attempt_identity(base, attempt)
        fence_version = expected_state_version
    reserved = {
        "expected_state_version": expected_state_version,
        "attempt": attempt,
        "identity": identity,
        "base_identity": base,
        "authorized_by": actor,
        "new_task_id": None,
    }
    fenced_trace = {
        **trace,
        "superseded_by_task_id": None,
        "new_attempt_authorization": reserved,
    }
    # The fence consumes the authorisation: only the request that holds the
    # expected version may create the attempt, and only once.
    changed = db.execute(update(AgentTaskRecord).where(
        AgentTaskRecord.id == task_id,
        AgentTaskRecord.state_version == fence_version,
        AgentTaskRecord.status.in_(["NEEDS_REVIEW", "FAILED", "PAUSED"]),
    ).values(state_version=AgentTaskRecord.state_version + 1,
             trace=fenced_trace)).rowcount
    if not changed:
        raise ValueError("PIPELINE_TASK_STATE_CHANGED")
    rebuilt = {key: value for key, value in trace.items() if key not in _ATTEMPT_RESULT_KEYS}
    rebuilt.pop("external_outcome", None)
    new_trace = {
        **rebuilt,
        "stage": "DRAFT",
        "external_outcome": None,
        "attempt": attempt,
        "base_identity": base,
        "retry_of_task_id": row.id,
        "prior_external_outcome": trace.get("external_outcome") or "UNKNOWN",
        "authorized_by": actor,
        "authorization": "explicit_new_attempt_after_unknown",
        "automatic_replay": False,
    }
    new_row = AgentTaskRecord(
        project_id=project_id,
        role_id=row.role_id,
        request_identity=identity,
        input_hash=base,
        asset_manifest_hash=row.asset_manifest_hash,
        rulepack_id=row.rulepack_id,
        rulepack_hash=row.rulepack_hash,
        model_profile_id=row.model_profile_id,
        model_id=row.model_id,
        schema_version=row.schema_version,
        status="QUEUED",
        trace=new_trace,
    )
    db.add(new_row)
    db.flush()
    db.execute(update(AgentTaskRecord).where(
        AgentTaskRecord.id == task_id,
        AgentTaskRecord.state_version == fence_version + 1,
    ).values(trace={
        **fenced_trace,
        "superseded_by_task_id": new_row.id,
        "new_attempt_authorization": {**reserved, "new_task_id": new_row.id},
    }))
    add_audit(db, actor, "PIPELINE_TASK_NEW_ATTEMPT", "agent_task", new_row.id,
              {"prior_task_id": row.id, "attempt": attempt,
               "prior_external_outcome": new_trace["prior_external_outcome"]},
              project_id=project_id)
    return {"task_id": new_row.id, "attempt": attempt, "status": "QUEUED",
            "supersedes": row.id, "reused": False}
