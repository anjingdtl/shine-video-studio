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

#: One scene's script text limit.  The writer contract and the person's editor
#: share this number, so an edit is refused by name instead of being truncated
#: into a scene that silently lost its ending.
SCENE_SCRIPT_TEXT_LIMIT = 24_000

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
            "volume_revision": volume.get("volume_revision"),
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


def run_bible_task(factory, settings, gateway, task_id: str, version: int, worker_id: str) -> bool:
    """Execute one bible synthesis task under the fenced claim."""
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
                "开放问题与结局方向。锁定事实（locked_facts）与结局方向（ending_direction）必须用英文写，"
                "其余字段用中文。只输出 JSON。"
            ),
            context=context,
            schema=schema,
            contract=GlobalStoryBiblePayload,
            task_category="creative",
            before_call=text_consent.guard(project_id, trace["document_id"], trace["document_sha256"],
                                           BIBLE_RUN_ROLE, trace["provider_fingerprint"], gateway),
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
        _commit_pipeline_result(
            factory, task_id=task_id, version=version, worker_id=worker_id,
            project_id=project_id, kind=BIBLE_KIND, role=BIBLE_RUN_ROLE, payload=payload,
            input_revision_ids=trace.get("input_revision_ids")
            or [ref["id"] for ref in trace["outline_refs"]],
            trace={**trace, "usage": outcome.details.get("usage"),
                   "output_budget": outcome.details.get("output_budget")})
        return True


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
    candidates = []
    for row in db.scalars(select(ProjectDocument).where(
            ProjectDocument.project_id == project_id,
            ProjectDocument.source_format == "volume")).all():
        parsed = row.parsed if isinstance(row.parsed, dict) else {}
        if parsed.get("kind") != "four_stage_volume" or parsed.get("volume_id") != volume_id:
            continue
        if planned is not None:
            if (parsed.get("master_document_id") != master.get("document_id")
                    or parsed.get("master_sha256") != master.get("sha256")
                    or parsed.get("start_offset") != planned.get("start_offset")
                    or parsed.get("end_offset") != planned.get("end_offset")):
                continue
        candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row.created_at, row.id))


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
        return {
            "mainline": str(payload.get("mainline") or "").strip(),
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


def run_scene_script_task(factory, settings, gateway, task_id: str, version: int, worker_id: str) -> bool:
    """Execute one scene-script task under the fenced claim."""
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
            before_call=text_consent.guard(
                project_id, trace["master_document_id"], trace["master_document_sha256"],
                SCENE_SCRIPT_RUN_ROLE, trace["provider_fingerprint"], gateway),
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
                                  "outline_sha256": trace["outline_sha256"],
                                  "bible_sha256": trace["bible_sha256"]}
        _commit_pipeline_result(
            factory, task_id=task_id, version=version, worker_id=worker_id,
            project_id=project_id, kind=SCENE_SCRIPT_KIND, role=SCENE_SCRIPT_RUN_ROLE,
            payload=payload,
            input_revision_ids=[trace["bible_artifact_id"], trace["outline_artifact_id"]],
            trace={**trace, "usage": outcome.details.get("usage"),
                   "output_budget": outcome.details.get("output_budget")})
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


def latest_unconfirmed_by_section(db, project_id: str, section_id: str) -> AgentArtifact | None:
    """The newest unconfirmed SceneScript of one section."""
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == SCENE_SCRIPT_KIND).order_by(AgentArtifact.created_at.desc())).all()
    return next((r for r in rows if not r.approved_at
                 and r.payload_json.get("section_id") == section_id), None)


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
                "script_sha256": script.sha256,
                "continuity_in": script.payload_json.get("continuity_in", ""),
                "continuity_out": script.payload_json.get("continuity_out", ""),
                "text": chunk,
                "char_count": len(chunk),
            })
    return units


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
            rejections.append({"unit_id": unit["unit_id"], "section_id": unit["section_id"],
                               "code": code, "external_outcome": "NOT_SENT"})
            incomplete = True
            if code.split(":")[0] in GLOBAL_STORYBOARD_STOPS:
                break
            continue
        apply_info = (result.get("next") or {}).get("apply_camera") or {}
        steps = result.get("steps") or {}
        receipt = {
            "unit_id": unit["unit_id"], "section_id": unit["section_id"],
            "scene_key": unit["scene_key"], "order": unit["order"], "title": unit["title"],
            "chunk_index": unit["chunk_index"], "chunk_count": unit["chunk_count"],
            "script_sha256": unit["script_sha256"], "char_count": unit["char_count"],
            "status": result.get("status"),
            "camera_artifact_id": apply_info.get("camera_artifact_id"),
            "camera_sha256": (steps.get("cinematographer") or {}).get("sha256"),
            "continuity_artifact_id": apply_info.get("continuity_artifact_id"),
            "arbitration_artifact_id": apply_info.get("arbitration_artifact_id"),
        }
        receipts.append(receipt)
        if result.get("status") != "PROPOSED":
            incomplete = True
            rejections.extend({**item, "unit_id": unit["unit_id"],
                               "section_id": unit["section_id"]}
                              for item in (result.get("rejections") or []))
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
    first_camera = next((unit for unit in units if unit.get("camera_artifact_id")), {})
    record = {
        "camera_artifact_id": first_camera.get("camera_artifact_id"),
        "camera_sha256": first_camera.get("camera_sha256"),
        "script_sha256": digest(script_text) if script_text else None,
        "status": result.get("status"),
        "units": units,
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


def _section_shot_groups(db, project_id: str, record: dict) -> tuple[list[dict], list[str]]:
    """The ordered scene -> shots mapping the apply receipts actually prove.

    Each unit's camera artifact must still be the artifact that was applied,
    must still belong to this project and must still hash to what the record
    pinned.  A receipt that names no shot, or a shot two units both claim, is
    a coverage defect - never a number to be compared against a count.
    """
    from shine.models import AppSetting
    from shine.models import Shot as _Shot

    groups: list[dict] = []
    problems: list[str] = []
    claimed: set[str] = set()
    units = record.get("units") or []
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


def build_volume_handoff(db, project_id: str, volume_id: str, workflow_profile_id: str,
                         *, acknowledge_scene_coverage: bool = False) -> dict:
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
    from shine.models import Project

    plan = current_volume_plan(db, project_id)
    if plan is None:
        raise ValueError("PIPELINE_VOLUME_PLAN_NOT_CONFIRMED")
    bible = current_bible(db, project_id)
    if bible is None:
        raise ValueError("PIPELINE_BIBLE_NOT_CONFIRMED")
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
    record = _read_pointer(db, f"four-stage-storyboard:{project_id}:{volume_id}")
    if record is None or not (record.get("units") or record.get("camera_artifact_id")):
        raise ValueError("PIPELINE_STORYBOARD_NOT_GENERATED")
    lineage = creative_lineage(db, project_id, volume_id)
    if record.get("script_sha256") and record["script_sha256"] != digest(
            serialize_volume_script(db, project_id, volume_id)):
        raise ValueError("PIPELINE_SCRIPT_CHANGED_SINCE_STORYBOARD")
    groups, problems = _section_shot_groups(db, project_id, record)
    sections = outline.payload_json.get("production_sections") or []
    grouped_sections = {group["section_id"] for group in groups if group["section_id"]}
    for section in sections:
        section_id = section["production_section_id"]
        if section_id not in grouped_sections:
            problems.append("PIPELINE_SCENE_WITHOUT_SHOT:" + section_id)
    if problems:
        raise ValueError(problems[0])
    episode_contracts = confirmed_episode_scripts(db, project_id, volume_id)
    if episode_contracts:
        contracted: list[str] = []
        for contract in episode_contracts:
            contracted.extend(entry["section_id"] for entry in contract.payload_json["sections"])
        volume_sections = {section["production_section_id"] for section in sections}
        if set(contracted) != volume_sections or len(contracted) != len(set(contracted)):
            raise ValueError("PIPELINE_EPISODE_COVERAGE_INCOMPLETE")
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
    handoff_digest = digest({
        "project": project_id, "volume": volume_id,
        "workflow_profile": workflow_profile_id,
        "lineage_sha256": lineage["sha256"],
        "episode_basis": episode_basis,
        "episode_plan_sha256": digest(plan_dump),
        "shot_order": planned,
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
    package = build_package(db, project, workflow_profile_id=workflow_profile_id,
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


def confirmed_episode_scripts(db, project_id: str, volume_id: str) -> list:
    """The confirmed EpisodeScript contracts of one volume, in creation order."""
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == EPISODE_SCRIPT_KIND).order_by(AgentArtifact.created_at)).all()
    return [r for r in rows if r.approved_at is not None
            and r.payload_json.get("volume_id") == volume_id]


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
        stage = trace.get("stage") or trace.get("phase") or ""
        summary.append({
            "task_id": row.id,
            "role": row.role_id,
            "state_version": row.state_version,
            "status": row.status,
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage or "—"),
            "progress_done": done,
            "progress_total": total,
            "error_code": row.error_code,
            "attempt": trace.get("attempt", 1),
            "external_outcome": trace.get("external_outcome"),
            "blocked_reason": trace.get("blocked_reason"),
            "available_actions": task_available_actions(row),
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


def start_pipeline_new_attempt(db, project_id: str, task_id: str,
                               expected_state_version: int, actor: str = "local-user") -> dict:
    """An explicitly authorised, physically separate retry of an unknown call.

    The original row stays exactly as it is - its ``UNKNOWN`` outcome remains
    the truth about that attempt, together with the cost already paid.  The
    new attempt is a different task with a different request identity, so no
    queue, claim or dedupe rule can ever confuse the two.
    """
    from shine.models import AgentTaskRecord

    row = db.get(AgentTaskRecord, task_id)
    if row is None or row.project_id != project_id or row.role_id not in {
            BIBLE_RUN_ROLE, SCENE_SCRIPT_RUN_ROLE}:
        raise ValueError("PIPELINE_TASK_NOT_FOUND")
    if row.state_version != expected_state_version:
        raise ValueError("PIPELINE_TASK_STATE_CHANGED")
    trace = dict(row.trace or {})
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
    row.trace = {**trace, "superseded_by_task_id": new_row.id}
    add_audit(db, actor, "PIPELINE_TASK_NEW_ATTEMPT", "agent_task", new_row.id,
              {"prior_task_id": row.id, "attempt": attempt,
               "prior_external_outcome": new_trace["prior_external_outcome"]},
              project_id=project_id)
    return {"task_id": new_row.id, "attempt": attempt, "status": "QUEUED",
            "supersedes": row.id}
