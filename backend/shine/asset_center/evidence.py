"""Real-sample evidence in the asset library, in one place.

The asset centre and the material queue are two doors into the same renderer.
A validation sample produced through either door proves the same thing about the
same runtime fingerprint, so both readers must use the same rule - otherwise a
user validates in one workspace and is still refused in the other.

This module owns that rule. It only *reads*; promotion and production contracts
stay where they are.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from shine.asset_center.models import LibraryRenderTask, LibraryTaskAttempt, LibraryVersion


def validated_library_task(
    db,
    mode: str,
    fingerprint: str | None,
    *,
    category_family: str | None = None,
    require_alpha: bool = False,
):
    """The newest validation task that really produced reviewable bytes.

    A task row is a claim. The evidence is a non-mock version whose QC hash
    matches its bytes, written by an attempt whose output hash matches that
    version, submitted to a renderer this installation owns, against the same
    graph revision - all under the fingerprint being asked about.
    """
    if not fingerprint:
        return None
    from shine.asset_center.contracts import qwen_edit_adapter_hash

    modes = {mode}
    if mode == "RGBA":
        modes.add("T2I")
    rows = db.scalars(
        select(LibraryRenderTask)
        .where(
            LibraryRenderTask.kind == "image",
            LibraryRenderTask.mode.in_(modes),
            LibraryRenderTask.status == "CANDIDATE_READY",
            LibraryRenderTask.external_outcome == "SUCCEEDED",
            LibraryRenderTask.runtime_fingerprint == fingerprint,
        )
        .order_by(LibraryRenderTask.updated_at.desc(), LibraryRenderTask.id)
    ).all()
    for task in rows:
        payload = task.payload or {}
        if not payload.get("validation_only"):
            continue
        if category_family:
            category = str(payload.get("category") or "")
            family = "character" if category in {"character", "costume"} else category
            if family != category_family:
                continue
        if task.mode != "T2I" and payload.get("validation_adapter_hash") != qwen_edit_adapter_hash(mode):
            continue
        if require_alpha and not payload.get("transparent"):
            continue
        if mode == "INPAINT" and not (payload.get("mask") or {}).get("sha256"):
            continue
        if mode == "WARDROBE":
            roles = {str(row.get("role")) for row in payload.get("references") or []}
            if not {"subject", "garment"}.issubset(roles):
                continue
        if mode == "MULTIREF" and len(payload.get("references") or []) < 2:
            continue
        version = db.get(LibraryVersion, task.result_version_id) if task.result_version_id else None
        if version is None or version.is_mock or version.status not in {"READY_FOR_REVIEW", "PROMOTED"}:
            continue
        qc = version.qc or {}
        if qc.get("technical") != "pass" or qc.get("sha256") != version.content_sha256:
            continue
        if mode == "INPAINT" and (qc.get("mask_composite") or {}).get("applied_sha256") != (
            payload.get("mask") or {}
        ).get("sha256"):
            continue
        if require_alpha and not (qc.get("alpha") or {}).get("transparent_pixels", 0):
            continue
        attempt = db.scalar(
            select(LibraryTaskAttempt)
            .where(LibraryTaskAttempt.task_id == task.id, LibraryTaskAttempt.external_outcome == "SUCCEEDED")
            .order_by(LibraryTaskAttempt.attempt_no.desc())
            .limit(1)
        )
        if (
            attempt is not None
            and attempt.output_sha256 == version.content_sha256
            and (attempt.payload or {}).get("renderer_owned") is True
            and (mode == "T2I" or (attempt.payload or {}).get("adapter_hash") == qwen_edit_adapter_hash(mode))
            and (attempt.payload or {}).get("graph_sha256") == task.workflow_revision_hash
        ):
            return task
    return None


def validated_families(db, fingerprint: str | None) -> dict[str, str]:
    """Family -> evidence task id for every T2I family validated in the library."""
    families: dict[str, str] = {}
    for family in ("character", "scene", "prop"):
        task = validated_library_task(db, "T2I", fingerprint, category_family=family)
        if task is not None:
            families[family] = task.id
    return families


def version_is_clean(version: LibraryVersion | None, payload: dict[str, Any] | None = None) -> bool:
    """Convenience predicate used by callers that already hold the version."""
    if version is None or version.is_mock:
        return False
    qc = version.qc or {}
    if qc.get("technical") != "pass" or qc.get("sha256") != version.content_sha256:
        return False
    return not (payload or {}).get("overlay_findings")
