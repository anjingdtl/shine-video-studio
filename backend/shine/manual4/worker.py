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

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from shine.config import Settings
from shine.manual4.comfy.client import M4ComfySubmissionUncertain
from shine.manual4.comfy.h3_adapter import (
    M4GraphCompileError,
    M4SecondPassSpec,
    compile_m4_ref2va_workflow,
    extract_last_frame_png,
    extract_video_output,
)
from shine.manual4.comfy.managed_instance import managed_m4_comfyui, reconcile_m4_renderer
from shine.manual4.comfy.workflow import apply_parameters, graph_hash
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


def reconcile_stalled_m4_runs(m4_factory: sessionmaker, *, stale_seconds: int = 120) -> int:
    """Recover runs left behind by crashed Manual4 workers without blind resubmits."""
    recovered = 0
    cutoff = m4_utcnow() - timedelta(seconds=stale_seconds)
    now = m4_utcnow()
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
            jobs = db.scalars(
                select(M4Job).where(M4Job.run_id == run.id).order_by(M4Job.ordinal)
            ).all()
            has_uncertain = False
            has_queued = False
            for job in jobs:
                if job.status in {"SUBMITTING", "RUNNING", "QC_PENDING"}:
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
    return recovered


def _claim_next_run(m4_factory: sessionmaker, worker_id: str) -> str | None:
    now = m4_utcnow()
    with m4_factory.begin() as db:
        candidate = db.scalar(
            select(M4ProductionRun)
            .where(M4ProductionRun.status == "PENDING")
            .order_by(M4ProductionRun.created_at.asc())
            .limit(1)
        )
        if candidate is None:
            return None
        candidate.status = "RUNNING"
        candidate.worker_id = worker_id
        candidate.lease_owner = f"m4-worker-{worker_id}"
        candidate.updated_at = now
        return candidate.id


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
                "config": snap.config_json,
            },
            items_by_shot,
            list(queued_job_ids),
        )


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
) -> str:
    # 1. Verify prompt + reference integrity + continuity dependency BEFORE touching renderer
    prev_video_path: Path | None = None
    with m4_factory.begin() as db:
        run = db.get(M4ProductionRun, run_info["id"])
        job = db.get(M4Job, job_id)
        if run is None or job is None or job.status != "QUEUED":
            return "SKIPPED"
        if run.status in {"PAUSED", "CANCELLED"}:
            return "STOPPED"

        shot = db.get(M4Shot, job.shot_id)
        prompt_row = db.get(M4ManualPromptRevision, job.prompt_revision_id) if job.prompt_revision_id else None
        ok, err_code = validate_frozen_prompt_for_shot(db, shot, prompt_row) if shot else (False, "M4_SHOT_NOT_FOUND")
        if not ok or prompt_row is None or prompt_row.prompt_sha256 != job.prompt_sha256:
            job.status = "FAILED"
            job.failure_code = err_code or "M4_MANUAL_PROMPT_INVALIDATED"
            job.failure_detail = "排片提交后分镜、参考素材或人工提示词发生变更，拒绝使用旧文本继续出片。"
            job.state_version += 1
            job.updated_at = m4_utcnow()
            return "FAILED"

        if job.continuity_mode == "last_frame" and job.previous_job_id:
            prev_job = db.get(M4Job, job.previous_job_id)
            if prev_job is None or prev_job.status != "PASSED" or not prev_job.output_storage_path:
                job.status = "BLOCKED"
                job.failure_code = "M4_CONTINUITY_SOURCE_NOT_PASSED"
                job.failure_detail = "前一衔接镜头未通过质检，禁止推测或强行衔接。"
                job.state_version += 1
                job.updated_at = m4_utcnow()
                return "BLOCKED"
            prev_video_path = safe_m4_path(m4_root, prev_job.output_storage_path)

        cfg = run_info["config"]
        sp_cfg = cfg.get("second_pass")
        sp_spec = (
            M4SecondPassSpec(
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
        extra_frame_count = 1 if prev_video_path is not None else 0

        try:
            compiled = compile_m4_ref2va_workflow(
                variant_id=str(cfg.get("variant_id", "h3-ref2va-int8-convrot")),
                accelerator_id=str(cfg.get("accelerator_id", "pdd8")),
                second_pass=sp_spec,
                aspect_ratio=str(snap_item.get("aspect_ratio", "16:9")),
                resolution_scale=float(cfg.get("resolution_scale", 1.0)),
                image_count=len(img_items) + extra_frame_count,
                audio_count=len(aud_items),
                video_count=len(vid_items),
                object_info=object_info,
            )
        except M4GraphCompileError as exc:
            job.status = "FAILED"
            job.failure_code = exc.code
            job.failure_detail = exc.message
            job.state_version += 1
            job.updated_at = m4_utcnow()
            return "FAILED"

        # 2. Verify reference bytes on disk match frozen manifest digests BEFORE upload
        ref_files_to_upload: list[tuple[str, str, bytes, str]] = []
        for idx, item in enumerate(img_items):
            p = safe_m4_path(m4_root, item["storage_path"])
            if not p.is_file():
                job.status = "FAILED"
                job.failure_code = "M4_REFERENCE_FILE_MISSING"
                job.failure_detail = f"参考素材文件丢失：{item.get('role_label')}"
                job.state_version += 1
                job.updated_at = m4_utcnow()
                return "FAILED"
            raw = p.read_bytes()
            if sha256_bytes(raw) != item["sha256"]:
                job.status = "FAILED"
                job.failure_code = "M4_REFERENCE_SHA_MISMATCH"
                job.failure_detail = f"参考素材字节校验不一致：{item.get('role_label')}"
                job.state_version += 1
                job.updated_at = m4_utcnow()
                return "FAILED"
            ref_files_to_upload.append(
                (f"reference_image_{idx + 1}", safe_m4_filename(p.name), raw, item["content_type"])
            )

        if prev_video_path is not None:
            frame_png = safe_m4_path(m4_root, f"projects/{run_info['project_id']}/continuity/c_{job.id}.png")
            try:
                extract_last_frame_png(prev_video_path, frame_png)
            except M4GraphCompileError as exc:
                job.status = "FAILED"
                job.failure_code = exc.code
                job.failure_detail = exc.message
                job.state_version += 1
                job.updated_at = m4_utcnow()
                return "FAILED"
            raw_frame = frame_png.read_bytes()
            ref_files_to_upload.append(
                (f"reference_image_{len(img_items) + 1}", frame_png.name, raw_frame, "image/png")
            )

        for idx, item in enumerate(aud_items):
            p = safe_m4_path(m4_root, item["storage_path"])
            if not p.is_file() or sha256_bytes(p.read_bytes()) != item["sha256"]:
                job.status = "FAILED"
                job.failure_code = "M4_REFERENCE_AUDIO_INVALID"
                job.failure_detail = f"参考音频文件缺失或哈希不匹配：{item.get('role_label')}"
                job.state_version += 1
                job.updated_at = m4_utcnow()
                return "FAILED"
            ref_files_to_upload.append(
                (f"reference_audio_{idx + 1}", safe_m4_filename(p.name), p.read_bytes(), item["content_type"])
            )

        # Persist submit intent BEFORE external ComfyUI call (Rule: never hold SQLite writer across /prompt)
        attempt_no = int(job.attempt_count) + 1
        attempt = M4JobAttempt(
            job_id=job.id,
            attempt_no=attempt_no,
            status="SUBMITTING",
            prompt_graph_sha256=compiled.graph_sha256,
            submit_intent_at=m4_utcnow(),
        )
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id
        job.attempt_count = attempt_no
        job.status = "SUBMITTING"
        job.state_version += 1
        job.updated_at = m4_utcnow()

        expected_state_ver = job.state_version
        prompt_text = str(snap_item["prompt_text"])
        target_seconds = float(snap_item["target_seconds"])
        width = int(snap_item["width"])
        height = int(snap_item["height"])
        seed = int(snap_item["seed"])

    # 3. Execute external call OUTSIDE SQLite write transaction
    out_bytes = b""
    out_filename = f"{snap_item['shot_code']}.mp4"
    is_mock_run = video_stub is not None
    prompt_id = ""
    submitted_graph_sha = compiled.graph_sha256

    try:
        if video_stub is not None:
            stub_res = video_stub(
                job_id=job_id,
                shot_code=snap_item["shot_code"],
                prompt_text=prompt_text,
                duration_s=target_seconds,
                width=width,
                height=height,
                seed=seed,
                references=ref_files_to_upload,
                has_continuity_frame=prev_video_path is not None,
            )
            out_bytes = stub_res["bytes"] if isinstance(stub_res, dict) else bytes(stub_res)
            is_mock_run = bool(stub_res.get("is_mock", True)) if isinstance(stub_res, dict) else True
            if isinstance(stub_res, dict) and stub_res.get("filename"):
                out_filename = safe_m4_filename(str(stub_res["filename"]))
            default_pid = f"stub-{uuid4().hex[:12]}"
            prompt_id = str(stub_res.get("prompt_id") or default_pid) if isinstance(stub_res, dict) else default_pid
        else:
            if comfy_client is None:
                raise RuntimeError("M4_COMFY_CLIENT_UNAVAILABLE")
            param_values: dict[str, Any] = {
                "positive_prompt": prompt_text,
                "duration": target_seconds,
                "seed": seed,
                "width": width,
                "height": height,
            }
            for param_key, fname, content, ctype in ref_files_to_upload:
                uploaded = comfy_client.upload_image(
                    fname,
                    content,
                    subfolder="shine_m4",
                    content_type=ctype,
                )
                name_in_comfy = str(uploaded.get("name") or fname)
                sub = str(uploaded.get("subfolder") or "").strip("/")
                param_values[param_key] = f"{sub}/{name_in_comfy}" if sub else name_in_comfy

            final_graph = apply_parameters(compiled.graph, compiled.parameter_map, param_values)
            submitted_graph_sha = graph_hash(final_graph)
            queued = comfy_client.queue_prompt(
                final_graph,
                extra_data={"shine_line": "manual4", "job_id": job_id, "run_id": run_info["id"]},
            )
            prompt_id = str(queued.get("prompt_id") or "")
            if not prompt_id:
                raise M4ComfySubmissionUncertain("ComfyUI /prompt did not return prompt_id")

            # Persist external prompt_id immediately after /prompt ack
            with m4_factory.begin() as db:
                j_row = db.get(M4Job, job_id)
                a_row = db.get(M4JobAttempt, attempt_id)
                if j_row and j_row.state_version == expected_state_ver:
                    j_row.status = "RUNNING"
                    j_row.state_version += 1
                    j_row.updated_at = m4_utcnow()
                    expected_state_ver = j_row.state_version
                if a_row:
                    a_row.prompt_id = prompt_id
                    a_row.prompt_graph_sha256 = submitted_graph_sha
                    a_row.submit_ack_at = m4_utcnow()
                    a_row.status = "RUNNING"
                    a_row.updated_at = m4_utcnow()

            heartbeat_m4_worker(m4_factory, worker_id, status="RUNNING", current_run_id=run_info["id"])
            record = comfy_client.poll_until_done(prompt_id, timeout_s=1800.0, poll_interval=2.0)
            fname, sub, ftype = extract_video_output(record)
            out_filename = safe_m4_filename(fname)
            out_bytes = comfy_client.view(fname, subfolder=sub, file_type=ftype)

    except M4ComfySubmissionUncertain as exc:
        with m4_factory.begin() as db:
            j_row = db.get(M4Job, job_id)
            a_row = db.get(M4JobAttempt, attempt_id)
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
            a_row = db.get(M4JobAttempt, attempt_id)
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
    qc = run_m4_technical_qc(
        abs_out,
        expectations=M4TechnicalExpectations(
            duration_s=target_seconds,
            duration_tolerance_s=1.0,
            expect_audio=False,
            expected_fps=24.0,
            require_decodable=not is_mock_run,
        ),
        ffprobe_path="" if is_mock_run else "ffprobe",
        ffmpeg_path="" if is_mock_run else "ffmpeg",
    )

    final_status = "PASSED" if qc["result"] == "passed" else "FAILED"
    with m4_factory.begin() as db:
        j_row = db.get(M4Job, job_id)
        a_row = db.get(M4JobAttempt, attempt_id)
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
            j_row.failure_code = None if final_status == "PASSED" else "M4_TECHNICAL_QC_FAILED"
            j_row.failure_detail = "" if final_status == "PASSED" else f"技术质检未通过：{qc['result']}"
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

            with managed_m4_comfyui(
                platform_factory,
                m4_factory,
                settings,
                m4_root,
                owner=f"m4-worker-{worker_id}-{run_id[:8]}",
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
            if r and r.status in {"CLAIMED", "RUNNING"}:
                jobs = db.scalars(select(M4Job).where(M4Job.run_id == run_id)).all()
                if any(j.status == "NEEDS_REVIEW" for j in jobs):
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
    reconcile_stalled_m4_runs(m4_factory)

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
