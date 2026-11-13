"""The RelayVSR X2 post-production stage: one work ledger, one scheduler.

This module is the execution chain of the RelayVSR upscale stage, driven by
the resident Worker through the same ``ResourceLease`` admission as every
other model stage (``resource_slot("RELAYVSR", ...)``) - there is no second
queue, no second scheduler and no sidecar process.  Items are created only
after a run's episode has all its H3 shots really passed (the H3 lease is
therefore already released), which is the two-stage handover the plan
requires: H3 first, Relay after, never both resident.

Discipline kept from the rest of the pipeline:

* the submit intent (``intent_sha256``) persists BEFORE ``/prompt`` and the
  external ``prompt_id`` right after it; an UNKNOWN outcome is a permanent
  fact (``external_outcome='UNKNOWN'``) that only reconciliation may settle;
* the source artifact's bytes are re-verified before the stage runs and the
  upscaled output must satisfy the 2x/fps/duration contract or the item goes
  to NEEDS_REVIEW - never a silent 1.5x;
* the source audio track is muxed back losslessly (``-c:v copy -c:a copy``)
  because the Relay graph deliberately renders video only;
* the readiness gate re-checks the live renderer before anything is
  submitted: on the pinned ComfyUI revision (pre-``2f89433``) every item
  fails with the named backend blocker instead of pretending to run.
"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.comfyops import relayvsr
from shine.comfyops.scheduler import resource_slot
from shine.security import sha256_bytes
from shine.models import Job, MediaArtifact, RelayUpscaleItem, utcnow

#: Item states.  ``SUBMIT_INTENT``/``RUNNING`` are live; ``NEEDS_REVIEW``
#: covers QC failures and UNKNOWN external outcomes - both wait for the
#: operator, never an automatic resubmission.
RELAY_ITEM_LIVE_STATUSES = ("PENDING", "SUBMIT_INTENT", "RUNNING")
RELAY_ITEM_TERMINAL_STATUSES = ("PASSED", "FAILED", "NEEDS_REVIEW", "CANCELLED")

CLAIM_LEASE_SECONDS = 900
HEARTBEAT_SECONDS = 300


def _run_strategy(package_manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    return ((package_manifest or {}).get("binding") or {}).get("strategy")


def run_upscale_enabled(package_manifest: dict[str, Any] | None) -> bool:
    strategy = _run_strategy(package_manifest)
    if not strategy:
        return False
    upscale = strategy.get("upscale") or {}
    return bool(upscale.get("enabled"))


def enqueue_relay_items_for_episode(db: Session, run, episode, passed_jobs: dict[str, Job]) -> int:
    """Create the episode's upscale items, idempotently, from passed jobs.

    Called from the run coordinator right before an episode delivery would
    be queued.  Only shots whose job really passed (with a real artifact)
    get an item; ``(project_id, job_id, stage)`` is unique so a re-advance
    never duplicates the work.
    """
    created = 0
    for shot_id in episode.shot_ids:
        job = passed_jobs.get(shot_id)
        if job is None or not job.artifact_id:
            continue
        existing = db.scalar(select(RelayUpscaleItem.id).where(
            RelayUpscaleItem.project_id == run.project_id,
            RelayUpscaleItem.job_id == job.id,
            RelayUpscaleItem.stage == "relayvsr_x2",
        ))
        if existing:
            continue
        artifact = db.get(MediaArtifact, job.artifact_id)
        if artifact is None or artifact.is_mock:
            continue
        db.add(RelayUpscaleItem(
            project_id=run.project_id,
            run_id=run.id,
            series_id=None,
            job_id=job.id,
            shot_id=shot_id,
            stage="relayvsr_x2",
            source_artifact_id=artifact.id,
            source_sha256=artifact.sha256,
            status="PENDING",
            payload={"engine": "relayvsr", "variant": "x2", "detail": 1.0},
        ))
        db.flush()  # the unique key must see this transaction's own rows
        created += 1
    return created


def episode_relay_items_unfinished(db: Session, run_id: str, episode) -> int:
    """How many of this episode's upscale items are not terminal yet.

    The episode delivery waits for the upscale stage when the run's frozen
    strategy enabled it: assembling before the upscale finishes would
    silently deliver the un-upscaled clip as the final one.
    """
    shot_ids = list(episode.shot_ids or [])
    if not shot_ids:
        return 0
    jobs = db.scalars(select(Job).where(
        Job.shot_id.in_(shot_ids), Job.status == "PASSED", Job.stale.is_(False))).all()
    if not jobs:
        return 0
    job_ids = [job.id for job in jobs]
    return len(db.scalars(select(RelayUpscaleItem.id).where(
        RelayUpscaleItem.run_id == run_id,
        RelayUpscaleItem.job_id.in_(job_ids),
        RelayUpscaleItem.status.in_(RELAY_ITEM_LIVE_STATUSES),
    )).all())


def relay_artifact_for_job(db: Session, job_id: str) -> MediaArtifact | None:
    """The passed upscale artifact of a job, or None.

    The delivery EDL prefers this artifact when the strategy enabled the
    upscale: it is the same shot's approved highest stage.
    """
    item = db.scalar(select(RelayUpscaleItem).where(
        RelayUpscaleItem.job_id == job_id,
        RelayUpscaleItem.status == "PASSED",
    ).order_by(RelayUpscaleItem.updated_at.desc()))
    if item is None or not item.output_artifact_id:
        return None
    artifact = db.get(MediaArtifact, item.output_artifact_id)
    if artifact is None or artifact.is_mock or artifact.sha256 != (item.output_sha256 or ""):
        return None
    return artifact


def claim_relay_item(db: Session, worker_id: str) -> RelayUpscaleItem | None:
    """CAS-claim the oldest pending item with a bounded lease."""
    item = db.scalar(select(RelayUpscaleItem).where(
        RelayUpscaleItem.status == "PENDING",
    ).order_by(RelayUpscaleItem.created_at, RelayUpscaleItem.id).limit(1))
    if item is None:
        return None
    now = utcnow()
    item.status = "RUNNING"
    item.state_version += 1
    item.worker_id = worker_id
    item.lease_owner = f"relayvsr:{item.id}"
    item.lease_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
    item.heartbeat_at = now
    db.flush()
    return item


def _fail_item(db: Session, item: RelayUpscaleItem, code: str, details: dict | None = None,
               *, needs_review: bool = True) -> None:
    item.status = "NEEDS_REVIEW" if needs_review else "FAILED"
    item.state_version += 1
    item.error_code = code
    item.lease_owner = None
    item.lease_expires_at = None
    if details:
        item.payload = {**(item.payload or {}), "last_error": details}


def _persist_intent(db: Session, item: RelayUpscaleItem, intent: str) -> None:
    item.intent_sha256 = intent
    item.status = "SUBMIT_INTENT"
    item.state_version += 1
    item.heartbeat_at = utcnow()


def _record_prompt_id(db: Session, item: RelayUpscaleItem, prompt_id: str) -> None:
    item.prompt_id = prompt_id
    item.external_outcome = "SENT"
    item.state_version += 1
    item.heartbeat_at = utcnow()


def _record_unknown(db: Session, item: RelayUpscaleItem, reason: str) -> None:
    item.external_outcome = "UNKNOWN"
    item.state_version += 1
    item.payload = {**(item.payload or {}), "unknown_reason": reason}
    _fail_item(db, item, "RELAY_SUBMISSION_OUTCOME_UNKNOWN")


def _heartbeat(db: Session, item_id: str) -> bool:
    item = db.get(RelayUpscaleItem, item_id)
    if item is None or item.status not in RELAY_ITEM_LIVE_STATUSES:
        return False
    item.heartbeat_at = utcnow()
    item.lease_expires_at = utcnow() + timedelta(seconds=CLAIM_LEASE_SECONDS)
    return True


def _wait_terminal(client, prompt_id: str, *, item_heartbeat, timeout_s: float, poll_s: float = 2.0):
    """Poll the external prompt to completion, keeping the claim alive."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = client.history(prompt_id)
        record = (last or {}).get(prompt_id)
        if isinstance(record, dict):
            status = (record.get("status") or {})
            status_str = str(status.get("status_str") or "")
            if status.get("completed") or status_str in ("success", "error", "failed"):
                return record
        if not item_heartbeat():
            raise RuntimeError("RELAY_CLAIM_LOST")
        time.sleep(poll_s)
    return None


def _resolve_ffmpeg(settings) -> str:
    """The configured FFmpeg binary, with the bundled fallback of last resort."""
    import shutil

    configured = settings.ffmpeg_path or "ffmpeg"
    found = shutil.which(configured) if configured else None
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - the binary is optional
        return configured


def _execute_item(session_factory, settings, item_id: str, worker_id: str,
                  client, asset_root: Path) -> str | None:
    """Run one claimed upscale item inside its own resource slot.

    Returns the item id when it reached a terminal state this round; None
    when the slot was busy (the item goes back to PENDING, not FAILED).
    """
    from shine.comfy.workflow import apply_parameters
    from shine.media.artifacts import safe_asset_path, store_artifact
    from shine.media.probe import probe_file
    from shine.services import add_audit

    ffprobe = settings.ffprobe_path or "ffprobe"
    ffmpeg = _resolve_ffmpeg(settings)

    def heartbeat():
        with session_factory.begin() as db:
            return _heartbeat(db, item_id)

    with session_factory() as db:
        item = db.get(RelayUpscaleItem, item_id)
        if item is None:
            return item_id
        project_id = item.project_id
        # 1. The readiness gate speaks before anything is submitted.
        try:
            object_info = client.object_info()
        except Exception:  # noqa: BLE001 - readiness re-check below stays honest
            object_info = {}
        readiness = relayvsr.relayvsr_readiness(object_info=object_info)
        if not readiness["available"]:
            blockers = readiness["blockers"]
            with session_factory.begin() as fail_db:
                target = fail_db.get(RelayUpscaleItem, item_id)
                _fail_item(fail_db, target, blockers[0]["code"],
                           {"blockers": blockers[:5]})
                add_audit(fail_db, worker_id, "RELAY_STAGE_BACKEND_UNAVAILABLE",
                          "relay_upscale_item", item_id, {"blockers": blockers[:5]},
                          project_id=project_id)
            return item_id
        # 2. The source bytes are verified before the stage touches them.
        artifact = db.get(MediaArtifact, item.source_artifact_id or "")
        if artifact is None or artifact.is_mock:
            with session_factory.begin() as fail_db:
                _fail_item(fail_db, fail_db.get(RelayUpscaleItem, item_id),
                           "RELAY_SOURCE_ARTIFACT_MISSING")
            return item_id
        source_path = safe_asset_path(asset_root, artifact.storage_path)
        source_bytes = source_path.read_bytes() if source_path.is_file() else b""

        if not source_bytes or sha256_bytes(source_bytes) != item.source_sha256:
            with session_factory.begin() as fail_db:
                _fail_item(fail_db, fail_db.get(RelayUpscaleItem, item_id),
                           "RELAY_SOURCE_HASH_MISMATCH")
            return item_id
        source_info = probe_file(source_path, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)
        if not source_info.has_video or source_info.width is None or source_info.height is None:
            with session_factory.begin() as fail_db:
                _fail_item(fail_db, fail_db.get(RelayUpscaleItem, item_id),
                           "RELAY_SOURCE_NOT_A_VIDEO")
            return item_id
        item.source_width = source_info.width
        item.source_height = source_info.height
        item.source_duration_s = source_info.duration_s
        item.source_fps = source_info.fps
        db.commit()

    detail = 1.0
    fps = 24.0
    with session_factory() as db:
        item = db.get(RelayUpscaleItem, item_id)
        detail = float((item.payload or {}).get("detail", 1.0))
        fps = float((item.source_fps) or 24.0)

    try:
        slot = resource_slot(session_factory, "RELAYVSR", 8192, wait_seconds=0)
        slot.__enter__()
    except Exception as exc:  # GPU busy is a re-queue, not a failure.
        message = str(getattr(exc, "detail", "") or exc)
        if "GPU_BUSY" in message or "RECONCILE" in message:
            with session_factory.begin() as db:
                item = db.get(RelayUpscaleItem, item_id)
                item.status = "PENDING"
                item.state_version += 1
                item.lease_owner = None
                item.lease_expires_at = None
                item.payload = {**(item.payload or {}), "last_requeue": message[:200]}
            return None
        raise

    try:
        # 3. Upload the source video for the LQ frames input.
        upload_name = f"shine-relay-{item_id[:12]}.mp4"
        client.upload_file(
            upload_name, source_bytes,
            upload_path="/upload/image", field_name="image",
            subfolder="shine", content_type="video/mp4",
        )
        graph = relayvsr.build_relayvsr_x2_graph()
        prompt = apply_parameters(graph, dict(relayvsr.RELAYVSR_PARAMETER_MAP), {
            "source_video": f"shine/{upload_name}",
            "seed": 0,
            "fps": fps,
            "detail": detail,
        })
        from shine.comfy.workflow import graph_hash

        intent = relayvsr.stage_intent_digest(
            source_sha256=_source_digest_bytes(source_bytes),
            detail=detail, fps=fps,
        )
        with session_factory.begin() as db:
            target = db.get(RelayUpscaleItem, item_id)
            _persist_intent(db, target, intent)
            add_audit(db, worker_id, "RELAY_SUBMIT_INTENT", "relay_upscale_item", item_id,
                      {"intent_sha256": intent, "prompt_graph_sha256": graph_hash(prompt),
                       "source_sha256": target.source_sha256}, project_id=project_id)

        # 4. The external call.  An uncertain outcome is UNKNOWN, never a retry.
        try:
            body = client.queue_prompt(prompt, extra_data={
                "shine": {"item_id": item_id, "intent_sha256": intent,
                          "engine": "relayvsr", "variant": "x2"},
            })
            prompt_id = str(body.get("prompt_id") or "")
        except Exception as exc:  # noqa: BLE001
            uncertain = type(exc).__name__ in ("ComfySubmissionUncertain",)
            with session_factory.begin() as db:
                target = db.get(RelayUpscaleItem, item_id)
                if uncertain:
                    _record_unknown(db, target, str(exc)[:200])
                else:
                    _fail_item(db, target, "RELAY_SUBMISSION_REFUSED", {"error": str(exc)[:200]},
                               needs_review=False)
            return item_id
        if not prompt_id:
            with session_factory.begin() as db:
                _record_unknown(db, db.get(RelayUpscaleItem, item_id), "NO_PROMPT_ID")
            return item_id
        with session_factory.begin() as db:
            _record_prompt_id(db, db.get(RelayUpscaleItem, item_id), prompt_id)

        # 5. Poll with heartbeat; collect the rendered video.
        record = _wait_terminal(client, prompt_id, item_heartbeat=heartbeat, timeout_s=1800.0)
        if record is None:
            with session_factory.begin() as db:
                _record_unknown(db, db.get(RelayUpscaleItem, item_id), "POLL_TIMEOUT")
            return item_id
        status = (record.get("status") or {})
        if str(status.get("status_str") or "") in ("error", "failed"):
            with session_factory.begin() as db:
                _fail_item(db, db.get(RelayUpscaleItem, item_id), "RELAY_RENDER_FAILED",
                           {"status": status})
            return item_id
        rendered = client.view(
            _output_filename(record), subfolder="", file_type="output")
        if not rendered:
            with session_factory.begin() as db:
                _fail_item(db, db.get(RelayUpscaleItem, item_id), "RELAY_OUTPUT_EMPTY")
            return item_id

        # 6. Mux the original audio back, losslessly.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            raw_path = tmp_dir / "relay-raw.mp4"
            raw_path.write_bytes(rendered)
            muxed_path = tmp_dir / "relay-muxed.mp4"
            errors = relayvsr.mux_source_audio(raw_path, source_path, muxed_path, ffmpeg=ffmpeg)
            if errors:
                with session_factory.begin() as db:
                    _fail_item(db, db.get(RelayUpscaleItem, item_id), "RELAY_AUDIO_MUX_FAILED",
                               {"stderr": errors[:5]})
                return item_id
            muxed_bytes = muxed_path.read_bytes()

            # 7. Deterministic output contract: exactly 2x, same fps, same duration.
            output_info = probe_file(muxed_path, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)
            violations = relayvsr.verify_upscale_contract(
                source_width=source_info.width or 0,
                source_height=source_info.height or 0,
                source_duration_s=source_info.duration_s or 0.0,
                source_fps=source_info.fps or 0.0,
                output_width=output_info.width or 0,
                output_height=output_info.height or 0,
                output_duration_s=output_info.duration_s or 0.0,
                output_fps=output_info.fps or 0.0,
            )
            if violations:
                with session_factory.begin() as db:
                    _fail_item(db, db.get(RelayUpscaleItem, item_id), violations[0],
                               {"violations": violations})
                return item_id

        # 8. Store the new artifact (never overwriting the source).
        storage_path, digest, size = store_artifact(
            asset_root, project_id, item.job_id, muxed_bytes, "video/mp4",
            filename=f"{item.shot_id or item.job_id}-relayvsr-x2.mp4", subdir="shots",
        )
        with session_factory.begin() as db:
            target = db.get(RelayUpscaleItem, item_id)
            artifact_row = MediaArtifact(
                project_id=project_id,
                job_id=item.job_id,
                attempt_id=None,
                artifact_kind="video",
                storage_path=storage_path,
                sha256=digest,
                byte_size=size,
                content_type="video/mp4",
                is_mock=False,
                provenance={
                    "stage": "relayvsr_x2",
                    "source_artifact_id": target.source_artifact_id,
                    "source_sha256": target.source_sha256,
                    "engine": "relayvsr",
                    "variant": "x2",
                    "detail": detail,
                    "fps": fps,
                    "prompt_id": prompt_id,
                    "intent_sha256": target.intent_sha256,
                },
            )
            db.add(artifact_row)
            db.flush()
            target.output_artifact_id = artifact_row.id
            target.output_sha256 = digest
            target.output_width = output_info.width
            target.output_height = output_info.height
            target.status = "PASSED"
            target.state_version += 1
            target.lease_owner = None
            target.lease_expires_at = None
            add_audit(db, worker_id, "RELAY_STAGE_PASSED", "relay_upscale_item", item_id,
                      {"artifact_id": artifact_row.id, "sha256": digest,
                       "width": output_info.width, "height": output_info.height},
                      project_id=project_id)
        return item_id
    finally:
        try:
            slot.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - release failure is reconciliation work
            pass


def _source_digest_bytes(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _output_filename(record: dict) -> str:
    for outputs in ((record.get("outputs") or {}).values()):
        for key in ("gifs", "videos", "images"):
            files = outputs.get(key) or []
            for entry in files:
                name = entry.get("filename")
                if name:
                    return str(name)
    return ""


def run_pending_relay_items(session_factory, *, settings, worker_id: str,
                            comfy_client=None, asset_root: Path | None = None,
                            limit: int = 1) -> int:
    """Drive pending upscale items in one bounded Worker round.

    One call claims at most ``limit`` items.  A busy GPU re-queues the item
    for the next round instead of failing it; every terminal transition is
    recorded on the item itself with its evidence.
    """
    client = comfy_client
    if client is None:
        from shine.worker import _renderer_client

        client = _renderer_client(settings, max_retries=0)
    root = asset_root or Path(settings.data_dir) / "assets"
    processed = 0
    while processed < limit:
        with session_factory.begin() as db:
            item = claim_relay_item(db, worker_id)
            if item is None:
                break
            item_id = item.id
        result = _execute_item(session_factory, settings, item_id, worker_id, client, root)
        if result is not None:
            processed += 1
        else:
            break  # busy slot - return the tick to the scheduler
    return processed
