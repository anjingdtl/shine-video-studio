"""The production gate for one generation profile.

A profile that belongs to an **accepted** variant keeps exactly the behaviour
it always had.  A profile that belongs to a **candidate** variant - the
official W6A8 low-end candidate, for example - is only allowed into a
production run when this machine can really execute it *and* when the caller
keeps its identity: the same project, the same registered profile, the same
verified bytes, the same validated graph.

Every blocker names itself and says what is missing.  Nothing here falls back
to another profile, another checkpoint or another generation mode: a refused
profile stays refused, and the reason travels with the refusal.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from shine.models import WorkflowProfile

#: The acceptance states that keep the ordinary, unchanged production path.
ACCEPTED_VARIANT_STATES = {"SUPPORTED", "EXPERIMENTAL"}


def variant_of(profile: WorkflowProfile | None) -> dict[str, Any] | None:
    """The variant manifest a profile belongs to, when it declares one.

    A profile whose graph names no declared variant keeps the historic
    behaviour: this gate only speaks about variants it can actually name.
    """
    if profile is None:
        return None
    identity = profile.model_identity or {}
    variant_id = identity.get("variant_id")
    if not variant_id:
        return None
    try:
        from shine.comfyops.variants import load_variant_manifest

        manifest = load_variant_manifest(str(variant_id))
    except (OSError, ValueError, FileNotFoundError):
        return None
    return manifest


def is_candidate(profile: WorkflowProfile | None) -> bool:
    """True when this profile belongs to a variant that was never accepted."""
    manifest = variant_of(profile)
    if manifest is None:
        return False
    return str(manifest.get("acceptance_status")) not in ACCEPTED_VARIANT_STATES


def _blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def profile_blockers(db: Session, settings, project_id: str,
                     profile: WorkflowProfile | None,
                     *, require_runtime: bool = True) -> list[dict[str, Any]]:
    """Everything that keeps this profile out of a production run right now.

    An empty list means the profile may run.  The list is ordered: the first
    entry is the step that has to happen first.
    """
    if profile is None:
        return [_blocker("PROFILE_NOT_FOUND", "找不到这个生成方案。")]
    blockers: list[dict[str, Any]] = []

    if profile.project_id is not None and profile.project_id != project_id:
        return [_blocker(
            "PROFILE_SCOPE_MISMATCH",
            "这个生成方案属于另一个作品，不能在这个作品里使用。",
            profile_project_id=profile.project_id,
        )]

    manifest = variant_of(profile)
    if manifest is None:
        # No declared variant: the historic path, unchanged.
        return blockers
    if str(manifest.get("acceptance_status")) in ACCEPTED_VARIANT_STATES:
        return blockers

    variant_id = str(manifest.get("variant_id"))
    if profile.project_id != project_id:
        blockers.append(_blocker(
            "PROFILE_NOT_REGISTERED_FOR_PROJECT",
            "这个候选方案还没有登记到当前作品；请到基础配置 → 模型与生成方案登记它。",
            variant_id=variant_id,
        ))

    declared_graphs = manifest.get("graph_sha256") or {}
    # The manifest maps a profile name to the hash of its graph.
    if declared_graphs and profile.graph_sha256 not in set(declared_graphs.values()):
        blockers.append(_blocker(
            "PROFILE_GRAPH_IDENTITY_CHANGED",
            "这个方案的图哈希与变体清单登记的不一致，不能按这个变体的身份执行。",
            variant_id=variant_id,
            profile_graph_sha256=profile.graph_sha256,
        ))

    if not profile.validated_against:
        blockers.append(_blocker(
            "PROFILE_NOT_VALIDATED",
            "这个方案还没有在本机真实渲染器上核对过节点与输入；请先执行「验证本机节点」。",
            variant_id=variant_id,
        ))

    blockers.extend(_weight_blockers(db, settings, manifest, variant_id))
    if require_runtime:
        blockers.extend(_runtime_blockers(db, settings))
    return blockers


def _weight_blockers(db: Session, settings, manifest: dict[str, Any],
                     variant_id: str) -> list[dict[str, Any]]:
    """The bytes have to be here, hash-verified and consumable before anything is queued.

    Three states are told apart, because they need three different actions:

    * the files are not on this machine at all;
    * the files are here but were never checked byte by byte - a file whose
      name is its digest proves nothing;
    * the bytes were checked, but the renderer that would execute them does
      not read them from that place: they are only in the verified cache, or
      in some other directory.

    ``prepared`` stays what it always was - a display fact that says the
    bytes were found.  It never stands in for a verification.
    """
    try:
        from shine.comfyops.variant_binding import load_binding
        from shine.comfyops.variant_readiness import weight_evidence

        binding = load_binding(variant_id)
    except HTTPException:
        return [_blocker(
            "VARIANT_CATALOG_MISSING",
            "这个变体的部署目录读不到，无法核对它的权重。",
            variant_id=variant_id,
        )]
    evidence = weight_evidence(db, settings, binding)
    missing = [row["component_id"] for row in evidence if not row["prepared"]]
    unverified = [row["component_id"] for row in evidence
                  if row["prepared"] and not row["verified"]]
    unbound = [row["component_id"] for row in evidence
               if row["verified"] and not row.get("consumable", True)]
    blockers: list[dict[str, Any]] = []
    if missing:
        blockers.append(_blocker(
            "PROFILE_WEIGHTS_NOT_AVAILABLE",
            "这个变体的权重还没有在本机就位：" + "、".join(missing)
            + "。候选变体不会自动下载，也不能跳过资格检查。",
            variant_id=variant_id,
            missing_components=missing,
            action="EXISTING_INSTALLATION_IMPORT",
        ))
    if unverified:
        blockers.append(_blocker(
            "PROFILE_WEIGHTS_NOT_VERIFIED",
            "这些权重文件在本机，但没有按锁定的 revision、字节数与 SHA-256 核验："
            + "、".join(unverified)
            + "。请用已有的本机核对或导入流程逐字节核验后再开拍。",
            variant_id=variant_id,
            unverified_components=unverified,
            action="EXISTING_INSTALLATION_VERIFY",
        ))
    if unbound:
        blockers.append(_blocker(
            "PROFILE_WEIGHTS_NOT_CONSUMABLE",
            "这些权重虽然已核验，但当前受管渲染器不会从那个目录读取："
            + "、".join(unbound)
            + "。请用已有的本机导入流程把它们装进本实例的模型目录后再开拍。",
            variant_id=variant_id,
            unbound_components=unbound,
            action="EXISTING_INSTALLATION_IMPORT",
        ))
    return blockers


def _runtime_blockers(db: Session, settings) -> list[dict[str, Any]]:
    """A managed runtime configuration has to exist for a real render."""
    try:
        from shine.comfyops.runtime_configuration import _current_identity

        identity = _current_identity(db, settings)
    except HTTPException as exc:
        return [_blocker(
            "MANAGED_INSTANCE_NOT_CONFIGURED",
            "本机还没有生成受管的渲染器配置，无法用真实 H3 出片；请先在基础配置完成安装与下载。",
            cause=exc.detail,
        )]
    except Exception as exc:  # noqa: BLE001 - an unreadable configuration is a fact
        return [_blocker(
            "MANAGED_INSTANCE_NOT_CONFIGURED",
            "本机的受管渲染器配置读不到，无法用真实 H3 出片。",
            cause=str(exc),
        )]
    if not identity or not identity.get("instance_id"):
        return [_blocker(
            "MANAGED_INSTANCE_NOT_CONFIGURED",
            "本机还没有生成受管的渲染器配置，无法用真实 H3 出片。",
        )]
    return []


def output_contract_blockers(output: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The frozen output must satisfy the H3 output contract.

    The same ``OutputSpec`` the Series and Job entries validate is the
    contract here: 24 fps, a paired resolution that is a multiple of 32 and
    a clip between 4 and 15 seconds.  A package that freezes anything else
    promises a render the renderer cannot make.
    """
    if not output:
        return []
    from pydantic import ValidationError

    from shine.schemas import OutputSpec

    try:
        OutputSpec.model_validate(output)
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            problems.append(f"{location or 'output'}: {error.get('msg', 'invalid')}")
        return [_blocker(
            "OUTPUT_SPEC_INVALID",
            "这份导演包冻结的输出不符合 H3 的输出合同：" + "；".join(problems[:10]),
            problems=problems[:20],
        )]
    return []


def size_blockers(profile: WorkflowProfile, output: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A requested output size only reaches the renderer through a mapping."""
    if not output or output.get("width") is None:
        return []
    unmapped = [name for name in ("width", "height") if name not in (profile.parameter_map or {})]
    if not unmapped:
        return []
    return [_blocker(
        "WORKFLOW_SIZE_NOT_MAPPED",
        "The selected workflow profile cannot apply the requested output size",
        missing_parameters=unmapped,
        mapped_parameters=sorted(profile.parameter_map or {}),
    )]


def require_production_ready(db: Session, settings, project_id: str,
                             profile: WorkflowProfile | None,
                             output: dict[str, Any] | None = None) -> None:
    """Refuse a candidate profile that this machine cannot really execute.

    The refusal is explicit and carries its reason; nothing is substituted.
    """
    blockers = profile_blockers(db, settings, project_id, profile)
    if profile is not None:
        blockers.extend(size_blockers(profile, output))
    if blockers:
        first = blockers[0]
        # A profile that does not exist, or that belongs to another project,
        # keeps its historic 404: the gate only adds new refusals, it never
        # reclassifies an existing one.
        if first["code"] in {"PROFILE_NOT_FOUND", "PROFILE_SCOPE_MISMATCH"}:
            status = 404
        elif first["code"] == "WORKFLOW_SIZE_NOT_MAPPED":
            status = 422
        else:
            status = 409
        detail = {
            "code": first["code"],
            "message": first["message"],
            "blockers": blockers,
            "variant_id": (profile.model_identity or {}).get("variant_id") if profile is not None else None,
            "workflow_profile_id": profile.id if profile is not None else None,
        }
        if first["code"] == "WORKFLOW_SIZE_NOT_MAPPED":
            detail["details"] = {
                "missing_parameters": first.get("missing_parameters") or [],
                "mapped_parameters": first.get("mapped_parameters") or [],
            }
        raise HTTPException(status, detail)


def profile_gate_report(db: Session, settings, project_id: str,
                        profile: WorkflowProfile | None) -> dict[str, Any]:
    """The same verdict, shaped for the interface."""
    manifest = variant_of(profile)
    blockers = profile_blockers(db, settings, project_id, profile)
    return {
        "profile_id": profile.id if profile is not None else None,
        "profile_name": profile.name if profile is not None else None,
        "variant_id": (manifest or {}).get("variant_id"),
        "acceptance_status": (manifest or {}).get("acceptance_status"),
        "candidate": bool(manifest) and str(manifest.get("acceptance_status")) not in ACCEPTED_VARIANT_STATES,
        "selectable": not blockers,
        "blockers": blockers,
        "reference_slots": sorted(
            key for key in (profile.parameter_map or {}) if key.startswith("reference_")
        ) if profile is not None else [],
        "size_mapped": bool(profile is not None
                             and "width" in (profile.parameter_map or {})
                             and "height" in (profile.parameter_map or {})),
    }