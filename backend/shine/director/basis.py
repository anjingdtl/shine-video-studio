"""One resolver for the current production basis.

Three entries produce an episode plan - grouping the shots a project already
has, materialising the script's sections into shots, and the four-stage volume
delivery - and all three must answer the same question with the same
evidence: *which script is this production about?*

The resolver reads three facts and nothing else:

* the document the person explicitly selected (or the newest one, for a
  project that never made the choice);
* the confirmed outline built from that document;
* the outline the project's existing shots really came from, read from the
  storyboard apply receipts and the section runs that produced them.

A plan that cannot name its own script never enters production silently: it
either bridges on trustworthy evidence, or it names the confirm / rebuild
action that already exists.  Nothing here downloads, renders or approves.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.models import AgentArtifact, Project, ProjectDocument

#: The task role the script section runner records under.
SECTION_RUN_ROLE = "story_section_run"

#: The artifact kind that carries a script outline.
OUTLINE_KIND = "StoryOutline"


def _blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def current_script_basis(db: Session, project: Project) -> dict[str, Any]:
    """The document the production basis is anchored to.

    An explicit choice wins.  A project that never made one keeps the legacy
    rule (the newest document) so packages confirmed before this identity
    existed stay readable and executable.

    Uploading a second script therefore only adds a *candidate*: it does not
    change the current basis by itself.  Selecting the document, or confirming
    an outline built from it, is the explicit action that switches it.
    """
    document = None
    explicit = False
    if project.current_document_id:
        candidate = db.get(ProjectDocument, project.current_document_id)
        if candidate is not None and candidate.project_id == project.id:
            document, explicit = candidate, True
    if document is None:
        document = db.scalars(
            select(ProjectDocument)
            .where(ProjectDocument.project_id == project.id)
            .order_by(ProjectDocument.created_at.desc())
        ).first()
    return {"document": document, "explicit": explicit}


def outline_for_document(db: Session, project_id: str, document) -> AgentArtifact | None:
    """The newest readable outline that was really built from this document.

    An outline written before provenance was recorded has no document_id.
    It cannot *claim* to belong to another script, so it stays usable as the
    legacy fallback; an outline that names a different document never is.
    """
    rows = db.scalars(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == OUTLINE_KIND,
            AgentArtifact.stale.is_(False),
        )
        .order_by(AgentArtifact.created_at.desc())
    ).all()
    legacy: AgentArtifact | None = None
    for row in rows:
        payload = row.payload_json or {}
        declared = payload.get("document_id")
        if document is not None and declared == document.id:
            return row
        if not declared and legacy is None:
            legacy = row
    return legacy if document is not None else None


def confirmed_outline(db: Session, project: Project) -> AgentArtifact | None:
    """The confirmed outline of the current basis, when the project has one."""
    basis = current_script_basis(db, project)
    document = basis["document"]
    if document is None:
        return None
    outline = outline_for_document(db, project.id, document)
    if outline is None or not outline.approved_at:
        return None
    return outline


def _section_ids(outline: AgentArtifact) -> set[str]:
    payload = outline.payload_json or {}
    return {
        str(row.get("production_section_id"))
        for row in (payload.get("production_sections") or [])
        if row.get("production_section_id")
    }


def shot_script_evidence(db: Session, project_id: str,
                         shot_ids: Iterable[str]) -> dict[str, Any]:
    """Which outline each shot really came from.

    The evidence is the product's own ledger: a section run names the script
    section it was planned from, and the storyboard apply receipt names the
    shots that run really produced.  A shot without such a receipt has no
    script lineage - it was written by hand - and is reported as unknown
    rather than guessed.  A shot two different outlines claim is ambiguous
    and is reported as such: it is never attributed to the one that happens
    to be confirmed now.
    """
    wanted = {str(shot_id) for shot_id in shot_ids}
    result: dict[str, Any] = {"outlines": {}, "ambiguous": [], "unknown": []}
    if not wanted:
        return result

    from shine.models import AgentTaskRecord, AppSetting

    runs = db.scalars(
        select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project_id,
            AgentTaskRecord.role_id == SECTION_RUN_ROLE,
        )
    ).all()
    if not runs:
        result["unknown"] = sorted(wanted)
        return result

    outlines = db.scalars(
        select(AgentArtifact).where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == OUTLINE_KIND,
            AgentArtifact.stale.is_(False),
        )
    ).all()
    by_section: dict[str, set[str]] = {}
    for outline in outlines:
        for section_id in _section_ids(outline):
            by_section.setdefault(section_id, set()).add(outline.id)

    claimed: dict[str, str] = {}
    ambiguous: set[str] = set()
    for run in runs:
        trace = run.trace or {}
        explicit = (trace.get("outline_artifact_id")
                    or (trace.get("production_context") or {}).get("outline_artifact_id"))
        outline_ids: set[str] = set()
        if explicit:
            outline_ids = {str(explicit)}
        else:
            for unit in (trace.get("units") or []):
                section_id = str(unit.get("production_section_id") or "")
                if section_id:
                    outline_ids |= by_section.get(section_id, set())
        if len(outline_ids) != 1:
            # No evidence, or several revisions claim this run: the shots
            # stay unattributed rather than attributed to a guess.
            continue
        outline_id = next(iter(outline_ids))
        for result_row in (trace.get("results") or []):
            camera = (((result_row or {}).get("storyboard") or {})
                      .get("steps", {}).get("cinematographer") or {})
            artifact_id = camera.get("artifact_id")
            if not artifact_id:
                continue
            receipt = db.get(AppSetting, f"film-apply:{artifact_id}")
            if receipt is None:
                continue
            for shot_id in (receipt.value or {}).get("shot_ids") or []:
                shot_id = str(shot_id)
                if shot_id not in wanted:
                    continue
                previous = claimed.get(shot_id)
                if previous is not None and previous != outline_id:
                    ambiguous.add(shot_id)
                claimed[shot_id] = outline_id

    for shot_id in sorted(wanted):
        if shot_id in ambiguous:
            result["ambiguous"].append(shot_id)
        elif shot_id in claimed:
            result["outlines"][shot_id] = claimed[shot_id]
        else:
            result["unknown"].append(shot_id)
    return result


def shots_plan_source(db: Session, project: Project,
                      shot_ids: Iterable[str]) -> dict[str, Any]:
    """The basis a shot-grouping plan really has.

    The compatible entry groups the shots a project already has.  What it
    must never do is *claim* the script that happens to be confirmed now: the
    plan records the outline its shots really came from, so a re-grouping of
    the old script's shots under a new script is visible instead of silent.

    Returns the source the plan records plus the evidence behind it.
    """
    ids = [str(shot_id) for shot_id in shot_ids]
    basis = current_script_basis(db, project)
    document = basis["document"]
    if document is None:
        # A hand-built shot list has no document lineage to be wrong about.
        return {"source": {},
                "evidence": {"outlines": {}, "ambiguous": [], "unknown": list(ids),
                             "basis": "no_script"}}
    evidence = shot_script_evidence(db, project.id, ids)
    proven = set(evidence["outlines"].values())
    source: dict[str, Any] = {}
    if evidence["ambiguous"] or len(proven) > 1:
        source["basis"] = "ambiguous_shot_sources"
        source["outline_artifact_ids"] = sorted(proven)
    elif proven:
        outline_id = next(iter(proven))
        outline = db.get(AgentArtifact, outline_id)
        source = {
            "basis": "shot_apply_receipts",
            "outline_artifact_id": outline_id,
            "outline_sha256": outline.sha256 if outline is not None else None,
        }
        if outline is not None:
            payload = outline.payload_json or {}
            if payload.get("document_id"):
                source["document_id"] = payload.get("document_id")
                source["document_sha256"] = payload.get("document_sha256")
    else:
        source["basis"] = "unattributed_shots"
        source["unattributed_shot_count"] = len(evidence["unknown"])
    return {"source": source, "evidence": evidence}


def plan_basis_blockers(db: Session, project: Project, plan_payload) -> list[dict[str, Any]]:
    """Is this episode plan still the plan of the *current* script?

    An uploaded script B is only a candidate until the person selects it or
    confirms an outline built from it.  Once that happens, a plan derived from
    A can no longer be packaged as B: the manifest would name B's document and
    outline while carrying A's episodes and shots (J06).

    The same rule covers the compatible entry that groups the shots a project
    already has (R01).  That entry used to record no source at all, so a
    re-grouping of A's shots under script B passed unnoticed.  It now records
    the outline its shots really came from, and a legacy row without that
    record bridges on the apply receipts when they still prove the claim.
    """
    source = getattr(plan_payload, "source", None) or {}
    mode = getattr(plan_payload, "mode", "shots")
    basis = current_script_basis(db, project)
    document = basis["document"]
    if document is None:
        # A hand-built shot list has no document lineage to be wrong about.
        return []
    outline = outline_for_document(db, project.id, document)
    confirmed = outline if outline is not None and outline.approved_at else None
    if confirmed is None:
        return [_blocker(
            "SCRIPT_OUTLINE_NOT_CONFIRMED",
            "当前剧本还没有确认的大纲；先整理并确认大纲，再生成分集和导演包",
            document_id=document.id,
            outline_artifact_id=outline.id if outline else None,
            action="CONFIRM_OUTLINE",
        )]
    declared_id = source.get("outline_artifact_id")
    declared_sha = source.get("outline_sha256")
    if not declared_id and mode == "sections":
        return [_blocker(
            "EPISODE_PLAN_SOURCE_MISSING",
            "这份分集方案没有记录它来自哪一版大纲，无法确认它属于当前剧本",
            current_outline_artifact_id=confirmed.id,
            current_outline_sha256=confirmed.sha256,
            action="REMATERIALIZE_PLAN",
        )]
    if declared_id and (declared_id != confirmed.id
                        or (declared_sha and declared_sha != confirmed.sha256)):
        return [_blocker(
            "EPISODE_PLAN_SCRIPT_CHANGED",
            "分集方案来自另一版剧本大纲；请按当前剧本重新生成分集，再落到镜头和导演包",
            plan_outline_artifact_id=declared_id,
            current_outline_artifact_id=confirmed.id,
            current_document_id=document.id,
            action="REMATERIALIZE_PLAN",
        )]
    if declared_id:
        return []
    # No declared outline: the compatible entry that groups existing shots.
    # Its lineage is proven by the apply receipts, not by the plan row.
    evidence = shot_script_evidence(db, project.id, plan_payload.shot_order())
    proven = set(evidence["outlines"].values())
    if evidence["ambiguous"] or len(proven) > 1:
        return [_blocker(
            "EPISODE_PLAN_SCRIPT_AMBIGUOUS",
            "这些镜头被不止一版剧本大纲认领，无法确认这份分集方案属于当前剧本；"
            "请按当前剧本重新拆分镜后再分集",
            current_outline_artifact_id=confirmed.id,
            current_document_id=document.id,
            action="REMATERIALIZE_PLAN",
        )]
    if proven:
        proven_id = next(iter(proven))
        if proven_id != confirmed.id:
            return [_blocker(
                "EPISODE_PLAN_SCRIPT_CHANGED",
                "分集方案里的镜头来自另一版剧本大纲；请按当前剧本重新拆分镜并分集，"
                "再落到导演包",
                plan_outline_artifact_id=proven_id,
                current_outline_artifact_id=confirmed.id,
                current_document_id=document.id,
                action="REMATERIALIZE_PLAN",
            )]
    return []


def plan_basis_pointer(db: Session, project: Project, plan_payload) -> dict[str, Any] | None:
    """A pointer, not a wall: this plan's shots have no script lineage.

    A hand-built shot list is a legitimate product of the compatible entry, so
    it is not refused here.  It is only *named*: the journey shows that the
    shots cannot be traced to the current script and points at the confirm /
    rebuild actions that already exist.
    """
    source = getattr(plan_payload, "source", None) or {}
    if source.get("outline_artifact_id"):
        return None
    basis = current_script_basis(db, project)
    document = basis["document"]
    if document is None:
        return None
    outline = outline_for_document(db, project.id, document)
    confirmed = outline if outline is not None and outline.approved_at else None
    if confirmed is None:
        return None
    evidence = shot_script_evidence(db, project.id, plan_payload.shot_order())
    if evidence["outlines"] or evidence["ambiguous"]:
        return None
    return _blocker(
        "EPISODE_PLAN_SCRIPT_UNATTRIBUTED",
        "这份分集方案的镜头没有剧本来源记录，无法确认它属于当前剧本；"
        "请确认大纲，或按当前剧本重新拆分镜后再分集",
        current_outline_artifact_id=confirmed.id,
        current_document_id=document.id,
        unattributed_shot_count=len(evidence["unknown"]),
        action="CONFIRM_OUTLINE",
    )
