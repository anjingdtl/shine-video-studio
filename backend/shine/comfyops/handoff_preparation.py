"""Owned handoff through review with an experimental worker finalization entry.

The normal worker startup and first-render admission are not wired to this
coordinator. Uncertain stages retain the lease for reconciliation.
"""

import hashlib
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import text

from shine.comfyops.handoff_journal import (
    CURRENT_HANDOFF_REVIEW,
    HandoffHeartbeat,
    begin_handoff,
    record_handoff_stage,
    reserve_handoff_review,
)
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.preproduction import enforce_job
from shine.media.artifacts import safe_asset_path
from shine.models import AgentTaskRecord, AppSetting, Job, MediaArtifact, Project, Shot, utcnow
from shine.quality.gates import QualityPolicy
from shine.quality.isolation_receipt import validate_receipt_fields
from shine.schemas import RenderRequest
from shine.services import add_audit


def _verify_artifact(data_dir, storage_path, sha256, byte_size):
    path = safe_asset_path(Path(data_dir), storage_path)
    try:
        if path.stat().st_size != byte_size:
            raise ValueError
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
                raise ValueError
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "HANDOFF_ARTIFACT_CHANGED") from exc


def _verify_preproduction(factory, job_id, settings):
    with factory() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(409, "HANDOFF_JOB_CLAIM_LOST")
        enforce_job(db, job, settings)


class HandoffReviewProvider:
    """Expose a tracked reviewer only through a verified serial handoff stage."""

    def __init__(self, session, tracked, kind, release_config, previous_release=None, cold_fence_id=None):
        from shine.comfyops.model_calls import TrackedProvider

        if (not isinstance(tracked, TrackedProvider) or kind not in {"ASR", "VLM"}
                or tracked.production_job_id != session.job_id or tracked.reviewer_kind != kind
                or not isinstance(release_config, dict)
                or release_config.get("gpu_uuid") != session.gpu_uuid
                or not getattr(getattr(tracked.provider, "config", None), "base_url", None)
                or release_config.get("base_url") != tracked.provider.config.base_url
                or any(key in release_config for key in {"task_id", "review_id", "receipt"})
                or (previous_release is not None and cold_fence_id is not None)):
            raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
        self._session, self._tracked, self._kind = session, tracked, kind
        self._release_config = dict(release_config)
        self._previous_release = previous_release
        self._cold_fence_id = cold_fence_id

    def __getattr__(self, name):
        from shine.comfyops.model_calls import MODEL_METHODS

        expected = "transcribe" if self._kind == "ASR" else "review"
        if name in MODEL_METHODS and name != expected:
            raise HTTPException(409, "HANDOFF_REVIEW_METHOD_INVALID")
        method = getattr(self._tracked, name)
        if name != expected:
            return method

        def call(*args, **kwargs):
            from shine.quality.review_admission import CURRENT_REVIEW_ADMISSION
            from shine.quality.review_release import (
                reserve_review_service,
                verify_cold_review_fence,
                verify_review_ready,
                verify_review_release,
            )

            self._session.guard.check()
            limits = {"min_ram_mb": self._session.min_ram_mb, "min_vram_mb": self._session.min_vram_mb}
            if self._cold_fence_id is not None:
                verify_cold_review_fence(**{**self._release_config, **limits, "fence_id": self._cold_fence_id})
            elif self._previous_release is None:
                if not self._session.settings.offline_simulation:
                    raise HTTPException(409, "HANDOFF_REVIEW_COLD_FENCE_REQUIRED")
                verify_review_ready(**{**self._release_config, **limits})
            else:
                if {k: v for k, v in self._previous_release.items() if k != "receipt"} != self._release_config:
                    raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
                verify_review_release(**{**self._previous_release, **limits})
            self._session.guard.check()
            with self._session.review_stage(self._kind) as review_id:
                admission = {**self._release_config, "review_id": review_id, **limits}
                if self._previous_release is not None:
                    admission["previous_receipt"] = self._previous_release["receipt"]
                if self._cold_fence_id is not None:
                    admission["cold_fence_id"] = self._cold_fence_id
                reserve_review_service(**admission)
                self._session.guard.check()
                token = CURRENT_REVIEW_ADMISSION.set((self._release_config["base_url"],
                                                      self._release_config["handoff_token"], review_id))
                try:
                    result = method(*args, **kwargs)
                finally:
                    CURRENT_REVIEW_ADMISSION.reset(token)
                with self._session.factory() as db:
                    journal = db.get(AppSetting, "handoff:" + self._session.attempt_id)
                    steps = journal.value.get("review_steps", []) if journal else []
                    if not steps or steps[-1].get("id") != review_id:
                        raise HTTPException(409, "HANDOFF_REVIEW_INTENT_MISSING")
                    task_id = steps[-1].get("task_id")
                    task = db.get(AgentTaskRecord, task_id) if task_id else None
                    receipt = (task.trace or {}).get("isolation_receipt") if task else None
                if not receipt:
                    raise HTTPException(409, "HANDOFF_REVIEW_RECEIPT_MISSING")
                self._session.accept_review({**self._release_config, "task_id": task_id,
                                             "review_id": review_id, "receipt": receipt})
                return result

        return call


class HandoffSession:
    """Experimental serial review/restart coordination under an existing lease."""

    def __init__(self, factory, settings, job_id, attempt_id, lease_id, guard, supervisor, gpu_uuid,
                 min_ram_mb, min_vram_mb):
        self.factory, self.settings, self.job_id = factory, settings, job_id
        self.attempt_id, self.lease_id, self.guard, self.supervisor = attempt_id, lease_id, guard, supervisor
        self.gpu_uuid, self.min_ram_mb, self.min_vram_mb = gpu_uuid, min_ram_mb, min_vram_mb
        self.accepted, self.review_requests, self.last_request, self.finished = {}, {}, None, False
        self.last_verified_epoch = None

    @contextmanager
    def review_stage(self, kind):
        """Reserve before a tracked model call; never replay an interrupted intent."""
        self.guard.check()
        self._check_review_releases()
        review_id = reserve_handoff_review(self.factory, self.attempt_id, kind, settings=self.settings)
        token = CURRENT_HANDOFF_REVIEW.set((self.attempt_id, review_id, self.job_id, kind, self.settings))
        try:
            yield review_id
        finally:
            CURRENT_HANDOFF_REVIEW.reset(token)

    def _check_review_releases(self):
        from shine.comfyops.resource_epoch import read_epoch
        from shine.quality.review_release import verify_review_release

        if set(self.accepted) != set(self.review_requests):
            raise HTTPException(409, "HANDOFF_REQUIRED_REVIEW_MISSING")
        if self.accepted:
            with self.factory() as db:
                if self.last_verified_epoch is None or read_epoch(db) != self.last_verified_epoch:
                    raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")
        for args in self.review_requests.values():
            verify_review_release(**{**args, "min_ram_mb": self.min_ram_mb, "min_vram_mb": self.min_vram_mb})
            self.guard.check()
        if self.accepted:
            with self.factory() as db:
                if read_epoch(db) != self.last_verified_epoch:
                    raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")

    def _task(self, db, request):
        task = db.get(AgentTaskRecord, request.get("task_id"))
        job = db.get(Job, self.job_id)
        trace = task.trace if task else {}
        kind = trace.get("kind")
        receipt = request.get("receipt")
        if (not task or not job or task.project_id != job.project_id or task.status != "COMPLETED"
                or kind not in {"ASR", "VLM"} or trace.get("resource_id") != self.lease_id
                or (trace.get("production_review") or {}).get("job_id") != self.job_id
                or trace.get("isolation_receipt") != receipt
                or (trace.get("review_provider") or {}).get("base_url_sha256") != hashlib.sha256(
                    str(request.get("base_url", "")).encode()).hexdigest()
                or request.get("gpu_uuid") != self.gpu_uuid):
            raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
        validate_receipt_fields(receipt)
        if (receipt["runtime_sha256"] != request.get("runtime_sha256")
                or receipt["path"] != ("/v1/audio/transcriptions" if kind == "ASR" else "/v1/chat/completions")):
            raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
        mock = trace.get("is_mock") is not False
        if mock and not self.settings.offline_simulation:
            raise HTTPException(409, "HANDOFF_MOCK_REVIEW_FORBIDDEN")
        executor = trace.get("executor_identity") or trace.get("received_executor_identity")
        if not mock and (trace.get("result_is_mock") is not False or not isinstance(executor, dict)
                         or executor.get("runtime_sha256") != receipt["runtime_sha256"]):
            raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
        return task, kind, mock

    def accept_review(self, request):
        """Seal and verify each completed review before allowing the next stage."""
        from shine.comfyops.resource_epoch import read_epoch
        from shine.quality.review_release import verify_review_release

        if self.finished or not isinstance(request, dict):
            raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
        self.guard.check()
        with self.factory() as db:
            _task, kind, mock = self._task(db, request)
            self._review_intent(db, request, kind)
            observed_epoch = read_epoch(db)
            if ((_task.trace or {}).get("resource_epoch") != observed_epoch
                    or observed_epoch.get("kind") != "model" or observed_epoch.get("lease_id") != self.lease_id):
                raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")
        if kind in self.accepted:
            raise HTTPException(409, "HANDOFF_REVIEW_DUPLICATE")
        args = {key: value for key, value in request.items() if key not in {"task_id", "review_id"}}
        proof = verify_review_release(**{**args, "min_ram_mb": self.min_ram_mb, "min_vram_mb": self.min_vram_mb})
        self.guard.check()
        with self.factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            task, current_kind, current_mock = self._task(db, request)
            journal, steps = self._review_intent(db, request, current_kind)
            if read_epoch(db) != observed_epoch or (task.trace or {}).get("resource_epoch") != observed_epoch:
                raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")
            if current_kind != kind or current_mock != mock:
                raise HTTPException(409, "HANDOFF_REVIEW_BINDING_INVALID")
            enforce_job(db, db.get(Job, self.job_id), self.settings)
            task.trace = {**task.trace, "handoff_release": {"attempt_id": self.attempt_id, "proof": proof}}
            steps[-1] = {**steps[-1], "status": "VERIFIED", "proof": proof, "resource_epoch": observed_epoch}
            journal.value = {**journal.value, "review_steps": steps}
            add_audit(db, "comfy-worker", "HANDOFF_REVIEW_RELEASE_VERIFIED", "agent_task", task.id,
                      {"attempt_id": self.attempt_id, "kind": kind, "is_mock": mock}, project_id=task.project_id)
        self.accepted[kind] = {"task_id": request["task_id"], "proof": proof, "is_mock": mock}
        self.review_requests[kind] = args
        self.last_request = args
        self.last_verified_epoch = observed_epoch
        return proof

    def _review_intent(self, db, request, kind):
        journal = db.get(AppSetting, "handoff:" + self.attempt_id)
        value = journal.value if journal else {}
        steps = list(value.get("review_steps", []))
        task = db.get(AgentTaskRecord, request.get("task_id"))
        job = db.get(Job, self.job_id)
        lease = db.get(ResourceLease, self.lease_id)
        shot = db.get(Shot, job.shot_id) if job else None
        if (value.get("stage") != "REVIEW_INTENT" or value.get("job_id") != self.job_id
                or value.get("resource_id") != self.lease_id or not steps
                or steps[-1].get("id") != request.get("review_id") or steps[-1].get("kind") != kind
                or steps[-1].get("status") != "INTENT" or steps[-1].get("task_id") != request.get("task_id")
                or not lease or lease.status != "ACTIVE" or not lease.exclusive or lease.expires_at <= utcnow()
                or lease.owner != "h3:" + self.job_id or not job or job.stale
                or job.status not in {"RUNNING", "QC_PENDING"} or job.lease_owner != value.get("worker_id")
                or job.state_version != value.get("state_version") or not job.lease_expires_at
                or job.lease_expires_at <= utcnow() or not shot or shot.revision != job.shot_revision
                or not task
                or (task.trace or {}).get("handoff_review_id") != request.get("review_id")):
            raise HTTPException(409, "HANDOFF_REVIEW_INTENT_MISSING")
        return journal, steps

    def finish(self, *, required_kinds):
        """Restart only after each required review has an individually sealed receipt."""
        with self.factory() as db:
            job = db.get(Job, self.job_id)
            project = db.get(Project, job.project_id) if job else None
            if not job or not project:
                raise HTTPException(409, "HANDOFF_JOB_CLAIM_LOST")
            request = RenderRequest.model_validate(job.request)
            policy = QualityPolicy.from_dict(project.quality_policy)
            if request.self_review_scope == "basic":
                required = ({"VLM"} if request.shot_spec.characters else set()) | (
                    {"ASR"} if request.shot_spec.dialogue else set())
            else:
                required = ({"VLM"} if policy.require_semantic_review or policy.require_style_review else set()) | (
                    {"ASR"} if policy.require_audio_review and request.shot_spec.dialogue else set())
        required.update(required_kinds)
        if (self.finished or not self.accepted or not required.issubset(self.accepted)
                or set(self.accepted) != set(self.review_requests)):
            raise HTTPException(409, "HANDOFF_REQUIRED_REVIEW_MISSING")
        from shine.comfyops.resource_epoch import read_epoch

        self.guard.check()
        with self.factory() as db:
            checked_epoch = read_epoch(db)
        if self.last_verified_epoch is None or checked_epoch != self.last_verified_epoch:
            raise HTTPException(409, "HANDOFF_RESOURCE_ACTIVITY_CHANGED")
        self._check_review_releases()
        record_handoff_stage(self.factory, self.attempt_id, "REVIEW_INTENT", "REVIEW_FINISHED", {
            "reviews": list(self.accepted.values()), "is_mock": any(item["is_mock"] for item in self.accepted.values()),
        }, settings=self.settings)
        record_handoff_stage(self.factory, self.attempt_id, "REVIEW_FINISHED", "RESTART_INTENT", {},
                             settings=self.settings)
        replacement, evidence = self.supervisor.restore_after_review(
            self.last_request, min_ram_mb=self.min_ram_mb, min_vram_mb=self.min_vram_mb,
        )
        self.guard.check()
        record_handoff_stage(self.factory, self.attempt_id, "RESTART_INTENT", "RESTARTED", evidence,
                             settings=self.settings, expected_resource_epoch=checked_epoch)
        self.finished = True
        return replacement, evidence


class ManagedReviewCoordinator:
    """Hold owned process and in-memory credentials for one worker's serial review.

    This coordinator does not authorize renderer startup or H3 submission.
    A caller must independently establish the worker and initial physical boundary.
    """

    def __init__(self, supervisor, instance_id, history_dir, gpu_uuid, *, min_ram_mb, min_vram_mb,
                 review_configs):
        from shine.comfyops.renderer_supervisor import RendererSupervisor

        if (not isinstance(supervisor, RendererSupervisor) or not isinstance(instance_id, str) or not instance_id
                or not isinstance(gpu_uuid, str) or not gpu_uuid or type(min_ram_mb) is not int or min_ram_mb <= 0
                or type(min_vram_mb) is not int or min_vram_mb <= 0 or not isinstance(review_configs, dict)
                or not review_configs or not set(review_configs).issubset({"ASR", "VLM"})):
            raise HTTPException(409, "HANDOFF_COORDINATOR_INVALID")
        self.supervisor, self.instance_id, self.history_dir, self.gpu_uuid = (
            supervisor, instance_id, Path(history_dir), gpu_uuid
        )
        self.min_ram_mb, self.min_vram_mb = min_ram_mb, min_vram_mb
        self._review_configs = {key: dict(value) for key, value in review_configs.items()}
        self._previous_releases = {}
        self._cold_fences = {}
        self.session = None
        self.replacement = None
        self.keeper = None

    @contextmanager
    def review(self, factory, settings, job_id, attempt_id, lease_id, worker_id, state_version,
               project_id, semantic, asr):
        from shine.comfyops.model_calls import TrackedProvider

        selected = {kind: provider for kind, provider in (("VLM", semantic), ("ASR", asr)) if provider is not None}
        if self.session is not None or not selected or not set(selected).issubset(self._review_configs):
            raise HTTPException(409, "HANDOFF_COORDINATOR_INVALID")
        ports = [urlsplit(self._review_configs[kind]["base_url"]).port for kind in selected]
        if len(ports) != len(set(ports)):
            raise HTTPException(409, "HANDOFF_REVIEW_SERVICE_SHARED")
        with prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, self.instance_id, worker_id,
                                    state_version, self.supervisor, self.history_dir, self.gpu_uuid,
                                    min_ram_mb=self.min_ram_mb, min_vram_mb=self.min_vram_mb) as session:
            self.session = session
            wrapped = {}
            for kind, provider in selected.items():
                tracked = TrackedProvider(provider, factory, project_id, reviewer_kind=kind, production_job_id=job_id)
                wrapped[kind] = HandoffReviewProvider(session, tracked, kind, self._review_configs[kind],
                                                      self._previous_releases.get(kind), self._cold_fences.get(kind))
            yield wrapped.get("VLM"), wrapped.get("ASR")
            self.replacement, _evidence = session.finish(required_kinds=set(selected))
            self._previous_releases.update(session.review_requests)
            for kind in session.review_requests:
                self._cold_fences.pop(kind, None)
        from shine.comfyops.handoff_idle import IdleHandoffKeeper

        self.keeper = IdleHandoffKeeper(factory, attempt_id, self.replacement).start()


@contextmanager
def prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, instance_id, worker_id,
                           state_version, supervisor, history_dir, gpu_uuid, *, min_ram_mb, min_vram_mb):
    """Drain and stop an owned renderer while holding the original H3 lease."""
    data_dir, history_dir = Path(settings.data_dir).resolve(), Path(history_dir).resolve()
    if not history_dir.is_relative_to(data_dir):
        raise HTTPException(409, "HANDOFF_HISTORY_DIRECTORY_INVALID")
    with factory() as db:
        instance, job = db.get(ComfyInstance, instance_id), db.get(Job, job_id)
        artifact = db.get(MediaArtifact, job.artifact_id) if job and job.artifact_id else None
        if not instance or not artifact:
            raise HTTPException(409, "HANDOFF_OUTPUT_NOT_PERSISTED")
        payload = dict(instance.payload)
        storage_path, digest, byte_size = artifact.storage_path, artifact.sha256, artifact.byte_size
    _verify_preproduction(factory, job_id, settings)
    _verify_artifact(data_dir, storage_path, digest, byte_size)
    supervisor.require_closed_restart()
    supervisor.verify_binding(payload.get("url", ""), payload.get("process"))
    begin_handoff(factory, job_id, attempt_id, lease_id, instance_id, worker_id, state_version, payload["url"],
                  settings=settings)
    with HandoffHeartbeat(factory, attempt_id) as guard:
        record_handoff_stage(factory, attempt_id, "INTENT", "DRAIN_INTENT", {}, settings=settings)
        drained = supervisor.drain_and_archive(history_dir, attempt_id)
        guard.check()
        record_handoff_stage(factory, attempt_id, "DRAIN_INTENT", "DRAINED", {
            **drained, "history_path": str(drained["history_path"]),
        }, settings=settings)
        _verify_preproduction(factory, job_id, settings)
        _verify_artifact(data_dir, storage_path, digest, byte_size)
        guard.check()
        record_handoff_stage(factory, attempt_id, "DRAINED", "STOP_INTENT", {}, settings=settings)
        stopped = supervisor.stop_after_drain()
        guard.check()
        record_handoff_stage(factory, attempt_id, "STOP_INTENT", "STOPPED", stopped, settings=settings)
        memory = supervisor.check_memory(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
        guard.check()
        _verify_preproduction(factory, job_id, settings)
        record_handoff_stage(factory, attempt_id, "STOPPED", "PHYSICAL_READY", memory, settings=settings)
        record_handoff_stage(factory, attempt_id, "PHYSICAL_READY", "REVIEW_INTENT", {}, settings=settings)
        session = HandoffSession(factory, settings, job_id, attempt_id, lease_id, guard, supervisor, gpu_uuid,
                                 min_ram_mb, min_vram_mb)
        yield session
        if not session.finished:
            # A caller that exits early cannot release the shared resource.
            raise HTTPException(409, "HANDOFF_RESTART_NOT_IMPLEMENTED")
