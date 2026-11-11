"""Candidate drafts: keep the chapter, then decide whether it is accepted.

The V2.1 contract separates two questions that used to be one:

``content_saved``
    a complete, recognisable literary answer is safely stored and can be shown,
    edited and reviewed;
``accepted``
    the answer satisfies the stage's required source, structure, continuity and
    approval gates and may become authoritative input for the next stage.

A candidate draft answers only the first question.  It is stored as its own
artifact kind (``LiteraryCandidateDraft``) so no existing adoption, pointer or
confirmation path can mistake it for a finished stage result: the pipeline's
``current_scene_script`` reads a confirmed pointer, and the orphan-adoption
guard matches the expected kind exactly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from shine.h3.rulebook import digest
from shine.literary import triage
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from shine.services import add_audit

CANDIDATE_KIND = "LiteraryCandidateDraft"
CANDIDATE_SCHEMA_VERSION = "1.0"


@dataclass
class Candidate:
    """One saved draft, as the workbench needs to present it."""

    artifact: AgentArtifact
    created: bool

    @property
    def content_saved(self) -> bool:
        payload = self.artifact.payload_json
        return bool(payload.get("content_saved"))

    @property
    def text(self) -> str:
        return str(self.artifact.payload_json.get("text") or "")

    @property
    def review(self) -> dict[str, Any]:
        return dict(self.artifact.payload_json.get("review") or {})

    def as_public(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact.id,
            "kind": self.artifact.kind,
            "target_kind": self.artifact.payload_json.get("target_kind"),
            "unit_id": self.artifact.payload_json.get("unit_id"),
            "text": self.text,
            "text_chars": len(self.text),
            "text_sha256": self.artifact.payload_json.get("text_sha256"),
            "content_saved": self.content_saved,
            "accepted": False,
            "review": self.review,
            "notes": list(self.artifact.payload_json.get("notes") or []),
            "created_at": self.artifact.created_at.isoformat() if self.artifact.created_at else None,
        }


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_candidate(
    db,
    *,
    project_id: str,
    task: AgentTaskRecord | None,
    target_kind: str,
    unit_id: str,
    text: str,
    payload: dict[str, Any] | None = None,
    findings: list[triage.Finding] | None = None,
    notes: list[str] | None = None,
    producer_role: str | None = None,
    input_revision_ids: list[str] | None = None,
) -> Candidate:
    """Persist one candidate draft idempotently.

    The identity is the producing task, the target kind and the text itself, so
    a crashed worker that re-runs the recovery path finds its own draft instead
    of writing a second copy.  A draft is never approved here.
    """
    findings = list(findings or [])
    text = text or ""
    if not text.strip() and not isinstance(payload, dict):
        raise ValueError("a candidate draft needs literary text or a payload")
    text_sha = _text_digest(text)
    if task is not None:
        existing = next((row for row in db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == CANDIDATE_KIND,
            AgentArtifact.source_task_id == task.id)).all()
            if row.payload_json.get("unit_id") == unit_id
            and row.payload_json.get("target_kind") == target_kind
            and row.payload_json.get("text_sha256") == text_sha), None)
        if existing is not None:
            return Candidate(artifact=existing, created=False)
    summary = triage.summarize(findings) if findings else {}
    body = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "target_kind": target_kind,
        "unit_id": unit_id,
        "text": text,
        "text_sha256": text_sha,
        # "Content saved" is a promise about prose.  A structured candidate
        # with no body must not claim it, or the panel would tell the person a
        # scene was kept when nothing literary arrived.
        "content_saved": bool(text.strip()),
        "accepted": False,
        "review": summary,
        "notes": list(notes or []),
        "candidate_payload": payload if isinstance(payload, dict) else None,
        "saved_at": utcnow().isoformat(),
    }
    artifact = AgentArtifact(
        project_id=project_id,
        kind=CANDIDATE_KIND,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        payload_json=body,
        sha256=digest(body),
        producer_role=producer_role or (task.role_id if task is not None else "system"),
        source_task_id=task.id if task is not None else _standalone_task(db, project_id),
        input_revision_ids=list(input_revision_ids or []),
    )
    db.add(artifact)
    db.flush()
    add_audit(db, "local-user", "LITERARY_CANDIDATE_SAVED", "agent_artifact", artifact.id,
              {"target_kind": target_kind, "unit_id": unit_id, "text_chars": len(text),
               "has_payload": isinstance(payload, dict),
               "severity": summary.get("state")}, project_id=project_id)
    return Candidate(artifact=artifact, created=True)


def _standalone_task(db, project_id: str) -> str:
    """A synthetic authoring task for drafts that have no provider task."""
    from shine.director.pipeline import _authoring_task
    return _authoring_task(db, project_id, "literary_candidate_import", {"imported": True})


def candidates_for(db, project_id: str, *, target_kind: str | None = None,
                   unit_id: str | None = None) -> list[dict[str, Any]]:
    """Every candidate draft of a project, newest first, as public dicts."""
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == CANDIDATE_KIND,
    ).order_by(AgentArtifact.created_at.desc())).all()
    result = []
    for row in rows:
        payload = row.payload_json or {}
        if target_kind and payload.get("target_kind") != target_kind:
            continue
        if unit_id and payload.get("unit_id") != unit_id:
            continue
        result.append(Candidate(artifact=row, created=False).as_public())
    return result


@dataclass
class PromotionProof:
    """What a candidate must prove before it may stop being only a candidate."""

    required: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not [item for item in self.required if item not in self.present]

    def as_dict(self) -> dict[str, Any]:
        return {"required": list(self.required), "present": list(self.present),
                "missing": [item for item in self.required if item not in self.present]}
