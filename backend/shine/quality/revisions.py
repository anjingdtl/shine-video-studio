"""Request revisions for automatic repair.

An automatic repair must produce a *different* generation request. Re-queueing
the same prompt with the same parameters is not a repair, it is a reroll that
also burns the retry budget.

This module owns the only sanctioned mutation paths:

* ``ADJUST_PROMPT_STRATEGY`` recompiles the prompt through
  :class:`shine.h3.H3PromptCompiler` after an authorised shot-intent change.
* ``REBUILD_REFERENCE_BINDINGS`` rebuilds the request-local manifest from the
  shot's saved, authoritative bindings.
* ``ADJUST_WORKFLOW_PARAMETERS`` / ``RERENDER_AFTER_TRANSIENT`` change the
  generation parameters (seed) and re-validate the workflow profile.

Locked story facts, master assets, the frozen Style Bible and the dialogue text
are never modified here. When a failure cannot be fixed inside those bounds the
builder returns ``None`` and the caller escalates to a human.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from shine.h3.compiler import CompiledPrompt, H3PromptCompiler, PromptCompilationError
from shine.schemas import (
    GenerationMode,
    ReferenceManifestItem,
    ShotSpec,
    StyleBible,
)

#: Shot-intent fields an automatic repair may touch. Everything else in the
#: shot specification is user-owned and stays untouched.
AUTHORIZED_SPEC_FIELDS = ("camera", "sound")

#: Prompt directives injected for a given root cause. They are camera-level
#: directing notes, not new story facts.
_DIRECTIVES = {
    "STYLE_DRIFT": "严格对齐冻结风格圣经的配色、光线与镜头质感，保持全片视觉统一",
    "PROMPT_INVALID": "先复述人物、场景与动作，再给出镜头与光线，保持描述顺序稳定",
    "H3_UNRESOLVED_LABEL": "严格按引用标签顺序描述人物与场景，避免出现未定义的指代",
    "H3_LABEL_MISMATCH": "逐一说明每个引用素材对应的角色，标签与素材一一对应",
    "H3_SECTION_MISSING": "补全镜头、声音与配乐描述，保持结构完整",
    "IDENTITY_MISMATCH": "锁定人物身份、发型与服装特征，避免面部与造型漂移",
    "COSTUME_DRIFT": "锁定服装细节与配色，避免换装或形变",
    "SPEAKER_MISMATCH": "明确说话人与口型归属，避免台词错位",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RequestRevision:
    """A new, materially different render request."""

    request: dict[str, Any]
    changes: list[str] = field(default_factory=list)
    prompt_sha256: str = ""
    parameter_sha256: str = ""
    manifest_sha256: str = ""
    reason: str = ""
    action: str = ""
    spec: ShotSpec | None = None
    manifest: list[ReferenceManifestItem] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def parameter_fingerprint(request: dict[str, Any]) -> str:
    return digest_of(
        {
            "seed": request.get("seed"),
            "expected_duration_s": request.get("expected_duration_s"),
            "generation_mode": request.get("generation_mode"),
            "workflow_profile_hash": request.get("workflow_profile_hash"),
            "prompt_sha256": request.get("prompt_sha256"),
        }
    )


def manifest_fingerprint(manifest: list[ReferenceManifestItem]) -> str:
    return digest_of([item.model_dump(mode="json") for item in manifest])


def _copy_request(request: dict[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(request))


def _apply_directive(spec: ShotSpec, *, root_cause: str, attempt_no: int) -> ShotSpec | None:
    """Apply one authorised, deterministic camera/sound directive."""
    code = root_cause.split(":", 1)[0].strip() if root_cause else ""
    directive = _DIRECTIVES.get(code)
    if directive is None:
        directive = (
            "加强镜头稳定性与主体清晰度，确保关键信息在画面中明确可读"
        )
    camera = dict(spec.camera)
    camera["repair_directive"] = f"{directive}（自动返工第 {attempt_no} 轮）"
    updates: dict[str, Any] = {"camera": camera}
    return spec.model_copy(update=updates)


def revision_for_prompt(
    *,
    request: dict[str, Any],
    spec: ShotSpec,
    style: StyleBible,
    manifest: list[ReferenceManifestItem],
    root_cause: str,
    attempt_no: int,
    project_language: str = "Chinese",
    mode_hint: GenerationMode | None = None,
) -> RequestRevision | None:
    """Recompile the prompt through the H3 compiler after an authorised change."""
    if request.get("manual_prompt"):
        # A person wrote and froze this prompt.  Recompiling would silently
        # replace their words with the compiler's, so a repair keeps the text
        # and changes only parameters or references.
        return None
    revised_spec = _apply_directive(spec, root_cause=root_cause, attempt_no=attempt_no)
    if revised_spec is None:
        return None
    if request.get("rulepack_id"):
        # V1.1 official descriptive-language gate also applies to repairs.
        camera = dict(revised_spec.camera)
        camera["repair_directive"] = (
            "Preserve the approved identity, costume, dialogue and frozen style. "
            f"Keep composition stable and clarify the visible action; repair iteration {attempt_no}."
        )
        revised_spec = revised_spec.model_copy(update={"camera": camera})
    compiler = H3PromptCompiler()
    try:
        compiled: CompiledPrompt = compiler.compile(
            spec=revised_spec,
            style=style,
            manifest=manifest,
            project_language=project_language,
            mode_hint=mode_hint,
        )
    except PromptCompilationError:
        return None
    old_prompt = request.get("compiled_prompt") or ""
    if compiled.prompt == old_prompt:
        # A repair that produces the same prompt is not a repair.
        return None
    new_request = _copy_request(request)
    new_request["compiled_prompt"] = compiled.prompt
    new_request["compiled_prompt_sections"] = compiled.sections
    new_request["prompt_sha256"] = compiled.prompt_sha256
    new_request["prompt_skill_hash"] = compiled.skill_sha256
    new_request["compiler_version"] = compiled.compiler_version
    new_request["rule_version"] = compiled.rule_version
    new_request["shot_spec"] = revised_spec.model_dump(mode="json")
    return RequestRevision(
        request=new_request,
        changes=[
            "Recompiled the H3 prompt through the pinned compiler",
            f"Applied an authorised camera directive for root cause {root_cause[:120]}",
        ],
        prompt_sha256=compiled.prompt_sha256,
        parameter_sha256=parameter_fingerprint(new_request),
        manifest_sha256=manifest_fingerprint(manifest),
        reason=root_cause,
        action="adjust_prompt_strategy",
        spec=revised_spec,
        manifest=list(manifest),
    )


def revision_for_references(
    *,
    request: dict[str, Any],
    manifest: list[ReferenceManifestItem],
    root_cause: str,
    attempt_no: int,
) -> RequestRevision | None:
    """Rebuild the request-local manifest from the shot's saved bindings."""
    rebuilt = [ReferenceManifestItem.model_validate(item) for item in manifest]
    new_request = _copy_request(request)
    new_request["reference_manifest"] = [item.model_dump(mode="json") for item in rebuilt]
    new_manifest_hash = manifest_fingerprint(rebuilt)
    old_manifest_hash = manifest_fingerprint(
        [
            ReferenceManifestItem.model_validate(item)
            for item in request.get("reference_manifest", [])
        ]
    )
    if new_manifest_hash == old_manifest_hash:
        return None
    return RequestRevision(
        request=new_request,
        changes=[
            "Rebuilt the request-local reference manifest from the shot's saved bindings",
            "Re-numbered the H3 reference labels deterministically",
        ],
        prompt_sha256=str(request.get("prompt_sha256") or ""),
        parameter_sha256=parameter_fingerprint(new_request),
        manifest_sha256=new_manifest_hash,
        reason=root_cause,
        action="rebuild_reference_bindings",
        manifest=rebuilt,
    )


def revision_for_parameters(
    *,
    request: dict[str, Any],
    root_cause: str,
    attempt_no: int,
    new_seed: int,
    reason: str,
) -> RequestRevision | None:
    """Change the generation parameters so the new attempt is materially different."""
    new_request = _copy_request(request)
    new_request["seed"] = int(new_seed)
    manifest = [
        ReferenceManifestItem.model_validate(item)
        for item in new_request.get("reference_manifest", [])
    ]
    return RequestRevision(
        request=new_request,
        changes=[
            reason,
            f"Set a new generation seed ({new_seed}) for attempt {attempt_no}",
        ],
        prompt_sha256=str(new_request.get("prompt_sha256") or ""),
        parameter_sha256=parameter_fingerprint(new_request),
        manifest_sha256=manifest_fingerprint(manifest),
        reason=root_cause,
        action="adjust_workflow_parameters",
        manifest=manifest,
    )


def derive_seed(request: dict[str, Any], attempt_no: int) -> int:
    """Derive a deterministic but different seed for a repair attempt."""
    base = request.get("seed")
    if base is None:
        base = int(hashlib.sha256(str(request.get("idempotency_key", "")).encode()).hexdigest()[:8], 16)
    return (int(base) + attempt_no * 7919) % 2_147_483_647


__all__ = [
    "AUTHORIZED_SPEC_FIELDS",
    "RequestRevision",
    "canonical_json",
    "derive_seed",
    "digest_of",
    "manifest_fingerprint",
    "parameter_fingerprint",
    "revision_for_parameters",
    "revision_for_prompt",
    "revision_for_references",
]
