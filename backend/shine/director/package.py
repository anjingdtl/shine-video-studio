"""Build, inspect, confirm and invalidate the immutable director package.

The package is a *snapshot of identities*, never a copy of the assets: it stores
ids, revisions and hashes of objects that already exist under their own
governance.  It cannot approve an asset, freeze a style or apply a professional
proposal — those are user actions and stay outside this module.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director.basis import (
    current_script_basis,
    plan_basis_blockers,
)
from shine.director.basis import (
    outline_for_document as _outline_for_document,
)
from shine.director.contracts import (
    EpisodePlanPayload,
    PackagePolicyContract,
    continuity_mode,
    digest_of,
    planned_seconds,
    scope_digest,
    shot_order,
    validate_episode_coverage,
)
from shine.director.models import (
    DirectorEpisodePlan,
    DirectorPackageEpisode,
    DirectorPackageVersion,
)
from shine.models import (
    AgentArtifact,
    Asset,
    Project,
    Shot,
    StyleVersion,
    WorkflowProfile,
    utcnow,
)
from shine.schemas import ShotSpec

PACKAGE_SCHEMA_VERSION = "1.0"
DEFAULT_POLICY: dict[str, Any] = {
    "auto_repair": True,
    "max_repairs_per_shot": 2,
    "batch_shot_limit": 6,
    "split_continuity": True,
    "stop_on_blocker": False,
    "deliver_partial_episode": False,
}
"""The default execution policy a package freezes.

Values are recorded, not guessed at run time: a run executes the policy it
started with even if the project default changes later.
"""


def _style_snapshot(db: Session, project: Project) -> dict[str, Any] | None:
    from shine.director_styles.service import binding_summary

    if not project.frozen_style_version_id:
        return None
    style = db.get(StyleVersion, project.frozen_style_version_id)
    if style is None or style.project_id != project.id:
        return None
    return {
        "style_version_id": style.id,
        "version": style.version,
        "sha256": style.sha256,
        "director_style": binding_summary(style.director_style_binding),
    }


def _script_ref(db: Session, project: Project) -> dict[str, Any]:
    """The confirmed script identity, when the project has one.

    A project may legitimately have no imported document (ideas entered directly
    in the storyboard).  The package records ``None`` rather than inventing a
    script version.

    The outline is resolved *through* the current basis document, never by
    recency alone: an outline built from an older script must not be attached
    to a newer upload.
    """
    basis = current_script_basis(db, project)
    document = basis["document"]
    outline = _outline_for_document(db, project.id, document) if document is not None else None
    return {
        "document_id": document.id if document else None,
        "document_sha256": document.sha256 if document else None,
        "outline_artifact_id": outline.id if outline else None,
        "outline_sha256": outline.sha256 if outline else None,
        "outline_confirmed": bool(outline and outline.approved_at),
        "basis_explicit": basis["explicit"],
    }


def _binding_ref(db: Session, project_id: str) -> dict[str, Any] | None:
    from shine.comfyops.preproduction import current_plan

    plan = current_plan(db, project_id)
    if plan is None:
        return None
    return {"plan_id": plan.id, "status": plan.status, "sha256": plan.sha256}


def _shot_manifest(db: Session, shot: Shot) -> dict[str, Any]:
    spec = ShotSpec.model_validate(shot.spec)
    bindings = []
    for link in sorted(shot.bindings, key=lambda row: row.ordinal):
        asset = db.get(Asset, link.asset_id)
        bindings.append(
            {
                "asset_id": link.asset_id,
                "use_as": link.use_as,
                "role": link.role,
                "frame_role": link.frame_role,
                "ordinal": link.ordinal,
                "sha256": asset.sha256 if asset else None,
                "approved": bool(asset and asset.approved),
                "locked": bool(asset and asset.locked),
                "is_master": bool(asset and asset.approved and asset.locked),
            }
        )
    return {
        "shot_id": shot.id,
        "revision": shot.revision,
        "name": shot.name,
        "spec_sha256": digest_of(shot.spec),
        "duration_target_s": spec.duration_target_s,
        "style_version_id": spec.style_version_id,
        "director_style_resolution_sha256": spec.director_style_resolution_sha256,
        "director_style_technique_ids": spec.director_style_technique_ids,
        "continuity_mode": continuity_mode(shot),
        "characters": list(spec.characters),
        "dialogue_line_count": len(spec.dialogue),
        "bindings": bindings,
    }


def latest_confirmed_plan(db: Session, project_id: str) -> DirectorEpisodePlan | None:
    return db.scalars(
        select(DirectorEpisodePlan)
        .where(DirectorEpisodePlan.project_id == project_id, DirectorEpisodePlan.status == "CONFIRMED")
        .order_by(DirectorEpisodePlan.version.desc())
    ).first()


def validate_policy(policy: dict[str, Any] | None) -> PackagePolicyContract:
    """Validate a package policy at the boundary where it enters a freeze.

    An immutable package must not be able to freeze a policy it can never
    execute: unknown keys, wrong types and out-of-range values are refused
    here with a readable reason, not discovered by the Worker mid-run.
    """
    from pydantic import ValidationError

    try:
        return PackagePolicyContract.model_validate(policy or {})
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            problems.append(f"{location or 'policy'}: {error.get('msg', 'invalid')}")
        raise HTTPException(422, {
            "code": "DIRECTOR_PACKAGE_POLICY_INVALID",
            "problems": problems[:20],
        }) from exc


def latest_package(db: Session, project_id: str) -> DirectorPackageVersion | None:
    return db.scalars(
        select(DirectorPackageVersion)
        .where(DirectorPackageVersion.project_id == project_id)
        .order_by(DirectorPackageVersion.revision.desc())
    ).first()



def build_package(
    db: Session,
    project: Project,
    *,
    workflow_profile_id: str,
    plan: DirectorEpisodePlan | None = None,
    scope_shot_ids: list[str] | None = None,
    generation_mode: str | None = None,
    output: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    creative_lineage: dict[str, Any] | None = None,
    created_by: str = "user",
    settings=None,
) -> DirectorPackageVersion:
    """Snapshot the current confirmed project state into a new package revision.

    Building never mutates the underlying objects and never assumes that a state
    is executable: an incomplete package is still built (so the person can see
    exactly what is missing) and stays ``DRAFT``.

    ``creative_lineage`` is the four-stage pipeline's contract: when supplied,
    the package pins the exact scene scripts, blueprint and episode contracts
    it was built from, so a later upstream edit is detected by
    :func:`package_readiness` before a run starts or a batch is dispatched.
    """
    plan = plan or latest_confirmed_plan(db, project.id)
    if plan is None:
        raise HTTPException(409, "DIRECTOR_EPISODE_PLAN_NOT_CONFIRMED")

    shots = list(db.scalars(select(Shot).where(Shot.project_id == project.id)))
    if not shots:
        raise HTTPException(422, "DIRECTOR_PACKAGE_REQUIRES_SHOTS")
    by_id = {shot.id: shot for shot in shots}
    # A volume-scoped build validates coverage against the scope it was
    # handed, never against the whole project's shots.
    coverage_scope = list(scope_shot_ids or by_id.keys())
    plan_payload = EpisodePlanPayload.model_validate(plan.payload)
    basis_problems = plan_basis_blockers(db, project, plan_payload)
    if basis_problems:
        raise HTTPException(409, {
            "code": basis_problems[0]["code"],
            "message": basis_problems[0]["message"],
            "blockers": basis_problems,
        })
    if plan_payload.mode == "sections":
        # A script-first plan names its scope by sections; it may only be
        # packaged once every episode was materialised into the shots its
        # sections really produced.  Shots outside the scope are not part of
        # this production, so only unknown or missing plan shots are refused.
        if not plan_payload.materialized():
            raise HTTPException(409, {"code": "EPISODE_PLAN_NOT_MATERIALIZED",
                                      "message": "分集方案中的场次尚未全部拆成镜头"})
        unknown = [shot_id for shot_id in plan_payload.shot_order() if shot_id not in by_id]
        problems = (["PLAN_REFERENCES_UNKNOWN_SHOTS:" + ",".join(unknown)[:400]] if unknown else [])
    else:
        problems = validate_episode_coverage(plan_payload, coverage_scope)
    if problems:
        raise HTTPException(409, {"code": "DIRECTOR_EPISODE_PLAN_STALE", "problems": problems})

    profile = db.get(WorkflowProfile, workflow_profile_id)
    if profile is None or (profile.project_id not in (None, project.id)):
        raise HTTPException(404, "WORKFLOW_PROFILE_NOT_FOUND")

    # A candidate variant may only be packaged when this project really owns
    # it and this machine really checked it.  The package pins exactly one
    # identity, so a profile that cannot run here never reaches a package and
    # can never be silently replaced by the INT8 baseline.
    from shine.comfyops.profile_gate import profile_blockers, size_blockers

    # The package promises what the run will really do, so it reads the same
    # facts: the live machine (when the caller can provide it) and the output
    # size this package freezes.  A size the profile cannot map is refused
    # here, at the same boundary that names it later, instead of confirming a
    # package that the first batch dispatch would refuse (J09).
    gate_blockers = profile_blockers(
        db, settings, project.id, profile,
        require_runtime=settings is not None,
    )
    gate_blockers.extend(size_blockers(profile, output))
    if gate_blockers:
        first = gate_blockers[0]
        raise HTTPException(422 if first["code"] == "WORKFLOW_SIZE_NOT_MAPPED" else 409, {
            "code": first["code"],
            "message": first["message"],
            "blockers": gate_blockers,
            "details": {
                "missing_parameters": first.get("missing_parameters") or [],
                "mapped_parameters": first.get("mapped_parameters") or [],
            } if first["code"] == "WORKFLOW_SIZE_NOT_MAPPED" else None,
        })

    style = _style_snapshot(db, project)
    binding = {
        "workflow_profile_id": profile.id,
        "mode": profile.mode,
        "graph_sha256": profile.graph_sha256,
        "checkpoint_id": profile.checkpoint_id,
        "model_identity": profile.model_identity or {},
        "parameter_map": profile.parameter_map or {},
        "media_transfers": profile.media_transfers or {},
        "generation_mode": generation_mode,
        "output": output or {},
        "validated_against": profile.validated_against or {},
    }
    resolved_policy = {**DEFAULT_POLICY, **validate_policy(policy).model_dump(mode="json")}

    revision = (db.scalars(
        select(DirectorPackageVersion.revision)
        .where(DirectorPackageVersion.project_id == project.id)
        .order_by(DirectorPackageVersion.revision.desc())
        .limit(1)
    ).first() or 0) + 1

    episodes: list[dict[str, Any]] = []
    for planned in plan_payload.episodes:
        manifest_shots = [_shot_manifest(db, by_id[shot_id]) for shot_id in planned.shot_ids]
        episode_entry: dict[str, Any] = {
            "episode_id": planned.episode_id,
            "position": planned.order,
            "title": planned.title,
            "planned_seconds": planned.planned_seconds,
            "boundary_note": planned.boundary_note,
            "shots": manifest_shots,
        }
        if planned.section_ids:
            # The script range this episode covers: source section -> episode
            # -> shots, so coverage can be audited back to the original text.
            episode_entry["section_ids"] = list(planned.section_ids)
        if planned.episode_script_id:
            # Which confirmed contract produced this episode.  A delivery
            # whose episodes carry no contract was cut by the server, not
            # confirmed by the person, and the manifest has to say so.
            episode_entry["episode_script_id"] = planned.episode_script_id
            episode_entry["episode_script_sha256"] = planned.episode_script_sha256
        episodes.append(episode_entry)

    root = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "project_id": project.id,
        "revision": revision,
        "episode_plan": {
            "plan_id": plan.id,
            "version": plan.version,
            "sha256": plan.sha256,
            "target_seconds": plan_payload.target_seconds,
            "tolerance_seconds": plan_payload.tolerance_seconds,
            # The plan's own source travels into the frozen manifest: a package
            # that names B's script and A's plan is detectable after the fact,
            # not only at the moment it was built.
            "source": plan_payload.source or {},
        },
        "script": _script_ref(db, project),
        "style": style,
        "binding": binding,
        "preproduction": _binding_ref(db, project.id),
        "policy": resolved_policy,
        "episodes": [
            {
                "episode_id": episode["episode_id"],
                "position": episode["position"],
                "title": episode["title"],
                "planned_seconds": episode["planned_seconds"],
                "shot_count": len(episode["shots"]),
                "manifest_sha256": digest_of(episode),
            }
            for episode in episodes
        ],
    }
    root["scope_digest"] = scope_digest(
        project.id, episodes, style=style, binding=binding, script=root["script"]
    )
    if creative_lineage is not None:
        # The pinned creative lineage is inside the hashed root, so the
        # manifest digest covers it like every other frozen identity.
        root["creative_lineage"] = creative_lineage

    package = DirectorPackageVersion(
        project_id=project.id,
        revision=revision,
        status="DRAFT",
        manifest=root,
        scope_digest=root["scope_digest"],
        manifest_sha256=digest_of(root),
        policy_hash=digest_of(resolved_policy),
        episode_count=len(episodes),
        shot_count=sum(len(episode["shots"]) for episode in episodes),
        episode_plan_id=plan.id,
        created_by=created_by,
    )
    db.add(package)
    db.flush()
    for episode in episodes:
        db.add(
            DirectorPackageEpisode(
                package_id=package.id,
                project_id=project.id,
                episode_id=episode["episode_id"],
                position=episode["position"],
                title=episode["title"],
                manifest=episode,
                manifest_sha256=digest_of(episode),
                shot_count=len(episode["shots"]),
                planned_seconds=episode["planned_seconds"],
            )
        )
    db.flush()
    return package


def _blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


#: The parts of a workflow profile that decide what the renderer actually
#: receives.  ``validated_against`` and other declaration fields are excluded:
#: they describe how the profile was qualified, not what it executes.
EXECUTION_BINDING_FIELDS = (
    "mode",
    "graph_sha256",
    "checkpoint_id",
    "model_identity",
    "parameter_map",
    "media_transfers",
)


def _profile_execution_drift(pinned: dict[str, Any], profile: WorkflowProfile) -> list[str]:
    """Fields whose in-place edit invalidates the frozen execution promise."""
    changed: list[str] = []
    for field in EXECUTION_BINDING_FIELDS:
        current = getattr(profile, field, None)
        if field in {"model_identity", "parameter_map", "media_transfers"}:
            current = current or {}
        if pinned.get(field) != current:
            changed.append(field)
    return changed


def _compiled_shot_blockers(
    db: Session, project: Project, manifest: dict[str, Any], episodes: list[Any]
) -> list[dict[str, Any]]:
    """Re-run the one H3 compiler over every shot this package freezes.

    A package that says ``startable`` while the same shot, profile and mode is
    refused at job creation is a lie: the person waits for a run that cannot
    start.  The readiness check therefore executes the same compiler with the
    same profile constraints the worker will use, and names the shot, the mode
    and the missing slot before anything is queued.

    A dynamic continuity reference is *dependency waiting*, not a static
    impossibility: it can only be compiled once the real predecessor passed.
    """
    from shine.comfy.capabilities import continuity_capability, profile_metadata
    from shine.h3.compiler import H3PromptCompiler, PromptCompilationError
    from shine.schemas import GenerationMode, ShotBindingInput, StyleBible
    from shine.services import build_reference_manifest, order_bindings_for_cast

    binding = manifest.get("binding") or {}
    profile = db.get(WorkflowProfile, binding.get("workflow_profile_id") or "")
    pinned_style = manifest.get("style") or {}
    style_row = db.get(StyleVersion, pinned_style.get("style_version_id") or "")
    if profile is None or style_row is None:
        return []
    try:
        bible = StyleBible.model_validate(style_row.snapshot)
    except Exception:
        return [_blocker("STYLE_SNAPSHOT_INVALID", "冻结风格的快照无法解析，请重新冻结风格")]

    mode_hint = None
    if binding.get("generation_mode"):
        try:
            mode_hint = GenerationMode(binding["generation_mode"])
        except ValueError:
            return [_blocker("PACKAGE_GENERATION_MODE_INVALID",
                             "导演包记录的生片模式无效", mode=binding["generation_mode"])]

    compiler = H3PromptCompiler()
    problems: list[dict[str, Any]] = []
    for episode in episodes:
        for entry in (episode.manifest or {}).get("shots", []):
            shot = db.get(Shot, entry.get("shot_id") or "")
            if shot is None or shot.revision != entry.get("revision"):
                continue  # already reported as SHOT_MISSING / SHOT_REVISION_CHANGED
            try:
                spec = ShotSpec.model_validate(shot.spec)
            except Exception:
                continue
            if spec.continuity.mode.value != "cut":
                problems.append(_blocker(
                    "CONTINUITY_DEPENDENCY_WAITING",
                    f"镜头「{shot.name}」等待前序成片，属于依赖等待，不是静态缺口",
                    shot_id=shot.id,
                    dependency_only=True,
                ))
                continue
            assets: dict[str, Any] = {}
            bindings = []
            for link in entry.get("bindings", []):
                asset = db.get(Asset, link.get("asset_id") or "")
                if asset is None:
                    break
                assets[asset.id] = asset
                bindings.append(ShotBindingInput(
                    asset_id=asset.id,
                    use_as=link["use_as"],
                    role=link.get("role"),
                    frame_role=link.get("frame_role"),
                    ordinal=link.get("ordinal", 0),
                ))
            if len(assets) != len(entry.get("bindings", [])):
                continue  # ASSET_MISSING already reported
            try:
                ordered = order_bindings_for_cast(bindings, list(spec.characters), assets)
                reference_manifest = build_reference_manifest(ordered, assets)
                compiled = compiler.compile(
                    spec=spec, style=bible, manifest=reference_manifest,
                    project_language=project.language, mode_hint=mode_hint,
                )
            except PromptCompilationError as exc:
                problems.append(_blocker(
                    exc.code,
                    f"镜头「{shot.name}」按当前生成方案无法执行：{exc}",
                    shot_id=shot.id,
                    mode=profile.mode,
                    details=exc.details,
                ))
                continue
            if profile.mode and compiled.mode.value.lower() != profile.mode.lower():
                problems.append(_blocker(
                    "H3_MODE_REFERENCE_MISMATCH",
                    f"镜头「{shot.name}」编译出 {compiled.mode.value}，生成方案登记的是 {profile.mode}",
                    shot_id=shot.id,
                    mode=profile.mode,
                    compiled_mode=compiled.mode.value,
                ))
                continue
            try:
                from shine.comfy.provider import validate_reference_slots

                validate_reference_slots(profile.parameter_map or {}, reference_manifest)
            except Exception as exc:  # ComfyPendingWorkflowMapping
                problems.append(_blocker(
                    "PENDING_WORKFLOW_MAPPING",
                    f"镜头「{shot.name}」的参考素材在工作流里没有落点",
                    shot_id=shot.id,
                    details=getattr(exc, "details", {}),
                ))
                continue
            capability = continuity_capability(spec.continuity.mode.value)
            if capability:
                capabilities = profile_metadata(
                    profile.graph, profile.mode, profile.parameter_map, profile.media_transfers
                )
                if capability not in capabilities["continuity_modes"]:
                    problems.append(_blocker(
                        "PENDING_HARD_CONTINUITY_TEST" if capability == "hard_frame_anchor"
                        else "PENDING_WORKFLOW_MAPPING",
                        f"镜头「{shot.name}」请求的连续性能力尚未在生成方案里验证",
                        shot_id=shot.id,
                        requested_continuity=capability,
                    ))
    return problems


def _execution_fingerprint(
    manifest: dict[str, Any], policy_hash: str, project_language: str, episodes: list[Any],
    drifting_fields: list[str] | None = None,
    shot_drift: list[str] | None = None,
    binding_drift: list[str] | None = None,
) -> str:
    """The complete execution identity a package freezes.

    It covers every field that can change what lands on disk: workflow graph,
    checkpoint, model, parameter map, media transfers, the chosen mode and
    output, the style, the pre-production manifest, the policy, the project
    language and every shot's own revision and reference set.
    """
    binding = manifest.get("binding") or {}
    shots = [
        {
            "shot_id": shot.get("shot_id"),
            "revision": shot.get("revision"),
            "spec_sha256": shot.get("spec_sha256"),
            "style_version_id": shot.get("style_version_id"),
            "bindings": [
                {
                    "asset_id": link.get("asset_id"),
                    "use_as": link.get("use_as"),
                    "role": link.get("role"),
                    "frame_role": link.get("frame_role"),
                    "ordinal": link.get("ordinal"),
                    "sha256": link.get("sha256"),
                }
                for link in shot.get("bindings", [])
            ],
        }
        for episode in episodes
        for shot in (episode.manifest or {}).get("shots", [])
    ]
    return digest_of({
        "graph": binding.get("graph_sha256"),
        "checkpoint": binding.get("checkpoint_id"),
        "model": binding.get("model_identity") or {},
        "parameter_map": binding.get("parameter_map") or {},
        "media_transfers": binding.get("media_transfers") or {},
        "profile_mode": binding.get("mode"),
        "generation_mode": binding.get("generation_mode"),
        "output": binding.get("output") or {},
        "style": (manifest.get("style") or {}).get("sha256"),
        "director_style": (manifest.get("style") or {}).get("director_style"),
        "preproduction": (manifest.get("preproduction") or {}).get("sha256"),
        "policy": policy_hash,
        "project_language": project_language,
        "episodes": [
            {"episode_id": row.episode_id, "position": row.position, "manifest_sha256": row.manifest_sha256}
            for row in episodes
        ],
        "shots": shots,
        # Whether the live profile still matches what was frozen is part of the
        # execution identity the workbench compares: a silent drift must change
        # the number, not only a blocker list.
        "drifting_fields": sorted(drifting_fields or []),
        "shot_drift": sorted(shot_drift or []),
        "binding_drift": sorted(binding_drift or []),
    })


def package_readiness(db: Session, package: DirectorPackageVersion) -> dict[str, Any]:
    """Everything that must be true before this package may really be started.

    The checks are read-only and repeatable.  A missing dynamic last frame is a
    *declared dependency*, not a readiness failure: the final request is built
    from the real passed predecessor at submit time by the one compiler.
    """
    project = db.get(Project, package.project_id)
    blockers: list[dict[str, Any]] = []
    manifest = package.manifest or {}

    if project is None:
        blockers.append(_blocker("PROJECT_MISSING", "作品已不存在"))
    if package.stale:
        blockers.append(_blocker("PACKAGE_STALE", package.stale_reason or "导演包已失效"))
    if package.status == "DRAFT":
        blockers.append(_blocker("PACKAGE_NOT_CONFIRMED", "导演包尚未确认"))

    # Integrity of the frozen content itself.  The pinned hashes are the whole
    # point of an immutable package: a root manifest whose bytes no longer
    # hash to the pinned value — whatever wrote it — is not the contract the
    # user confirmed, and the policy/scope hashes must agree with it too.
    if digest_of(manifest) != package.manifest_sha256:
        blockers.append(_blocker("PACKAGE_MANIFEST_TAMPERED",
                                 "导演包根清单与固定哈希不一致，拒绝执行"))
    else:
        if package.scope_digest != manifest.get("scope_digest"):
            blockers.append(_blocker("PACKAGE_SCOPE_TAMPERED",
                                     "导演包范围摘要与根清单不一致，拒绝执行"))
        if package.policy_hash != digest_of(manifest.get("policy") or {}):
            blockers.append(_blocker("PACKAGE_POLICY_TAMPERED",
                                     "导演包政策摘要与根清单不一致，拒绝执行"))

    style = manifest.get("style")
    if not style:
        blockers.append(_blocker("STYLE_NOT_FROZEN", "尚未冻结作品风格，无法开拍"))
    elif project is not None:
        pinned_style_id = style.get("style_version_id") or ""
        current = db.get(StyleVersion, pinned_style_id)
        if (project.frozen_style_version_id != pinned_style_id
                or current is None or current.sha256 != style.get("sha256")):
            blockers.append(_blocker("STYLE_SUPERSEDED", "冻结风格已变更，请重新生成导演包"))
        else:
            from shine.director_styles.service import binding_summary

            if style.get("director_style") != binding_summary(current.director_style_binding):
                blockers.append(_blocker("STYLE_PROFILE_SUPERSEDED", "导演风格来源快照已变化，请重新生成导演包"))

    script = manifest.get("script") or {}
    plan_source = ((manifest.get("episode_plan") or {}).get("source") or {})
    if (script.get("outline_artifact_id") and plan_source.get("outline_artifact_id")
            and plan_source["outline_artifact_id"] != script["outline_artifact_id"]):
        blockers.append(_blocker(
            "PACKAGE_SCRIPT_PLAN_MISMATCH",
            "导演包里的分集方案来自另一版剧本大纲，和它声明的剧本不一致；请重新生成分集与导演包",
            plan_outline_artifact_id=plan_source.get("outline_artifact_id"),
            script_outline_artifact_id=script.get("outline_artifact_id"),
        ))
    if project is not None and script.get("document_id"):
        basis = current_script_basis(db, project)
        live_document = basis["document"]
        if (
            live_document is None
            or live_document.id != script.get("document_id")
            or live_document.sha256 != script.get("document_sha256")
        ):
            # The person picked another basis (or the document bytes changed)
            # after this package was frozen: rendering it would produce a
            # superseded script under the identity that was confirmed.
            blockers.append(_blocker(
                "SCRIPT_BASIS_CHANGED",
                "这份导演包绑定的剧本已不是当前选用的剧本；请重新生成导演包",
                document_id=script.get("document_id"),
                current_document_id=live_document.id if live_document else None,
            ))
        elif script.get("outline_artifact_id"):
            frozen_outline = db.get(AgentArtifact, script["outline_artifact_id"])
            if (frozen_outline is None or frozen_outline.stale
                    or frozen_outline.sha256 != script.get("outline_sha256")):
                blockers.append(_blocker(
                    "SCRIPT_OUTLINE_CHANGED", "导演包绑定的剧本大纲已失效或变更，请重新生成导演包",
                    outline_artifact_id=script.get("outline_artifact_id"),
                ))

    profile_row = db.get(WorkflowProfile, (manifest.get("binding") or {}).get("workflow_profile_id") or "")
    drift: list[str] = []
    if profile_row is None:
        blockers.append(_blocker("WORKFLOW_PROFILE_MISSING", "生成方案已不存在"))
    else:
        drift = _profile_execution_drift(manifest.get("binding") or {}, profile_row)
        if drift:
            # The pin is the whole execution identity: a graph hash alone does
            # not decide what the renderer receives.  A parameter map edited in
            # place after confirmation drifts the frozen promise silently.
            blockers.append(_blocker(
                "WORKFLOW_PROFILE_CHANGED",
                "生成方案的执行身份已变更，请重新生成导演包",
                changed_fields=drift,
            ))
        elif not (profile_row.parameter_map or {}):
            blockers.append(_blocker("WORKFLOW_PARAMETER_MAP_MISSING", "生成方案未声明参数映射"))
        else:
            from shine.comfyops.profile_gate import size_blockers

            blockers.extend(size_blockers(profile_row, manifest.get("binding", {}).get("output")))

    # The frozen output is a promise about the delivered clip: it has to
    # satisfy the same H3 output contract the Series and Job entries enforce.
    # A draft may show this gap; a confirmation or a start may not.
    from shine.comfyops.profile_gate import output_contract_blockers

    blockers.extend(output_contract_blockers((manifest.get("binding") or {}).get("output")))

    from shine.comfyops.preproduction import current_plan

    plan = current_plan(db, package.project_id)
    pinned = manifest.get("preproduction") or {}
    if plan is not None and plan.status != "FROZEN":
        # The same rule ``comfyops.preproduction.enforce`` applies to every real
        # job: an analysed plan must be frozen before it may govern a render.
        # A project without a plan is a legacy project and keeps that contract;
        # the package does not invent a stricter gate than the gate itself.
        blockers.append(_blocker("PREPRODUCTION_NOT_FROZEN", "制片准备计划尚未冻结"))
    elif plan is not None and pinned.get("sha256") and plan.sha256 != pinned.get("sha256"):
        blockers.append(_blocker("PREPRODUCTION_CHANGED", "制片准备计划已变更，请重新生成导演包"))

    episodes = db.scalars(
        select(DirectorPackageEpisode)
        .where(DirectorPackageEpisode.package_id == package.id)
        .order_by(DirectorPackageEpisode.position)
    ).all()
    if not episodes:
        blockers.append(_blocker("PACKAGE_HAS_NO_EPISODES", "导演包没有分集"))
    # Static impossibility is named before anything is queued.  A dynamic
    # continuity reference stays a declared dependency instead of a refusal.
    compile_problems = _compiled_shot_blockers(db, project, manifest, episodes)
    blockers.extend(compile_problems)
    lineage = manifest.get("creative_lineage") or {}
    if not lineage:
        # A four-stage delivery that predates the lineage contract cannot
        # enter new production without a re-delivery.  An ordinary director
        # package keeps its original path.
        plan_row = (db.get(DirectorEpisodePlan, package.episode_plan_id)
                    if package.episode_plan_id else None)
        if plan_row is not None and plan_row.source == "four_stage_volume":
            blockers.append(_blocker("CREATIVE_LINEAGE_MISSING",
                                     "这份四阶段交付缺少上游依据，进入新生产前请重新交付本卷"))
    else:
        from shine.director.pipeline import (
            bible_basis_blockers,
            current_bible,
            current_lineage_digest,
        )

        current = current_lineage_digest(db, package.project_id, lineage.get("volume_id") or "")
        if current is None:
            blockers.append(_blocker("CREATIVE_LINEAGE_UNREADABLE",
                                     "本卷的上游剧本已无法解析，请重新交付本卷"))
        elif current != lineage.get("sha256"):
            # An upstream scene, blueprint or episode contract changed after
            # this package was confirmed: the frozen scope no longer describes
            # the current work, so starting or dispatching it would render a
            # superseded script.  The history stays readable; only new
            # production is refused.
            blockers.append(_blocker("CREATIVE_LINEAGE_CHANGED",
                                     "上游剧本或分集契约已变更，请重新交付本卷后再开拍"))
        bible = current_bible(db, package.project_id)
        if bible is not None and bible_basis_blockers(db, package.project_id, bible):
            # The blueprint must still describe the current outlines before
            # a package built on it may be produced.
            blockers.append(_blocker("CREATIVE_BASIS_CHANGED",
                                     "上游蓝图与当前大纲已不一致，请重新生成蓝图并重新交付本卷"))
    shot_drift: list[str] = []
    binding_drift: list[str] = []
    for episode in episodes:
        if digest_of(episode.manifest) != episode.manifest_sha256:
            blockers.append(
                _blocker("EPISODE_MANIFEST_TAMPERED", f"{episode.title or episode.episode_id} 的分集清单被改动",
                         episode_id=episode.episode_id)
            )
        for shot in episode.manifest.get("shots", []):
            row = db.get(Shot, shot.get("shot_id") or "")
            if row is None:
                blockers.append(_blocker("SHOT_MISSING", f"镜头「{shot.get('name')}」已不存在",
                                         shot_id=shot.get("shot_id")))
                continue
            if row.revision != shot.get("revision"):
                shot_drift.append(row.id)
                blockers.append(_blocker("SHOT_REVISION_CHANGED", f"镜头「{row.name}」已修改",
                                         shot_id=row.id))
            current_links = sorted(
                (link.asset_id, link.use_as, link.role, link.frame_role, link.ordinal)
                for link in row.bindings
            )
            pinned_links = sorted(
                (link.get("asset_id"), link.get("use_as"), link.get("role"),
                 link.get("frame_role"), link.get("ordinal"))
                for link in shot.get("bindings", [])
            )
            if current_links != pinned_links:
                # The frozen reference set is the contract: a binding edited in
                # place (even without a revision bump by an older client) makes
                # the confirmed package describe a different film.
                binding_drift.append(row.id)
                blockers.append(_blocker(
                    "SHOT_BINDINGS_CHANGED",
                    f"镜头「{row.name}」的参考绑定与导演包不一致，请重新生成导演包",
                    shot_id=row.id,
                ))
            if style and shot.get("style_version_id") != style.get("style_version_id"):
                blockers.append(_blocker("SHOT_STYLE_MISMATCH", f"镜头「{row.name}」不执行当前冻结风格",
                                         shot_id=row.id))
            bound_characters = []
            for link in shot.get("bindings", []):
                asset = db.get(Asset, link.get("asset_id") or "")
                if asset is None:
                    blockers.append(_blocker("ASSET_MISSING", "参考素材已不存在",
                                             shot_id=shot.get("shot_id"), asset_id=link.get("asset_id")))
                elif not (asset.approved and asset.locked):
                    blockers.append(_blocker("ASSET_NOT_MASTER", f"素材「{asset.filename}」尚未确认并锁定",
                                             shot_id=shot.get("shot_id"), asset_id=asset.id))
                else:
                    from shine.comfyops.assets import rights_status

                    rights = rights_status(
                        db, package.project_id, (asset.provenance or {}).get("rights_id"))
                    if not rights["approved"]:
                        blockers.append(_blocker(
                            "ASSET_RIGHTS_NOT_APPROVED",
                            f"素材「{asset.filename}」的授权已经不在有效状态：{rights['message']}",
                            shot_id=shot.get("shot_id"), asset_id=asset.id,
                            rights_code=rights["code"],
                            action="REPLACE_WITH_AUTHORIZED_VERSION",
                        ))
                    bound_characters.append({"id": asset.id, "kind": asset.kind,
                                             "role": asset.role, "entity_id": asset.entity_id})
            # A character the shot names without a bound master is a material
            # requirement, not a production blocker: the picture is optional
            # and H3 generates it.  A reference that IS bound still has to be
            # an approved master (checked above).

    # A frozen clip length has to fit the dialogue and the internal cuts of
    # every shot it would shorten.  The Series entry refuses the same fact by
    # name, so the package and the dispatch always agree.
    frozen_clip = ((manifest.get("binding") or {}).get("output") or {}).get("clip_seconds")
    if frozen_clip:
        from shine.comfyops.production_series import clip_seconds_conflict

        for episode in episodes:
            for entry in (episode.manifest or {}).get("shots", []):
                row = db.get(Shot, entry.get("shot_id") or "")
                if row is None:
                    continue
                try:
                    spec = ShotSpec.model_validate(row.spec)
                except Exception:
                    continue
                conflict = clip_seconds_conflict(row.name, spec, float(frozen_clip))
                if conflict is not None:
                    blockers.append(_blocker(
                        conflict["code"], conflict["message"],
                        shot_id=row.id, **conflict["details"],
                    ))

    fingerprint = _execution_fingerprint(
        manifest, package.policy_hash, project.language if project is not None else "", list(episodes),
        drifting_fields=drift, shot_drift=shot_drift, binding_drift=binding_drift,
    )
    # ``ready`` answers "is the content satisfiable right now" — everything a
    # confirmation would have to fix.  ``startable`` additionally requires the
    # user's confirmation, because a confirmed package is a separate decision
    # from a satisfiable one.  A pending continuity dependency is not a
    # readiness failure: the compiler runs it against the real predecessor.
    hard = [
        row for row in blockers
        if row["code"] != "PACKAGE_NOT_CONFIRMED" and not row.get("dependency_only")
    ]
    return {
        "package_id": package.id,
        "revision": package.revision,
        "status": package.status,
        "stale": package.stale,
        "scope_digest": package.scope_digest,
        "manifest_sha256": package.manifest_sha256,
        "config_fingerprint": fingerprint,
        "ready": not hard,
        "startable": not hard and not package.stale and package.status == "CONFIRMED",
        "blockers": hard,
        "pending_dependencies": [row for row in blockers if row.get("dependency_only")],
        "episode_count": len(episodes),
        "shot_count": package.shot_count,
        "checked_at": utcnow().isoformat() + "Z",
    }


def confirm_package(db: Session, package: DirectorPackageVersion, *, actor: str) -> DirectorPackageVersion:
    if package.status == "CONFIRMED":
        return package
    if package.stale:
        raise HTTPException(409, "DIRECTOR_PACKAGE_STALE")
    readiness = package_readiness(db, package)
    hard = [row for row in readiness["blockers"] if row["code"] not in {"PACKAGE_NOT_CONFIRMED"}]
    if hard:
        raise HTTPException(409, {"code": "DIRECTOR_PACKAGE_NOT_READY", "blockers": hard})
    package.status = "CONFIRMED"
    package.confirmed_by = actor
    package.confirmed_at = utcnow()
    db.flush()
    return package


def mark_package_stale(db: Session, package: DirectorPackageVersion, reason: str) -> bool:
    """Invalidate a package without rewriting what was confirmed."""
    if package.stale and package.stale_reason == reason:
        return False
    package.stale = True
    package.stale_reason = reason[:200]
    return True


def package_public(
    package: DirectorPackageVersion, *, episodes: list[DirectorPackageEpisode] | None = None
) -> dict[str, Any]:
    manifest = package.manifest or {}
    return {
        "id": package.id,
        "project_id": package.project_id,
        "revision": package.revision,
        "status": package.status,
        "stale": package.stale,
        "stale_reason": package.stale_reason,
        "scope_digest": package.scope_digest,
        "manifest_sha256": package.manifest_sha256,
        "policy_hash": package.policy_hash,
        "episode_count": package.episode_count,
        "shot_count": package.shot_count,
        "episode_plan": manifest.get("episode_plan"),
        "script": manifest.get("script"),
        "style": manifest.get("style"),
        "binding": manifest.get("binding"),
        "policy": manifest.get("policy"),
        "creative_lineage": manifest.get("creative_lineage"),
        "episodes": [
            {
                "episode_id": row.episode_id,
                "position": row.position,
                "title": row.title,
                "shot_count": row.shot_count,
                "planned_seconds": row.planned_seconds,
                "manifest_sha256": row.manifest_sha256,
            }
            for row in (episodes or [])
        ],
        "created_at": package.created_at.isoformat() + "Z",
        "confirmed_at": package.confirmed_at.isoformat() + "Z" if package.confirmed_at else None,
    }


def package_episodes(db: Session, package_id: str) -> list[DirectorPackageEpisode]:
    return list(db.scalars(
        select(DirectorPackageEpisode)
        .where(DirectorPackageEpisode.package_id == package_id)
        .order_by(DirectorPackageEpisode.position)
    ).all())


def episode_manifest(package_episode: DirectorPackageEpisode) -> dict[str, Any]:
    """The referenced sub-manifest, re-verified against the root's pinned hash."""
    if digest_of(package_episode.manifest) != package_episode.manifest_sha256:
        raise HTTPException(409, "DIRECTOR_EPISODE_MANIFEST_TAMPERED")
    return package_episode.manifest


def ordered_shot_ids(shots: list[Shot]) -> list[str]:
    return [shot.id for shot in shot_order(shots)]


def planned_total_seconds(shots: list[Shot]) -> float:
    return round(sum(planned_seconds(shot) for shot in shots), 3)


def hash_file_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
