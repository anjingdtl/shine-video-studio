"""Execute global image candidates through Shine's existing Worker and GPU lease.

T2I uses the pinned Qwen T2I graph. Edit modes use the separately pinned native
Qwen edit graph only after live node validation; no mode silently falls back to
T2I. This module never starts another renderer or worker.
"""

from __future__ import annotations

import hashlib
import mimetypes
import time
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from shine.asset_center import service
from shine.asset_center.character_sheet import (
    CHARACTER_SHEET_BASE_SAMPLING,
    CHARACTER_SHEET_LAYOUT_VERSION,
    CHARACTER_SHEET_LORA_SAMPLING,
    CHARACTER_SHEET_OPERATION,
    CHARACTER_SHEET_SOURCE_MODE,
    STEP_SPECS,
    build_step_prompts,
    compose_character_sheet,
    step_dimensions,
)
from shine.asset_center.contracts import (
    QWEN_EDIT_COMMIT,
    build_qwen_edit_graph,
    canonical_hash,
    composite_masked_png,
    cutout_png_by_mask,
    qwen_edit_adapter_hash,
    validate_rgba_output,
)
from shine.asset_center.models import (
    LibraryAsset,
    LibraryMedia,
    LibraryRenderTask,
    LibraryRights,
    LibraryTaskAttempt,
    LibraryVersion,
)
from shine.comfy.client import ComfyUIClient
from shine.comfy.workflow import graph_hash
from shine.comfyops.loras import StyleLoraError, image_style_payload, resolve_live_image_loras
from shine.comfyops.scheduler import resource_slot
from shine.materialprep import execution as image_execution
from shine.materialprep import qwen_workflow
from shine.models import utcnow
from shine.security import probe_upload, verify_upload
from shine.services import add_audit

_IMAGE_BUDGET_MB = image_execution.IMAGE_BUDGET_MB
_POLL_INTERVAL_S = image_execution.POLL_INTERVAL_S
_POLL_LIMIT = image_execution.POLL_LIMIT


def _claim_next(factory, worker_id: str) -> str | None:
    with factory() as db:
        row = db.scalar(
            select(LibraryRenderTask)
            .where(
                LibraryRenderTask.kind == "image",
                LibraryRenderTask.mode.in_({"T2I", "I2I", "INPAINT", "MULTIREF", "WARDROBE", "RGBA"}),
                LibraryRenderTask.status == "QUEUED",
                LibraryRenderTask.external_outcome == "NOT_SENT",
            )
            .order_by(LibraryRenderTask.created_at, LibraryRenderTask.id)
            .limit(1)
        )
        if row is None:
            return None
        result = db.execute(
            update(LibraryRenderTask)
            .where(
                LibraryRenderTask.id == row.id,
                LibraryRenderTask.state_version == row.state_version,
                LibraryRenderTask.status == "QUEUED",
                LibraryRenderTask.external_outcome == "NOT_SENT",
            )
            .values(
                status="RUNNING",
                worker_id=worker_id,
                heartbeat_at=utcnow(),
                state_version=row.state_version + 1,
                updated_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        db.commit()
        return row.id


def _load_task(factory, task_id: str) -> tuple[dict[str, Any], int]:
    with factory() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None:
            raise RuntimeError("LIBRARY_TASK_MISSING")
        return dict(task.payload or {}), task.state_version


def _heartbeat(factory, task_id: str, worker_id: str, expected_state: int) -> None:
    with factory.begin() as db:
        db.execute(
            update(LibraryRenderTask)
            .where(
                LibraryRenderTask.id == task_id,
                LibraryRenderTask.worker_id == worker_id,
                LibraryRenderTask.state_version == expected_state,
                LibraryRenderTask.status == "RUNNING",
            )
            .values(heartbeat_at=utcnow(), updated_at=utcnow())
        )


def _open_attempt(
    factory,
    task_id: str,
    worker_id: str,
    payload: dict[str, Any],
    graph_sha: str,
    fingerprint: str,
    width: int,
    height: int,
    seed: int,
    renderer: dict[str, Any],
    prompt_inputs: dict[str, Any] | None = None,
) -> tuple[str, int]:
    with factory() as db:
        task_row = db.get(LibraryRenderTask, task_id)
        request_hash = task_row.request_hash if task_row else ""
    snapshot = canonical_hash(
        {
            "task_id": task_id,
            "request_hash": request_hash,
            "payload": payload,
            "graph_sha256": graph_sha,
            "runtime_fingerprint": fingerprint,
            "output_node_id": qwen_workflow.OUTPUT_NODE,
            "seed": seed,
            "width": width,
            "height": height,
        }
    )
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None or task.status != "RUNNING" or task.worker_id != worker_id:
            raise RuntimeError("LIBRARY_TASK_CLAIM_LOST")
        if task.external_outcome != "NOT_SENT" or task.submit_intent_at is not None:
            raise RuntimeError("LIBRARY_TASK_ALREADY_SUBMITTED")
        attempt_no = (
            db.scalar(
                select(LibraryTaskAttempt.attempt_no)
                .where(LibraryTaskAttempt.task_id == task_id)
                .order_by(LibraryTaskAttempt.attempt_no.desc())
                .limit(1)
            )
            or 0
        ) + 1
        attempt = LibraryTaskAttempt(
            task_id=task_id,
            attempt_no=attempt_no,
            snapshot_hash=snapshot,
            submit_stage="SUBMIT_INTENT",
            status="SUBMIT_INTENT",
            external_outcome="NOT_SENT",
            payload={
                "graph_sha256": graph_sha,
                "runtime_fingerprint": fingerprint,
                "output_node_id": qwen_workflow.OUTPUT_NODE,
                "output_class_type": qwen_workflow.OUTPUT_CLASS_TYPE,
                "model_revision": qwen_workflow.MODEL_REVISION,
                "template_commit": QWEN_EDIT_COMMIT if payload.get("mode") != "T2I" else qwen_workflow.TEMPLATE_COMMIT,
                "adapter_hash": qwen_edit_adapter_hash(str(payload.get("mode")))
                if payload.get("mode") in {"I2I", "INPAINT", "MULTIREF", "WARDROBE", "RGBA"}
                else None,
                "seed": seed,
                "width": width,
                "height": height,
                "renderer_owned": bool(renderer.get("renderer_owned")),
                "renderer_instance_id": str(renderer.get("renderer_instance_id") or ""),
                **image_style_payload(payload),
                **({key: prompt_inputs[key] for key in ("prompt", "negative_prompt")} if prompt_inputs else {}),
            },
        )
        db.add(attempt)
        task.submit_intent_at = utcnow()
        task.workflow_revision_hash = graph_sha
        task.runtime_fingerprint = fingerprint
        task.state_version += 1
        task.heartbeat_at = utcnow()
        task.updated_at = utcnow()
        db.flush()
        return attempt.id, task.state_version


def _open_local_attempt(
    factory, task_id: str, worker_id: str, payload: dict[str, Any], operation_hash: str, expected_state: int
) -> str:
    with factory() as db:
        task_row = db.get(LibraryRenderTask, task_id)
        request_hash = task_row.request_hash if task_row else ""
    snapshot_hash = canonical_hash(
        {"task_id": task_id, "request_hash": request_hash, "operation_hash": operation_hash, "payload": payload}
    )
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if (
            task is None
            or task.status != "RUNNING"
            or task.worker_id != worker_id
            or task.state_version != expected_state
            or task.submit_intent_at is not None
            or task.external_outcome != "NOT_SENT"
        ):
            raise RuntimeError("LIBRARY_LOCAL_TASK_CLAIM_LOST")
        attempt_no = (
            db.scalar(
                select(LibraryTaskAttempt.attempt_no)
                .where(LibraryTaskAttempt.task_id == task_id)
                .order_by(LibraryTaskAttempt.attempt_no.desc())
                .limit(1)
            )
            or 0
        ) + 1
        attempt = LibraryTaskAttempt(
            task_id=task_id,
            attempt_no=attempt_no,
            snapshot_hash=snapshot_hash,
            submit_stage="LOCAL_OPERATION",
            status="RUNNING",
            external_outcome="NOT_SENT",
            payload={"operation": "MASK_CUTOUT_V1", "operation_hash": operation_hash},
        )
        db.add(attempt)
        task.workflow_revision_hash = operation_hash
        task.state_version += 1
        task.heartbeat_at = utcnow()
        task.updated_at = utcnow()
        db.flush()
        return attempt.id


def _set_attempt_and_task(
    factory,
    task_id: str,
    attempt_id: str,
    *,
    task_status: str,
    attempt_status: str,
    outcome: str,
    error_code: str | None = None,
    prompt_id: str | None = None,
    output_sha256: str | None = None,
    result_version_id: str | None = None,
    expected_state: int | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        attempt = db.get(LibraryTaskAttempt, attempt_id)
        if task is None or attempt is None:
            return None
        if expected_state is not None and task.state_version != expected_state:
            return None
        task.status = task_status
        task.external_outcome = outcome
        task.external_prompt_id = prompt_id or task.external_prompt_id
        task.error_code = error_code
        task.result_version_id = result_version_id
        task.state_version += 1
        task.heartbeat_at = utcnow()
        task.updated_at = utcnow()
        if task_status in {"CANDIDATE_READY", "QC_FAILED", "FAILED", "UNKNOWN", "BLOCKED_CAPABILITY", "CANCELLED"}:
            task.worker_id = None
            task.lease_owner = None
        attempt.status = attempt_status
        attempt.external_outcome = outcome
        attempt.external_prompt_id = prompt_id or attempt.external_prompt_id
        attempt.error_code = error_code
        attempt.output_sha256 = output_sha256
        attempt.submit_stage = task_status
        if details:
            attempt.payload = {**(attempt.payload or {}), "details": {**details}}
        return task.state_version


def _poll_with_heartbeat(
    client: ComfyUIClient, factory, task_id: str, worker_id: str, state_version: int, prompt_id: str
) -> dict[str, Any]:
    for index in range(_POLL_LIMIT):
        history = client.history(prompt_id)
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise image_execution.ImageExecutionError("IMAGE_EXECUTION_FAILED", prompt_id=prompt_id)
            if entry.get("outputs") or status.get("completed"):
                return entry
        if index and index % 15 == 0:
            _heartbeat(factory, task_id, worker_id, state_version)
        time.sleep(_POLL_INTERVAL_S)
    raise image_execution.ImageExecutionError("IMAGE_OUTCOME_RECONCILIATION_REQUIRED", prompt_id=prompt_id)


def _save_candidate(
    factory,
    settings,
    task_id: str,
    payload: dict[str, Any],
    content: bytes,
    graph_sha: str,
    fingerprint: str,
    prompt_id: str,
    renderer: dict[str, Any],
    attempt_id: str | None = None,
) -> tuple[str, str, str]:
    filename = f"shine-assets-{task_id[:8]}.png"
    content_type = verify_upload("image", filename, content, settings.max_upload_bytes)
    media_info = probe_upload("image", content, content_type)
    digest = hashlib.sha256(content).hexdigest()
    for reference in payload.get("references") or []:
        _read_reference(factory, settings, reference)
    if payload.get("mask"):
        _read_mask(factory, settings, payload["mask"])
    _verify_parent_snapshot(factory, settings, payload)
    from shine.media.output_layout import (
        ensure_storage_writable,
        output_root,
        store_library_asset,
    )

    with factory() as db:
        ensure_storage_writable(db)
        root = output_root(db, settings)
    storage_path, stored_name = store_library_asset(
        None, settings, content, digest,
        kind="image",
        base=str(payload.get("title") or "图片候选"),
        extension=".png",
        root=root,
    )
    filename = stored_name
    expected = payload.get("output") or {}
    dimensions_match = media_info.width == expected.get("width") and media_info.height == expected.get("height")

    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None or task.external_prompt_id != prompt_id:
            raise RuntimeError("LIBRARY_TASK_PROMPT_ID_CHANGED")
        version = db.get(LibraryVersion, task.result_version_id) if task.result_version_id else None
        if version:
            if version.content_sha256 != digest or (version.provenance or {}).get("task_id") != task_id:
                raise RuntimeError("LIBRARY_CANDIDATE_ADOPTION_CONFLICT")
            storage_path = version.storage_path
            filename = version.filename
        else:
            if payload.get("alpha_operation") == "MASK_CUTOUT_V1":
                source_ref = (payload.get("references") or [])[0]
                source_row = (
                    db.get(LibraryMedia, source_ref.get("media_id"))
                    if source_ref.get("media_id")
                    else db.get(LibraryVersion, source_ref.get("version_id"))
                )
                source_rights = db.get(LibraryRights, source_row.rights_id) if source_row else None
                service.require_rights(source_rights, usage_scope="local")
                if source_rights is None or canonical_hash(service.rights_snapshot(source_rights)) != canonical_hash(
                    source_ref.get("rights_snapshot") or {}
                ):
                    raise RuntimeError("LIBRARY_SOURCE_RIGHTS_CHANGED")
                rights = LibraryRights(
                    source_type="inherited_image_rights",
                    license_kind=source_rights.license_kind,
                    scope="asset",
                    statement=f"手动画布去背景结果继承底图授权：{source_rights.statement}",
                    subject=source_rights.subject,
                    allowed_project_ids=list(source_rights.allowed_project_ids or []),
                    permitted_uses=list(source_rights.permitted_uses or []),
                    commercial_allowed=source_rights.commercial_allowed,
                    status="APPROVED",
                    recorded_actor="shine-worker",
                    approved_by=source_rights.approved_by,
                    expires_at=source_rights.expires_at,
                )
            else:
                rights = _new_image_output_rights(db, payload)
            db.add(rights)
            db.flush()
            media = LibraryMedia(
                kind="image",
                purpose="generated",
                filename=filename,
                content_type=content_type,
                sha256=digest,
                storage_path=storage_path,
                byte_size=len(content),
                width=media_info.width,
                height=media_info.height,
                duration_s=None,
                rights_id=rights.id,
                created_by="shine-worker",
            )
            db.add(media)
            db.flush()
            category = str(payload.get("category") or "other")
            if payload.get("target_asset_id"):
                asset = db.get(LibraryAsset, payload["target_asset_id"])
                parent = db.get(LibraryVersion, payload.get("parent_version_id"))
                if asset is None or parent is None or parent.library_asset_id != asset.id:
                    raise RuntimeError("LIBRARY_IMAGE_PARENT_CHANGED")
            else:
                title = str(payload.get("title") or f"{category} 图片候选").strip()[:200]
                if int(payload.get("candidate_index", 0)):
                    title = f"{title} · 候选 {int(payload['candidate_index']) + 1}"[:200]
                asset = LibraryAsset(title=title, kind="image", category=category, tags=[], created_by="shine-worker")
                db.add(asset)
                db.flush()
            manifest = list(payload.get("references") or [])
            if payload.get("parent_version_id"):
                manifest.append(
                    {
                        "order": len(manifest),
                        "role": "asset_parent",
                        "version_id": payload["parent_version_id"],
                        "rights_snapshot": (payload.get("parent_version_snapshot") or {}).get("rights_snapshot"),
                    }
                )
            if payload.get("mask"):
                manifest.append(
                    {
                        "order": len(manifest),
                        "role": "mask",
                        "media_id": payload["mask"]["media_id"],
                        "mask_sha256": payload["mask"]["sha256"],
                        "rights_snapshot": payload["mask"].get("rights_snapshot"),
                    }
                )
            version = service.create_version_from_media(
                db,
                settings,
                asset=asset,
                media=media,
                actor="shine-worker",
                source_mode=str(payload.get("mode") or "T2I"),
                prompt=str(payload.get("prompt") or ""),
                parent_version_id=payload.get("parent_version_id"),
                workflow_revision_hash=graph_sha,
                references_manifest=manifest,
                provenance={
                    "source": "shine_assets_image_worker",
                    "task_id": task_id,
                    "external_prompt_id": prompt_id,
                    "output_node_id": qwen_workflow.OUTPUT_NODE,
                    "runtime_fingerprint": fingerprint,
                    "model_revision": None if payload.get("alpha_operation") else qwen_workflow.MODEL_REVISION,
                    "template_commit": "MASK_CUTOUT_V1"
                    if payload.get("alpha_operation")
                    else QWEN_EDIT_COMMIT
                    if payload.get("mode") != "T2I"
                    else qwen_workflow.TEMPLATE_COMMIT,
                    "usage_scope": "RESEARCH_AND_EVALUATION_ONLY",
                    "renderer_owned": bool(renderer.get("renderer_owned")),
                    "candidate_index": payload.get("candidate_index", 0),
                    "target_asset_id": payload.get("target_asset_id"),
                    "parent_version_id": payload.get("parent_version_id"),
                    "target_asset_state_version": payload.get("expected_asset_state_version"),
                    "transparent": bool(payload.get("transparent")),
                    "validation_only": bool(payload.get("validation_only")),
                    **image_style_payload(payload),
                },
            )
            version.qc = {
                "technical": "PENDING",
                "semantic": "PENDING_HUMAN_REVIEW",
                "dimensions_match_request": dimensions_match,
                "sha256": digest,
            }
            task.result_version_id = version.id
            add_audit(
                db,
                "shine-worker",
                "LIBRARY_IMAGE_CANDIDATE_STORED",
                "library_version",
                version.id,
                {"task_id": task_id, "sha256": digest, "prompt_id": prompt_id, "graph_sha256": graph_sha},
            )
            db.flush()
        version_id = version.id

    from shine.media.artifacts import safe_asset_path
    from shine.quality.technical import _full_decode_check

    path = safe_asset_path(root, storage_path)
    decoded = _full_decode_check(path, settings.ffmpeg_path)
    qc = {
        "technical": decoded.status,
        "decode": decoded.model_dump(),
        "dimensions": {"width": media_info.width, "height": media_info.height},
        "dimensions_match_request": dimensions_match,
        "sha256": digest,
        "semantic": "PENDING_HUMAN_REVIEW",
        "is_mock": False,
    }
    mode_contract_pass = True
    if payload.get("mask"):
        qc["mask_composite"] = {
            "applied_sha256": payload["mask"].get("sha256"),
            "changed_pixels": payload["mask"].get("changed_pixels"),
            "retained_pixels": payload["mask"].get("retained_pixels"),
            "semantics": payload["mask"].get("semantics"),
        }
    if payload.get("transparent"):
        try:
            qc["alpha"] = validate_rgba_output(content)
        except Exception as exc:  # noqa: BLE001 - alpha is a QC failure, not an unknown submit
            qc["alpha"] = {"status": "failed", "error_code": getattr(exc, "detail", None)}
            mode_contract_pass = False
    with factory.begin() as db:
        version = db.get(LibraryVersion, version_id)
        task = db.get(LibraryRenderTask, task_id)
        if version is None or task is None or task.external_prompt_id != prompt_id:
            raise RuntimeError("LIBRARY_CANDIDATE_ADOPTION_CONFLICT")
        passed = decoded.status == "pass" and dimensions_match and mode_contract_pass
        version.qc = qc
        version.status = "READY_FOR_REVIEW" if passed else "QC_FAILED"
        version_id = version.id
        task.result_version_id = version.id
        task.status = "CANDIDATE_READY" if passed else "QC_FAILED"
        external_outcome = "NOT_SENT" if payload.get("alpha_operation") else "SUCCEEDED"
        task.external_outcome = external_outcome
        task.error_code = None if passed else "IMAGE_TECHNICAL_QC_FAILED"
        task.state_version += 1
        task.updated_at = utcnow()
        task.heartbeat_at = utcnow()
        task.worker_id = None
        task.lease_owner = None
        if attempt_id:
            attempt = db.get(LibraryTaskAttempt, attempt_id)
            if attempt:
                attempt.status = task.status
                attempt.external_outcome = external_outcome
                attempt.output_sha256 = digest
                attempt.submit_stage = "LOCAL_OUTPUT_STORED" if payload.get("alpha_operation") else "OUTPUT_STORED"
                attempt.payload = {**(attempt.payload or {}), "version_id": version.id}
        add_audit(db, "shine-worker", "LIBRARY_IMAGE_CANDIDATE_QC", "library_version", version.id, qc)
    return version_id, digest, "CANDIDATE_READY" if passed else "QC_FAILED"


def _read_reference(factory, settings, reference: dict[str, Any]) -> tuple[bytes, str]:
    """Recheck the snapshotted rights and digest immediately before execution."""
    with factory() as db:
        if reference.get("media_id"):
            row = db.get(LibraryMedia, reference["media_id"])
            if row is None or row.kind != "image" or row.purpose == "mask":
                raise RuntimeError("LIBRARY_REFERENCE_CHANGED")
            from shine.media.output_layout import output_root

            content = service.media_bytes(settings, row, root=output_root(db, settings))
            content_type = row.content_type
            digest = row.sha256
            rights = service.require_rights(db.get(LibraryRights, row.rights_id), usage_scope="local")
        else:
            row = db.get(LibraryVersion, reference.get("version_id"))
            if row is None or row.kind != "image" or row.status != "PROMOTED" or row.is_mock:
                raise RuntimeError("LIBRARY_REFERENCE_CHANGED")
            from shine.media.output_layout import output_root

            content = service.version_bytes(settings, row, root=output_root(db, settings))
            content_type = row.content_type
            digest = row.content_sha256
            rights = service.require_version_rights(db, row, usage_scope="local")
        if canonical_hash(service.rights_snapshot(rights)) != canonical_hash(reference.get("rights_snapshot") or {}):
            raise RuntimeError("LIBRARY_RIGHTS_SNAPSHOT_CHANGED")
        if digest != reference.get("sha256") or hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError("LIBRARY_REFERENCE_HASH_CHANGED")
        return content, content_type


def _read_mask(factory, settings, mask_info: dict[str, Any]) -> bytes:
    if not mask_info or not mask_info.get("media_id"):
        raise RuntimeError("LIBRARY_MASK_MISSING")
    with factory() as db:
        row = db.get(LibraryMedia, mask_info["media_id"])
        if row is None or row.kind != "image" or row.purpose != "mask":
            raise RuntimeError("LIBRARY_MASK_CHANGED")
        rights = service.require_rights(db.get(LibraryRights, row.rights_id), usage_scope="local")
        if canonical_hash(service.rights_snapshot(rights)) != canonical_hash(mask_info.get("rights_snapshot") or {}):
            raise RuntimeError("LIBRARY_MASK_RIGHTS_CHANGED")
        from shine.media.output_layout import output_root

        content = service.media_bytes(settings, row, root=output_root(db, settings))
        if row.sha256 != mask_info.get("sha256") or hashlib.sha256(content).hexdigest() != row.sha256:
            raise RuntimeError("LIBRARY_MASK_HASH_CHANGED")
        return content


def _verify_parent_snapshot(factory, settings, payload: dict[str, Any]) -> None:
    snapshot = payload.get("parent_version_snapshot")
    if not snapshot:
        return
    with factory() as db:
        version = db.get(LibraryVersion, snapshot.get("version_id"))
        if version is None or version.status != "PROMOTED" or version.is_mock:
            raise RuntimeError("LIBRARY_PARENT_VERSION_CHANGED")
        rights = service.require_version_rights(db, version, usage_scope="local")
        if version.content_sha256 != snapshot.get("sha256") or version.rights_id != snapshot.get("rights_id"):
            raise RuntimeError("LIBRARY_PARENT_VERSION_CHANGED")
        if canonical_hash(service.rights_snapshot(rights)) != canonical_hash(snapshot.get("rights_snapshot") or {}):
            raise RuntimeError("LIBRARY_PARENT_RIGHTS_CHANGED")
        from shine.media.output_layout import output_root

        service.version_bytes(settings, version, root=output_root(db, settings))


def _reference_prompt(prompt: str, references: list[dict[str, Any]]) -> str:
    roles = {
        "canvas": "the edit target",
        "subject": "the edit target and subject",
        "face": "the face reference",
        "garment": "the garment reference",
        "hair": "the hair reference",
        "shoe": "the shoe reference",
        "bag": "the bag reference",
        "hat": "the hat reference",
        "prop": "the prop reference",
        "scene": "the scene reference",
        "style": "the style reference",
        "accessory": "the accessory reference",
        "mask_annotation": (
            "the binary edit mask; white pixels are selected for editing "
            "and black pixels must be preserved"
        ),
    }
    mapping = "; ".join(
        f"<image{index}> is {roles.get(str(row.get('role')), str(row.get('role')))}"
        for index, row in enumerate(references, 1)
    )
    return f"{prompt.strip()}\n\nReference image map (use each assigned image): {mapping}"


def _edit_graph(
    client,
    payload: dict[str, Any],
    width: int,
    height: int,
    seed: int,
    reference_contents: list[tuple[bytes, str]],
    *,
    object_info: dict | None = None,
    style_loras=(),
) -> dict[str, Any]:
    references = sorted(
        payload.get("graph_references") or payload.get("references") or [], key=lambda row: row["order"]
    )
    if not references:
        raise RuntimeError("QWEN_EDIT_SOURCE_REQUIRED")
    uploaded_names: list[str] = []
    for reference, (content, content_type) in zip(references, reference_contents, strict=True):
        extension = mimetypes.guess_extension(content_type) or ".img"
        if extension == ".jpe":
            extension = ".jpg"
        uploaded = client.upload_image(
            f"{reference['sha256']}{extension}",
            content,
            subfolder="",
            content_type=content_type,
        )
        name = str(uploaded.get("name") or "")
        if not name or "/" in name or "\\" in name:
            raise RuntimeError("COMFY_INPUT_UPLOAD_RESPONSE_INVALID")
        uploaded_names.append(name)
    mode = str(payload.get("mode") or "I2I")
    graph = build_qwen_edit_graph(
        prompt=_reference_prompt(str(payload.get("prompt") or ""), references),
        negative_prompt=str(payload.get("negative_prompt") or ""),
        uploaded_names=uploaded_names,
        width=width,
        height=height,
        seed=seed,
        style_loras=style_loras,
        **(payload.get("style_sampling") or {}),
    )
    if object_info is None:
        object_info = client.object_info()
    from shine.comfy.workflow import validate_against_object_info, validate_graph

    validate_graph(graph, required_nodes=[str(node["class_type"]) for node in graph.values()])
    report = validate_against_object_info(graph, {}, object_info)
    if report["missing_nodes"] or report["missing_inputs"]:
        raise RuntimeError("QWEN_EDIT_WORKFLOW_CONTRACT_CHANGED")
    if mode == "INPAINT":
        # The pinned official edit graph has no mask input. The actual mask is
        # applied after harvest, with exact-pixel preservation enforced below.
        if not (payload.get("mask") or {}).get("sha256"):
            raise RuntimeError("LIBRARY_MASK_MISSING")
    return graph


def _new_image_output_rights(db, payload: dict[str, Any]) -> LibraryRights:
    source_rights_rows = []
    source_entries = list(payload.get("references") or [])
    if payload.get("parent_version_snapshot"):
        source_entries.append(payload["parent_version_snapshot"])
    if payload.get("mask"):
        source_entries.append(payload["mask"])
    seen_source_ids: set[tuple[str, str]] = set()
    for source in source_entries:
        media_id = source.get("media_id")
        version_id = source.get("version_id")
        identity = ("media", str(media_id)) if media_id else ("version", str(version_id))
        if identity in seen_source_ids:
            continue
        seen_source_ids.add(identity)
        source_row = db.get(LibraryMedia, media_id) if media_id else db.get(LibraryVersion, version_id)
        if isinstance(source_row, LibraryVersion):
            source_rights = service.require_version_rights(db, source_row, usage_scope="local")
        else:
            source_rights = service.require_rights(
                db.get(LibraryRights, source_row.rights_id) if source_row else None,
                usage_scope="local",
            )
        expected_snapshot = source.get("rights_snapshot") or {}
        current_snapshot = canonical_hash(service.rights_snapshot(source_rights)) if source_rights else None
        if source_rights is None or current_snapshot != canonical_hash(expected_snapshot):
            raise RuntimeError("LIBRARY_SOURCE_RIGHTS_CHANGED")
        expected_sha = source.get("sha256")
        actual_sha = source_row.sha256 if isinstance(source_row, LibraryMedia) else source_row.content_sha256
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError("LIBRARY_SOURCE_HASH_CHANGED")
        source_rights_rows.append(source_rights)

    project_scopes = [
        set(row.allowed_project_ids or [])
        for row in source_rights_rows
        if "*" not in (row.allowed_project_ids or [])
    ]
    allowed_project_ids = (
        sorted(set.intersection(*project_scopes)) if project_scopes else ["*"]
    ) if source_rights_rows else []
    return LibraryRights(
        source_type="qwen_derived_output",
        license_kind="QWEN_RESEARCH_WITH_SOURCE_RESTRICTIONS",
        scope="asset",
        statement="Qwen-Image-2.1 输出；仅研究用途，来源授权范围继续适用。授权不会由模型自动产生。",
        subject="Qwen-Image-2.1 model output with inherited source restrictions",
        allowed_project_ids=allowed_project_ids,
        permitted_uses=["research"],
        commercial_allowed=False,
        status="DECLARED",
        recorded_actor="shine-worker",
        expires_at=min((row.expires_at for row in source_rights_rows if row.expires_at), default=None),
    )


def _execute_one(factory, settings, task_id: str, worker_id: str) -> None:
    payload, expected_state = _load_task(factory, task_id)
    mode = str(payload.get("mode") or "T2I")
    category = str(payload.get("category") or "")
    family = "scene" if category == "scene" else "prop" if category == "prop" else "character"
    if mode == "RGBA" and payload.get("alpha_operation") == "MASK_CUTOUT_V1":
        _execute_mask_cutout(factory, settings, task_id, worker_id, payload, expected_state)
        return
    if mode == "I2I" and payload.get("i2i_operation") == CHARACTER_SHEET_OPERATION:
        _execute_character_sheet(factory, settings, task_id, worker_id, payload, expected_state)
        return
    if mode == "T2I" and category not in {"character", "costume", "scene", "prop"}:
        _finish_without_submit(factory, task_id, "BLOCKED_CAPABILITY", "IMAGE_FAMILY_NOT_VERIFIED")
        return
    width = int((payload.get("output") or {}).get("width", 1024))
    height = int((payload.get("output") or {}).get("height", 1024))
    seed = payload.get("seed")
    if seed is None:
        seed = int.from_bytes(hashlib.sha256(task_id.encode()).digest()[:8], "big")
    has_2k_capability = bool((payload.get("capability") or {}).get("can_generate"))
    if (
        width > 2048
        or height > 2048
        or ((width > 1024 or height > 1024) and not payload.get("validation_only") and not has_2k_capability)
    ):
        _finish_without_submit(factory, task_id, "BLOCKED_CAPABILITY", "IMAGE_2K_NOT_LOCALLY_VERIFIED")
        return
    clients: dict[str, Any] = {}

    def release_models() -> None:
        client = clients.get("client")
        if client is None:
            return
        try:
            client.free_models()
        except Exception:
            if image_execution._renderer_answers(client.base_url):
                raise

    try:
        with resource_slot(
            factory, "IMAGE", _IMAGE_BUDGET_MB, release_models=release_models, wait_seconds=0
        ) as lease_id:
            renderer = image_execution._ensure_renderer(factory, settings, family)
            # A validation-only task is the evidence that clears the production
            # family gate. Sending it through the production resolver would
            # make first validation impossible (a circular readiness check).
            runtime_kind = "validation" if payload.get("validation_only") else "production"
            resolved = image_execution.resolve_runtime(factory, settings, family, kind=runtime_kind)
            config = resolved["config"]
            client = ComfyUIClient(resolved["base_url"], max_retries=0)
            clients["client"] = client
            object_info = resolved.get("object_info")
            style_loras = resolve_live_image_loras(
                factory, settings, payload,
                object_info=object_info if object_info is not None else client.object_info(),
            ) if payload.get("style_loras") else ()
            if style_loras:
                payload = {**payload, "style_loras": [row.canonical_dict() for row in style_loras]}
            reference_rows = sorted(payload.get("references") or [], key=lambda row: row["order"])
            reference_contents = [_read_reference(factory, settings, reference) for reference in reference_rows]
            mask_content = _read_mask(factory, settings, payload["mask"]) if mode == "INPAINT" else None
            _verify_parent_snapshot(factory, settings, payload)
            if mode == "T2I" or (mode == "RGBA" and not reference_rows):
                # The prompt was composed at creation (project film medium + world
                # note + negatives).  Submit exactly that text and its negative;
                # an empty negative here would drop the medium exclusions.
                prompt = image_execution.image_prompts.ImagePrompt(
                    prompt=str(payload.get("prompt") or ""),
                    negative_prompt=str(payload.get("negative_prompt") or ""),
                    template_version=str(payload.get("prompt_template_version") or "shine-assets-user-prompt-v1"),
                    sha256=hashlib.sha256(str(payload.get("prompt") or "").encode()).hexdigest(),
                )
                graph = image_execution._build_graph(
                    config, prompt, seed=int(seed), width=width, height=height,
                    style_loras=style_loras, style_sampling=payload.get("style_sampling"),
                )
            else:
                if mode == "INPAINT":
                    if mask_content is None:
                        raise RuntimeError("LIBRARY_MASK_MISSING")
                    reference_contents.append((mask_content, "image/png"))
                graph = _edit_graph(client, payload, width, height, int(seed), reference_contents,
                                    object_info=object_info, style_loras=style_loras)
                # Recheck all rights and content hashes after uploads and live node
                # validation, immediately before writing the submission intent.
                for reference in reference_rows:
                    _read_reference(factory, settings, reference)
                if mode == "INPAINT":
                    _read_mask(factory, settings, payload["mask"])
                _verify_parent_snapshot(factory, settings, payload)
            graph_sha = graph_hash(graph)
            attempt_id, expected_state = _open_attempt(
                factory,
                task_id,
                worker_id,
                payload,
                graph_sha,
                resolved["fingerprint"],
                width,
                height,
                int(seed),
                renderer,
                prompt_inputs=graph["474" if "474" in graph else qwen_workflow.TEXT_ENCODE_NODE]["inputs"],
            )
            with factory.begin() as db:
                task = db.get(LibraryRenderTask, task_id)
                if task is None or task.state_version != expected_state or task.worker_id != worker_id:
                    raise RuntimeError("LIBRARY_TASK_CLAIM_LOST")
                task.lease_owner = lease_id
                task.heartbeat_at = utcnow()
            try:
                response = client.queue_prompt(
                    graph,
                    extra_data={"shine_kind": "asset_center_image", "shine_task": task_id, "shine_attempt": attempt_id},
                )
            except Exception as exc:  # noqa: BLE001 - submission outcomes are not all knowable
                attempt_status, outcome, error_code = image_execution._submit_outcome(exc)
                task_status = "UNKNOWN" if outcome == "UNKNOWN" else "FAILED"
                _set_attempt_and_task(
                    factory,
                    task_id,
                    attempt_id,
                    task_status=task_status,
                    attempt_status=attempt_status,
                    outcome=outcome,
                    error_code=error_code,
                    expected_state=expected_state,
                )
                return
            prompt_id = str(response.get("prompt_id") or "")
            if not prompt_id:
                _set_attempt_and_task(
                    factory,
                    task_id,
                    attempt_id,
                    task_status="UNKNOWN",
                    attempt_status="UNKNOWN",
                    outcome="UNKNOWN",
                    error_code="IMAGE_SUBMISSION_ACK_MISSING_PROMPT_ID",
                    expected_state=expected_state,
                )
                return
            next_state = _set_attempt_and_task(
                factory,
                task_id,
                attempt_id,
                task_status="RUNNING",
                attempt_status="SUBMITTED",
                outcome="ACKNOWLEDGED",
                prompt_id=prompt_id,
                expected_state=expected_state,
            )
            if next_state is None:
                return
            try:
                history_entry = _poll_with_heartbeat(client, factory, task_id, worker_id, next_state, prompt_id)
                content, source = image_execution._harvest(client, history_entry)
                if mode == "INPAINT":
                    if not reference_contents or mask_content is None:
                        raise RuntimeError("INPAINT_INPUTS_MISSING")
                    content = composite_masked_png(reference_contents[0][0], content, mask_content)
                version_id, digest, task_status = _save_candidate(
                    factory,
                    settings,
                    task_id,
                    payload,
                    content,
                    graph_sha,
                    resolved["fingerprint"],
                    prompt_id,
                    renderer,
                    attempt_id,
                )
                with factory.begin() as db:
                    attempt = db.get(LibraryTaskAttempt, attempt_id)
                    task = db.get(LibraryRenderTask, task_id)
                    if attempt and task:
                        attempt.status = task_status
                        attempt.external_outcome = "SUCCEEDED"
                        attempt.output_sha256 = digest
                        attempt.submit_stage = "OUTPUT_STORED"
                        attempt.payload = {**(attempt.payload or {}), "harvest": source, "version_id": version_id}
                        task.lease_owner = None
                        task.worker_id = None
                        task.result_version_id = version_id
                        task.status = task_status
                        task.external_outcome = "SUCCEEDED"
                        task.error_code = None if task_status == "CANDIDATE_READY" else "IMAGE_TECHNICAL_QC_FAILED"
                        task.state_version += 1
                        task.updated_at = utcnow()
                        task.heartbeat_at = utcnow()
                        add_audit(
                            db,
                            "shine-worker",
                            "LIBRARY_IMAGE_TASK_FINISHED",
                            "library_render_task",
                            task_id,
                            {
                                "status": task_status,
                                "prompt_id": prompt_id,
                                "version_id": version_id,
                                "sha256": digest,
                                "lease_id": lease_id,
                            },
                        )
            except image_execution.ImageExecutionError as exc:
                unknown = exc.code == "IMAGE_OUTCOME_RECONCILIATION_REQUIRED"
                _set_attempt_and_task(
                    factory,
                    task_id,
                    attempt_id,
                    task_status="UNKNOWN" if unknown else "FAILED",
                    attempt_status="UNKNOWN" if unknown else "FAILED",
                    outcome="UNKNOWN" if unknown else "FAILED",
                    error_code=exc.code,
                    prompt_id=prompt_id,
                    expected_state=next_state,
                )
            except Exception as exc:  # noqa: BLE001 - after submit, an unproven fault is UNKNOWN
                # After the submission the outcome is unknown, but the *reason*
                # has to be readable.  "IMAGE_POST_SUBMIT_ComfyUnavailable" only
                # repeats the exception class; a renderer that is provably gone
                # gets its own code, and the exception class stays in the
                # attempt payload as the raw detail.
                gone = image_execution._renderer_gone(factory, settings, renderer)
                _set_attempt_and_task(
                    factory,
                    task_id,
                    attempt_id,
                    task_status="UNKNOWN",
                    attempt_status="UNKNOWN",
                    outcome="UNKNOWN",
                    error_code=("IMAGE_RENDERER_GONE" if gone
                                else f"IMAGE_POST_SUBMIT_{type(exc).__name__}"),
                    prompt_id=prompt_id,
                    expected_state=next_state,
                    details={"error_type": type(exc).__name__, "renderer_gone": gone},
                )
    except Exception as exc:  # noqa: BLE001 - no external submission has happened before _open_attempt
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if (
                task
                and task.status == "RUNNING"
                and task.external_outcome == "NOT_SENT"
                and task.submit_intent_at is None
            ):
                task.status = "QUEUED" if "GPU_BUSY_RETRY_OR_QUEUE" in str(exc) else "FAILED"
                if isinstance(exc, image_execution.ImageExecutionError):
                    task.error_code = exc.details.get("code_detail") or exc.code
                elif isinstance(exc, StyleLoraError):
                    task.error_code = exc.code
                else:
                    task.error_code = (
                        "GPU_BUSY_RETRY_OR_QUEUE" if "GPU_BUSY_RETRY_OR_QUEUE" in str(exc) else type(exc).__name__
                    )
                task.worker_id = None
                task.lease_owner = None
                task.state_version += 1
                task.updated_at = utcnow()


def _open_character_sheet_step_attempt(
    factory,
    task_id: str,
    worker_id: str,
    payload: dict[str, Any],
    *,
    step_index: int,
    step_key: str,
    step_label: str,
    completed_steps: list[dict[str, Any]],
    graph_sha: str,
    fingerprint: str,
    width: int,
    height: int,
    seed: int,
    renderer: dict[str, Any],
    lease_id: str,
    prompt_inputs: dict[str, Any],
    step_loras: tuple,
    expected_state: int,
) -> tuple[str, int]:
    with factory() as db:
        task_row = db.get(LibraryRenderTask, task_id)
        request_hash = task_row.request_hash if task_row else ""
    attempt_no = step_index + 1
    snapshot = canonical_hash(
        {
            "task_id": task_id,
            "request_hash": request_hash,
            "operation": CHARACTER_SHEET_OPERATION,
            "step_key": step_key,
            "attempt_no": attempt_no,
            "graph_sha256": graph_sha,
            "runtime_fingerprint": fingerprint,
            "output_node_id": qwen_workflow.OUTPUT_NODE,
            "seed": seed,
            "width": width,
            "height": height,
        }
    )
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if (
            task is None
            or task.status != "RUNNING"
            or task.worker_id != worker_id
            or task.state_version != expected_state
        ):
            raise RuntimeError("LIBRARY_TASK_CLAIM_LOST")
        existing = db.scalar(
            select(LibraryTaskAttempt).where(
                LibraryTaskAttempt.task_id == task_id,
                LibraryTaskAttempt.attempt_no == attempt_no,
            )
        )
        if existing is not None:
            raise RuntimeError("CHARACTER_SHEET_STEP_ALREADY_SUBMITTED")
        if step_index == 0 and (task.external_outcome != "NOT_SENT" or task.submit_intent_at is not None):
            raise RuntimeError("LIBRARY_TASK_ALREADY_SUBMITTED")
        attempt = LibraryTaskAttempt(
            task_id=task_id,
            attempt_no=attempt_no,
            snapshot_hash=snapshot,
            submit_stage="SUBMIT_INTENT",
            status="SUBMIT_INTENT",
            external_outcome="NOT_SENT",
            payload={
                "operation": CHARACTER_SHEET_OPERATION,
                "step_key": step_key,
                "step_label": step_label,
                "step_index": step_index,
                "graph_sha256": graph_sha,
                "runtime_fingerprint": fingerprint,
                "output_node_id": qwen_workflow.OUTPUT_NODE,
                "output_class_type": qwen_workflow.OUTPUT_CLASS_TYPE,
                "model_revision": qwen_workflow.MODEL_REVISION,
                "template_commit": QWEN_EDIT_COMMIT,
                "adapter_hash": qwen_edit_adapter_hash("I2I"),
                "seed": seed,
                "width": width,
                "height": height,
                "renderer_owned": bool(renderer.get("renderer_owned")),
                "renderer_instance_id": str(renderer.get("renderer_instance_id") or ""),
                **({"style_loras": [spec.canonical_dict() for spec in step_loras]} if step_loras else {}),
                "prompt": prompt_inputs["prompt"],
                "negative_prompt": prompt_inputs["negative_prompt"],
            },
        )
        db.add(attempt)
        task.submit_intent_at = task.submit_intent_at or utcnow()
        task.external_outcome = "NOT_SENT"
        task.lease_owner = lease_id
        task.workflow_revision_hash = graph_sha
        task.runtime_fingerprint = fingerprint
        task.payload = {
            **(task.payload or {}),
            "character_sheet_progress": {
                "current_step": step_key,
                "current_step_label": step_label,
                "completed_steps": completed_steps,
                "total_steps": len(STEP_SPECS),
            },
        }
        task.state_version += 1
        task.heartbeat_at = utcnow()
        task.updated_at = utcnow()
        db.flush()
        return attempt.id, task.state_version


def _save_character_sheet_candidates(
    factory,
    settings,
    task_id: str,
    payload: dict[str, Any],
    *,
    closeup_front_bytes: bytes,
    closeup_front_prompt: str,
    closeup_front_graph_sha: str,
    closeup_3_4_bytes: bytes,
    closeup_3_4_prompt: str,
    closeup_3_4_graph_sha: str,
    triview_bytes: bytes,
    triview_prompt: str,
    triview_graph_sha: str,
    composite_bytes: bytes,
    composite_layout: dict[str, Any],
    step_records: list[dict[str, Any]],
    fingerprint: str,
    renderer: dict[str, Any],
    lease_id: str,
    expected_state: int,
) -> tuple[str, str, str]:
    for reference in payload.get("references") or []:
        _read_reference(factory, settings, reference)
    _verify_parent_snapshot(factory, settings, payload)

    from shine.media.artifacts import safe_asset_path
    from shine.media.output_layout import ensure_storage_writable, output_root, store_library_asset
    from shine.quality.technical import _full_decode_check

    with factory() as db:
        ensure_storage_writable(db)
        root = output_root(db, settings)

    base_title = str(payload.get("title") or "角色定妆照").strip() or "角色定妆照"
    artifact_specs = [
        (
            "closeup_front",
            "肩上正面照",
            f"{base_title} · 肩上正面照",
            closeup_front_bytes,
            closeup_front_prompt,
            closeup_front_graph_sha,
        ),
        (
            "closeup_3_4",
            "3/4 侧面肩上照",
            f"{base_title} · 3/4 侧面肩上照",
            closeup_3_4_bytes,
            closeup_3_4_prompt,
            closeup_3_4_graph_sha,
        ),
        (
            "fullbody_triview",
            "全身三视图（正面/侧面/背面）",
            f"{base_title} · 全身三视图",
            triview_bytes,
            triview_prompt,
            triview_graph_sha,
        ),
        (
            "composite_sheet",
            "16:9 整体角色定妆照（正面/3/4 侧面肩上 + 全身三视图）",
            base_title,
            composite_bytes,
            payload.get("character_sheet_note") or "",
            canonical_hash([step["graph_sha256"] for step in step_records]),
        ),
    ]

    prepared: list[dict[str, Any]] = []
    for role_key, role_label, storage_base, content, ver_prompt, ver_graph_sha in artifact_specs:
        filename = f"shine-assets-{task_id[:8]}-{role_key}.png"
        content_type = verify_upload("image", filename, content, settings.max_upload_bytes)
        media_info = probe_upload("image", content, content_type)
        digest = hashlib.sha256(content).hexdigest()
        storage_path, stored_name = store_library_asset(
            None,
            settings,
            content,
            digest,
            kind="image",
            base=storage_base,
            extension=".png",
            root=root,
        )
        prepared.append(
            {
                "role_key": role_key,
                "role_label": role_label,
                "content_type": content_type,
                "width": media_info.width,
                "height": media_info.height,
                "byte_size": len(content),
                "sha256": digest,
                "storage_path": storage_path,
                "filename": stored_name,
                "prompt": ver_prompt,
                "graph_sha256": ver_graph_sha,
            }
        )

    source_ref_sha = str(((payload.get("references") or [{}])[0]).get("sha256") or "")
    lora_meta = dict((payload.get("style_loras") or [{}])[0])
    last_prompt_id = str(step_records[-1]["prompt_id"])

    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None or task.state_version != expected_state:
            raise RuntimeError("LIBRARY_TASK_CLAIM_LOST")
        category = str(payload.get("category") or "character")
        parent_version_id = payload.get("parent_version_id")
        if payload.get("target_asset_id"):
            asset = db.get(LibraryAsset, payload["target_asset_id"])
            parent = db.get(LibraryVersion, parent_version_id) if parent_version_id else None
            if asset is None or parent is None or parent.library_asset_id != asset.id:
                raise RuntimeError("LIBRARY_IMAGE_PARENT_CHANGED")
        else:
            asset = LibraryAsset(
                title=base_title[:200],
                kind="image",
                category=category,
                tags=[],
                created_by="shine-worker",
            )
            db.add(asset)
            db.flush()

        manifest = list(payload.get("references") or [])
        if parent_version_id:
            manifest.append(
                {
                    "order": len(manifest),
                    "role": "asset_parent",
                    "version_id": parent_version_id,
                    "rights_snapshot": (payload.get("parent_version_snapshot") or {}).get("rights_snapshot"),
                }
            )

        created_versions: list[tuple[dict[str, Any], LibraryVersion]] = []
        for item in prepared:
            rights = _new_image_output_rights(db, payload)
            db.add(rights)
            db.flush()
            media = LibraryMedia(
                kind="image",
                purpose="generated",
                filename=item["filename"],
                content_type=item["content_type"],
                sha256=item["sha256"],
                storage_path=item["storage_path"],
                byte_size=item["byte_size"],
                width=item["width"],
                height=item["height"],
                duration_s=None,
                rights_id=rights.id,
                created_by="shine-worker",
            )
            db.add(media)
            db.flush()
            version = service.create_version_from_media(
                db,
                settings,
                asset=asset,
                media=media,
                actor="shine-worker",
                source_mode=CHARACTER_SHEET_SOURCE_MODE,
                prompt=item["prompt"],
                parent_version_id=parent_version_id,
                workflow_revision_hash=item["graph_sha256"],
                references_manifest=manifest,
                provenance={
                    "source": "shine_assets_image_worker",
                    "task_id": task_id,
                    "external_prompt_id": last_prompt_id,
                    "output_node_id": qwen_workflow.OUTPUT_NODE,
                    "runtime_fingerprint": fingerprint,
                    "model_revision": qwen_workflow.MODEL_REVISION,
                    "template_commit": QWEN_EDIT_COMMIT,
                    "usage_scope": "RESEARCH_AND_EVALUATION_ONLY",
                    "renderer_owned": bool(renderer.get("renderer_owned")),
                    "candidate_index": payload.get("candidate_index", 0),
                    "target_asset_id": payload.get("target_asset_id"),
                    "parent_version_id": parent_version_id,
                    "target_asset_state_version": payload.get("expected_asset_state_version"),
                    "transparent": False,
                    "validation_only": False,
                    **image_style_payload(payload),
                },
            )
            version.qc = {
                "technical": "PENDING",
                "semantic": "PENDING_HUMAN_REVIEW",
                "dimensions_match_request": True,
                "sha256": item["sha256"],
            }
            created_versions.append((item, version))

        derived_versions = {
            item["role_key"]: {
                "version_id": ver.id,
                "asset_id": asset.id,
                "ordinal": ver.ordinal,
                "label": item["role_label"],
                "sha256": item["sha256"],
                "width": item["width"],
                "height": item["height"],
                "media_url": f"/api/v1/asset-center/versions/{ver.id}/media",
            }
            for item, ver in created_versions
        }
        for item, ver in created_versions:
            ver.provenance = {
                **(ver.provenance or {}),
                "character_sheet": {
                    "operation": CHARACTER_SHEET_OPERATION,
                    "artifact_role": item["role_key"],
                    "artifact_label": item["role_label"],
                    "layout_version": CHARACTER_SHEET_LAYOUT_VERSION,
                    "layout": composite_layout,
                    "source_reference_sha256": source_ref_sha,
                    "note": payload.get("character_sheet_note") or "",
                    "lora": lora_meta,
                    "steps": step_records,
                    "derived_versions": derived_versions,
                },
            }
            add_audit(
                db,
                "shine-worker",
                "LIBRARY_IMAGE_CANDIDATE_STORED",
                "library_version",
                ver.id,
                {
                    "task_id": task_id,
                    "sha256": item["sha256"],
                    "prompt_id": last_prompt_id,
                    "graph_sha256": item["graph_sha256"],
                    "artifact_role": item["role_key"],
                },
            )

        composite_ver = created_versions[-1][1]
        task.result_version_id = composite_ver.id
        task.payload = {
            **(task.payload or {}),
            "character_sheet_outputs": derived_versions,
        }
        task.state_version += 1
        task.updated_at = utcnow()
        task.heartbeat_at = utcnow()
        next_state = task.state_version
        version_ids = [(item["role_key"], item["storage_path"], item["width"], item["height"], item["sha256"], ver.id)
                       for item, ver in created_versions]

    qc_results: list[tuple[str, str, dict[str, Any], bool]] = []
    all_passed = True
    for role_key, storage_path, width, height, digest, ver_id in version_ids:
        path = safe_asset_path(root, storage_path)
        decoded = _full_decode_check(path, settings.ffmpeg_path)
        qc = {
            "technical": decoded.status,
            "decode": decoded.model_dump(),
            "dimensions": {"width": width, "height": height},
            "dimensions_match_request": True,
            "sha256": digest,
            "semantic": "PENDING_HUMAN_REVIEW",
            "is_mock": False,
            "character_sheet_role": role_key,
        }
        passed = decoded.status == "pass"
        all_passed = all_passed and passed
        qc_results.append((ver_id, role_key, qc, passed))

    final_status = "CANDIDATE_READY" if all_passed else "QC_FAILED"
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None or task.state_version != next_state:
            raise RuntimeError("LIBRARY_CANDIDATE_ADOPTION_CONFLICT")
        for ver_id, _role_key, qc, passed in qc_results:
            ver = db.get(LibraryVersion, ver_id)
            if ver is None:
                raise RuntimeError("LIBRARY_CANDIDATE_ADOPTION_CONFLICT")
            ver.qc = qc
            ver.status = "READY_FOR_REVIEW" if passed else "QC_FAILED"
            add_audit(db, "shine-worker", "LIBRARY_IMAGE_CANDIDATE_QC", "library_version", ver.id, qc)
        composite_entry = derived_versions["composite_sheet"]
        task.result_version_id = composite_entry["version_id"]
        task.status = final_status
        task.external_outcome = "SUCCEEDED"
        task.error_code = None if all_passed else "IMAGE_TECHNICAL_QC_FAILED"
        task.worker_id = None
        task.lease_owner = None
        task.payload = {
            **(task.payload or {}),
            "character_sheet_progress": {
                "current_step": None,
                "current_step_label": None,
                "completed_steps": step_records,
                "total_steps": len(STEP_SPECS),
            },
        }
        task.state_version += 1
        task.heartbeat_at = utcnow()
        task.updated_at = utcnow()
        add_audit(
            db,
            "shine-worker",
            "LIBRARY_IMAGE_TASK_FINISHED",
            "library_render_task",
            task_id,
            {
                "status": final_status,
                "prompt_id": last_prompt_id,
                "version_id": composite_entry["version_id"],
                "sha256": composite_entry["sha256"],
                "lease_id": lease_id,
                "operation": CHARACTER_SHEET_OPERATION,
            },
        )
    return composite_entry["version_id"], composite_entry["sha256"], final_status


def _execute_character_sheet(
    factory, settings, task_id: str, worker_id: str, payload: dict[str, Any], expected_state: int
) -> None:
    base_seed = payload.get("seed")
    if base_seed is None:
        base_seed = int.from_bytes(hashlib.sha256(task_id.encode()).digest()[:8], "big")
    base_seed = int(base_seed)
    note = str(payload.get("character_sheet_note") or "")
    clients: dict[str, Any] = {}

    def release_models() -> None:
        client = clients.get("client")
        if client is None:
            return
        try:
            client.free_models()
        except Exception:
            if image_execution._renderer_answers(client.base_url):
                raise

    try:
        with resource_slot(
            factory, "IMAGE", _IMAGE_BUDGET_MB, release_models=release_models, wait_seconds=0
        ) as lease_id:
            renderer = image_execution._ensure_renderer(factory, settings, "character")
            resolved = image_execution.resolve_runtime(factory, settings, "character", kind="production")
            client = ComfyUIClient(resolved["base_url"], max_retries=0)
            clients["client"] = client
            object_info = resolved.get("object_info")
            live_object_info = object_info if object_info is not None else client.object_info()
            style_loras = resolve_live_image_loras(
                factory, settings, payload, object_info=live_object_info,
            )
            if not style_loras:
                raise RuntimeError("CHARACTER_SHEET_LORA_REQUIRED")
            payload = {**payload, "style_loras": [row.canonical_dict() for row in style_loras]}

            reference_rows = sorted(payload.get("references") or [], key=lambda row: row["order"])
            if len(reference_rows) != 1:
                raise RuntimeError("CHARACTER_SHEET_SINGLE_SOURCE_REQUIRED")
            ref_bytes, ref_content_type = _read_reference(factory, settings, reference_rows[0])
            ref_info = probe_upload("image", ref_bytes, ref_content_type)
            _verify_parent_snapshot(factory, settings, payload)

            step_outputs: dict[str, bytes] = {}
            step_records: list[dict[str, Any]] = []
            step_prompts: dict[str, str] = {}
            step_graph_hashes: dict[str, str] = {}
            front_dims: tuple[int, int] | None = None

            for step_index, spec in enumerate(STEP_SPECS):
                step_seed = (base_seed + step_index) & qwen_workflow.MAX_SEED
                step_loras = style_loras if spec.uses_lora else ()
                step_sampling = dict(CHARACTER_SHEET_LORA_SAMPLING if spec.uses_lora else CHARACTER_SHEET_BASE_SAMPLING)

                if spec.reference_source == "input":
                    if spec.key.startswith("closeup_"):
                        step_w, step_h = spec.target_width, spec.target_height
                    else:
                        step_w, step_h = step_dimensions(
                            ref_info.width,
                            ref_info.height,
                            target_width=spec.target_width,
                            target_height=spec.target_height,
                        )
                    step_ref_bytes, step_ref_type = ref_bytes, ref_content_type
                    step_ref_sha = str(reference_rows[0]["sha256"])
                else:
                    front_bytes = step_outputs["fullbody_front"]
                    step_w, step_h = front_dims or (spec.target_width, spec.target_height)
                    step_ref_bytes, step_ref_type = front_bytes, "image/png"
                    step_ref_sha = hashlib.sha256(front_bytes).hexdigest()

                step_pos, step_neg = build_step_prompts(
                    spec.key,
                    note=note,
                    style_loras=payload["style_loras"] if spec.uses_lora else (),
                )
                step_payload = {
                    **payload,
                    "prompt": step_pos,
                    "negative_prompt": step_neg,
                    "style_sampling": step_sampling,
                    "graph_references": [{"order": 0, "role": "subject", "sha256": step_ref_sha}],
                }
                graph = _edit_graph(
                    client,
                    step_payload,
                    step_w,
                    step_h,
                    step_seed,
                    [(step_ref_bytes, step_ref_type)],
                    object_info=live_object_info,
                    style_loras=step_loras,
                )
                # Recheck authoritative source rights & digest immediately before SUBMIT_INTENT.
                _read_reference(factory, settings, reference_rows[0])
                _verify_parent_snapshot(factory, settings, payload)

                graph_sha = graph_hash(graph)
                attempt_id, expected_state = _open_character_sheet_step_attempt(
                    factory,
                    task_id,
                    worker_id,
                    payload,
                    step_index=step_index,
                    step_key=spec.key,
                    step_label=spec.label,
                    completed_steps=list(step_records),
                    graph_sha=graph_sha,
                    fingerprint=resolved["fingerprint"],
                    width=step_w,
                    height=step_h,
                    seed=step_seed,
                    renderer=renderer,
                    lease_id=lease_id,
                    prompt_inputs=graph["474"]["inputs"],
                    step_loras=step_loras,
                    expected_state=expected_state,
                )
                try:
                    response = client.queue_prompt(
                        graph,
                        extra_data={
                            "shine_kind": "asset_center_image",
                            "shine_task": task_id,
                            "shine_attempt": attempt_id,
                            "shine_step": spec.key,
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - submission outcomes are not all knowable
                    attempt_status, outcome, error_code = image_execution._submit_outcome(exc)
                    task_status = "UNKNOWN" if outcome == "UNKNOWN" else "FAILED"
                    _set_attempt_and_task(
                        factory,
                        task_id,
                        attempt_id,
                        task_status=task_status,
                        attempt_status=attempt_status,
                        outcome=outcome,
                        error_code=error_code,
                        expected_state=expected_state,
                        details={"step_key": spec.key},
                    )
                    return

                prompt_id = str(response.get("prompt_id") or "")
                if not prompt_id:
                    _set_attempt_and_task(
                        factory,
                        task_id,
                        attempt_id,
                        task_status="UNKNOWN",
                        attempt_status="UNKNOWN",
                        outcome="UNKNOWN",
                        error_code="IMAGE_SUBMISSION_ACK_MISSING_PROMPT_ID",
                        expected_state=expected_state,
                        details={"step_key": spec.key},
                    )
                    return

                next_state = _set_attempt_and_task(
                    factory,
                    task_id,
                    attempt_id,
                    task_status="RUNNING",
                    attempt_status="SUBMITTED",
                    outcome="ACKNOWLEDGED",
                    prompt_id=prompt_id,
                    expected_state=expected_state,
                )
                if next_state is None:
                    return

                try:
                    history_entry = _poll_with_heartbeat(client, factory, task_id, worker_id, next_state, prompt_id)
                    content, source = image_execution._harvest(client, history_entry)
                    harvested_type = verify_upload("image", f"{spec.key}.png", content, settings.max_upload_bytes)
                    harvested_info = probe_upload("image", content, harvested_type)
                    if harvested_info.width != step_w or harvested_info.height != step_h:
                        raise image_execution.ImageExecutionError("IMAGE_STEP_DIMENSIONS_MISMATCH", prompt_id=prompt_id)
                    step_sha = hashlib.sha256(content).hexdigest()
                    record = {
                        "step_key": spec.key,
                        "label": spec.label,
                        "prompt_id": prompt_id,
                        "graph_sha256": graph_sha,
                        "sha256": step_sha,
                        "seed": step_seed,
                        "width": harvested_info.width,
                        "height": harvested_info.height,
                        "uses_lora": spec.uses_lora,
                        "sampling": step_sampling,
                    }
                    step_records.append(record)
                    step_outputs[spec.key] = content
                    step_prompts[spec.key] = step_pos
                    step_graph_hashes[spec.key] = graph_sha
                    if spec.key == "fullbody_front":
                        front_dims = (harvested_info.width, harvested_info.height)
                    with factory.begin() as db:
                        attempt = db.get(LibraryTaskAttempt, attempt_id)
                        task = db.get(LibraryRenderTask, task_id)
                        if attempt is None or task is None or task.state_version != next_state:
                            raise RuntimeError("LIBRARY_TASK_CLAIM_LOST")
                        attempt.status = "SUCCEEDED"
                        attempt.external_outcome = "SUCCEEDED"
                        attempt.output_sha256 = step_sha
                        attempt.submit_stage = "STEP_HARVESTED"
                        attempt.payload = {**(attempt.payload or {}), "harvest": source, "step_record": record}
                        task.external_outcome = "SUCCEEDED"
                        task.payload = {
                            **(task.payload or {}),
                            "character_sheet_progress": {
                                "current_step": spec.key,
                                "current_step_label": spec.label,
                                "completed_steps": list(step_records),
                                "total_steps": len(STEP_SPECS),
                            },
                        }
                        task.state_version += 1
                        task.heartbeat_at = utcnow()
                        task.updated_at = utcnow()
                        expected_state = task.state_version
                except image_execution.ImageExecutionError as exc:
                    unknown = exc.code == "IMAGE_OUTCOME_RECONCILIATION_REQUIRED"
                    _set_attempt_and_task(
                        factory,
                        task_id,
                        attempt_id,
                        task_status="UNKNOWN" if unknown else "FAILED",
                        attempt_status="UNKNOWN" if unknown else "FAILED",
                        outcome="UNKNOWN" if unknown else "FAILED",
                        error_code=exc.code,
                        prompt_id=prompt_id,
                        expected_state=next_state,
                        details={"step_key": spec.key},
                    )
                    return
                except Exception as exc:  # noqa: BLE001 - post-submit unknown fault must not auto-retry
                    gone = image_execution._renderer_gone(factory, settings, renderer)
                    _set_attempt_and_task(
                        factory,
                        task_id,
                        attempt_id,
                        task_status="UNKNOWN",
                        attempt_status="UNKNOWN",
                        outcome="UNKNOWN",
                        error_code=("IMAGE_RENDERER_GONE" if gone else f"IMAGE_POST_SUBMIT_{type(exc).__name__}"),
                        prompt_id=prompt_id,
                        expected_state=next_state,
                        details={"step_key": spec.key, "error_type": type(exc).__name__, "renderer_gone": gone},
                    )
                    return

            try:
                composed = compose_character_sheet(
                    closeup_front_bytes=step_outputs["closeup_front"],
                    closeup_3_4_bytes=step_outputs["closeup_3_4"],
                    front_bytes=step_outputs["fullbody_front"],
                    side_bytes=step_outputs["fullbody_side"],
                    back_bytes=step_outputs["fullbody_back"],
                )
                _save_character_sheet_candidates(
                    factory,
                    settings,
                    task_id,
                    payload,
                    closeup_front_bytes=step_outputs["closeup_front"],
                    closeup_front_prompt=step_prompts["closeup_front"],
                    closeup_front_graph_sha=step_graph_hashes["closeup_front"],
                    closeup_3_4_bytes=step_outputs["closeup_3_4"],
                    closeup_3_4_prompt=step_prompts["closeup_3_4"],
                    closeup_3_4_graph_sha=step_graph_hashes["closeup_3_4"],
                    triview_bytes=composed["fullbody_triview_bytes"],
                    triview_prompt=step_prompts["fullbody_front"],
                    triview_graph_sha=canonical_hash(
                        [
                            step_graph_hashes["fullbody_front"],
                            step_graph_hashes["fullbody_side"],
                            step_graph_hashes["fullbody_back"],
                        ]
                    ),
                    composite_bytes=composed["composite_sheet_bytes"],
                    composite_layout=composed["layout"],
                    step_records=step_records,
                    fingerprint=resolved["fingerprint"],
                    renderer=renderer,
                    lease_id=lease_id,
                    expected_state=expected_state,
                )
            except Exception as exc:  # noqa: BLE001 - composition/save failure after 4 steps must release lease & avoid stuck RUNNING
                with factory.begin() as db:
                    task = db.get(LibraryRenderTask, task_id)
                    if task and task.status == "RUNNING":
                        task.status = "FAILED"
                        task.error_code = f"CHARACTER_SHEET_{type(exc).__name__}"
                        task.worker_id = None
                        task.lease_owner = None
                        task.state_version += 1
                        task.updated_at = utcnow()
                        task.heartbeat_at = utcnow()
    except Exception as exc:  # noqa: BLE001 - pre-submit failure only when step 1 has not opened intent
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if (
                task
                and task.status == "RUNNING"
                and task.external_outcome == "NOT_SENT"
                and task.submit_intent_at is None
            ):
                task.status = "QUEUED" if "GPU_BUSY_RETRY_OR_QUEUE" in str(exc) else "FAILED"
                if isinstance(exc, image_execution.ImageExecutionError):
                    task.error_code = exc.details.get("code_detail") or exc.code
                elif isinstance(exc, StyleLoraError):
                    task.error_code = exc.code
                else:
                    task.error_code = (
                        "GPU_BUSY_RETRY_OR_QUEUE" if "GPU_BUSY_RETRY_OR_QUEUE" in str(exc) else type(exc).__name__
                    )
                task.worker_id = None
                task.lease_owner = None
                task.state_version += 1
                task.updated_at = utcnow()


def _execute_mask_cutout(
    factory, settings, task_id: str, worker_id: str, payload: dict[str, Any], expected_state: int
) -> None:
    attempt_id = None
    try:
        references = sorted(payload.get("references") or [], key=lambda row: row["order"])
        if len(references) != 1:
            raise RuntimeError("RGBA_CUTOUT_SINGLE_SOURCE_REQUIRED")
        source, _ = _read_reference(factory, settings, references[0])
        mask = _read_mask(factory, settings, payload.get("mask") or {})
        _verify_parent_snapshot(factory, settings, payload)
        output = cutout_png_by_mask(source, mask)
        operation_hash = canonical_hash(
            {
                "operation": "MASK_CUTOUT_V1",
                "source_sha256": references[0]["sha256"],
                "mask_sha256": (payload.get("mask") or {}).get("sha256"),
                "output": payload.get("output"),
            }
        )
        attempt_id = _open_local_attempt(factory, task_id, worker_id, payload, operation_hash, expected_state)
        _save_candidate(
            factory,
            settings,
            task_id,
            payload,
            output,
            operation_hash,
            "local-mask-cutout-v1",
            None,
            {"renderer_owned": False, "renderer_instance_id": ""},
            attempt_id,
        )
    except Exception as exc:  # noqa: BLE001 - local transforms have no external outcome to reconcile
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if task and task.status == "RUNNING" and task.external_outcome == "NOT_SENT":
                task.status = "FAILED"
                task.error_code = f"RGBA_CUTOUT_{type(exc).__name__}"
                task.worker_id = None
                task.lease_owner = None
                task.state_version += 1
                task.updated_at = utcnow()
            if attempt_id:
                attempt = db.get(LibraryTaskAttempt, attempt_id)
                if attempt:
                    attempt.status = "FAILED"
                    attempt.external_outcome = "NOT_SENT"
                    attempt.submit_stage = "LOCAL_FAILED"
                    attempt.error_code = f"RGBA_CUTOUT_{type(exc).__name__}"


def _finish_without_submit(factory, task_id: str, status: str, error_code: str) -> None:
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task and task.status == "RUNNING" and task.external_outcome == "NOT_SENT" and task.submit_intent_at is None:
            task.status = status
            task.error_code = error_code
            task.worker_id = None
            task.state_version += 1
            task.updated_at = utcnow()


def run_one_pending_job(factory, settings, worker_id: str) -> int:
    """Process at most one projectless T2I job after project and H3 work."""
    task_id = _claim_next(factory, worker_id)
    if task_id is None:
        return 0
    _execute_one(factory, settings, task_id, worker_id)
    return 1


def recover_stale_projectless_tasks(factory, *, stale_minutes: int = 30) -> int:
    """Recover worker claims without ever replaying an intent that may have run."""
    cutoff = utcnow() - timedelta(minutes=stale_minutes)
    recovered = 0
    with factory.begin() as db:
        rows = db.scalars(
            select(LibraryRenderTask)
            .where(
                LibraryRenderTask.kind.in_({"image", "voice"}),
                LibraryRenderTask.status == "RUNNING",
                LibraryRenderTask.updated_at < cutoff,
            )
            .with_for_update()
        ).all()
        for task in rows:
            submitted_or_uncertain = bool(
                task.submit_intent_at is not None or task.external_prompt_id or task.external_outcome != "NOT_SENT"
            )
            task.status = "UNKNOWN" if submitted_or_uncertain else "QUEUED"
            task.error_code = "WORKER_HEARTBEAT_EXPIRED" if submitted_or_uncertain else None
            task.worker_id = None
            task.lease_owner = None
            task.state_version += 1
            task.updated_at = utcnow()
            task.heartbeat_at = utcnow()
            attempts = db.scalars(
                select(LibraryTaskAttempt).where(
                    LibraryTaskAttempt.task_id == task.id,
                    LibraryTaskAttempt.status.in_({"SUBMIT_INTENT", "SUBMITTED", "UNKNOWN"}),
                )
            ).all()
            if submitted_or_uncertain:
                task.external_outcome = "UNKNOWN"
                for attempt in attempts:
                    attempt.status = "UNKNOWN"
                    attempt.external_outcome = "UNKNOWN"
                    attempt.error_code = "WORKER_HEARTBEAT_EXPIRED"
                    attempt.submit_stage = "UNKNOWN"
            add_audit(
                db,
                "shine-worker",
                "LIBRARY_TASK_STALE_RECOVERED",
                "library_render_task",
                task.id,
                {"status": task.status, "submit_intent": submitted_or_uncertain},
            )
            recovered += 1
    return recovered


def reconcile_completed_history(
    factory, settings, task_id: str, prompt_id: str, history: dict[str, Any], client, evidence: str
) -> dict[str, Any]:
    """Adopt a known Comfy result exactly once; this path never submits work."""
    history_status = str((history.get("status") or {}).get("status_str") or "")
    outputs = history.get("outputs") or {}
    with factory() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None or task.external_prompt_id != prompt_id:
            raise RuntimeError("LIBRARY_RECONCILIATION_PROMPT_MISMATCH")
        if task.status in {"CANDIDATE_READY", "QC_FAILED"} and task.result_version_id:
            return {"status": task.status, "version_id": task.result_version_id, "reconciliation": "ALREADY_ADOPTED"}
        if task.status not in {"UNKNOWN", "RECONCILE_REQUIRED", "RECONCILING"}:
            raise RuntimeError("LIBRARY_TASK_NOT_RECONCILABLE")
        expected_state = task.state_version
        prior_status = task.status
        prior_heartbeat = task.heartbeat_at
        attempt = db.scalar(
            select(LibraryTaskAttempt)
            .where(LibraryTaskAttempt.task_id == task_id, LibraryTaskAttempt.external_prompt_id == prompt_id)
            .order_by(LibraryTaskAttempt.attempt_no.desc())
            .limit(1)
        )
        if attempt is None:
            raise RuntimeError("LIBRARY_ATTEMPT_NOT_FOUND")
        attempt_id = attempt.id
        attempt_payload = dict(attempt.payload or {})
        payload = dict(task.payload or {})
    if prior_status == "RECONCILING" and (prior_heartbeat is None or prior_heartbeat > utcnow() - timedelta(hours=1)):
        return {"status": "RECONCILING", "reconciliation": "ALREADY_IN_PROGRESS"}

    if history_status == "error":
        with factory.begin() as db:
            updated = db.execute(
                update(LibraryRenderTask)
                .where(
                    LibraryRenderTask.id == task_id,
                    LibraryRenderTask.state_version == expected_state,
                    LibraryRenderTask.external_prompt_id == prompt_id,
                    LibraryRenderTask.status == prior_status,
                )
                .values(
                    status="FAILED",
                    error_code="IMAGE_EXECUTION_FAILED",
                    worker_id=None,
                    lease_owner=None,
                    state_version=expected_state + 1,
                    updated_at=utcnow(),
                    heartbeat_at=utcnow(),
                )
            )
            if updated.rowcount != 1:
                raise RuntimeError("LIBRARY_RECONCILIATION_RACE")
            attempt = db.get(LibraryTaskAttempt, attempt_id)
            if attempt:
                attempt.status = "FAILED"
                attempt.external_outcome = "FAILED"
                attempt.error_code = "IMAGE_EXECUTION_FAILED"
                attempt.submit_stage = "FAILED"
            add_audit(
                db,
                "local-user",
                "LIBRARY_TASK_RECONCILED_FAILED",
                "library_render_task",
                task_id,
                {"prompt_id": prompt_id, "history_status": history_status, "evidence": evidence},
            )
        return {"status": "FAILED", "reconciliation": "COMFY_HISTORY_FAILED"}

    if not outputs and not (history.get("status") or {}).get("completed"):
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if task and task.state_version == expected_state:
                task.payload = {
                    **(task.payload or {}),
                    "reconciliation": {"history_status": history_status or "pending", "evidence": evidence},
                }
                add_audit(
                    db,
                    "local-user",
                    "LIBRARY_TASK_RECONCILIATION_PENDING",
                    "library_render_task",
                    task_id,
                    {"prompt_id": prompt_id, "history_status": history_status or "pending"},
                )
        return {"status": prior_status, "reconciliation": "HISTORY_PENDING"}

    if payload.get("i2i_operation") == CHARACTER_SHEET_OPERATION:
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if task is None or task.state_version != expected_state or task.external_prompt_id != prompt_id:
                raise RuntimeError("LIBRARY_RECONCILIATION_RACE")
            task.status = "UNKNOWN"
            task.error_code = "CHARACTER_SHEET_PARTIAL_RECONCILED"
            task.worker_id = None
            task.lease_owner = None
            task.state_version = expected_state + 1
            task.updated_at = utcnow()
            task.heartbeat_at = utcnow()
            task.payload = {
                **(task.payload or {}),
                "reconciliation": {
                    "history_status": history_status or "completed",
                    "prompt_id": prompt_id,
                    "step_key": attempt_payload.get("step_key"),
                    "output_nodes": sorted(outputs),
                    "evidence": evidence,
                },
            }
            add_audit(
                db,
                "local-user",
                "LIBRARY_CHARACTER_SHEET_STEP_RECONCILED",
                "library_render_task",
                task_id,
                {
                    "prompt_id": prompt_id,
                    "step_key": attempt_payload.get("step_key"),
                    "output_nodes": sorted(outputs),
                    "evidence": evidence,
                },
            )
        return {
            "status": "UNKNOWN",
            "reconciliation": "CHARACTER_SHEET_STEP_RECONCILED_INCOMPLETE",
            "step_key": attempt_payload.get("step_key"),
            "prompt_id": prompt_id,
        }

    token = f"reconcile:{uuid.uuid4()}"
    claim = update(LibraryRenderTask).where(
        LibraryRenderTask.id == task_id,
        LibraryRenderTask.state_version == expected_state,
        LibraryRenderTask.external_prompt_id == prompt_id,
        LibraryRenderTask.status == prior_status,
    )
    if prior_status == "RECONCILING":
        claim = claim.where(LibraryRenderTask.heartbeat_at == prior_heartbeat)
    with factory.begin() as db:
        result = db.execute(
            claim.values(
                status="RECONCILING",
                worker_id=token,
                heartbeat_at=utcnow(),
                state_version=expected_state + 1,
                updated_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("LIBRARY_RECONCILIATION_RACE")
        add_audit(
            db,
            "local-user",
            "LIBRARY_TASK_RECONCILIATION_STARTED",
            "library_render_task",
            task_id,
            {"prompt_id": prompt_id, "output_nodes": sorted(outputs), "evidence": evidence},
        )

    try:
        content, harvest = image_execution._harvest(client, history)
        renderer = {
            "renderer_owned": bool(attempt_payload.get("renderer_owned")),
            "renderer_instance_id": attempt_payload.get("renderer_instance_id"),
        }
        version_id, digest, status = _save_candidate(
            factory,
            settings,
            task_id,
            payload,
            content,
            str(attempt_payload.get("graph_sha256") or ""),
            str(attempt_payload.get("runtime_fingerprint") or ""),
            prompt_id,
            renderer,
            attempt_id,
        )
        with factory.begin() as db:
            attempt = db.get(LibraryTaskAttempt, attempt_id)
            if attempt:
                attempt.payload = {
                    **(attempt.payload or {}),
                    "harvest": harvest,
                    "version_id": version_id,
                    "reconciled": True,
                }
            add_audit(
                db,
                "local-user",
                "LIBRARY_TASK_RECONCILIATION_ADOPTED",
                "library_render_task",
                task_id,
                {"prompt_id": prompt_id, "version_id": version_id, "sha256": digest, "status": status},
            )
        return {"status": status, "version_id": version_id, "reconciliation": "CANDIDATE_ADOPTED"}
    except Exception:
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if task and task.status == "RECONCILING" and task.worker_id == token:
                task.status = "UNKNOWN"
                task.error_code = "IMAGE_RECONCILIATION_HARVEST_FAILED"
                task.worker_id = None
                task.lease_owner = None
                task.state_version += 1
                task.updated_at = utcnow()
                attempt = db.get(LibraryTaskAttempt, attempt_id)
                if attempt:
                    attempt.status = "UNKNOWN"
                    attempt.external_outcome = "UNKNOWN"
                    attempt.error_code = task.error_code
                add_audit(
                    db,
                    "local-user",
                    "LIBRARY_TASK_RECONCILIATION_FAILED",
                    "library_render_task",
                    task_id,
                    {"prompt_id": prompt_id, "error_code": task.error_code},
                )
        raise
