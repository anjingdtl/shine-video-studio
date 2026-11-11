"""First managed renderer startup with a durable intent and a physical baseline.

This is the only entry point that may create the owned ComfyUI process group.
The intent is persisted before the process is launched, ownership is proved by
PID, OS birth identity and named Job Object membership, the queue must start
closed, and the memory baseline is measured from the host instead of being
inferred from a lease. An uncertain outcome is retained for reconciliation and
is never relaunched blindly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from threading import Event, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import literal_column, select, text
from sqlalchemy.exc import OperationalError

from shine.comfy.workflow import graph_hash
from shine.comfyops.handoff_memory import evaluate_readiness, probe_memory
from shine.comfyops.installer import process_identity
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.owned_process import (
    OwnedProcessGroup,
    inspect_named_group,
    named_group_active_processes,
)
from shine.comfyops.renderer_supervisor import RendererSupervisor, windows_port_listeners
from shine.models import AppSetting, Job, utcnow
from shine.services import add_audit

logger = logging.getLogger(__name__)

STARTUP_PREFIX = "renderer-startup:"
REVIEW_PREFIX = "review-startup:"
HANDOFF_PREFIX = "handoff:"
TERMINAL_STAGES = {"RECONCILE_REQUIRED", "STOPPED"}
COMPLETE_STAGES = {"RUNNING"}
# The renderer is already stopped at these stages, so a failed restart has
# nothing left to protect once the recorded process and its port are gone.
STOPPED_HANDOFF_STAGES = {"STOPPED", "PHYSICAL_READY", "REVIEW_INTENT", "REVIEW_FINISHED",
                          "RESTART_INTENT", "RESTARTED"}


def startup_key(instance_id: str, prefix: str = STARTUP_PREFIX) -> str:
    if not isinstance(instance_id, str) or not instance_id or len(instance_id) > 128:
        raise HTTPException(409, "COMFY_STARTUP_IDENTITY_INVALID")
    return prefix + instance_id


def _digest(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class StartupHeartbeat:
    """Keep the exclusive lease and the job claim alive while the renderer starts.

    A startup that loses its lease stops immediately: the renderer must never
    keep a GPU without an exclusive admission record.
    """

    def __init__(self, factory, *, lease_id, job_id, worker_id, state_version,
                 lease_seconds=60, interval=5):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("HANDOFF_HEARTBEAT_INTERVAL_INVALID")
        if interval <= 0 or interval >= lease_seconds:
            raise ValueError("HANDOFF_HEARTBEAT_INTERVAL_INVALID")
        self.factory = factory
        self.lease_id = lease_id
        self.job_id = job_id
        self.worker_id = worker_id
        self.state_version = state_version
        self.lease_seconds = lease_seconds
        self.interval = interval
        self.stopped = Event()
        self.error = None
        self.thread = None

    def _pulse(self) -> bool:
        with self.factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            now = utcnow()
            lease = db.get(ResourceLease, self.lease_id)
            job = db.get(Job, self.job_id)
            if (not lease or lease.status != "ACTIVE" or not lease.exclusive
                    or lease.expires_at <= now or not job
                    or job.lease_owner != self.worker_id or job.state_version != self.state_version
                    or job.stale or not job.lease_expires_at or job.lease_expires_at <= now):
                if lease and lease.owner.startswith("h3:") and lease.status == "ACTIVE":
                    lease.status = "RECONCILE_REQUIRED"
                return False
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=self.lease_seconds)
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        return True

    def _run(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                if not self._pulse():
                    self.error = "COMFY_STARTUP_LEASE_LOST"
                    self.stopped.set()
                    return
            except Exception:
                self.error = "COMFY_STARTUP_HEARTBEAT_FAILED"
                self.stopped.set()
                return

    def __enter__(self):
        if not self._pulse():
            raise HTTPException(409, "COMFY_STARTUP_LEASE_LOST")
        self.thread = Thread(target=self._run, name="shine-startup-heartbeat", daemon=True)
        self.thread.start()
        return self

    def check(self) -> None:
        if self.error:
            raise HTTPException(409, self.error)

    def __exit__(self, exc_type, exc, traceback):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise HTTPException(409, "COMFY_STARTUP_HEARTBEAT_EXIT_UNCONFIRMED")


@dataclass(frozen=True)
class ManagedRenderer:
    """A running owned renderer whose intent and baseline are already persisted."""

    instance_id: str
    startup_id: str
    group: OwnedProcessGroup
    supervisor: RendererSupervisor
    process: dict
    url: str
    memory: dict
    gpu_uuid: str
    min_ram_mb: int
    min_vram_mb: int
    lease_id: str
    job_id: str
    probe: object = None


def _read_startup(db, instance_id, prefix=STARTUP_PREFIX):
    row = db.get(AppSetting, startup_key(instance_id, prefix))
    return row.value if row else {}


def startup_record_for_lease(db, lease_id: str) -> dict | None:
    """The newest managed-startup record that launched the renderer owning a lease.

    A lease can survive a renderer restart, so the newest record is the one
    that owns the GPU right now.  An older record may name a process that has
    already exited and must never be used to prove that the GPU is free.
    """
    if not lease_id:
        return None
    rows = db.scalars(
        select(AppSetting)
        .where(AppSetting.key.like(STARTUP_PREFIX + "%"))
        .order_by(literal_column("app_settings.rowid").desc())
    ).all()
    for row in rows:
        value = row.value if isinstance(row.value, dict) else {}
        if value.get("lease_id") == lease_id:
            return dict(value)
    return None


def renderer_is_gone(record: dict) -> bool:
    """Whether the recorded renderer process has really exited.

    A lease is a claim on a live process.  The claim only holds nothing when
    three independent facts agree:

    * the recorded process identity is gone (an unreadable identity is
      uncertainty, never death),
    * no member of the named owned group is alive any more, and
    * nothing answers on its loopback port.

    A clock timeout proves none of them: the model may still be resident in
    GPU memory.
    """
    process = record.get("process") or {}
    pid, birth = process.get("pid"), process.get("birth")
    if type(pid) is not int or pid <= 0 or not isinstance(birth, str) or not birth:
        return False
    try:
        identity = process_identity(pid)
    except (OSError, ValueError):
        # Uncertainty is never interpreted as death.
        return False
    if identity == birth:
        return False  # the recorded renderer is still alive
    job_name = process.get("job_name")
    if isinstance(job_name, str) and job_name:
        try:
            if named_group_active_processes(job_name) > 0:
                return False  # a member of the owned group is still alive
        except (OSError, ValueError):
            return False
    port = record.get("port")
    if not port:
        return False
    try:
        return not windows_port_listeners(int(port))
    except (OSError, ValueError):
        return False


def _release_orphan_h3_lease(db, lease) -> bool:
    """Release an h3: lease whose managed-startup record was never written.

    A worker that died between acquiring the exclusive lease and writing the
    startup journal leaves a lease that no record can ever match, which would
    deadlock every later GPU user.  The orphan proof stays conservative: the
    owning job is no longer running (or the lease expired), no managed startup
    record on any instance still names a live process or owned process group,
    and every managed instance's loopback port is free.  Any uncertainty keeps
    the lease blocked for reconciliation; nothing is resubmitted.
    """
    owner = lease.owner or ""
    if not owner.startswith("h3:"):
        return False
    job = db.get(Job, owner.removeprefix("h3:"))
    # A handoff reservation proves the renderer handover already began for this
    # job; its lease belongs to that reconciliation path, not to the orphan
    # proof, and must stay blocked for reconciliation.
    if db.get(AppSetting, "handoff-reservation:" + owner.removeprefix("h3:")) is not None:
        return False
    if job is not None:
        # A lease whose job really reached a renderer (an attempt exists, or an
        # external prompt id was ever recorded) may have loaded models into the
        # GPU: losing its journal is uncertainty, and it stays blocked.  Only
        # a lease from a run that never started rendering is a true orphan.
        if job.provider_job_id or job.attempts:
            return False
        job_active = job.status in ("QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING")
        if job_active and lease.expires_at is not None and lease.expires_at > utcnow():
            return False
    for row in db.scalars(select(AppSetting).where(AppSetting.key.like(STARTUP_PREFIX + "%"))).all():
        value = row.value if isinstance(row.value, dict) else {}
        process = value.get("process") or {}
        pid, birth = process.get("pid"), process.get("birth")
        if type(pid) is int and pid > 0 and isinstance(birth, str) and birth:
            try:
                if process_identity(pid) == birth:
                    return False  # a recorded renderer is still alive
            except (OSError, ValueError):
                return False  # an unreadable identity is uncertainty, never death
        job_name = process.get("job_name")
        if isinstance(job_name, str) and job_name:
            try:
                if named_group_active_processes(job_name) > 0:
                    return False
            except (OSError, ValueError):
                return False
    for instance in db.scalars(select(ComfyInstance)).all():
        # The port lives in the row payload, not as a column attribute; the
        # previous getattr(instance, "port", ...) was always None, so this
        # port-free proof never actually ran (audit R11).
        payload = instance.payload if isinstance(instance.payload, dict) else {}
        port = payload.get("port")
        if not port:
            continue
        try:
            if windows_port_listeners(int(port)):
                return False
        except (OSError, ValueError):
            return False
    from shine.comfyops.scheduler import ResourceScheduler

    ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
    if job is not None:
        add_audit(
            db,
            "comfy-worker",
            "COMFY_ORPHAN_H3_LEASE_RELEASED",
            "job",
            job.id,
            {"lease_id": lease.id, "job_active": False, "port_free": True, "resubmitted": False},
            project_id=job.project_id,
        )
    return True


def confirm_agent_text_unloaded(factory, settings=None) -> bool:
    """Confirm no local text model is resident, through the declared protocol.

    This runs *outside* any write transaction on purpose: confirming a loopback
    text profile means an HTTP unload whose timeout is the provider's own, and
    holding the SQLite writer across it would starve every heartbeat.  A
    remote-only configuration has nothing an AGENT slot could ever have loaded,
    so it confirms without any call at all.
    """
    from shine.llm.lifecycle import release_text_model
    from shine.models import ProviderProfile
    from shine.security import is_loopback_url

    with factory() as db:
        local = [
            profile
            for profile in db.scalars(select(ProviderProfile).where(ProviderProfile.enabled.is_(True)))
            if profile.kind != "mock" and is_loopback_url(profile.base_url)
        ]
        if not local:
            return True
        from shine.config import Settings
        from shine.llm.gateway import LLMGateway
        from shine.llm.secrets import SecretStore

        resolved = settings or Settings.from_env()
        gateway = LLMGateway(SecretStore(resolved.secret_store_dir or resolved.data_dir / "secrets"))
        configs = []
        for profile in local:
            try:
                configs.append(gateway.config_for(profile))
            except Exception:
                return False  # an unreadable local profile is uncertainty
    for config in configs:
        try:
            release_text_model(config)
        except Exception:
            return False  # an unconfirmed unload is uncertainty, never death
    return True


def release_orphan_agent_lease(db, lease, *, text_unloaded: bool) -> bool:
    """Release an expired ``agent:`` lease that provably holds nothing resident.

    An AGENT slot guards a *text* call, so it never owns a renderer process: no
    startup journal can ever match it and the renderer proof can never run.  A
    workbench killed during such a call therefore left the lease
    ``RECONCILE_REQUIRED`` forever, and because one such row blocks every later
    admission the whole GPU ledger deadlocked with no recovery path at all.

    ``text_unloaded`` is established by :func:`confirm_agent_text_unloaded`
    before the write transaction opens; without it the lease stays blocked.
    """
    owner = lease.owner or ""
    if not owner.startswith("agent:"):
        return False
    if lease.expires_at is None or lease.expires_at > utcnow():
        return False  # a live owner may still be inside the call
    if not text_unloaded:
        return False
    from shine.comfyops.scheduler import ResourceScheduler

    ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
    add_audit(
        db,
        "comfy-worker",
        "RESOURCE_ORPHAN_AGENT_LEASE_RELEASED",
        "resource_lease",
        lease.id,
        {"owner": owner, "renderer_owned": False, "resubmitted": False},
    )
    return True


def release_dead_renderer_lease(db, lease, *, text_unloaded: bool = False) -> bool:
    """Release one exclusive lease whose renderer is provably gone."""
    from shine.comfyops.scheduler import ResourceScheduler

    record = startup_record_for_lease(db, lease.id)
    if record is None:
        # No journal was ever written for this lease: fall back to the
        # conservative orphan proof instead of deadlocking the GPU ledger.
        if _release_orphan_h3_lease(db, lease):
            return True
        # A text slot owns no renderer, so the proof above can never match it.
        return release_orphan_agent_lease(db, lease, text_unloaded=text_unloaded)
    if not renderer_is_gone(record):
        return False
    ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
    return True


def reconcile_dead_renderer_leases(factory, settings=None) -> list[dict]:
    """Release the exclusive lease of a renderer that is provably gone.

    A worker that died while it owned the GPU leaves an exclusive lease behind
    that blocks every later GPU user, even though the renderer it protects has
    already exited.  Only a renderer whose recorded process has really exited
    and whose loopback port is free may release its lease; every other lease
    stays blocked for reconciliation.
    """
    findings: list[dict] = []
    with factory() as db:
        leases = db.scalars(
            select(ResourceLease).where(ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"]))
        ).all()
        candidates = [(lease.id, lease.owner, lease.status) for lease in leases]
    # A text slot owns no renderer, so its orphan proof is the declared unload
    # protocol. Establish it once, outside every write transaction, and only
    # when an agent orphan is actually waiting on it.
    text_unloaded: bool | None = None
    if any((owner or "").startswith("agent:") for _, owner, _ in candidates):
        text_unloaded = confirm_agent_text_unloaded(factory, settings)
    for lease_id, owner, status in candidates:
        try:
            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                lease = db.get(ResourceLease, lease_id)
                if lease is None or lease.status not in {"ACTIVE", "RECONCILE_REQUIRED"}:
                    continue
                if not release_dead_renderer_lease(db, lease, text_unloaded=bool(text_unloaded)):
                    continue
                job_id = owner.removeprefix("h3:") if owner.startswith("h3:") else None
                job = db.get(Job, job_id) if job_id else None
                if job is not None:
                    add_audit(db, "comfy-worker", "COMFY_DEAD_RENDERER_LEASE_RELEASED", "job", job.id,
                              {"lease_id": lease_id, "renderer_gone": True, "resubmitted": False},
                              project_id=job.project_id)
        except OperationalError:
            # A momentary writer conflict is not a lost lease: try again on the
            # next beat instead of killing the resident worker.
            logger.warning("dead_renderer_lease_reconcile_deferred lease_id=%s", lease_id)
            continue
        findings.append({"lease_id": lease_id, "owner": owner, "previous_status": status, "action": "released"})
    return findings


def _write_startup(db, instance_id, value, prefix=STARTUP_PREFIX):
    key = startup_key(instance_id, prefix)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.flush()
    return value


def _record(factory, instance_id, value, prefix=STARTUP_PREFIX):
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        current = _read_startup(db, instance_id, prefix)
        if current.get("stage") != value.get("previous_stage"):
            raise HTTPException(409, "COMFY_STARTUP_STAGE_CHANGED")
        return _write_startup(db, instance_id, {**current, **{k: v for k, v in value.items()
                                                             if k != "previous_stage"}}, prefix)


def _fail(factory, instance_id, reason, lease_id=None, job_id=None, prefix=STARTUP_PREFIX):
    """Retain an uncertain startup for reconciliation; never release the GPU."""
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        current = _read_startup(db, instance_id, prefix)
        if current.get("stage") != "RECONCILE_REQUIRED":
            current = {**current, "stage": "RECONCILE_REQUIRED",
                       "last_confirmed_stage": current.get("stage"), "reason": reason,
                       "recorded_at": utcnow().isoformat()}
            _write_startup(db, instance_id, current, prefix)
        if lease_id:
            lease = db.get(ResourceLease, lease_id)
            if lease and lease.status == "ACTIVE":
                lease.status = "RECONCILE_REQUIRED"
        if job_id:
            job = db.get(Job, job_id)
            if job:
                add_audit(db, "comfy-worker", "COMFY_STARTUP_RECONCILE_REQUIRED", "job", job.id,
                          {"instance_id": instance_id, "reason": reason}, project_id=job.project_id)


def _bind_lease(db, lease_id, job_id):
    """The startup may only run under an exclusive, unexpired admission."""
    lease = db.get(ResourceLease, lease_id)
    job = db.get(Job, job_id)
    now = utcnow()
    if (not lease or lease.owner != "h3:" + job_id or lease.status != "ACTIVE"
            or not lease.exclusive or lease.expires_at <= now):
        raise HTTPException(409, "COMFY_STARTUP_RESOURCE_LEASE_INVALID")
    if not job or job.provider != "comfyui" or job.stale:
        raise HTTPException(409, "COMFY_STARTUP_JOB_INVALID")
    others = db.scalars(select(ResourceLease).where(
        ResourceLease.id != lease_id, ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"])))
    if any(True for _ in others):
        raise HTTPException(409, "COMFY_STARTUP_RESOURCE_CONFLICT")
    return lease, job


def _wait_for_owned_listener(group, port, *, timeout=600):
    """Wait until the only loopback listener belongs to the owned group.

    The listener may be a descendant of the group leader, which is what the
    Job Object proves; it must not be a process outside the owned tree. A real
    renderer loads thousands of node types before it answers, so the wait is
    generous: the startup intent is already durable.
    """
    deadline = time.monotonic() + timeout
    while True:
        listeners = windows_port_listeners(port)
        if listeners:
            if len(listeners) != 1 or listeners[0][0] != "127.0.0.1":
                raise HTTPException(409, "COMFY_STARTUP_LISTENER_UNVERIFIED")
            pid = listeners[0][1]
            birth = process_identity(pid)
            if not birth or not group.owns(pid, birth):
                raise HTTPException(409, "COMFY_STARTUP_OWNERSHIP_UNVERIFIED")
            return {"pid": pid, "birth": birth}
        if time.monotonic() >= deadline:
            raise HTTPException(409, "COMFY_STARTUP_LISTENER_TIMEOUT")
        time.sleep(0.05)


def _installation_evidence(db, settings, binding, *, port, cwd=None):
    """Prove that the installation a run is bound to is still the proven one.

    The installation row is durable evidence: it is never rewritten by a run,
    and a run may only use it while it is real, stopped and complete.
    """
    from shine.comfyops.deployment import installed_instance_blockers, load_default_catalog
    from shine.comfyops.instances import instance_identity

    installed = db.get(ComfyInstance, binding['id'])
    if (not installed or installed.id != instance_identity('local', port)
            or installed.sha256 != binding['sha256'] or installed.payload.get('is_mock', True)
            or installed.payload.get('process') or installed.status != 'STOPPED'
            or (cwd is not None
                and os.path.realpath(installed.payload.get('root', '')) != os.path.realpath(cwd))
            or installed_instance_blockers(db, settings, installed, load_default_catalog())):
        raise HTTPException(409, 'COMFY_INSTALLATION_BINDING_CHANGED')
    return installed


def _installation_binding(db, settings, binding, *, instance_id, job_id, cwd, port):
    """Bind one run to one installation, both inside the same database."""
    from shine.comfyops.managed_recipe import managed_run_identity
    from shine.models import JobAttempt

    if (not isinstance(binding, dict) or set(binding) != {'id', 'sha256', 'attempt_id'}
            or any(not isinstance(v, str) or not v for v in binding.values())):
        raise HTTPException(409, 'COMFY_INSTALLATION_BINDING_INVALID')
    attempt = db.get(JobAttempt, binding['attempt_id'])
    if (not attempt or attempt.job_id != job_id
            or instance_id != managed_run_identity(binding['id'], attempt.id)):
        raise HTTPException(409, 'COMFY_INSTALLATION_BINDING_CHANGED')
    _installation_evidence(db, settings, binding, port=port, cwd=cwd)
    return dict(binding)


def start_managed_renderer(
    factory,
    settings,
    *,
    instance_id: str,
    lease_id: str,
    job_id: str,
    worker_id: str,
    argv,
    cwd,
    env,
    pinned_files,
    port: int,
    handoff_token: str,
    adapter_sha256: str,
    gpu_uuid: str,
    min_ram_mb: int,
    min_vram_mb: int,
    launch=None,
    supervisor_factory=None,
    probe=None,
    required_node_types=None,
    installation=None,
) -> ManagedRenderer:
    """Create the owned renderer exactly once, behind a durable intent.

    The intent is written before the process group exists. A repeated call for
    the same instance is refused, and an uncertain launch is retained for
    reconciliation rather than retried.
    """
    if (not isinstance(argv, list | tuple) or not argv
            or not isinstance(port, int) or not 0 < port < 65536
            or not isinstance(handoff_token, str) or len(handoff_token) < 32
            or not isinstance(adapter_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", adapter_sha256)
            or not isinstance(gpu_uuid, str) or not gpu_uuid
            or type(min_ram_mb) is not int or min_ram_mb <= 0
            or type(min_vram_mb) is not int or min_vram_mb <= 0):
        raise HTTPException(409, "COMFY_STARTUP_ARGUMENTS_INVALID")
    key = startup_key(instance_id)
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if db.get(AppSetting, key):
            raise HTTPException(409, "COMFY_STARTUP_ALREADY_RECORDED")
        lease, job = _bind_lease(db, lease_id, job_id)
        if installation is not None:
            _installation_binding(db, settings, installation, instance_id=instance_id, job_id=job_id,
                                  cwd=cwd, port=port)
        owner_birth = process_identity(os.getpid())
        if not owner_birth:
            raise HTTPException(409, "COMFY_STARTUP_IDENTITY_UNVERIFIED")
        pins = {}
        for source in pinned_files:
            path = os.path.abspath(str(source))
            try:
                with open(path, "rb") as stream:
                    pins[path] = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as exc:
                raise HTTPException(409, "COMFY_STARTUP_SOURCE_INVALID") from exc
        intent = {
            "stage": "INTENT",
            "instance_id": instance_id,
            "startup_id": str(uuid4()),
            "lease_id": lease_id,
            "job_id": job_id,
            "worker_id": worker_id,
            "owner_pid": os.getpid(),
            "owner_birth": owner_birth,
            "argv_sha256": _digest(" ".join(str(arg) for arg in argv)),
            "cwd": str(cwd),
            "pins": pins,
            "port": port,
            "gpu_uuid": gpu_uuid,
            "min_ram_mb": min_ram_mb,
            "min_vram_mb": min_vram_mb,
            # The control token itself never reaches SQLite; only its digest.
            "handoff_token_sha256": _digest(handoff_token),
            "adapter_sha256": adapter_sha256,
            "recorded_at": utcnow().isoformat(),
            "installation": installation,
        }
        _write_startup(db, instance_id, intent)
        add_audit(db, "comfy-worker", "COMFY_STARTUP_INTENT", "job", job_id,
                  {"instance_id": instance_id, "port": port}, project_id=job.project_id)
        if installation is not None:
            # The run identity and the installation it uses are one durable
            # fact; neither may be established without the other.
            add_audit(db, "comfy-worker", "COMFY_INSTALLATION_BOUND", "comfy_instance",
                      installation["id"],
                      {"instance_id": instance_id, "installation_id": installation["id"],
                       "installation_sha256": installation["sha256"],
                       "attempt_id": installation["attempt_id"]},
                      project_id=job.project_id)
        startup_id = intent["startup_id"]

    group = None
    try:
        launcher = launch or OwnedProcessGroup.launch
        group = launcher(list(argv), cwd=cwd, env=dict(env), pinned_files=list(pinned_files),
                         job_name="Local\\ShineHandoff-" + uuid4().hex)
        birth = process_identity(group.pid)
        if not birth or not group.owns(group.pid, birth):
            raise HTTPException(409, "COMFY_STARTUP_OWNERSHIP_UNVERIFIED")
        _record(factory, instance_id, {
            "previous_stage": "INTENT", "stage": "LAUNCHED",
            "process": {"pid": group.pid, "birth": birth, "job_name": group.job_name},
        })
        with StartupHeartbeat(factory, lease_id=lease_id, job_id=job_id, worker_id=worker_id,
                              state_version=job.state_version) as guard:
            guard.check()
            _wait_for_owned_listener(group, port)
            supervisor = (supervisor_factory or RendererSupervisor)(group, f"http://127.0.0.1:{port}",
                                                                     handoff_token, adapter_sha256)
            # The renderer must come up closed: a fresh start never accepts a prompt.
            supervisor.require_closed_restart()
            owner = supervisor._control("GET", "/shine-handoff/status", "drained")
            _record(factory, instance_id, {
                "previous_stage": "LAUNCHED", "stage": "LISTENING",
                "listener": owner, "process": {"pid": group.pid, "birth": birth, "job_name": group.job_name},
            })
            guard.check()
            # Resource admission comes first: a renderer that cannot be measured
            # must not be registered, whatever it can serve.
            measured = (probe or probe_memory)(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
            verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                          gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                          min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
            if measured.get("ready") is not True or not verified["ready"]:
                raise HTTPException(409, "COMFY_STARTUP_MEMORY_INSUFFICIENT")
            guard.check()
            required = (required_workflow_node_types(factory)
                        if required_node_types is None else set(required_node_types))
            try:
                inventory = verify_required_nodes(f"http://127.0.0.1:{port}", required=required)
            except MissingRequiredNodes as exc:
                _record(factory, instance_id, {
                    "previous_stage": "LISTENING", "stage": "LISTENING",
                    "missing_nodes": exc.missing, "required_nodes": sorted(required),
                })
                raise
            _record(factory, instance_id, {
                "previous_stage": "LISTENING", "stage": "LISTENING", "node_inventory": inventory,
            })
            process = {"pid": group.pid, "birth": birth, "job_name": group.job_name}
            payload = {
                "managed": True,
                "is_mock": False,
                "url": f"http://127.0.0.1:{port}",
                "port": port,
                "process": process,
                "startup_id": startup_id,
                "gpu_uuid": gpu_uuid,
                "installation": installation,
            }
            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                if db.get(AppSetting, key):
                    current = _read_startup(db, instance_id)
                    if current.get("stage") != "LISTENING":
                        raise HTTPException(409, "COMFY_STARTUP_STAGE_CHANGED")
                instance = db.get(ComfyInstance, instance_id)
                if installation is not None:
                    _installation_binding(db, settings, installation, instance_id=instance_id, job_id=job_id,
                                          cwd=cwd, port=port)
                if instance is not None and instance.sha256 != graph_hash(payload):
                    raise HTTPException(409, "COMFY_STARTUP_INSTANCE_CHANGED")
                if instance is None:
                    instance = ComfyInstance(id=instance_id, payload=payload, sha256=graph_hash(payload),
                                             status="RUNNING_PENDING_HEALTH")
                    db.add(instance)
                else:
                    instance.payload = payload
                    instance.sha256 = graph_hash(payload)
                    instance.status = "RUNNING_PENDING_HEALTH"
                from shine.comfyops.resource_epoch import read_epoch

                epoch = read_epoch(db)
                current = _read_startup(db, instance_id)
                if current.get("stage") != "LISTENING":
                    raise HTTPException(409, "COMFY_STARTUP_STAGE_CHANGED")
                _write_startup(db, instance_id, {
                    **current,
                    "stage": "RUNNING", "memory": verified, "process": process,
                    "instance_sha256": graph_hash(payload), "resource_epoch": epoch,
                })
                add_audit(db, "comfy-worker", "COMFY_STARTUP_RUNNING", "job", job_id,
                          {"instance_id": instance_id, "pid": group.pid}, project_id=job.project_id)
    except BaseException as exc:
        _fail(factory, instance_id, getattr(exc, "detail", None) or type(exc).__name__,
              lease_id=lease_id, job_id=job_id)
        raise
    return ManagedRenderer(
        instance_id=instance_id,
        startup_id=startup_id,
        group=group,
        supervisor=supervisor,
        process=process,
        url=f"http://127.0.0.1:{port}",
        memory=verified,
        gpu_uuid=gpu_uuid,
        min_ram_mb=min_ram_mb,
        min_vram_mb=min_vram_mb,
        lease_id=lease_id,
        job_id=job_id,
        probe=probe,
    )


def reconcile_managed_startups(factory, settings, *, terminate=None):
    """Recover startup intents left behind by an interrupted worker.

    Only a process whose recorded PID and birth identity still match, and whose
    membership in the recorded named Job Object is confirmed, may be stopped.
    Anything else is left untouched and kept blocked for manual reconciliation.
    """
    terminate = terminate or _terminate_owned_group
    with factory() as db:
        rows = db.scalars(select(AppSetting).where(
            AppSetting.key.like(STARTUP_PREFIX + "%") | AppSetting.key.like(REVIEW_PREFIX + "%"))).all()
        pending = []
        for row in rows:
            value = row.value if isinstance(row.value, dict) else {}
            if value.get("stage") in COMPLETE_STAGES | TERMINAL_STAGES | {"FAILED"}:
                # ``FAILED`` is terminal for the image renderer's own start
                # path (it already terminated its group and dropped the
                # process field); rewriting it into RECONCILE_REQUIRED only
                # produced a phantom blocker (audit R10).
                continue
            prefix = REVIEW_PREFIX if row.key.startswith(REVIEW_PREFIX) else STARTUP_PREFIX
            pending.append((row.key.removeprefix(prefix), prefix, dict(value)))
    findings = []
    for instance_id, prefix, value in pending:
        owner_pid = value.get("owner_pid")
        owner_birth = value.get("owner_birth")
        owner_alive = False
        if type(owner_pid) is int and owner_pid > 0 and isinstance(owner_birth, str) and owner_birth:
            try:
                owner_alive = process_identity(owner_pid) == owner_birth
            except (OSError, ValueError):
                owner_alive = False
        if owner_alive:
            # Another live worker owns this startup; it must not be reconciled
            # or blocked from here.
            continue
        process = value.get("process") or {}
        pid = process.get("pid")
        birth = process.get("birth")
        job_name = process.get("job_name")
        finding = {"instance_id": instance_id, "stage": value.get("stage"),
                   "job_name": job_name, "pid": pid, "action": "none"}
        alive = False
        owned = False
        if type(pid) is int and pid > 0 and isinstance(birth, str) and birth:
            try:
                alive = process_identity(pid) == birth
            except (OSError, ValueError):
                alive = False
        if alive and isinstance(job_name, str):
            try:
                owned = inspect_named_group(job_name, pid, birth)
            except OSError:
                owned = False
        if alive and owned:
            try:
                finding["action"] = "terminated"
                finding["evidence"] = terminate(job_name)
            except Exception as exc:
                finding["action"] = "blocked"
                finding["reason"] = type(exc).__name__
        elif alive:
            finding["action"] = "blocked"
            finding["reason"] = "OWNERSHIP_UNVERIFIED"
        else:
            finding["action"] = "already_exited"
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            current = _read_startup(db, instance_id, prefix)
            if current.get("stage") in COMPLETE_STAGES | TERMINAL_STAGES:
                continue
            lease_id = current.get("lease_id")
            lease = db.get(ResourceLease, lease_id) if lease_id else None
            if lease and lease.status == "ACTIVE":
                lease.status = "RECONCILE_REQUIRED"
            _write_startup(db, instance_id, {
                **current,
                "stage": "RECONCILE_REQUIRED",
                "last_confirmed_stage": current.get("stage"),
                "reason": "COMFY_STARTUP_INTERRUPTED",
                "reconciliation": finding,
                "recorded_at": utcnow().isoformat(),
            }, prefix)
        findings.append(finding)
    return findings


def reconcile_stopped_handoffs(factory) -> list[dict]:
    """Release a failed handoff whose owned renderer is provably stopped.

    A handoff that failed at or after ``STOPPED`` has no live process left to
    protect, but its lease stays ``RECONCILE_REQUIRED`` and blocks every later
    GPU user. Only a dead recorded renderer process and a free loopback port may
    release it. The job itself stays ``NEEDS_REVIEW``; this never turns an
    unreviewed render into a pass.
    """
    from shine.comfyops.scheduler import ResourceScheduler

    findings: list[dict] = []
    with factory() as db:
        rows = db.scalars(select(AppSetting).where(AppSetting.key.like(HANDOFF_PREFIX + "%"))).all()
        pending = [(row.key, dict(row.value)) for row in rows
                   if isinstance(row.value, dict) and row.value.get("stage") == "RECONCILE_REQUIRED"]
    for key, value in pending:
        attempt_id = key.removeprefix(HANDOFF_PREFIX)
        if value.get("last_confirmed_stage") not in STOPPED_HANDOFF_STAGES:
            continue
        process = value.get("process") or {}
        pid, birth = process.get("pid"), process.get("birth")
        if type(pid) is not int or pid <= 0 or not isinstance(birth, str) or not birth:
            continue  # nothing proves the renderer exited
        try:
            if process_identity(pid) == birth:
                continue  # the recorded renderer is still alive
        except (OSError, ValueError):
            pass
        port = urlsplit(value.get("instance_url") or "").port
        if not port or windows_port_listeners(port):
            continue  # an unclear or occupied port cannot be declared released
        finding = {"attempt_id": attempt_id, "job_id": value.get("job_id"), "action": "released"}
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = db.get(AppSetting, key)
            current = dict(row.value) if row and isinstance(row.value, dict) else {}
            if current.get("stage") != "RECONCILE_REQUIRED":
                continue
            lease = db.get(ResourceLease, current.get("resource_id"))
            if (not lease or lease.owner != "h3:" + str(current.get("job_id") or "")
                    or lease.status != "RECONCILE_REQUIRED"):
                continue
            ResourceScheduler().release(db, lease.id, lease.owner, reconciled=True)
            row.value = {**current, "stage": "RECONCILE_COMPLETE",
                         "last_confirmed_stage": "RECONCILE_REQUIRED",
                         "reconciled_at": utcnow().isoformat(),
                         "reason": "OWNED_RENDERER_STOPPED",
                         "reconciliation": finding}
            job = db.get(Job, current.get("job_id"))
            if job:
                add_audit(db, "comfy-worker", "COMFY_HANDOFF_RECONCILED_RELEASE", "job", job.id,
                          {"attempt_id": attempt_id, "resource_id": lease.id,
                           "renderer_stopped": True}, project_id=job.project_id)
        findings.append(finding)
    return findings


def _terminate_owned_group(job_name):
    """Stop a named owned group whose ownership was already proved."""
    from shine.comfyops.owned_process import terminate_named_group

    return terminate_named_group(job_name)


def verify_managed_startup(factory, settings, renderer, *, instance_id=None):
    """Re-prove the startup binding and the live physical baseline."""
    instance_id = instance_id or renderer.instance_id
    with factory() as db:
        value = _read_startup(db, instance_id)
        instance = db.get(ComfyInstance, instance_id)
        lease = db.get(ResourceLease, renderer.lease_id)
        now = utcnow()
        if (value.get("stage") != "RUNNING" or not instance
                or instance.sha256 != value.get("instance_sha256")
                or graph_hash(instance.payload) != instance.sha256
                or instance.payload.get("process") != renderer.process
                or instance.payload.get("managed") is not True
                or instance.payload.get("is_mock") is not False
                or not lease or lease.status != "ACTIVE" or not lease.exclusive
                or lease.expires_at <= now):
            raise HTTPException(409, "COMFY_STARTUP_BINDING_INVALID")
        binding = instance.payload.get("installation")
        if binding:
            # A continued run stays bound to the installation it started from:
            # the evidence must not have moved underneath the running renderer.
            _installation_evidence(db, settings, binding, port=instance.payload.get("port"),
                                   cwd=value.get("cwd"))
    renderer.supervisor.verify_drained_binding(renderer.url, renderer.process)
    measured = (renderer.probe or probe_memory)(renderer.gpu_uuid, min_ram_mb=renderer.min_ram_mb,
                                                min_vram_mb=renderer.min_vram_mb)
    verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                  gpu=measured.get("gpu"), min_ram_mb=renderer.min_ram_mb,
                                  min_vram_mb=renderer.min_vram_mb, expected_gpu=renderer.gpu_uuid)
    if measured.get("ready") is not True or not verified["ready"]:
        raise HTTPException(409, "COMFY_STARTUP_MEMORY_INSUFFICIENT")
    return verified


def _review_runtime(*, base_url, port, listener, group):
    """Read the owned review supervisor's own runtime identity."""
    import httpx

    if not group.owns(listener["pid"], listener["birth"]):
        raise HTTPException(409, "COMFY_STARTUP_OWNERSHIP_UNVERIFIED")
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get(base_url + "/health")
    if response.status_code != 200:
        raise HTTPException(409, "COMFY_REVIEW_SUPERVISOR_NOT_READY")
    body = response.json()
    if (not isinstance(body, dict) or body.get("status") != "ok" or body.get("is_mock") is not False
            or not isinstance(body.get("runtime_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", body["runtime_sha256"])):
        raise HTTPException(409, "COMFY_REVIEW_SUPERVISOR_NOT_READY")
    return body["runtime_sha256"]


def start_managed_review_service(
    factory,
    settings,
    *,
    service_id: str,
    lease_id: str,
    job_id: str,
    worker_id: str,
    argv,
    cwd,
    env,
    pinned_files,
    port: int,
    handoff_token: str,
    runtime_sha256: str,
    gpu_uuid: str,
    min_ram_mb: int,
    min_vram_mb: int,
    launch=None,
    probe=None,
    base_url=None,
    renderer_instance_id=None,
    logical_service_id=None,
):
    """Start an owned review supervisor and close it with a first-use fence.

    The supervisor must be an owned process: the review receipt is only
    trustworthy while the process identity and its exit can be proved. The
    returned configuration is the only thing a later review may be reserved
    against, and the fence binds that first reservation.
    """
    from shine.quality.review_release import create_cold_review_fence

    if (not isinstance(runtime_sha256, str | None)
            or (runtime_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", runtime_sha256))
            or not isinstance(port, int) or not 0 < port < 65536
            or not isinstance(handoff_token, str) or len(handoff_token) < 32):
        raise HTTPException(409, "COMFY_REVIEW_STARTUP_ARGUMENTS_INVALID")
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if db.get(AppSetting, REVIEW_PREFIX + service_id):
            raise HTTPException(409, "COMFY_REVIEW_STARTUP_ALREADY_RECORDED")
        lease, job = _bind_lease(db, lease_id, job_id)
        if renderer_instance_id is not None:
            parent = _read_startup(db, renderer_instance_id)
            if (parent.get('stage') != 'RUNNING' or parent.get('lease_id') != lease_id
                    or parent.get('job_id') != job_id or not logical_service_id):
                raise HTTPException(409, 'COMFY_REVIEW_PARENT_RUNTIME_INVALID')
        owner_birth = process_identity(os.getpid())
        if not owner_birth:
            raise HTTPException(409, "COMFY_STARTUP_IDENTITY_UNVERIFIED")
        _write_startup(db, service_id, {
            "stage": "INTENT", "service_id": service_id, "kind": "review",
            "lease_id": lease_id, "job_id": job_id, "worker_id": worker_id,
            "owner_pid": os.getpid(), "owner_birth": owner_birth,
            "argv_sha256": _digest(" ".join(str(arg) for arg in argv)),
            "cwd": str(cwd), "port": port, "gpu_uuid": gpu_uuid,
            "min_ram_mb": min_ram_mb, "min_vram_mb": min_vram_mb,
            "runtime_sha256": runtime_sha256,
            "handoff_token_sha256": _digest(handoff_token),
            "recorded_at": utcnow().isoformat(),
            "renderer_instance_id": renderer_instance_id, "logical_service_id": logical_service_id,
        }, REVIEW_PREFIX)
    group = None
    try:
        launcher = launch or OwnedProcessGroup.launch
        group = launcher(list(argv), cwd=cwd, env=dict(env), pinned_files=list(pinned_files),
                         job_name="Local\\ShineHandoff-" + uuid4().hex)
        birth = process_identity(group.pid)
        if not birth or not group.owns(group.pid, birth):
            raise HTTPException(409, "COMFY_STARTUP_OWNERSHIP_UNVERIFIED")
        _record(factory, service_id, {
            "previous_stage": "INTENT", "stage": "LAUNCHED",
            "process": {"pid": group.pid, "birth": birth, "job_name": group.job_name},
        }, REVIEW_PREFIX)
        listener = _wait_for_owned_listener(group, port)
        observed_runtime = _review_runtime(base_url=f"http://127.0.0.1:{port}", port=port,
                                           listener=listener, group=group)
        if runtime_sha256 and observed_runtime != runtime_sha256:
            raise HTTPException(409, "COMFY_REVIEW_RUNTIME_CHANGED")
        runtime_sha256 = observed_runtime
        measured = (probe or probe_memory)(gpu_uuid, min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb)
        verified = evaluate_readiness(available_ram_bytes=measured.get("available_ram_bytes"),
                                      gpu=measured.get("gpu"), min_ram_mb=min_ram_mb,
                                      min_vram_mb=min_vram_mb, expected_gpu=gpu_uuid)
        if measured.get("ready") is not True or not verified["ready"]:
            raise HTTPException(409, "COMFY_STARTUP_MEMORY_INSUFFICIENT")
        fence = create_cold_review_fence(
            base_url=f"http://127.0.0.1:{port}", runtime_sha256=runtime_sha256,
            supervisor={"pid": listener["pid"], "birth": listener["birth"]}, gpu_uuid=gpu_uuid,
            min_ram_mb=min_ram_mb, min_vram_mb=min_vram_mb, handoff_token=handoff_token,
            owner_group=group,
        )
        # The review configuration must address the supervisor exactly as the
        # reviewer's provider profile does, including its version path.
        service_base_url = base_url or f"http://127.0.0.1:{port}"
        if urlsplit(service_base_url).port != port:
            raise HTTPException(409, "COMFY_REVIEW_SERVICE_PORT_MISMATCH")
        config = {
            "base_url": service_base_url,
            "runtime_sha256": runtime_sha256,
            "supervisor": {"pid": listener["pid"], "birth": listener["birth"]},
            "gpu_uuid": gpu_uuid,
            "handoff_token": handoff_token,
            "owner_group": group,
        }
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            current = _read_startup(db, service_id, REVIEW_PREFIX)
            if current.get("stage") != "LAUNCHED":
                raise HTTPException(409, "COMFY_STARTUP_STAGE_CHANGED")
            _write_startup(db, service_id, {
                **current,
                "stage": "RUNNING",
                "process": {"pid": group.pid, "birth": birth, "job_name": group.job_name},
                "cold_fence_id": fence["fence_id"], "memory": fence["memory"],
            }, REVIEW_PREFIX)
    except BaseException as exc:
        _fail(factory, service_id, getattr(exc, "detail", None) or type(exc).__name__,
              lease_id=lease_id, job_id=job_id, prefix=REVIEW_PREFIX)
        raise
    return {"group": group, "config": config, "fence": fence}


def required_workflow_node_types(factory):
    """Every node type that a registered production workflow needs.

    The registered workflow profiles are the authoritative statement of what
    this installation must be able to run. A renderer that cannot serve all of
    them is not a valid renderer for this project.
    """
    from shine.models import WorkflowProfile

    with factory() as db:
        rows = db.scalars(select(WorkflowProfile)).all()
    needed = set()
    for row in rows:
        graph = row.graph
        if isinstance(graph, str):
            try:
                graph = json.loads(graph)
            except ValueError:
                continue
        if not isinstance(graph, dict):
            continue
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type"):
                needed.add(str(node["class_type"]))
    return needed


class MissingRequiredNodes(HTTPException):
    """The renderer cannot serve every registered production workflow."""

    def __init__(self, missing):
        super().__init__(409, "COMFY_REQUIRED_NODE_MISSING")
        self.missing = list(missing)


def verify_required_nodes(base_url, *, required, timeout=30):
    """Confirm the renderer really exposes every required node type.

    A static whitelist can drift from the installed node packs. Reading the
    live inventory closes that gap: a renderer that cannot serve every
    registered workflow is rejected before any prompt is accepted.
    """
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = client.get(base_url.rstrip("/") + "/object_info")
    if response.status_code != 200:
        raise HTTPException(409, "COMFY_NODE_INVENTORY_UNAVAILABLE")
    try:
        info = response.json()
    except ValueError as exc:
        raise HTTPException(409, "COMFY_NODE_INVENTORY_UNAVAILABLE") from exc
    if not isinstance(info, dict):
        raise HTTPException(409, "COMFY_NODE_INVENTORY_UNAVAILABLE")
    missing = sorted(name for name in required if name not in info)
    if missing:
        raise MissingRequiredNodes(missing)
    return {"verified": len(required), "available": len(info), "missing": missing}


class StartupBoundary:
    """Live proofs for the first managed startup, never a stored pass flag.

    It replaces the review-release boundary for the very first attempt: there
    is no prior review to release, so the proof is the live startup binding and
    a freshly measured physical baseline.
    """

    def __init__(self, renderer):
        if not isinstance(renderer, ManagedRenderer):
            raise HTTPException(409, "COMFY_STARTUP_BOUNDARY_INVALID")
        self._renderer = renderer

    def verify(self, factory, settings, instance_id, current_attempt_id, current_lease_id):
        from shine.comfyops.resource_epoch import read_epoch

        renderer = self._renderer
        if instance_id != renderer.instance_id or current_lease_id != renderer.lease_id:
            raise HTTPException(409, "COMFY_STARTUP_BOUNDARY_INVALID")
        with factory() as db:
            value = _read_startup(db, instance_id)
            instance = db.get(ComfyInstance, instance_id)
            lease = db.get(ResourceLease, current_lease_id)
            now = utcnow()
            epoch = read_epoch(db)
            if (value.get("stage") != "RUNNING" or not instance
                    or instance.sha256 != value.get("instance_sha256")
                    or graph_hash(instance.payload) != instance.sha256
                    or instance.payload.get("process") != renderer.process
                    or instance.payload.get("managed") is not True
                    or instance.payload.get("is_mock") is not False
                    or instance.status != "RUNNING_PENDING_HEALTH"
                    or not lease or lease.owner != "h3:" + renderer.job_id
                    or lease.status != "ACTIVE" or not lease.exclusive or lease.expires_at <= now
                    or epoch != value.get("resource_epoch")):
                raise HTTPException(409, "COMFY_STARTUP_BOUNDARY_INVALID")
            others = db.scalars(select(ResourceLease).where(
                ResourceLease.id != current_lease_id,
                ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"])))
            if any(True for _ in others):
                raise HTTPException(409, "COMFY_STARTUP_BOUNDARY_INVALID")
        memory = verify_managed_startup(factory, settings, renderer, instance_id=instance_id)
        return {"prior_attempt_id": None, "gpu_uuid": renderer.gpu_uuid, "reviews": [],
                "memory": memory, "observed_resource_epoch": epoch}

