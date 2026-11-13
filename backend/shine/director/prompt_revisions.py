"""A shot's H3 prompt, previewed, edited and frozen by a person.

The compiler stays the default author.  This module adds the second author: the
person watching the shot table.  A frozen revision is submitted verbatim instead
of the compiled prompt, and it stays in effect until the thing it was written
*for* changes.

The uniqueness rule this module keeps:

* **One manifest.**  The reference list a prompt is compared against is the
  very manifest the request carries.  Nothing rebuilds a second, approximate
  copy of it.
* **One validity answer.**  A frozen prompt is valid for one exact situation:
  the same references, the same dialogue and the same duration.  Every other
  fact of the shot may change without touching it.
* **One resolution.**  ``resolve_manual_prompt`` is the only place that
  decides whether the compiled text or the frozen text is submitted, so the
  render path and the package readiness check can never disagree.

The official prompt-writing guidance is reviewed here as warnings.  Nothing in
this module refuses a save or a freeze because of style.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from shine.director.contracts import digest_of
from shine.director.models import ShotPromptRevision
from shine.h3.compiler import COMPILER_VERSION, RULE_VERSION, SKILL_SHA256
from shine.h3.spec_review import review_h3_prompt, serialise, split_h3_sections
from shine.models import Shot, ShotAssetBinding, utcnow
from shine.schemas import ReferenceUse
from shine.services import add_audit

MANUAL_SOURCE = "manual-frozen-v1"
MAX_MANUAL_PROMPT_CHARS = 12000
_GENERATION_MODES = {"T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"}

#: Why a frozen prompt is not the one being submitted.  Each name says which
#: fact of the shot moved out from under the text.
REASON_REFERENCES_CHANGED = "MANUAL_PROMPT_REFERENCES_CHANGED"
REASON_DIALOGUE_CHANGED = "MANUAL_PROMPT_DIALOGUE_CHANGED"
REASON_DURATION_CHANGED = "MANUAL_PROMPT_DURATION_CHANGED"
REASON_MODE_CHANGED = "MANUAL_PROMPT_MODE_CHANGED"

REASON_LABELS: dict[str, str] = {
    REASON_REFERENCES_CHANGED: "参考素材变了，人工提示词里的角色编号已不对应",
    REASON_DIALOGUE_CHANGED: "这一镜的台词变了，人工提示词不再包含现在的台词",
    REASON_DURATION_CHANGED: "这一镜的时长变了，人工提示词里的时间点不再合适",
    REASON_MODE_CHANGED: "这一次的生片方式变了，人工提示词不是按它写的",
}


@dataclass
class ManualPromptResolution:
    """What will actually be submitted for one shot, and where it came from."""

    prompt: str
    sections: dict[str, str]
    prompt_sha256: str
    source: str
    """``compiler`` or ``manual``: which text is being submitted."""
    status: str | None = None
    """Why a frozen prompt is not in effect, when one exists but cannot be used."""
    warnings: list[str] = field(default_factory=list)
    revision_id: str | None = None

    @property
    def manual(self) -> bool:
        return self.source == "manual"


def _binding_inputs(db, shot: Shot):
    from shine.schemas import ShotBindingInput

    rows = db.scalars(select(ShotAssetBinding).where(
        ShotAssetBinding.shot_id == shot.id).order_by(ShotAssetBinding.ordinal)).all()
    return [
        ShotBindingInput(asset_id=row.asset_id, use_as=ReferenceUse(row.use_as),
                         role=row.role, frame_role=row.frame_role, ordinal=row.ordinal)
        for row in rows
    ]


def reference_signature(manifest) -> list[dict[str, str]]:
    """The label-to-asset mapping the request actually carries.

    ``<Subject 1>`` means one specific performer.  This is the pair that must
    not change under a frozen prompt, so it is read off the real manifest rather
    than from a second, approximate copy of it.
    """
    return [{"label": item.label, "asset_id": item.asset_id} for item in manifest]


def content_signature(spec) -> dict[str, Any]:
    """The parts of the shot a written prompt describes by name.

    The spoken lines and the clip length are quoted inside the prompt text, so
    a change to either makes the written text wrong.  The story beat and the
    camera are not: a frozen prompt *is* the picture, and the crew may keep
    rewriting the beat around it.
    """
    return {
        "dialogue": [
            {"speaker_id": line.speaker_id, "text": line.text,
             "start_s": line.start_s, "end_s": line.end_s}
            for line in spec.dialogue
        ],
        "duration_target_s": spec.duration_target_s,
    }


def validity_digest(references: list[dict[str, str]], content: dict[str, Any]) -> str:
    """The one digest that says "this prompt still describes this shot"."""
    return digest_of({"references": references, "content": content})


def _latest(db, shot_id: str, status: str | None = None) -> ShotPromptRevision | None:
    query = select(ShotPromptRevision).where(ShotPromptRevision.shot_id == shot_id)
    if status:
        query = query.where(ShotPromptRevision.status == status)
    return db.scalars(query.order_by(ShotPromptRevision.revision.desc())).first()


def _spec_of(shot: Shot):
    from shine.schemas import ShotSpec

    return ShotSpec.model_validate(shot.spec)


def _manual_inactive_reason(row: ShotPromptRevision, *, manifest, spec,
                             request_mode: str) -> str | None:
    """Why a frozen prompt may not be submitted, or ``None`` when it may.

    Each reason names one fact of the shot.  A frozen prompt that does not
    describe the current shot is never submitted: a prompt that names the wrong
    performer renders the wrong face, and a prompt that quotes the wrong line
    speaks the wrong words.
    """
    if row.mode != request_mode:
        return REASON_MODE_CHANGED
    if list(row.reference_set or []) != reference_signature(manifest):
        return REASON_REFERENCES_CHANGED
    if dict(row.content_signature or {}) != content_signature(spec):
        current = content_signature(spec)
        stored = dict(row.content_signature or {})
        if stored.get("duration_target_s") != current.get("duration_target_s"):
            return REASON_DURATION_CHANGED
        return REASON_DIALOGUE_CHANGED
    return None


def resolve_manual_prompt(db, shot: Shot, *, compiled, manifest, spec,
                          ignore_manual: bool = False) -> ManualPromptResolution:
    """Decide whether the compiled text or the frozen text is submitted.

    This is the only place that makes that decision, so the render path and the
    package readiness check always answer the same question.
    """
    if ignore_manual:
        return ManualPromptResolution(
            prompt=compiled.prompt, sections=dict(compiled.sections),
            prompt_sha256=compiled.prompt_sha256, source="compiler",
            warnings=list(compiled.warnings),
        )
    row = _latest(db, shot.id, "FROZEN")
    if row is None:
        return ManualPromptResolution(
            prompt=compiled.prompt, sections=dict(compiled.sections),
            prompt_sha256=compiled.prompt_sha256, source="compiler",
            warnings=list(compiled.warnings),
        )
    reason = _manual_inactive_reason(row, manifest=manifest, spec=spec,
                                      request_mode=compiled.mode.value)
    if reason is not None:
        # The frozen text is kept, not deleted: it becomes effective again the
        # moment the shot returns to the situation it was written for.
        return ManualPromptResolution(
            prompt=compiled.prompt, sections=dict(compiled.sections),
            prompt_sha256=compiled.prompt_sha256, source="compiler",
            status=reason,
            warnings=[*compiled.warnings, REASON_LABELS.get(reason, reason)],
        )
    warnings = serialise(review_h3_prompt(
        row.prompt, dict(row.sections or {}), mode=row.mode,
        dialogue=spec.dialogue, duration_s=spec.duration_target_s,
        defined_labels={item.label for item in manifest},
    ))
    return ManualPromptResolution(
        prompt=row.prompt, sections=dict(row.sections or {}),
        prompt_sha256=row.prompt_sha256, source="manual",
        warnings=warnings, revision_id=row.id,
    )


def manual_status_for(db, shot: Shot, row: ShotPromptRevision) -> str | None:
    """Why a frozen prompt is currently inactive, without compiling the shot.

    The table needs this to avoid showing "人工冻结" over a prompt that will not
    be submitted.  The generation mode is a function of the reference manifest,
    so identical references and identical quoted facts imply the identical mode:
    the cheap comparison is also the exact one.
    """
    from shine.services import manifest_from_shot_bindings

    spec = _spec_of(shot)
    manifest = manifest_from_shot_bindings(db, shot)
    if list(row.reference_set or []) != reference_signature(manifest):
        return REASON_REFERENCES_CHANGED
    if dict(row.content_signature or {}) != content_signature(spec):
        stored = dict(row.content_signature or {})
        current = content_signature(spec)
        if stored.get("duration_target_s") != current.get("duration_target_s"):
            return REASON_DURATION_CHANGED
        return REASON_DIALOGUE_CHANGED
    return None


def frozen_prompt_for(db, shot: Shot) -> tuple[ShotPromptRevision | None, str | None]:
    """The frozen revision of this shot, or the reason it cannot be used.

    Callers that do not hold the request manifest use this to answer "does this
    shot have a frozen prompt?" without re-deriving one.
    """
    row = _latest(db, shot.id, "FROZEN")
    return (row, None) if row is not None else (None, None)


def shot_prompt_view(db, project, shot: Shot, *, compiler_preview: bool = False) -> dict[str, Any]:
    """The prompt a person would edit: the compiler's preview plus the frozen row."""
    from shine.schemas import CreateJob
    from shine.services import make_render_request

    spec = _spec_of(shot)
    frozen = _latest(db, shot.id, "FROZEN")
    payload: dict[str, Any] = {
        "shot_id": shot.id,
        "shot_revision": shot.revision,
        "duration_target_s": spec.duration_target_s,
        "characters": list(spec.characters),
        "dialogue": [line.model_dump(mode="json") for line in spec.dialogue],
        "has_bindings": bool(_binding_inputs(db, shot)),
    }
    try:
        request, _assets, meta = make_render_request(
            db, project, shot, CreateJob(shot_id=shot.id),
            ignore_manual_prompt=compiler_preview,
            # The preview must answer for the mode this project really renders
            # in, not the development mock's inferred one.
            require_ref2va=True,
        )
        payload.update({
            "mode": request.generation_mode.value,
            "prompt": request.compiled_prompt or "",
            "sections": request.compiled_prompt_sections,
            "prompt_sha256": request.prompt_sha256,
            "labels": meta.get("labels") or {},
            "warnings": list(meta.get("warnings") or []),
            "compiler_version": request.compiler_version,
            "source": meta.get("source") or "compiler",
            "manual_status": meta.get("manual_status"),
        })
    except HTTPException as exc:
        detail = exc.detail
        payload.update({
            "mode": None,
            "prompt": "",
            "sections": {},
            "prompt_sha256": None,
            "labels": {},
            "warnings": [],
            "compiler_version": None,
            "source": "compiler",
            "manual_status": None,
            "blocked": detail if isinstance(detail, dict) else {
                "code": "PROMPT_PREVIEW_BLOCKED", "message": str(detail)},
        })
    if frozen is not None:
        payload["frozen"] = {
            "id": frozen.id,
            "revision": frozen.revision,
            "shot_revision": frozen.shot_revision,
            "mode": frozen.mode,
            "prompt": frozen.prompt,
            "sections": frozen.sections,
            "prompt_sha256": frozen.prompt_sha256,
            "spec_report": frozen.spec_report,
            "frozen_at": frozen.frozen_at.isoformat() + "Z" if frozen.frozen_at else None,
        }
    else:
        payload["frozen"] = None
    return payload


def save_shot_prompt(
    db, project, shot: Shot, *, prompt: str, mode: str, freeze: bool,
    expected_revision: int | None, actor: str = "user",
) -> dict[str, Any]:
    """Store one prompt revision; freezing makes it the submitted text."""
    from shine.schemas import CreateJob
    from shine.services import make_render_request

    if expected_revision is not None and expected_revision != shot.revision:
        raise HTTPException(409, {
            "code": "SHOT_REVISION_CHANGED",
            "message": "镜头已更新，请刷新后再改提示词",
            "current_revision": shot.revision,
        })
    text = (prompt or "").strip()
    if not text:
        raise HTTPException(422, {"code": "SHOT_PROMPT_EMPTY", "message": "提示词不能为空"})
    if len(text) > MAX_MANUAL_PROMPT_CHARS:
        raise HTTPException(422, {
            "code": "SHOT_PROMPT_TOO_LONG",
            "message": f"提示词最多 {MAX_MANUAL_PROMPT_CHARS} 个字符",
            "chars": len(text),
        })
    if mode not in _GENERATION_MODES:
        raise HTTPException(422, {"code": "SHOT_PROMPT_MODE_INVALID", "mode": mode})
    from shine.h3.mode_policy import MODE_POLICY_MESSAGE, REQUIRED_GENERATION_MODE

    if mode != REQUIRED_GENERATION_MODE.value:
        # The project renders Ref2VA only; freezing a text-only prompt would
        # only produce a revision that can never be submitted.
        raise HTTPException(422, {
            "code": "PROJECT_REQUIRES_REF2VA",
            "message": MODE_POLICY_MESSAGE,
            "mode": mode,
            "required_mode": REQUIRED_GENERATION_MODE.value,
        })

    # The reference list and the mode are read off the request this shot would
    # actually make, so the frozen text is recorded against the same facts the
    # render will compare it with.  The project renders Ref2VA only, so a shot
    # with no reference is refused here rather than frozen as T2VA.
    request, _assets, _meta = make_render_request(
        db, project, shot, CreateJob(shot_id=shot.id),
        ignore_manual_prompt=True, require_ref2va=True)
    manifest = request.reference_manifest
    spec = _spec_of(shot)
    references = reference_signature(manifest)
    content = content_signature(spec)
    sections = split_h3_sections(text, mode)
    report = review_h3_prompt(
        text, sections, mode=mode, dialogue=spec.dialogue,
        duration_s=spec.duration_target_s,
        defined_labels={item.label for item in manifest},
    )
    previous = db.scalars(select(ShotPromptRevision).where(
        ShotPromptRevision.shot_id == shot.id)).all()
    revision = max((row.revision for row in previous), default=0) + 1
    row = ShotPromptRevision(
        project_id=shot.project_id,
        shot_id=shot.id,
        shot_revision=shot.revision,
        revision=revision,
        status="DRAFT",
        mode=mode,
        prompt=text,
        sections=sections,
        prompt_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        reference_set=references,
        content_signature=content,
        manifest_sha256=validity_digest(references, content),
        spec_report=report,
        created_by=actor,
    )
    if freeze:
        for other in previous:
            if other.status == "FROZEN":
                other.status = "SUPERSEDED"
        row.status = "FROZEN"
        row.frozen_at = utcnow()
    db.add(row)
    db.flush()
    if freeze:
        from shine.services import bump_shot_revision

        # The picture changes, so every take of the previous text is stale.
        # The frozen prompt itself is bound to the references, the dialogue and
        # the duration, so this revision bump does not disturb it.
        bump_shot_revision(
            db, project, shot, actor=actor,
            event_type="SHOT_PROMPT_FROZEN",
            payload={"prompt_revision": row.revision, "prompt_sha256": row.prompt_sha256,
                     "spec_warnings": len(report)},
        )
    add_audit(
        db, actor, "SHOT_PROMPT_FROZEN" if freeze else "SHOT_PROMPT_SAVED", "shot", shot.id,
        {"revision": row.revision, "prompt_sha256": row.prompt_sha256, "mode": mode,
         "warnings": len(report), "compiler_version": COMPILER_VERSION,
         "rule_version": RULE_VERSION, "skill_sha256": SKILL_SHA256},
        project_id=shot.project_id,
    )
    db.flush()
    return {
        "id": row.id, "revision": row.revision, "shot_revision": shot.revision,
        "status": row.status, "prompt_sha256": row.prompt_sha256,
        "sections": row.sections, "warnings": report,
    }


def unfreeze_shot_prompt(db, project, shot: Shot, *, actor: str = "user") -> dict[str, Any]:
    """Return this shot to the compiler's prompt."""
    row = _latest(db, shot.id, "FROZEN")
    if row is None:
        return {"shot_id": shot.id, "unfrozen": False}
    row.status = "SUPERSEDED"
    add_audit(db, actor, "SHOT_PROMPT_UNFROZEN", "shot", shot.id,
              {"prompt_revision": row.revision, "prompt_sha256": row.prompt_sha256},
              project_id=shot.project_id)
    db.flush()
    return {"shot_id": shot.id, "unfrozen": True, "prompt_revision": row.revision}


__all__ = [
    "MANUAL_SOURCE",
    "REASON_DIALOGUE_CHANGED",
    "REASON_DURATION_CHANGED",
    "REASON_LABELS",
    "REASON_MODE_CHANGED",
    "REASON_REFERENCES_CHANGED",
    "ManualPromptResolution",
    "content_signature",
    "frozen_prompt_for",
    "manual_status_for",
    "reference_signature",
    "resolve_manual_prompt",
    "save_shot_prompt",
    "shot_prompt_view",
    "unfreeze_shot_prompt",
    "validity_digest",
]
