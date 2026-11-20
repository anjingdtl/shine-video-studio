"""Dedicated resident worker for Manual4 Ref2VA production runs.

Isolation & Single-Executor invariants:
* Claims exclusively from ``manual4.sqlite`` (``m4_production_runs`` / ``m4_jobs``).
* Never reads or writes seven-stage ``jobs``, ``job_attempts``, or ``production_series``.
* Holds ONE platform GPU lease (``platform_gpu``) and ONE managed ComfyUI runtime
  (port 8198+, ``<m4_root>/comfy-runtime/``) across an entire ``M4ProductionRun``
  series, releasing both cleanly once the run finishes.
* Persists submit intent BEFORE calling ``/prompt`` and marks uncertain HTTP
  submissions ``NEEDS_REVIEW`` without blind re-submission.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from shine.config import Settings
from shine.foundation.h3 import (
    ComfyH3Executor,
    ComfySubmissionUncertain,
    GraphCompileError,
    H3ExecutionError,
    H3ExecutionRequest,
    H3ReferenceInput,
    SecondPassSpec,
    compile_h3_ref2va_graph,
    evaluate_h3_technical_qc,
    extract_last_frame_png,
    extract_video_output,
    reconcile_submission,
)
from shine.foundation.materials import resolve_library_version_file
from shine.manual4.comfy.client import M4ComfySubmissionUncertain
from shine.manual4.comfy.managed_instance import managed_m4_comfyui, reconcile_m4_renderer
from shine.manual4.delivery.technical_qc import M4TechnicalExpectations, run_m4_technical_qc
from shine.manual4.prompts.service import validate_frozen_prompt_for_shot
from shine.manual4.storage.db import make_m4_engine, make_m4_session_factory, manual4_data_root, manual4_database_url
from shine.manual4.storage.files import safe_m4_filename, safe_m4_path, sha256_bytes, store_m4_bytes
from shine.manual4.storage.migrations import MANUAL4_RUNTIME_VERSION, ensure_m4_schema
from shine.manual4.storage.models import (
    M4AuditEvent,
    M4ExecutionSnapshot,
    M4Job,
    M4JobAttempt,
    M4ManualPromptRevision,
    M4ProductionRun,
    M4Shot,
    M4WorkerInstance,
    m4_utcnow,
)


def register_m4_worker(m4_factory: sessionmaker, worker_id: str, *, status: str = "IDLE") -> None:
    now = m4_utcnow()
    with m4_factory.begin() as db:
        row = db.get(M4WorkerInstance, worker_id)
        if row is None:
            row = M4WorkerInstance(
                worker_id=worker_id,
                pid=os.getpid(),
                version=MANUAL4_RUNTIME_VERSION,
                status=status,
                last_heartbeat_at=now,
            )
            db.add(row)
        else:
            row.pid = os.getpid()
            row.version = MANUAL4_RUNTIME_VERSION
            row.status = status
            row.last_heartbeat_at = now


def heartbeat_m4_worker(
    m4_factory: sessionmaker,
    worker_id: str,
    *,
    status: str | None = None,
    current_run_id: str | None = None,
) -> None:
    now = m4_utcnow()
    with m4_factory.begin() as db:
        row = db.get(M4WorkerInstance, worker_id)
        if row is not None:
            row.last_heartbeat_at = now
            if status is not None:
                row.status = status
            row.current_run_id = current_run_id


def _resolve_manifest_ref_path(
    m4_root: Path,
    item: dict[str, Any],
    *,
    platform_factory: sessionmaker | None = None,
    settings: Any = None,
) -> Path:
    storage_path = str(item.get("storage_path") or "")
    if storage_path.startswith("library:"):
        version_id = storage_path.split(":", 1)[1]
        if platform_factory is None or settings is None:
            raise H3ExecutionError("M4_LIBRARY_CONTEXT_MISSING", "缺少公共素材库会话上下文，无法解析引用路径。")
        with platform_factory() as p_db:
            path, _ct, _sha = resolve_library_version_file(
                p_db,
                settings,
                version_id,
                expected_sha256=item.get("sha256"),
            )
            return path
    return safe_m4_path(m4_root, storage_path)


def _qc_status_for(qc: dict[str, Any]) -> str:
    res = str(qc.get("result") or "failed").lower()
    if res == "passed":
        return "PASSED"
    if res == "needs_review":
        return "NEEDS_REVIEW"
    return "FAILED"


def reconcile_stalled_m4_runs(
    m4_factory: sessionmaker,
    *,
    stale_seconds: int = 120,
    comfy_client: Any = None,
    m4_root: Path | None = None,
    settings: Any = None,
) -> int:
    """Recover runs left behind by crashed Manual4 workers without blind resubmits.

    When a ComfyUI client is reachable, attempts in ``SUBMITTING``/``RUNNING`` are
    reconciled against ``/history`` and ``/queue`` by ``(prompt_id, prompt_graph_sha256)``
    before escalating unresolved ones to ``NEEDS_REVIEW``.
    """
    recovered = 0
    cutoff = m4_utcnow() - timedelta(seconds=stale_seconds)
    now = m4_utcnow()
    pending_reconcile: list[tuple[str, str, str, str, str | None, str | None, float, bool]] = []

    with m4_factory.begin() as db:
        active_runs = db.scalars(
            select(M4ProductionRun).where(M4ProductionRun.status.in_(["CLAIMED", "RUNNING"]))
        ).all()
        for run in active_runs:
            worker = db.get(M4WorkerInstance, run.worker_id) if run.worker_id else None
            is_stale = (
                worker is None
                or worker.status == "OFFLINE"
                or worker.last_heartbeat_at < cutoff
            )
            if not is_stale:
                continue
            snap = db.get(M4ExecutionSnapshot, run.execution_snapshot_id)
            snap_by_shot = {str(it["shot_id"]): it for it in (snap.items_json if snap else [])}
            jobs = db.scalars(
                select(M4Job).where(M4Job.run_id == run.id).order_by(M4Job.ordinal)
            ).all()
            has_uncertain = False
            has_queued = False
            for job in jobs:
                if job.status in {"SUBMITTING", "RUNNING", "QC_PENDING"}:
                    latest_att = db.scalar(
                        select(M4JobAttempt)
                        .where(
                            M4JobAttempt.job_id == job.id,
                            M4JobAttempt.status.in_(["SUBMITTING", "RUNNING"]),
                        )
                        .order_by(M4JobAttempt.attempt_no.desc())
                        .limit(1)
                    )
                    if (
                        comfy_client is not None
                        and latest_att is not None
                        and (latest_att.prompt_id or latest_att.prompt_graph_sha256)
                    ):
                        item = snap_by_shot.get(job.shot_id) or {}
                        has_audio = any(
                            r.get("use_as") == "audio" for r in (item.get("reference_manifest") or [])
                        )
                        pending_reconcile.append(
                            (
                                run.id,
                                run.project_id,
                                job.id,
                                latest_att.id,
                                latest_att.prompt_id,
                                latest_att.prompt_graph_sha256,
                                float(job.target_seconds),
                                has_audio,
                            )
                        )
                    else:
                        job.status = "NEEDS_REVIEW"
                        job.failure_code = "M4_WORKER_CRASHED_IN_FLIGHT"
                        job.failure_detail = "Worker 在任务执行期间退出，已挂起等待对账，禁止自动重投。"
                        job.state_version += 1
                        job.updated_at = now
                        for att in db.scalars(
                            select(M4JobAttempt).where(
                                M4JobAttempt.job_id == job.id,
                                M4JobAttempt.status.in_(["SUBMITTING", "RUNNING"]),
                            )
                        ).all():
                            att.status = "NEEDS_REVIEW"
                            att.error_code = "M4_WORKER_CRASHED_IN_FLIGHT"
                            att.updated_at = now
                    has_uncertain = True
                elif job.status == "QUEUED":
                    has_queued = True
            if not pending_reconcile:
                if has_uncertain:
                    run.status = "NEEDS_REVIEW"
                elif has_queued:
                    run.status = "PENDING"
                    run.worker_id = None
                    run.lease_id = None
                    run.lease_owner = None
                else:
                    run.status = "COMPLETED" if all(j.status == "PASSED" for j in jobs) else "FAILED"
                run.updated_at = now
                recovered += 1

    # Reconcile in-flight submissions outside DB transaction against ComfyUI /history & /queue
    touched_runs: set[str] = set()
    for run_id, proj_id, job_id, att_id, pid, gsha, dur_s, has_audio in pending_reconcile:
        touched_runs.add(run_id)
        matched_id, state, rec = reconcile_submission(
            comfy_client,
            prompt_id=pid,
            prompt_graph_sha256=gsha,
        )
        if state == "completed" and rec is not None and m4_root is not None:
            try:
                fname, sub, ftype = extract_video_output(rec)
                out_name = safe_m4_filename(fname)
                out_bytes = comfy_client.view(fname, subfolder=sub, file_type=ftype)
                rel_out, out_sha, abs_out = store_m4_bytes(m4_root, proj_id, "renders", out_name, out_bytes)
                qc = evaluate_h3_technical_qc(
                    abs_out,
                    duration_s=dur_s,
                    expect_audio=has_audio,
                    ffprobe_path=getattr(settings, "ffprobe_path", "ffprobe") or "ffprobe",
                    ffmpeg_path=getattr(settings, "ffmpeg_path", "ffmpeg") or "ffmpeg",
                )
                j_status = _qc_status_for(qc)
            except Exception as exc:
                with m4_factory.begin() as db:
                    j = db.get(M4Job, job_id)
                    a = db.get(M4JobAttempt, att_id)
                    if j:
                        j.status = "NEEDS_REVIEW"
                        j.failure_code = "M4_RECONCILE_FETCH_FAILED"
                        j.failure_detail = str(exc)[:400]
                        j.state_version += 1
                        j.updated_at = m4_utcnow()
                    if a:
                        a.prompt_id = a.prompt_id or matched_id
                        a.status = "NEEDS_REVIEW"
                        a.error_code = "M4_RECONCILE_FETCH_FAILED"
                        a.updated_at = m4_utcnow()
                continue

            with m4_factory.begin() as db:
                j = db.get(M4Job, job_id)
                a = db.get(M4JobAttempt, att_id)
                if j:
                    j.status = j_status
                    j.output_storage_path = rel_out
                    j.output_sha256 = out_sha
                    j.output_bytes = len(out_bytes)
                    j.measured_duration_s = qc["duration_s"]
                    j.measured_width = qc["width"]
                    j.measured_height = qc["height"]
                    j.measured_fps = qc["fps"]
                    j.qc_result = qc
                    j.failure_code = None if j_status == "PASSED" else f"M4_TECHNICAL_QC_{j_status}"
                    j.state_version += 1
                    j.updated_at = m4_utcnow()
                if a:
                    a.prompt_id = a.prompt_id or matched_id
                    a.output_filename = out_name
                    a.output_sha256 = out_sha
                    a.output_bytes = len(out_bytes)
                    a.status = j_status
                    a.updated_at = m4_utcnow()
            continue

        with m4_factory.begin() as db:
            j = db.get(M4Job, job_id)
            a = db.get(M4JobAttempt, att_id)
            if state == "failed":
                if j:
                    j.status = "FAILED"
                    j.failure_code = "COMFY_EXECUTION_ERROR"
                    j.state_version += 1
                    j.updated_at = m4_utcnow()
                if a:
                    a.prompt_id = a.prompt_id or matched_id
                    a.status = "FAILED"
                    a.error_code = "COMFY_EXECUTION_ERROR"
                    a.updated_at = m4_utcnow()
            else:
                if j:
                    j.status = "NEEDS_REVIEW"
                    j.failure_code = (
                        "M4_COMFY_STILL_ACTIVE" if state in {"running", "pending"} else "M4_WORKER_CRASHED_IN_FLIGHT"
                    )
                    j.state_version += 1
                    j.updated_at = m4_utcnow()
                if a:
                    a.prompt_id = a.prompt_id or matched_id
                    a.status = "NEEDS_REVIEW"
                    a.error_code = j.failure_code if j else "M4_WORKER_CRASHED_IN_FLIGHT"
                    a.updated_at = m4_utcnow()

    for run_id in touched_runs:
        with m4_factory.begin() as db:
            run = db.get(M4ProductionRun, run_id)
            if run is None:
                continue
            jobs = db.scalars(select(M4Job).where(M4Job.run_id == run_id)).all()
            if any(j.status == "NEEDS_REVIEW" for j in jobs):
                run.status = "NEEDS_REVIEW"
            elif jobs and all(j.status == "PASSED" for j in jobs):
                run.status = "COMPLETED"
            elif any(j.status == "QUEUED" for j in jobs):
                run.status = "PENDING"
                run.worker_id = None
                run.lease_id = None
                run.lease_owner = None
            else:
                run.status = "FAILED"
            run.updated_at = m4_utcnow()
            recovered += 1
    return recovered


def _claim_next_run(m4_factory: sessionmaker, worker_id: str) -> str | None:
    now = m4_utcnow()
    with m4_factory.begin() as db:
        candidate_id = db.scalar(
            select(M4ProductionRun.id)
            .where(M4ProductionRun.status == "PENDING")
            .order_by(M4ProductionRun.created_at.asc())
            .limit(1)
        )
        if candidate_id is None:
            return None
        res = db.execute(
            update(M4ProductionRun)
            .where(M4ProductionRun.id == candidate_id, M4ProductionRun.status == "PENDING")
            .values(
                status="RUNNING",
                worker_id=worker_id,
                lease_owner=f"m4-worker-{worker_id}",
                updated_at=now,
            )
        )
        if res.rowcount != 1:
            return None
        return candidate_id


def _load_run_context(
    m4_factory: sessionmaker,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    with m4_factory() as db:
        run = db.get(M4ProductionRun, run_id)
        if run is None:
            raise RuntimeError(f"M4ProductionRun {run_id} disappeared")
        snap = db.get(M4ExecutionSnapshot, run.execution_snapshot_id)
        if snap is None:
            raise RuntimeError(f"M4ExecutionSnapshot {run.execution_snapshot_id} missing")
        queued_job_ids = db.scalars(
            select(M4Job.id)
            .where(M4Job.run_id == run_id, M4Job.status == "QUEUED")
            .order_by(M4Job.ordinal.asc())
        ).all()
        items_by_shot = {str(item["shot_id"]): item for item in snap.items_json}
        return (
            {
                "id": run.id,
                "project_id": run.project_id,
                "provider": run.provider,
                "execution_generation": int(getattr(run, "execution_generation", 1) or 1),
                "config": snap.config_json,
            },
            items_by_shot,
            list(queued_job_ids),
        )


def _fail_job_precheck(
    m4_factory: sessionmaker,
    job_id: str,
    *,
    expected_state_ver: int,
    status: str,
    code: str,
    detail: str,
) -> str:
    with m4_factory.begin() as db:
        job = db.get(M4Job, job_id)
        if job and job.state_version == expected_state_ver:
            job.status = status
            job.failure_code = code
            job.failure_detail = detail
            job.state_version += 1
            job.updated_at = m4_utcnow()
    return status


def _execute_single_job(
    m4_factory: sessionmaker,
    m4_root: Path,
    run_info: dict[str, Any],
    job_id: str,
    snap_item: dict[str, Any],
    *,
    worker_id: str,
    comfy_client: Any = None,
    object_info: dict[str, Any] | None = None,
    video_stub: Any = None,
    platform_factory: sessionmaker | None = None,
    settings: Any = None,
) -> str:
    # 1. Verify frozen prompt + reference integrity + continuity dependency BEFORE I/O or renderer
    prev_video_rel: str | None = None
    prev_output_sha: str | None = None
    exec_gen = int(run_info.get("execution_generation", 2) or 2)
    with m4_factory.begin() as db:
        run = db.get(M4ProductionRun, run_info["id"])
        job = db.get(M4Job, job_id)
        if run is None or job is None or job.status != "QUEUED":
            return "SKIPPED"
        if run.status in {"PAUSED", "PAUSE_REQUESTED", "CANCELLED"}:
            return "STOPPED"

        exec_gen = int(getattr(job, "execution_generation", exec_gen) or exec_gen)
        expected_state_ver = job.state_version
        prompt_text = str(snap_item["prompt_text"])
        shot = db.get(M4Shot, job.shot_id)
        prompt_row = db.get(M4ManualPromptRevision, job.prompt_revision_id) if job.prompt_revision_id else None
        ok, err_code = validate_frozen_prompt_for_shot(db, shot, prompt_row) if shot else (False, "M4_SHOT_NOT_FOUND")
        actual_snap_sha = sha256_bytes(prompt_text.encode())
        if (
            not ok
            or prompt_row is None
            or prompt_row.prompt_sha256 != job.prompt_sha256
            or actual_snap_sha != job.prompt_sha256
        ):
            job.status = "FAILED"
            job.failure_code = (
                err_code
                if (not ok and err_code)
                else (
                    "M4_MANUAL_PROMPT_SHA_MISMATCH"
                    if actual_snap_sha != job.prompt_sha256
                    else "M4_MANUAL_PROMPT_INVALIDATED"
                )
            )
            job.failure_detail = "排片提交后分镜、参考素材或人工提示词发生变更，拒绝使用旧文本继续出片。"
            job.state_version += 1
            job.updated_at = m4_utcnow()
            return "FAILED"

        if job.continuity_mode == "last_frame" and job.previous_job_id:
            prev_job = db.get(M4Job, job.previous_job_id)
            if (
                prev_job is None
                or prev_job.status != "PASSED"
                or not prev_job.output_storage_path
                or not prev_job.output_sha256
            ):
                job.status = "BLOCKED"
                job.failure_code = "M4_CONTINUITY_SOURCE_NOT_PASSED"
                job.failure_detail = "前一衔接镜头未通过质检，禁止推测或强行衔接。"
                job.state_version += 1
                job.updated_at = m4_utcnow()
                return "BLOCKED"
            prev_video_rel = prev_job.output_storage_path
            prev_output_sha = prev_job.output_sha256

        cfg = run_info["config"]
        sp_cfg = cfg.get("second_pass")
        sp_spec = (
            SecondPassSpec(
                denoise=float(sp_cfg.get("denoise", 0.25)),
                latent_scale=float(sp_cfg.get("latent_scale", 1.0)),
                nfe=str(sp_cfg.get("nfe", "8")),
            )
            if isinstance(sp_cfg, dict) and sp_cfg.get("enabled")
            else None
        )
        ref_items = list(snap_item.get("reference_manifest") or [])
        img_items = [r for r in ref_items if r["use_as"] == "image"]
        aud_items = [r for r in ref_items if r["use_as"] == "audio"]
        vid_items = [r for r in ref_items if r["use_as"] == "video"]
        attempt_no_base = int(job.attempt_count)
        target_seconds = float(snap_item["target_seconds"])
        width = int(snap_item["width"])
        height = int(snap_item["height"])
        seed = int(snap_item["seed"])
        variant_id = str(cfg.get("variant_id", "h3-ref2va-int8-convrot"))
        accelerator_id = str(cfg.get("accelerator_id", "pdd8"))

    # 2. Disk I/O, SHA checks & FFmpeg tail frame extraction OUTSIDE any SQLite transaction
    h3_refs: list[H3ReferenceInput] = []
    ref_files_to_upload: list[tuple[str, str, bytes, str]] = []
    try:
        for idx, item in enumerate(img_items):
            p = _resolve_manifest_ref_path(m4_root, item, platform_factory=platform_factory, settings=settings)
            if not p.is_file():
                return _fail_job_precheck(
                    m4_factory,
                    job_id,
                    expected_state_ver=expected_state_ver,
                    status="FAILED",
                    code="M4_REFERENCE_FILE_MISSING",
                    detail=f"参考素材文件丢失：{item.get('role_label')}",
                )
            raw = p.read_bytes()
            if sha256_bytes(raw) != item["sha256"]:
                return _fail_job_precheck(
                    m4_factory,
                    job_id,
                    expected_state_ver=expected_state_ver,
                    status="FAILED",
                    code="M4_REFERENCE_SHA_MISMATCH",
                    detail=f"参考素材字节校验不一致：{item.get('role_label')}",
                )
            h3_refs.append(
                H3ReferenceInput(
                    family="image",
                    path=p,
                    filename=safe_m4_filename(p.name),
                    content_type=str(item.get("content_type") or "image/png"),
                    sha256=str(item["sha256"]),
                    role_label=str(item.get("role_label") or ""),
                )
            )
            ref_files_to_upload.append(
                (f"reference_image_{idx + 1}", safe_m4_filename(p.name), raw, item["content_type"])
            )

        if prev_video_rel is not None and prev_output_sha is not None:
            prev_video_path = safe_m4_path(m4_root, prev_video_rel)
            if not prev_video_path.is_file() or sha256_bytes(prev_video_path.read_bytes()) != prev_output_sha:
                return _fail_job_precheck(
                    m4_factory,
                    job_id,
                    expected_state_ver=expected_state_ver,
                    status="FAILED",
                    code="M4_CONTINUITY_SOURCE_SHA_MISMATCH",
                    detail="前一衔接成片缺失或校验和变化，拒绝续帧。",
                )
            frame_png = safe_m4_path(m4_root, f"projects/{run_info['project_id']}/continuity/c_{job_id}.png")
            ffmpeg_bin = (getattr(settings, "ffmpeg_path", "ffmpeg") if settings else None) or "ffmpeg"
            extract_last_frame_png(prev_video_path, frame_png, ffmpeg_path=ffmpeg_bin)
            raw_frame = frame_png.read_bytes()
            frame_sha = sha256_bytes(raw_frame)
            h3_refs.append(
                H3ReferenceInput(
                    family="image",
                    path=frame_png,
                    filename=frame_png.name,
                    content_type="image/png",
                    sha256=frame_sha,
                    role_label="last_frame_continuity",
                )
            )
            ref_files_to_upload.append(
                (f"reference_image_{len(img_items) + 1}", frame_png.name, raw_frame, "image/png")
            )

        for idx, item in enumerate(aud_items):
            p = _resolve_manifest_ref_path(m4_root, item, platform_factory=platform_factory, settings=settings)
            if not p.is_file():
                return _fail_job_precheck(
                    m4_factory,
                    job_id,
                    expected_state_ver=expected_state_ver,
                    status="FAILED",
                    code="M4_REFERENCE_AUDIO_INVALID",
                    detail=f"参考音频文件缺失：{item.get('role_label')}",
                )
            raw_aud = p.read_bytes()
            if sha256_bytes(raw_aud) != item["sha256"]:
                return _fail_job_precheck(
                    m4_factory,
                    job_id,
                    expected_state_ver=expected_state_ver,
                    status="FAILED",
                    code="M4_REFERENCE_AUDIO_INVALID",
                    detail=f"参考音频哈希不匹配：{item.get('role_label')}",
                )
            h3_refs.append(
                H3ReferenceInput(
                    family="audio",
                    path=p,
                    filename=safe_m4_filename(p.name),
                    content_type=str(item.get("content_type") or "audio/wav"),
                    sha256=str(item["sha256"]),
                    role_label=str(item.get("role_label") or ""),
                    duration_s=item.get("duration_s"),
                )
            )
            ref_files_to_upload.append(
                (f"reference_audio_{idx + 1}", safe_m4_filename(p.name), raw_aud, item["content_type"])
            )

        compiled = compile_h3_ref2va_graph(
            variant_id=variant_id,
            accelerator_id=accelerator_id,
            second_pass=sp_spec,
            image_count=len(img_items) + (1 if prev_video_rel is not None else 0),
            audio_count=len(aud_items),
            video_count=len(vid_items),
            object_info=object_info,
        )
    except (H3ExecutionError, GraphCompileError) as exc:
        return _fail_job_precheck(
            m4_factory,
            job_id,
            expected_state_ver=expected_state_ver,
            status="FAILED",
            code=getattr(exc, "code", "M4_H3_PRECHECK_FAILED"),
            detail=str(exc)[:400],
        )

    # 3. Execute external call OUTSIDE SQLite write transaction, persisting intent right before /prompt
    attempt_id: str | None = None
    out_bytes = b""
    out_filename = f"{snap_item['shot_code']}.mp4"
    is_mock_run = video_stub is not None
    prompt_id = ""
    submitted_graph_sha = compiled.graph_sha256

    def _persist_submit_intent(graph_sha: str, _graph: dict[str, Any] | None = None) -> None:
        nonlocal attempt_id, expected_state_ver, submitted_graph_sha
        submitted_graph_sha = graph_sha
        with m4_factory.begin() as db:
            j_row = db.get(M4Job, job_id)
            if j_row is None or j_row.state_version != expected_state_ver:
                raise H3ExecutionError("M4_STATE_CONFLICT", "任务状态已被并发修改，中止提交。")
            attempt_no = attempt_no_base + 1
            att = M4JobAttempt(
                job_id=job_id,
                attempt_no=attempt_no,
                execution_generation=exec_gen,
                status="SUBMITTING",
                prompt_graph_sha256=graph_sha,
                submit_intent_at=m4_utcnow(),
            )
            db.add(att)
            db.flush()
            attempt_id = att.id
            j_row.attempt_count = attempt_no
            j_row.status = "SUBMITTING"
            j_row.state_version += 1
            j_row.updated_at = m4_utcnow()
            expected_state_ver = j_row.state_version

    def _persist_submitted(pid: str) -> None:
        nonlocal expected_state_ver, prompt_id
        prompt_id = pid
        with m4_factory.begin() as db:
            j_row = db.get(M4Job, job_id)
            a_row = db.get(M4JobAttempt, attempt_id) if attempt_id else None
            if j_row and j_row.state_version == expected_state_ver:
                j_row.status = "RUNNING"
                j_row.state_version += 1
                j_row.updated_at = m4_utcnow()
                expected_state_ver = j_row.state_version
            if a_row:
                a_row.prompt_id = pid
                a_row.prompt_graph_sha256 = submitted_graph_sha
                a_row.submit_ack_at = m4_utcnow()
                a_row.status = "RUNNING"
                a_row.updated_at = m4_utcnow()

    try:
        if video_stub is not None:
            _persist_submit_intent(compiled.graph_sha256)
            stub_res = video_stub(
                job_id=job_id,
                shot_code=snap_item["shot_code"],
                prompt_text=prompt_text,
                duration_s=target_seconds,
                width=width,
                height=height,
                seed=seed,
                references=ref_files_to_upload,
                has_continuity_frame=prev_video_rel is not None,
            )
            out_bytes = stub_res["bytes"] if isinstance(stub_res, dict) else bytes(stub_res)
            is_mock_run = bool(stub_res.get("is_mock", True)) if isinstance(stub_res, dict) else True
            if isinstance(stub_res, dict) and stub_res.get("filename"):
                out_filename = safe_m4_filename(str(stub_res["filename"]))
            default_pid = f"stub-{uuid4().hex[:12]}"
            prompt_id = str(stub_res.get("prompt_id") or default_pid) if isinstance(stub_res, dict) else default_pid
        else:
            if comfy_client is None:
                raise H3ExecutionError("M4_COMFY_CLIENT_UNAVAILABLE", "ComfyUI 客户端不可用。")
            h3_req = H3ExecutionRequest(
                prompt_text=prompt_text,
                prompt_sha256=str(snap_item["prompt_sha256"]),
                duration_s=target_seconds,
                width=width,
                height=height,
                seed=seed,
                references=h3_refs,
                variant_id=variant_id,
                accelerator_id=accelerator_id,
                second_pass=sp_spec,
                extra_data={"shine_line": "manual4", "job_id": job_id, "run_id": run_info["id"]},
            )
            receipt = ComfyH3Executor().execute(
                comfy_client,
                h3_req,
                on_before_submit=_persist_submit_intent,
                on_submitted=_persist_submitted,
                poll_timeout_s=1800.0,
                poll_interval_s=0.5,
            )
            heartbeat_m4_worker(m4_factory, worker_id, status="RUNNING", current_run_id=run_info["id"])
            if receipt.status == "timeout":
                raise ComfySubmissionUncertain(receipt.error or "ComfyUI 轮询超时，需要核对队列与历史。")
            if receipt.status != "completed" or receipt.output_bytes is None:
                raise H3ExecutionError(
                    receipt.error_code or "M4_RENDER_FAILED",
                    receipt.error or "ComfyUI 执行失败。",
                )
            out_filename = safe_m4_filename(receipt.output_filename or out_filename)
            out_bytes = receipt.output_bytes
            prompt_id = receipt.prompt_id or prompt_id
            submitted_graph_sha = receipt.prompt_graph_sha256

    except (M4ComfySubmissionUncertain, ComfySubmissionUncertain) as exc:
        with m4_factory.begin() as db:
            j_row = db.get(M4Job, job_id)
            a_row = db.get(M4JobAttempt, attempt_id) if attempt_id else None
            if j_row:
                j_row.status = "NEEDS_REVIEW"
                j_row.failure_code = "M4_COMFY_SUBMISSION_OUTCOME_UNKNOWN"
                j_row.failure_detail = str(exc)
                j_row.state_version += 1
                j_row.updated_at = m4_utcnow()
            if a_row:
                a_row.status = "NEEDS_REVIEW"
                a_row.error_code = "M4_COMFY_SUBMISSION_OUTCOME_UNKNOWN"
                a_row.error_detail = str(exc)
                a_row.updated_at = m4_utcnow()
        return "NEEDS_REVIEW"
    except Exception as exc:
        with m4_factory.begin() as db:
            j_row = db.get(M4Job, job_id)
            a_row = db.get(M4JobAttempt, attempt_id) if attempt_id else None
            if j_row:
                j_row.status = "FAILED"
                j_row.failure_code = getattr(exc, "code", "M4_RENDER_FAILED")
                j_row.failure_detail = str(exc)[:400]
                j_row.state_version += 1
                j_row.updated_at = m4_utcnow()
            if a_row:
                a_row.status = "FAILED"
                a_row.error_code = getattr(exc, "code", "M4_RENDER_FAILED")
                a_row.error_detail = str(exc)[:400]
                a_row.updated_at = m4_utcnow()
        return "FAILED"

    # 4. Store render output immutably & run Technical QC
    rel_out, out_sha, abs_out = store_m4_bytes(
        m4_root,
        run_info["project_id"],
        "renders",
        out_filename,
        out_bytes,
    )
    expect_audio = len(aud_items) > 0
    if is_mock_run:
        qc = run_m4_technical_qc(
            abs_out,
            expectations=M4TechnicalExpectations(
                duration_s=target_seconds,
                duration_tolerance_s=1.0,
                expect_audio=expect_audio,
                expected_fps=24.0,
                require_decodable=False,
            ),
            ffprobe_path="",
            ffmpeg_path="",
        )
    else:
        qc = evaluate_h3_technical_qc(
            abs_out,
            duration_s=target_seconds,
            duration_tolerance_s=1.0,
            expect_audio=expect_audio,
            ffprobe_path=(getattr(settings, "ffprobe_path", "ffprobe") if settings else None) or "ffprobe",
            ffmpeg_path=(getattr(settings, "ffmpeg_path", "ffmpeg") if settings else None) or "ffmpeg",
        )

    final_status = _qc_status_for(qc)
    with m4_factory.begin() as db:
        j_row = db.get(M4Job, job_id)
        a_row = db.get(M4JobAttempt, attempt_id) if attempt_id else None
        if j_row:
            j_row.status = final_status
            j_row.output_storage_path = rel_out
            j_row.output_sha256 = out_sha
            j_row.output_bytes = len(out_bytes)
            j_row.measured_duration_s = qc["duration_s"]
            j_row.measured_width = qc["width"]
            j_row.measured_height = qc["height"]
            j_row.measured_fps = qc["fps"]
            j_row.qc_result = qc
            j_row.failure_code = (
                None
                if final_status == "PASSED"
                else ("M4_TECHNICAL_QC_NEEDS_REVIEW" if final_status == "NEEDS_REVIEW" else "M4_TECHNICAL_QC_FAILED")
            )
            j_row.failure_detail = "" if final_status == "PASSED" else f"技术质检结论：{qc['result']}"
            j_row.state_version += 1
            j_row.updated_at = m4_utcnow()
        if a_row:
            a_row.prompt_id = a_row.prompt_id or prompt_id
            a_row.prompt_graph_sha256 = submitted_graph_sha
            a_row.output_filename = out_filename
            a_row.output_sha256 = out_sha
            a_row.output_bytes = len(out_bytes)
            a_row.status = final_status
            a_row.updated_at = m4_utcnow()
        db.add(
            M4AuditEvent(
                project_id=run_info["project_id"],
                actor=f"m4-worker-{worker_id}",
                stage="production",
                event_type=f"M4_JOB_{final_status}",
                entity_type="m4_job",
                entity_id=job_id,
                payload={"shot_code": snap_item["shot_code"], "sha256": out_sha, "is_mock": is_mock_run},
            )
        )
    return final_status


def execute_next_m4_run(
    platform_factory: sessionmaker,
    m4_factory: sessionmaker,
    settings: Any,
    m4_root: Path,
    *,
    worker_id: str,
    video_stub: Any = None,
) -> str | None:
    """Claim and run one pending M4ProductionRun end-to-end. Returns run_id or None."""
    run_id = _claim_next_run(m4_factory, worker_id)
    if run_id is None:
        return None

    run_info, items_by_shot, queued_job_ids = _load_run_context(m4_factory, run_id)
    heartbeat_m4_worker(m4_factory, worker_id, status="RUNNING", current_run_id=run_id)

    try:
        if video_stub is not None or run_info["provider"] == "mock":
            for jid in queued_job_ids:
                with m4_factory() as db:
                    job_obj = db.get(M4Job, jid)
                    shot_id = job_obj.shot_id if job_obj else ""
                item = items_by_shot.get(shot_id)
                if item is None:
                    continue
                outcome = _execute_single_job(
                    m4_factory,
                    m4_root,
                    run_info,
                    jid,
                    item,
                    worker_id=worker_id,
                    video_stub=video_stub,
                    platform_factory=platform_factory,
                    settings=settings,
                )
                if outcome in {"STOPPED", "NEEDS_REVIEW"}:
                    break
        else:
            local_enabled = (
                getattr(settings, "render_provider", "mock") == "comfyui"
                or os.getenv("SHINE_ENABLE_LOCAL_EXECUTION", "").strip().lower() in {"1", "true", "yes"}
            )
            if not local_enabled:
                with m4_factory.begin() as db:
                    r = db.get(M4ProductionRun, run_id)
                    if r:
                        r.status = "FAILED"
                        r.error_code = "M4_LOCAL_COMFY_DISABLED"
                        r.error_detail = "本地 ComfyUI 执行未开启（SHINE_ENABLE_LOCAL_EXECUTION=0）。"
                        r.updated_at = m4_utcnow()
                return run_id

            def _on_lease(lease_id: str, lease_owner: str) -> None:
                with m4_factory.begin() as db:
                    r = db.get(M4ProductionRun, run_id)
                    if r is not None:
                        r.lease_id = lease_id
                        r.lease_owner = lease_owner
                        r.updated_at = m4_utcnow()

            def _on_hb() -> None:
                heartbeat_m4_worker(m4_factory, worker_id, status="RUNNING", current_run_id=run_id)

            with managed_m4_comfyui(
                platform_factory,
                m4_factory,
                settings,
                m4_root,
                owner=f"m4-worker-{worker_id}-{run_id[:8]}",
                on_lease_acquired=_on_lease,
                on_heartbeat=_on_hb,
            ) as (client, _spec):
                object_info = client.object_info()
                for jid in queued_job_ids:
                    with m4_factory() as db:
                        job_obj = db.get(M4Job, jid)
                        shot_id = job_obj.shot_id if job_obj else ""
                    item = items_by_shot.get(shot_id)
                    if item is None:
                        continue
                    outcome = _execute_single_job(
                        m4_factory,
                        m4_root,
                        run_info,
                        jid,
                        item,
                        worker_id=worker_id,
                        comfy_client=client,
                        object_info=object_info,
                        platform_factory=platform_factory,
                        settings=settings,
                    )
                    if outcome in {"STOPPED", "NEEDS_REVIEW"}:
                        break
    except Exception as exc:
        with m4_factory.begin() as db:
            r = db.get(M4ProductionRun, run_id)
            if r and r.status in {"CLAIMED", "RUNNING"}:
                r.status = "FAILED"
                r.error_code = getattr(exc, "code", "M4_RUN_EXECUTION_ERROR")
                r.error_detail = str(exc)[:400]
                r.updated_at = m4_utcnow()
    finally:
        with m4_factory.begin() as db:
            r = db.get(M4ProductionRun, run_id)
            if r and r.status in {"CLAIMED", "RUNNING", "PAUSE_REQUESTED"}:
                jobs = db.scalars(select(M4Job).where(M4Job.run_id == run_id)).all()
                if r.status == "PAUSE_REQUESTED":
                    r.status = "PAUSED"
                elif any(j.status == "NEEDS_REVIEW" for j in jobs):
                    r.status = "NEEDS_REVIEW"
                elif jobs and all(j.status == "PASSED" for j in jobs):
                    r.status = "COMPLETED"
                elif any(j.status == "FAILED" for j in jobs):
                    r.status = "FAILED"
                elif any(j.status == "BLOCKED" for j in jobs):
                    r.status = "FAILED"
                else:
                    r.status = "PAUSED"
                r.updated_at = m4_utcnow()
        heartbeat_m4_worker(m4_factory, worker_id, status="IDLE", current_run_id=None)
    return run_id


def serve_worker(*, once: bool = False, poll_interval: float = 2.0, worker_id: str | None = None) -> int:
    settings = Settings.from_env()
    m4_root = manual4_data_root(settings.data_dir)
    m4_root.mkdir(parents=True, exist_ok=True)
    platform_engine = make_m4_engine(settings.database_url)
    platform_factory = make_m4_session_factory(platform_engine)
    m4_engine = make_m4_engine(manual4_database_url(settings.data_dir))
    ensure_m4_schema(m4_engine)
    m4_factory = make_m4_session_factory(m4_engine)

    wid = worker_id or f"m4-{os.getpid()}-{uuid4().hex[:6]}"
    register_m4_worker(m4_factory, wid, status="IDLE")
    reconcile_m4_renderer(platform_factory, m4_factory, settings.data_dir)
    reconcile_stalled_m4_runs(m4_factory, m4_root=m4_root, settings=settings)

    stopping = False

    def _handle_sig(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        while not stopping:
            handled = execute_next_m4_run(platform_factory, m4_factory, settings, m4_root, worker_id=wid)
            if once:
                break
            if handled is None:
                try:
                    reconcile_m4_renderer(platform_factory, m4_factory, settings.data_dir)
                except Exception:
                    pass
                heartbeat_m4_worker(m4_factory, wid, status="IDLE", current_run_id=None)
                time.sleep(max(0.5, poll_interval))
    finally:
        heartbeat_m4_worker(m4_factory, wid, status="OFFLINE", current_run_id=None)
        m4_engine.dispose()
        platform_engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Shine Manual4 isolated Worker")
    parser.add_argument("--once", action="store_true", help="Claim and execute at most one pending run")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--worker-id", type=str, default=None)
    args = parser.parse_args()
    return serve_worker(once=args.once, poll_interval=args.poll_interval, worker_id=args.worker_id)


if __name__ == "__main__":
    raise SystemExit(main())
