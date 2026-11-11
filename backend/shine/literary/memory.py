"""Five dimensions, one fact ledger.

Characters, relationships, mainline, clues and foreshadowing are five *views*
of the same story facts, never five stores and never five model calls.  A single
event - "the ledger's torn page was found in the tavern" - is written once and
mapped to the dimensions it serves.

The external contract is deliberately tiny: a model reports what appeared or
changed, with the source span it read it from.  Everything else (ids, versions,
document hashes, approval state, defaults) is written here by the server:

    {"observations": [
        {"kind": "clue", "subjects": ["甲", "账册"],
         "statement": "甲在酒馆发现账册的残页",
         "evidence_span_ids": ["S12#p3"]}
    ]}

A malformed observation is a warning about *that observation*, never a reason to
discard the outline or the scene that produced it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from shine.h3.rulebook import digest
from shine.literary import triage
from shine.literary.models import StoryMemoryFact, StoryMemoryRevision, StoryMemorySnapshot
from shine.literary.requests import estimate_tokens

DIMENSIONS = ("character", "relationship", "mainline", "clue", "foreshadow")
DIMENSION_ZH = {
    "character": "人物", "relationship": "人物关系", "mainline": "故事主线",
    "clue": "线索", "foreshadow": "伏笔",
}
ORIGINS = ("source_fact", "approved_adaptation", "ai_proposal")
AUTHORITIES = ("candidate", "approved", "authoritative")
VISIBILITY_SCOPES = ("audience_known", "characters_known", "production_only")

STATEMENT_LIMIT = 600
SUBJECT_LIMIT = 8


@dataclass
class Observation:
    """One reported fact, parsed from a model answer."""

    kind: str
    subjects: list[str]
    statement: str
    evidence_span_ids: list[str] = field(default_factory=list)
    story_time: str | None = None
    visibility: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    dimensions: list[str] = field(default_factory=list)

    def key(self) -> str:
        material = {"kind": self.kind, "subjects": sorted(self.subjects),
                    "statement": self.statement}
        return hashlib.sha256(json.dumps(material, ensure_ascii=False,
                                         sort_keys=True).encode()).hexdigest()


@dataclass
class IngestResult:
    facts: list[StoryMemoryFact] = field(default_factory=list)
    skipped: int = 0
    findings: list[triage.Finding] = field(default_factory=list)
    snapshot_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "saved": len(self.facts), "skipped": self.skipped,
            "fact_ids": [fact.id for fact in self.facts],
            "snapshot_id": self.snapshot_id,
            "review": triage.summarize(self.findings),
        }


@dataclass
class Retrieval:
    """The bounded memory view one writing task receives."""

    context_text: str
    fact_ids: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    omitted: int = 0
    snapshot_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_text": self.context_text, "fact_ids": list(self.fact_ids),
            "evidence_refs": list(self.evidence_refs), "warnings": list(self.warnings),
            "omitted": self.omitted, "snapshot_id": self.snapshot_id,
            "fact_count": len(self.fact_ids),
        }


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_observations(payload: Any, *, unit_id: str) -> tuple[list[Observation], list[triage.Finding]]:
    """Read ``observations[]`` tolerantly.

    A missing list is a legitimate empty result (a transition scene really has
    nothing new).  One bad entry is skipped with a warning naming it; the batch
    is not rejected because of it.
    """
    observations: list[Observation] = []
    findings: list[triage.Finding] = []
    if not isinstance(payload, dict):
        return observations, findings
    raw = payload.get("observations")
    if raw is None:
        return observations, findings
    if not isinstance(raw, list):
        findings.append(triage.Finding(
            severity=triage.WARNING, reason_code="MEMORY_OBSERVATION_INVALID",
            message_zh="故事记忆观测不是列表，已忽略；正文不受影响。",
            next_action="retry_memory_only", affected_unit_ids=(unit_id,),
            source_evidence={"type": type(raw).__name__}))
        return observations, findings
    for index, item in enumerate(raw[:200]):
        if not isinstance(item, dict):
            findings.append(_invalid(index, unit_id, "not_an_object"))
            continue
        kind = _string(item.get("kind")).strip().lower()
        statement = _string(item.get("statement")).strip()
        if kind not in DIMENSIONS:
            findings.append(_invalid(index, unit_id, f"unknown_kind:{kind or 'missing'}"))
            continue
        if not statement:
            findings.append(_invalid(index, unit_id, "empty_statement"))
            continue
        subjects = [str(value).strip() for value in (item.get("subjects") or [])
                    if str(value).strip()][:SUBJECT_LIMIT]
        spans = [str(value) for value in (item.get("evidence_span_ids") or [])][:12]
        visibility = item.get("visibility")
        observations.append(Observation(
            kind=kind, subjects=subjects, statement=statement[:STATEMENT_LIMIT],
            evidence_span_ids=spans,
            story_time=_string(item.get("story_time")).strip()[:64] or None,
            visibility=dict(visibility) if isinstance(visibility, dict) else {},
            status=_string(item.get("status")).strip()[:24] or "active",
            dimensions=[value for value in (item.get("dimensions") or [])
                        if value in DIMENSIONS][:5],
        ))
    return observations, findings


def _invalid(index: int, unit_id: str, reason: str) -> triage.Finding:
    return triage.Finding(
        severity=triage.WARNING, reason_code="MEMORY_OBSERVATION_INVALID",
        message_zh="有一条故事记忆观测无法使用，已跳过；正文不受影响。",
        next_action="retry_memory_only", affected_unit_ids=(unit_id,),
        source_evidence={"index": index, "reason": reason})


def _evidence(document_sha256: str | None, spans: list[str], statement: str) -> dict[str, Any]:
    return {
        "document_sha256": document_sha256,
        "span_ids": list(spans),
        "statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        "verifiable": bool(document_sha256 and spans),
    }


def record_observations(
    db,
    *,
    project_id: str,
    observations: list[Observation],
    origin: str = "ai_proposal",
    authority: str = "candidate",
    unit_id: str | None = None,
    source_task_id: str | None = None,
    document_sha256: str | None = None,
    known_span_ids: set[str] | None = None,
) -> IngestResult:
    """Write observations as facts, idempotently.

    ``authority`` defaults to ``candidate``: a model's observation is never
    promoted to authoritative by this function.  Evidence is checked against the
    span catalogue when one was supplied; an unverifiable observation is still
    kept, marked ``verifiable=False``, because a doubt about the citation is not
    a doubt about the story.
    """
    if origin not in ORIGINS:
        origin = "ai_proposal"
    if authority not in AUTHORITIES:
        authority = "candidate"
    result = IngestResult()
    known_keys = {row.evidence.get("fact_key") for row in db.scalars(select(StoryMemoryFact).where(
        StoryMemoryFact.project_id == project_id,
        StoryMemoryFact.status != "superseded",
    )).all()}
    for observation in observations:
        fact_sha = digest({"project": project_id, "key": observation.key()})
        if fact_sha in known_keys:
            result.skipped += 1
            continue
        known_keys.add(fact_sha)
        evidence = _evidence(document_sha256, observation.evidence_span_ids, observation.statement)
        evidence["fact_key"] = fact_sha
        if known_span_ids is not None and observation.evidence_span_ids:
            unknown = [span for span in observation.evidence_span_ids if span not in known_span_ids]
            if unknown:
                evidence["unverified_span_ids"] = unknown[:8]
                evidence["verifiable"] = False
                result.findings.append(triage.Finding(
                    severity=triage.WARNING, reason_code="MEMORY_OBSERVATION_INVALID",
                    message_zh="有一条记忆引用的原文片段未找到，已保留但不算权威。",
                    next_action="review_candidate", affected_unit_ids=(unit_id or "memory",),
                    source_evidence={"span_ids": unknown[:8]}))
        dimensions = list(dict.fromkeys([observation.kind, *observation.dimensions]))
        fact = StoryMemoryFact(
            project_id=project_id, kind=observation.kind, dimensions=dimensions,
            subjects=observation.subjects, statement=observation.statement,
            evidence=evidence, origin=origin, authority=authority,
            # An unannotated fact is ordinary story knowledge.  A secret must be
            # marked as one, because withholding everything by default makes the
            # memory useless while a person can still see the scope before
            # promoting the fact.
            visibility=observation.visibility or {"scope": "audience_known"},
            story_time=observation.story_time, status=observation.status,
            unit_id=unit_id, source_task_id=source_task_id,
        )
        db.add(fact)
        db.flush()
        db.add(StoryMemoryRevision(
            project_id=project_id, fact_id=fact.id, action="create",
            patch={"statement": fact.statement, "authority": authority, "origin": origin},
            actor="system", actor_kind="system",
            note=f"unit={unit_id}" if unit_id else None))
        result.facts.append(fact)
    db.flush()
    return result


def promote(db, fact_id: str, *, actor: str = "local-user",
            authority: str = "approved") -> StoryMemoryFact:
    """A person accepts one fact; only a person may raise its authority."""
    if authority not in {"approved", "authoritative"}:
        raise ValueError("MEMORY_AUTHORITY_UNSUPPORTED")
    fact = db.get(StoryMemoryFact, fact_id)
    if fact is None:
        raise ValueError("MEMORY_FACT_NOT_FOUND")
    previous = fact.authority
    fact.authority = authority
    fact.revision += 1
    db.add(StoryMemoryRevision(
        project_id=fact.project_id, fact_id=fact.id, action="confirm",
        patch={"authority": authority, "previous": previous},
        actor=actor, actor_kind="user"))
    db.flush()
    return fact


def supersede(db, fact_id: str, *, replacement: StoryMemoryFact, actor: str = "local-user") -> None:
    """A later fact replaces an earlier one without deleting the original."""
    old = db.get(StoryMemoryFact, fact_id)
    if old is None:
        raise ValueError("MEMORY_FACT_NOT_FOUND")
    old.status = "superseded"
    old.superseded_by = replacement.id
    db.add(StoryMemoryRevision(
        project_id=old.project_id, fact_id=old.id, action="supersede",
        patch={"superseded_by": replacement.id}, actor=actor, actor_kind="user"))
    db.flush()


def snapshot(db, project_id: str, *, as_of_story_time: str | None = None,
             upstream: dict[str, Any] | None = None) -> StoryMemorySnapshot:
    """The immutable hash of every authoritative fact right now."""
    facts = authoritative_facts(db, project_id)
    material = [{"id": fact.id, "statement": fact.statement, "kind": fact.kind,
                 "authority": fact.authority, "revision": fact.revision}
                for fact in sorted(facts, key=lambda row: row.id)]
    sha = digest({"project": project_id, "facts": material,
                  "as_of": as_of_story_time})
    existing = db.scalars(select(StoryMemorySnapshot).where(
        StoryMemorySnapshot.project_id == project_id,
        StoryMemorySnapshot.sha256 == sha)).first()
    if existing is not None:
        return existing
    row = StoryMemorySnapshot(project_id=project_id, sha256=sha, fact_count=len(facts),
                              as_of_story_time=as_of_story_time,
                              upstream=dict(upstream or {}))
    db.add(row)
    db.flush()
    return row


def authoritative_facts(db, project_id: str) -> list[StoryMemoryFact]:
    return list(db.scalars(select(StoryMemoryFact).where(
        StoryMemoryFact.project_id == project_id,
        StoryMemoryFact.authority.in_(("approved", "authoritative")),
        StoryMemoryFact.status != "superseded",
    ).order_by(StoryMemoryFact.created_at)).all())


def pending_facts(db, project_id: str) -> list[StoryMemoryFact]:
    return list(db.scalars(select(StoryMemoryFact).where(
        StoryMemoryFact.project_id == project_id,
        StoryMemoryFact.authority == "candidate",
        StoryMemoryFact.status != "superseded",
    ).order_by(StoryMemoryFact.created_at.desc())).all())


def _line(fact: StoryMemoryFact) -> str:
    who = "、".join(fact.subjects) if fact.subjects else "—"
    label = DIMENSION_ZH.get(fact.kind, fact.kind)
    return f"[{label}] {who}：{fact.statement}"


def retrieve(
    db,
    project_id: str,
    *,
    token_budget: int = 1_500,
    kinds: tuple[str, ...] | None = None,
    subject_names: tuple[str, ...] = (),
    scene_fact_ids: tuple[str, ...] = (),
    include_candidates: bool = False,
    max_secret_lines: int = 0,
) -> Retrieval:
    """Build the bounded memory view for one writing task.

    Three tiers, in this order, and the budget is spent on whole facts only - a
    fact is never cut in half:

    ``mandatory`` - facts pinned to this scene, plus every mainline fact;
    ``preferred`` - relationships and clues involving the scene's characters;
    ``optional``  - everything else authoritative.

    Secret facts (``production_only``) are withheld unless the caller explicitly
    asks for them (the L1 reviewer, never the scene writer).  A fact is never
    silently dropped from the database - only from this budgeted view.
    """
    all_facts = authoritative_facts(db, project_id)
    if include_candidates:
        all_facts = [*all_facts, *pending_facts(db, project_id)]
    if kinds:
        all_facts = [fact for fact in all_facts if fact.kind in kinds]

    def is_secret(fact: StoryMemoryFact) -> bool:
        scope = (fact.visibility or {}).get("scope")
        return scope == "production_only"

    pinned = [fact for fact in all_facts if fact.id in scene_fact_ids]
    mandatory = [fact for fact in all_facts
                 if fact.kind == "mainline" and fact not in pinned]
    preferred: list[StoryMemoryFact] = []
    optional: list[StoryMemoryFact] = []
    names = set(subject_names)
    for fact in all_facts:
        if fact in pinned or fact in mandatory:
            continue
        if fact.kind in {"relationship", "clue", "foreshadow"} and (
                not names or names.intersection(fact.subjects)):
            preferred.append(fact)
        elif fact.kind == "character" and names.intersection(fact.subjects):
            preferred.append(fact)
        else:
            optional.append(fact)

    warnings: list[str] = []
    lines: list[str] = []
    fact_ids: list[str] = []
    evidence: list[dict[str, Any]] = []
    used = 0
    omitted = 0
    secret_lines = 0
    for fact in [*pinned, *mandatory, *preferred, *optional]:
        if is_secret(fact):
            # A production-only secret must not leak into a scene writer's view;
            # an explicit caller (the semantic reviewer) may ask for it.
            if max_secret_lines == 0:
                omitted += 1
                continue
            if secret_lines >= max_secret_lines:
                omitted += 1
                continue
            secret_lines += 1
        line = _line(fact)
        cost = estimate_tokens(line)
        if used + cost > token_budget:
            omitted += 1
            continue
        used += cost
        lines.append(line)
        fact_ids.append(fact.id)
        evidence.append({"fact_id": fact.id, "kind": fact.kind,
                         "spans": (fact.evidence or {}).get("span_ids") or [],
                         "document_sha256": (fact.evidence or {}).get("document_sha256")})
    if omitted:
        warnings.append(f"MEMORY_TRIMMED:{omitted}")
    if not lines:
        # A legitimate empty view: a transition scene with no stored fact.
        warnings.append("MEMORY_EMPTY")
    latest = db.scalars(select(StoryMemorySnapshot).where(
        StoryMemorySnapshot.project_id == project_id
    ).order_by(StoryMemorySnapshot.created_at.desc())).first()
    return Retrieval(context_text="\n".join(lines), fact_ids=fact_ids,
                     evidence_refs=evidence, warnings=warnings, omitted=omitted,
                     snapshot_id=latest.id if latest else None)


def context_block(retrieval: Retrieval) -> dict[str, Any]:
    """The shape a scene request embeds; empty views are legal."""
    return {"facts": retrieval.context_text, "fact_ids": retrieval.fact_ids,
            "warnings": retrieval.warnings}
