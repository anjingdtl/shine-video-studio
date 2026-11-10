"""Run projectless voice work through the existing local provider and GPU lease.

The worker is deliberately fail closed. It never falls back to FakeVoiceProvider,
rechecks consent immediately before inference, and leaves uncertain in-flight
work UNKNOWN instead of generating a duplicate preview.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from sqlalchemy import select, update

from shine.asset_center import service
from shine.asset_center.models import (
    LibraryAsset,
    LibraryMedia,
    LibraryRenderTask,
    LibraryRights,
    LibraryTaskAttempt,
    LibraryVersion,
    LibraryVoiceProfile,
)
from shine.comfy.workflow import graph_hash
from shine.comfyops.scheduler import resource_slot
from shine.models import utcnow
from shine.security import probe_upload, verify_upload
from shine.services import add_audit
from shine.voiceops.sdk_runtime import CURRENT_LIBRARY_VOICE_ATTEMPT


def _voice_role_codes(db, settings) -> dict[str, str | None]:
    from shine.comfyops.llm_readiness import voice_readiness

    report = voice_readiness(db, settings)
    return {str(row["role"]): row.get("code") for row in report.get("roles", [])}


def can_queue_voice(factory, settings, mode: str) -> bool:
    """A configured local provider may accept a task; this is not READY evidence."""
    if settings.local_voice_url:
        return True
    required = {
        "DESIGN": {"design"},
        "AUTHORIZED_CLONE": {"base"},
        "TTS": {"base", "asr", "align"},
    }.get(mode, set())
    if hasattr(factory, "get_bind"):
        roles = _voice_role_codes(factory, settings)
    else:
        with factory() as db:
            roles = _voice_role_codes(db, settings)
    return bool(required) and all(role in roles and roles[role] is None for role in required)


def _provider(factory, settings):
    from shine.voiceops.runtime_config import load_voice_config

    config = load_voice_config(settings)
    routing = config.routing()
    runtimes = config.runtimes()
    if runtimes and not routing:
        raise RuntimeError("VOICE_ROLE_RUNTIME_PACKAGES_REQUIRED")
    if routing:
        from shine.voiceops.local_sdk import QwenLocalVoiceProvider
        from shine.voiceops.sdk_runtime import SDKProcessLoader

        loader = SDKProcessLoader({role: spec.model_dump(mode="json") for role, spec in runtimes.items()})
        return QwenLocalVoiceProvider(factory, settings, routing, loader)
    if settings.local_voice_url:
        from shine.voiceops.providers import LocalVoiceHTTPProvider

        return LocalVoiceHTTPProvider(settings.local_voice_url)
    raise RuntimeError("VOICE_PROVIDER_NOT_CONFIGURED")


def _release_models_between_stages(provider) -> None:
    """Require a confirmed model release before the next real voice stage.

    Mock providers do not allocate GPU memory. Every production provider must
    acknowledge unloading or prove that its owned inference process exited;
    otherwise the current task stops before the next model is invoked.
    """
    if getattr(provider, "is_mock", False):
        return
    release = getattr(provider, "release_models", None)
    if not callable(release):
        raise RuntimeError("VOICE_MODEL_RELEASE_UNCONFIRMED")
    release()


def _claim(factory, worker_id: str) -> str | None:
    with factory() as db:
        task = db.scalar(
            select(LibraryRenderTask)
            .where(
                LibraryRenderTask.kind == "voice",
                LibraryRenderTask.status == "QUEUED",
                LibraryRenderTask.external_outcome == "NOT_SENT",
            )
            .order_by(LibraryRenderTask.created_at, LibraryRenderTask.id)
            .limit(1)
        )
        if task is None:
            return None
        result = db.execute(
            update(LibraryRenderTask)
            .where(
                LibraryRenderTask.id == task.id,
                LibraryRenderTask.state_version == task.state_version,
                LibraryRenderTask.status == "QUEUED",
                LibraryRenderTask.external_outcome == "NOT_SENT",
            )
            .values(
                status="RUNNING",
                worker_id=worker_id,
                heartbeat_at=utcnow(),
                updated_at=utcnow(),
                state_version=task.state_version + 1,
            )
        )
        if result.rowcount != 1:
            return None
        db.commit()
        return task.id


def _claim_inputs(factory, settings, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from shine.media.output_layout import output_root

    with factory() as db:
        task = db.get(LibraryRenderTask, task_id)
        if task is None or task.status != "RUNNING":
            raise RuntimeError("VOICE_TASK_CLAIM_LOST")
        root = output_root(db, settings)
        payload = dict(task.payload or {})
        mode = task.mode
        context: dict[str, Any] = {}
        if mode == "AUTHORIZED_CLONE":
            media = db.get(LibraryMedia, payload.get("reference_media_id"))
            rights = db.get(LibraryRights, payload.get("rights_id"))
            if media is None or rights is None or media.kind != "audio" or media.purpose != "voice_sample":
                raise RuntimeError("VOICE_CONSENT_SOURCE_MISSING")
            if media.content_type != "audio/wav" or media.sha256 != payload.get("reference_media_sha256"):
                raise RuntimeError("VOICE_REFERENCE_WAV_OR_HASH_INVALID")
            service.require_rights(rights, usage_scope="local")
            if rights.id != media.rights_id or rights.scope != "voice_clone":
                raise RuntimeError("VOICE_CONSENT_SOURCE_MISMATCH")
            reference = service.media_bytes(settings, media, root=root)
            context.update(reference_media=media, reference_rights=rights, reference=reference)
        elif mode == "TTS":
            profile = db.get(LibraryVoiceProfile, payload.get("voice_profile_id"))
            if profile is None or profile.status != "FROZEN" or profile.voice_hash != payload.get("voice_hash"):
                raise RuntimeError("VOICE_PROFILE_NOT_FROZEN_OR_HASH_CHANGED")
            rights = db.get(LibraryRights, profile.rights_id)
            service.require_rights(rights, usage_scope="local")
            version = db.get(LibraryVersion, profile.version_id)
            if version is None or version.status != "PROMOTED" or version.is_mock:
                raise RuntimeError("VOICE_PROFILE_VERSION_NOT_PROMOTED")
            service.require_version_rights(db, version, usage_scope="local")
            conditioning = service.version_bytes(settings, version, root=root)
            if hashlib.sha256(conditioning).hexdigest() != profile.voice_hash:
                raise RuntimeError("VOICE_PROFILE_CONDITIONING_HASH_CHANGED")
            if not version.prompt.strip():
                raise RuntimeError("VOICE_PROFILE_REFERENCE_TEXT_MISSING")
            context.update(
                profile=profile,
                profile_rights=rights,
                profile_version=version,
                conditioning=conditioning,
                conditioning_text=version.prompt,
            )
        elif mode != "DESIGN":
            raise RuntimeError("VOICE_MODE_UNSUPPORTED")
        return payload, context


def _open_attempt(
    factory, settings, task_id: str, worker_id: str, lease_id: str, payload: dict[str, Any], runtime: dict[str, Any]
):
    snapshot = graph_hash({"task_id": task_id, "payload": payload, "runtime": runtime})
    from shine.media.output_layout import output_root

    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        root = output_root(db, settings)
        if task is None or task.status != "RUNNING" or task.worker_id != worker_id:
            raise RuntimeError("VOICE_TASK_CLAIM_LOST")
        if task.submit_intent_at is not None or task.external_outcome != "NOT_SENT":
            raise RuntimeError("VOICE_TASK_ALREADY_SUBMITTED")
        if task.mode == "AUTHORIZED_CLONE":
            media = db.get(LibraryMedia, payload.get("reference_media_id"))
            rights = db.get(LibraryRights, payload.get("rights_id"))
            service.require_rights(rights, usage_scope="local")
            if media is None or rights is None or media.rights_id != rights.id:
                raise RuntimeError("VOICE_CONSENT_SOURCE_MISMATCH")
            if service.rights_snapshot(rights) != payload.get("rights_snapshot"):
                raise RuntimeError("VOICE_CONSENT_CHANGED_AFTER_QUEUE")
            service.media_bytes(settings, media, root=root)
        elif task.mode == "TTS":
            profile = db.get(LibraryVoiceProfile, payload.get("voice_profile_id"))
            if profile is None or profile.status != "FROZEN" or profile.voice_hash != payload.get("voice_hash"):
                raise RuntimeError("VOICE_PROFILE_NOT_FROZEN_OR_HASH_CHANGED")
            rights = db.get(LibraryRights, profile.rights_id)
            service.require_rights(rights, usage_scope="local")
            if service.rights_snapshot(rights) != payload.get("rights_snapshot"):
                raise RuntimeError("VOICE_RIGHTS_CHANGED_AFTER_QUEUE")
            version = db.get(LibraryVersion, profile.version_id)
            if version is None or version.id != payload.get("voice_version_id") or version.status != "PROMOTED":
                raise RuntimeError("VOICE_PROFILE_VERSION_CHANGED_AFTER_QUEUE")
            service.require_version_rights(db, version, usage_scope="local")
            service.version_bytes(settings, version, root=root)
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
            payload={"runtime": runtime, "mode": task.mode, "lease_id": lease_id},
        )
        db.add(attempt)
        db.flush()
        task.submit_intent_at = utcnow()
        task.runtime_fingerprint = snapshot
        task.lease_owner = lease_id
        task.state_version += 1
        task.heartbeat_at = utcnow()
        return attempt.id


def _new_output_rights(db, mode: str, context: dict[str, Any]) -> LibraryRights:
    if mode in {"AUTHORIZED_CLONE", "TTS"}:
        source = context.get("reference_rights") or context.get("profile_rights")
        if source is None or source.status not in {"APPROVED", "DECLARED"} or source.revoked_at is not None:
            raise RuntimeError("VOICE_SOURCE_RIGHTS_REVOKED")
        return LibraryRights(
            source_type="inherited_voice_consent",
            license_kind=source.license_kind,
            scope="voice_clone" if source.scope == "voice_clone" else "voice_design",
            statement=f"Derived voice output inherits the source consent: {source.statement}",
            subject=source.subject,
            allowed_project_ids=list(source.allowed_project_ids or []),
            permitted_uses=list(source.permitted_uses or []),
            commercial_allowed=source.commercial_allowed,
            status=source.status,
            recorded_actor="shine-worker",
            approved_by=source.approved_by,
            expires_at=source.expires_at,
        )
    return LibraryRights(
        source_type="model_output",
        license_kind="QWEN_TTS_LICENSE_REVIEW_REQUIRED",
        scope="voice_design",
        statement="本地声音模型生成；仅保留研究与评估用途。商业许可需按模型包许可另行确认。",
        subject="generated synthetic voice",
        allowed_project_ids=[],
        permitted_uses=["research"],
        commercial_allowed=False,
        status="DECLARED",
        recorded_actor="shine-worker",
    )


def _revalidate_output_sources(db, settings, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from shine.media.output_layout import output_root

    current = dict(context)
    root = output_root(db, settings)
    mode = str(payload.get("mode"))
    if mode == "AUTHORIZED_CLONE":
        media = db.get(LibraryMedia, payload.get("reference_media_id"))
        rights = db.get(LibraryRights, payload.get("rights_id"))
        if media is None or rights is None or media.rights_id != rights.id:
            raise RuntimeError("VOICE_CONSENT_SOURCE_MISMATCH")
        service.require_rights(rights, usage_scope="local")
        if media.sha256 != payload.get("reference_media_sha256"):
            raise RuntimeError("VOICE_REFERENCE_HASH_CHANGED_AFTER_INFERENCE")
        if service.rights_snapshot(rights) != payload.get("rights_snapshot"):
            raise RuntimeError("VOICE_CONSENT_CHANGED_AFTER_INFERENCE")
        service.media_bytes(settings, media, root=root)
        current.update(reference_media=media, reference_rights=rights)
    elif mode == "TTS":
        profile = db.get(LibraryVoiceProfile, payload.get("voice_profile_id"))
        if profile is None or profile.status != "FROZEN" or profile.voice_hash != payload.get("voice_hash"):
            raise RuntimeError("VOICE_PROFILE_CHANGED_AFTER_INFERENCE")
        rights = db.get(LibraryRights, profile.rights_id)
        service.require_rights(rights, usage_scope="local")
        if service.rights_snapshot(rights) != payload.get("rights_snapshot"):
            raise RuntimeError("VOICE_RIGHTS_CHANGED_AFTER_INFERENCE")
        version = db.get(LibraryVersion, profile.version_id)
        if version is None or version.id != payload.get("voice_version_id") or version.status != "PROMOTED":
            raise RuntimeError("VOICE_PROFILE_VERSION_CHANGED_AFTER_INFERENCE")
        service.require_version_rights(db, version, usage_scope="local")
        conditioning = service.version_bytes(settings, version, root=root)
        if hashlib.sha256(conditioning).hexdigest() != profile.voice_hash:
            raise RuntimeError("VOICE_PROFILE_CONDITIONING_HASH_CHANGED")
        current.update(profile=profile, profile_rights=rights, profile_version=version, conditioning=conditioning)
    return current


def _store_candidate(
    factory,
    settings,
    task_id: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    result: dict[str, Any],
    provider,
    attempt_id: str,
    runtime: dict[str, Any],
    dialogue_qc: dict[str, Any] | None,
) -> tuple[str, str, str]:
    content = result.get("audio")
    if not isinstance(content, bytes) or not content:
        raise RuntimeError("VOICE_OUTPUT_BYTES_MISSING")
    filename = f"shine-assets-voice-{task_id[:8]}.wav"
    content_type = verify_upload("audio", filename, content, settings.max_upload_bytes)
    media_info = probe_upload("audio", content, content_type)
    digest = hashlib.sha256(content).hexdigest()
    with factory() as db:
        context = _revalidate_output_sources(db, settings, payload, context)
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
        kind="audio",
        base=str(payload.get("title") or "声音候选"),
        extension=".wav",
        root=root,
    )
    filename = stored_name
    mocked = bool(result.get("is_mock", getattr(provider, "is_mock", False)))
    mode = str(payload["mode"])
    category = "voice" if mode in {"DESIGN", "AUTHORIZED_CLONE"} else "dialogue"
    references: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {
        "source": "shine_assets_voice_worker",
        "task_id": task_id,
        "provider": type(provider).__name__,
        "model": result.get("model"),
        "runtime_fingerprint": runtime["fingerprint"],
        "voice_source": "AUTHORIZED_CLONE" if mode == "AUTHORIZED_CLONE" else "ORIGINAL_DESIGN",
        "usage_scope": "RESEARCH_AND_EVALUATION_ONLY",
    }
    if mode == "AUTHORIZED_CLONE":
        media = context["reference_media"]
        provenance.update(
            {
                "reference_media_id": media.id,
                "reference_media_sha256": media.sha256,
                "reference_rights_snapshot": service.rights_snapshot(context["reference_rights"]),
                "reference_text_sha256": hashlib.sha256(payload["reference_text"].encode()).hexdigest(),
            }
        )
        references.append({"order": 0, "role": "voice_sample", "media_id": media.id})
    elif mode == "TTS":
        profile = context["profile"]
        version = context["profile_version"]
        provenance.update(
            {
                "voice_profile_id": profile.id,
                "voice_hash": profile.voice_hash,
                "voice_version_id": version.id,
                "rights_snapshot": service.rights_snapshot(context["profile_rights"]),
                "dialogue_qc": dialogue_qc or {},
            }
        )
        references.append({"order": 0, "role": "voice", "version_id": version.id})
    from shine.media.artifacts import safe_asset_path
    from shine.quality.technical import _full_decode_check

    path = safe_asset_path(root, storage_path)
    decoded = _full_decode_check(path, settings.ffmpeg_path)
    hard_errors = set((dialogue_qc or {}).get("hard_errors") or [])
    passed = decoded.status == "pass" and not hard_errors
    qc = {
        "technical": decoded.status,
        "decode": decoded.model_dump(),
        "sha256": digest,
        "is_mock": mocked,
        "semantic": "PENDING_HUMAN_REVIEW",
        "dialogue": dialogue_qc,
    }
    with factory.begin() as db:
        task = db.get(LibraryRenderTask, task_id)
        attempt = db.get(LibraryTaskAttempt, attempt_id)
        if task is None or attempt is None or task.status != "RUNNING" or task.worker_id is None:
            raise RuntimeError("VOICE_TASK_ADOPTION_CONFLICT")
        context = _revalidate_output_sources(db, settings, payload, context)
        rights = _new_output_rights(db, mode, context)
        db.add(rights)
        db.flush()
        media = LibraryMedia(
            kind="audio",
            purpose="generated",
            filename=filename,
            content_type=content_type,
            sha256=digest,
            storage_path=storage_path,
            byte_size=len(content),
            width=None,
            height=None,
            duration_s=media_info.duration_s,
            rights_id=rights.id,
            created_by="shine-worker",
        )
        db.add(media)
        db.flush()
        title = str(payload.get("title") or "声音候选").strip()[:200]
        asset = LibraryAsset(title=title, kind="audio", category=category, tags=[], created_by="shine-worker")
        db.add(asset)
        db.flush()
        prompt = (
            str(payload.get("text") or "") if mode in {"DESIGN", "TTS"} else str(payload.get("reference_text") or "")
        )
        version = service.create_version_from_media(
            db,
            settings,
            asset=asset,
            media=media,
            actor="shine-worker",
            source_mode=mode,
            prompt=prompt,
            provenance=provenance,
            references_manifest=references,
            workflow_revision_hash=runtime["fingerprint"],
            is_mock=mocked,
        )
        version.qc = qc
        version.status = "READY_FOR_REVIEW" if passed else "QC_FAILED"
        version_id = version.id
        task.result_version_id = version.id
        task.status = "CANDIDATE_READY" if passed else "QC_FAILED"
        task.external_outcome = "SUCCEEDED"
        task.error_code = None if passed else "VOICE_TECHNICAL_OR_DIALOGUE_QC_FAILED"
        task.worker_id = None
        task.lease_owner = None
        task.state_version += 1
        task.updated_at = utcnow()
        task.heartbeat_at = utcnow()
        attempt.status = task.status
        attempt.external_outcome = "SUCCEEDED"
        attempt.output_sha256 = digest
        attempt.submit_stage = "OUTPUT_STORED"
        attempt.payload = {**(attempt.payload or {}), "version_id": version.id, "dialogue_qc": dialogue_qc}
        add_audit(db, "shine-worker", "LIBRARY_VOICE_CANDIDATE_QC", "library_version", version.id, qc)
    return version_id, digest, "CANDIDATE_READY" if passed else "QC_FAILED"


def _run_one(factory, settings, task_id: str, worker_id: str) -> None:
    payload: dict[str, Any] = {}
    attempt_id: str | None = None
    intent_open = False
    sdk_context = None
    try:
        payload, context = _claim_inputs(factory, settings, task_id)
        provider = _provider(factory, settings)
        mode = str(payload["mode"])
        methods = {
            "DESIGN": ["design"],
            "AUTHORIZED_CLONE": ["clone"],
            "TTS": ["prepare_conditioning", "synthesize", "transcribe", "align"],
        }[mode]
        budget_fn = getattr(provider, "resource_budget_mb", None)
        budget = max((budget_fn(method) if callable(budget_fn) else 4000) for method in methods)
        if type(budget) is not int or not 0 < budget <= 16384:
            raise RuntimeError("VOICE_RESOURCE_BUDGET_INVALID")

        def release_models() -> None:
            release = getattr(provider, "release_models", None)
            if release:
                release()

        with resource_slot(factory, "TTS", budget, release_models=release_models, wait_seconds=0) as lease_id:
            from shine.voiceops.runtime_config import load_voice_config

            voice_config = load_voice_config(settings)
            runtime = {
                "provider": type(provider).__name__,
                "mode": mode,
                "budget_mb": budget,
                "mock": bool(getattr(provider, "is_mock", False)),
                "packages": voice_config.routing(),
                "runtimes": {role: spec.model_dump(mode="json") for role, spec in voice_config.runtimes().items()},
            }
            runtime["fingerprint"] = graph_hash(runtime)
            attempt_id = _open_attempt(factory, settings, task_id, worker_id, lease_id, payload, runtime)
            intent_open = True
            sdk_context = CURRENT_LIBRARY_VOICE_ATTEMPT.set((factory, attempt_id))
            if mode == "DESIGN":
                result = provider.design(str(payload["description"]), str(payload["text"]), str(payload["character"]))
            elif mode == "AUTHORIZED_CLONE":
                result = provider.clone(context["reference"], str(payload["reference_text"]), str(payload["character"]))
            else:
                profile = context["profile"]
                conditioning = context["conditioning"]
                digest = hashlib.sha256(conditioning).hexdigest()
                if hasattr(provider, "prepare_conditioning"):
                    provider.prepare_conditioning(conditioning, digest, context["conditioning_text"])
                result = provider.synthesize(
                    str(payload["text"]),
                    str(payload["character"]),
                    digest,
                    str(payload["emotion"]),
                    float(payload["speed"]),
                    float(payload["pause"]),
                )
                # TTS, ASR and alignment may use different checkpoints. Do not
                # start the next model until the current provider has confirmed
                # release; the shared lease remains held for the whole task.
                _release_models_between_stages(provider)
                transcript = provider.transcribe(result["audio"])
                _release_models_between_stages(provider)
                words = provider.align(result["audio"], str(payload["text"]))
                from shine.voiceops.providers import verify_dialogue

                errors = verify_dialogue(result, transcript, words, str(payload["text"]), str(payload["character"]))
                review_errors = {"ASR_REVIEW_REQUIRED", "VOICE_REVIEW_REQUIRED"}
                dialogue_qc = {
                    "transcript": transcript,
                    "words": words,
                    "errors": errors,
                    "hard_errors": sorted(set(errors) - review_errors),
                    "review_errors": sorted(set(errors) & review_errors),
                    "voice_hash": profile.voice_hash,
                }
            if mode != "TTS":
                dialogue_qc = None
            _store_candidate(
                factory, settings, task_id, payload, context, result, provider, attempt_id, runtime, dialogue_qc
            )
    except Exception as exc:  # noqa: BLE001 - if inference may have started, never submit it a second time
        with factory.begin() as db:
            task = db.get(LibraryRenderTask, task_id)
            if task is None or task.status not in {"RUNNING", "RECONCILING"}:
                return
            uncertain = intent_open or task.submit_intent_at is not None
            resource_busy = not uncertain and "GPU_BUSY_RETRY_OR_QUEUE" in str(exc)
            task.status = "UNKNOWN" if uncertain else "QUEUED" if resource_busy else "FAILED"
            task.external_outcome = "UNKNOWN" if uncertain else "NOT_SENT"
            task.error_code = "GPU_BUSY_RETRY_OR_QUEUE" if resource_busy else f"VOICE_EXECUTION_{type(exc).__name__}"
            task.worker_id = None
            task.lease_owner = None
            task.state_version += 1
            task.updated_at = utcnow()
            if attempt_id:
                attempt = db.get(LibraryTaskAttempt, attempt_id)
                if attempt:
                    attempt.status = task.status
                    attempt.external_outcome = task.external_outcome
                    attempt.error_code = task.error_code
                    attempt.submit_stage = task.status
            add_audit(
                db,
                "shine-worker",
                "LIBRARY_VOICE_TASK_DEFERRED" if resource_busy else "LIBRARY_VOICE_TASK_FAILED",
                "library_render_task",
                task_id,
                {"status": task.status, "error_code": task.error_code, "submit_intent": bool(task.submit_intent_at)},
            )
    finally:
        if sdk_context is not None:
            CURRENT_LIBRARY_VOICE_ATTEMPT.reset(sdk_context)


def run_one_pending_job(factory, settings, worker_id: str) -> int:
    task_id = _claim(factory, worker_id)
    if task_id is None:
        return 0
    stopped = threading.Event()

    def pulse() -> None:
        while not stopped.wait(20):
            with factory.begin() as db:
                db.execute(
                    update(LibraryRenderTask)
                    .where(
                        LibraryRenderTask.id == task_id,
                        LibraryRenderTask.worker_id == worker_id,
                        LibraryRenderTask.status == "RUNNING",
                    )
                    .values(heartbeat_at=utcnow(), updated_at=utcnow())
                )

    thread = threading.Thread(target=pulse, name="shine-voice-task-heartbeat", daemon=True)
    thread.start()
    try:
        _run_one(factory, settings, task_id, worker_id)
    finally:
        stopped.set()
        thread.join(timeout=2)
    return 1
