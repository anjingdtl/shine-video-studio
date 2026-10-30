"""Contract counterexamples for the first managed renderer startup.

Every case here is a negative or a boundary contract: the startup may only
create an owned process behind a durable intent, may only run under one
exclusive lease, must come up closed, must measure its own physical baseline
and may never relaunch an uncertain attempt.
"""

import hashlib
import json
import os
import socket
import sys
import time

import pytest
from fastapi import HTTPException

from shine.comfyops.handoff_startup import (
    STARTUP_PREFIX,
    reconcile_managed_startups,
    start_managed_renderer,
)
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.renderer_supervisor import windows_port_listeners
from shine.comfyops.scheduler import ResourceScheduler
from shine.models import AppSetting, Job, utcnow

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are required")

SERVER = r'''
import json, os, pathlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    drained = os.environ.get('SHINE_COMFY_START_DRAINED') == '1'
    def log_message(self, *args): pass
    def respond(self, code, body):
        raw = json.dumps(body).encode(); self.send_response(code); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        if (self.path not in ('/shine-handoff/drain','/shine-handoff/resume') or
                self.headers.get('Authorization') != 'Bearer ' + os.environ['TOKEN']):
            return self.respond(401, {})
        Handler.drained = self.path == '/shine-handoff/drain'
        return self.respond(200, {'status':'drained' if Handler.drained else 'accepting','protocol':1,
                                  'pid':int(os.environ.get('FAKE_PID',os.getpid())),
                                  'adapter_sha256':os.environ['BARRIER_HASH']})
    def do_GET(self):
        if self.path == '/history': return self.respond(200, {'historic-prompt':{'status':'complete'}})
        auth_ok = self.headers.get('Authorization') == 'Bearer ' + os.environ['TOKEN']
        if self.path == '/shine-handoff/status' and auth_ok:
            return self.respond(200, {'status':'drained' if Handler.drained else 'accepting','protocol':1,
                                      'pid':int(os.environ.get('FAKE_PID',os.getpid())),
                                      'adapter_sha256':os.environ['BARRIER_HASH']})
        return self.respond(401,{})
server = HTTPServer(('127.0.0.1',int(os.environ['PORT'])),Handler)
pathlib.Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
'''

TOKEN = "t" * 40
DIGEST = "a" * 64
FENCE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


@pytest.fixture
def renderer(tmp_path):
    """A closed-start renderer script on a reserved port, not yet running."""
    script, marker = tmp_path / "server.py", tmp_path / "port.txt"
    script.write_text(SERVER, encoding="utf-8")
    with socket.socket() as reserving:
        reserving.bind(("127.0.0.1", 0))
        port = reserving.getsockname()[1]
    return script, marker, port, tmp_path


def ready_memory(gpu_uuid, *, min_ram_mb, min_vram_mb):
    return {"ready": True, "available_ram_bytes": (min_ram_mb + 1024) * 1024**2,
            "gpu": {"uuid": gpu_uuid, "total_mb": 16303, "free_mb": min_vram_mb + 1024},
            "min_ram_mb": min_ram_mb, "min_vram_mb": min_vram_mb}


@pytest.fixture
def claimed(api):
    """A claimed ComfyUI job with one admitted exclusive lease."""
    from datetime import timedelta

    from shine.models import JobAttempt
    from tests.conftest import create_comfy_job, create_project, create_shot, freeze_style

    client, factory, settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    with factory.begin() as db:
        lease = ResourceScheduler().enqueue(db, "h3:" + job["id"], 16384, True, 100)
        db.flush()
        lease_id = lease.id
        row = db.get(Job, job["id"])
        row.status, row.lease_owner = "SUBMITTING", "worker-a"
        row.lease_expires_at = utcnow() + timedelta(seconds=120)
        row.state_version += 1
        attempt = JobAttempt(job_id=row.id, attempt_no=1, request_fingerprint="a" * 64,
                             status="SUBMIT_INTENT", lease_owner="worker-a",
                             lease_expires_at=utcnow() + timedelta(seconds=120),
                             prompt_graph_sha256="b" * 64)
        db.add(attempt)
        db.flush()
        attempt_id, version = attempt.id, row.state_version
    with factory() as db:
        assert ResourceScheduler().admit(db, lease_id)
    return factory, settings, job["id"], lease_id, attempt_id, version


def launch_arguments(renderer):
    script, marker, port, root = renderer
    return {
        "argv": [sys.executable, str(script), str(marker)],
        "cwd": str(root),
        "env": dict(os.environ, TOKEN=TOKEN, BARRIER_HASH=DIGEST, PORT=str(port),
                    SHINE_COMFY_START_DRAINED="1"),
        "pinned_files": [sys.executable, str(script)],
        "port": port,
    }


def start(factory, settings, renderer, claimed, **overrides):
    arguments = launch_arguments(renderer)
    arguments.update(overrides)
    return start_managed_renderer(
        factory, settings,
        instance_id="shine-test-renderer",
        lease_id=claimed[3],
        job_id=claimed[2],
        worker_id="worker-a",
        argv=arguments["argv"],
        cwd=arguments["cwd"],
        env=arguments["env"],
        pinned_files=arguments["pinned_files"],
        port=arguments["port"],
        handoff_token=TOKEN,
        adapter_sha256=DIGEST,
        gpu_uuid="GPU-test",
        min_ram_mb=1024,
        min_vram_mb=1024,
        probe=overrides.get("probe", ready_memory),
        launch=overrides.get("launch"),
        supervisor_factory=overrides.get("supervisor_factory"),
    )


def test_startup_persists_intent_before_the_process_exists(claimed, renderer):
    """A failed launch leaves a durable intent and is never relaunched."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    with pytest.raises(RuntimeError, match="launch refused"):
        start(factory, settings, renderer, claimed, launch=_refusing_launch())
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        assert journal is not None
        assert journal.value["stage"] == "RECONCILE_REQUIRED"
        assert journal.value["last_confirmed_stage"] == "INTENT"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(ComfyInstance, "shine-test-renderer") is None
    with pytest.raises(HTTPException, match="COMFY_STARTUP_ALREADY_RECORDED"):
        start(factory, settings, renderer, claimed)


def test_startup_refuses_without_an_exclusive_lease(claimed, renderer):
    """No renderer may be created without one exclusive admission."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    with factory.begin() as db:
        db.get(ResourceLease, lease_id).status = "RELEASED"
    with pytest.raises(HTTPException, match="COMFY_STARTUP_RESOURCE_LEASE_INVALID"):
        start(factory, settings, renderer, claimed)
    with factory() as db:
        assert db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer") is None
        assert db.get(ComfyInstance, "shine-test-renderer") is None


def test_startup_refuses_a_second_active_lease(claimed, renderer):
    """A foreign GPU holder blocks the startup before any process is created."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    with factory.begin() as db:
        ResourceScheduler().enqueue(db, "vlm:other", 8000, True, 10)
        db.flush()
        other = db.scalar(
            __import__("sqlalchemy").select(ResourceLease).where(ResourceLease.owner == "vlm:other"))
        other.status = "ACTIVE"
    with pytest.raises(HTTPException, match="COMFY_STARTUP_RESOURCE_CONFLICT"):
        start(factory, settings, renderer, claimed)
    with factory() as db:
        assert db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer") is None


def test_startup_requires_a_closed_renderer(claimed, renderer, monkeypatch):
    """A renderer that accepts work immediately is refused."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    from shine.comfyops import renderer_supervisor

    def accepting(self, method, path, expected_state, expected_owner=None):
        if expected_state == "drained":
            raise ValueError("COMFY_LISTENER_OWNERSHIP_UNVERIFIED")
        return renderer_supervisor.RendererSupervisor._control(self, method, path, expected_state,
                                                               expected_owner)

    monkeypatch.setattr(renderer_supervisor.RendererSupervisor, "_control", accepting)
    with pytest.raises(ValueError, match="COMFY_LISTENER_OWNERSHIP_UNVERIFIED"):
        start(factory, settings, renderer, claimed)
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        assert journal.value["stage"] == "RECONCILE_REQUIRED"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(ComfyInstance, "shine-test-renderer") is None


def test_startup_requires_a_measured_physical_baseline(claimed, renderer):
    """An insufficient measurement blocks the renderer instead of assuming room."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed

    def insufficient(gpu_uuid, *, min_ram_mb, min_vram_mb):
        return {"ready": False, "available_ram_bytes": 0,
                "gpu": {"uuid": gpu_uuid, "total_mb": 16303, "free_mb": 0}}

    with pytest.raises(HTTPException, match="COMFY_STARTUP_MEMORY_INSUFFICIENT"):
        start(factory, settings, renderer, claimed, probe=insufficient)
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        assert journal.value["stage"] == "RECONCILE_REQUIRED"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"


def test_startup_never_persists_the_control_token(claimed, renderer):
    """The handoff token stays in memory; only its digest is stored."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    start(factory, settings, renderer, claimed)
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        assert journal.value["stage"] == "RUNNING"
        assert TOKEN not in json.dumps(journal.value)
        assert journal.value["handoff_token_sha256"] == hashlib.sha256(TOKEN.encode()).hexdigest()
        instance = db.get(ComfyInstance, "shine-test-renderer")
        assert instance.payload["managed"] is True
        assert instance.payload["is_mock"] is False
        assert instance.payload["process"]["pid"] > 0
        assert instance.payload["process"]["birth"].isdigit()
        assert instance.status == "RUNNING_PENDING_HEALTH"


def test_recovery_stops_only_a_provably_owned_process(claimed, renderer):
    """A live process outside the owned group is left untouched and blocked."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    managed = start(factory, settings, renderer, claimed)
    with factory.begin() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        journal.value = {**journal.value, "stage": "LAUNCHED", "owner_pid": 999999, "owner_birth": "1",
                         "process": {"pid": managed.group.pid, "birth": _birth(managed.group.pid),
                                     "job_name": "Local\\ShineHandoff-" + "b" * 32}}
    findings = reconcile_managed_startups(factory, settings)
    assert findings and findings[0]["action"] == "blocked"
    assert findings[0]["reason"] == "OWNERSHIP_UNVERIFIED"
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        assert journal.value["stage"] == "RECONCILE_REQUIRED"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
    assert managed.group.owns(managed.group.pid, _birth(managed.group.pid))


def test_recovery_never_touches_a_startup_owned_by_a_live_worker(claimed, renderer):
    """A startup whose owner is still alive is left to its owner."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    managed = start(factory, settings, renderer, claimed)
    with factory.begin() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        journal.value = {**journal.value, "stage": "LAUNCHED"}
    findings = reconcile_managed_startups(factory, settings)
    assert findings == []
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        assert journal.value["stage"] == "LAUNCHED"
        assert db.get(ResourceLease, lease_id).status == "ACTIVE"
    assert managed.group.owns(managed.group.pid, _birth(managed.group.pid))


def test_recovery_reports_an_exited_process_without_touching_it(claimed, renderer):
    """A startup whose process is gone is reconciled without any termination."""
    factory, settings, job_id, lease_id, attempt_id, version = claimed
    with pytest.raises(RuntimeError, match="launch refused"):
        start(factory, settings, renderer, claimed, launch=_refusing_launch())
    with factory.begin() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + "shine-test-renderer")
        journal.value = {**journal.value, "stage": "LAUNCHED", "owner_pid": 999999, "owner_birth": "1",
                         "process": {"pid": 999999, "birth": "1", "job_name": "Local\\ShineHandoff-" + "a" * 32}}
    findings = reconcile_managed_startups(factory, settings)
    assert findings[0]["action"] == "already_exited"


def test_first_activation_keeps_the_lease_on_an_uncertain_resume(claimed, renderer, monkeypatch):
    """A resume whose outcome is unknown never becomes a silent retry."""
    from shine.comfyops.handoff_activation import activate_renderer_for_first_attempt
    from shine.comfyops.handoff_startup import StartupBoundary

    factory, settings, job_id, lease_id, attempt_id, version = claimed
    managed = start(factory, settings, renderer, claimed)

    def uncertain(self, method, path, expected_state, expected_owner=None):
        if path == "/shine-handoff/resume":
            raise ValueError("COMFY_DRAIN_UNCONFIRMED")
        return self._control_original(method, path, expected_state, expected_owner)

    from shine.comfyops import renderer_supervisor

    monkeypatch.setattr(renderer_supervisor.RendererSupervisor, "_control_original",
                        renderer_supervisor.RendererSupervisor._control, raising=False)
    monkeypatch.setattr(renderer_supervisor.RendererSupervisor, "_control", uncertain)
    with pytest.raises(ValueError, match="COMFY_DRAIN_UNCONFIRMED"):
        activate_renderer_for_first_attempt(
            factory, settings, managed.supervisor, job_id, attempt_id, lease_id,
            managed.instance_id, "worker-a", version, startup_boundary=StartupBoundary(managed))
    with factory() as db:
        activation = db.get(AppSetting, "renderer-activation:" + attempt_id)
        assert activation.value["stage"] == "RECONCILE_REQUIRED"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(Job, job_id).status == "NEEDS_REVIEW"
        assert db.get(Job, job_id).failure_code == "COMFY_ACTIVATION_RECONCILIATION_REQUIRED"
    with pytest.raises(HTTPException, match="COMFY_ACTIVATION_ALREADY_RECORDED"):
        activate_renderer_for_first_attempt(
            factory, settings, managed.supervisor, job_id, attempt_id, lease_id,
            managed.instance_id, "worker-a", version, startup_boundary=StartupBoundary(managed))


REVIEW_SERVER = r'''
import json, os, pathlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
RUNTIME = 'c' * 64
FENCE_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
STAGE = {'pid': 424242, 'birth': '1', 'joined': True, 'exitcode': 0,
         'group_active_processes': 0, 'group_members_waited': 0}
class Handler(BaseHTTPRequestHandler):
    fenced = False
    def log_message(self, *args): pass
    def respond(self, code, body):
        raw = json.dumps(body).encode(); self.send_response(code); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        if (self.path != '/handoff/cold-fence' or
                self.headers.get('X-Shine-Handoff-Token') != os.environ['TOKEN']):
            return self.respond(401, {})
        if os.environ.get('FENCE_STATUS') == '409':
            return self.respond(409, {'error': 'ISOLATED_REVIEW_NOT_COLD'})
        Handler.fenced = True
        return self.respond(200, {'fenced': True, 'fence_id': FENCE_ID, 'runtime_sha256': RUNTIME,
                                  'initial_stage': STAGE})
    def do_GET(self):
        if self.path == '/handoff/cold-status':
            if not Handler.fenced:
                return self.respond(409, {'error': 'ISOLATED_REVIEW_NOT_COLD'})
            return self.respond(200, {'fenced': True, 'fence_id': FENCE_ID, 'runtime_sha256': RUNTIME,
                                      'initial_stage': STAGE})
        if self.path == '/health':
            return self.respond(200, {'status': 'ok', 'is_mock': False, 'loaded': False,
                                      'asr_loaded': False, 'allocated_bytes': 0, 'reserved_bytes': 0,
                                      'runtime_sha256': RUNTIME, 'handoff_sealed': Handler.fenced,
                                      'last_stage': STAGE})
        return self.respond(404, {})
server = HTTPServer(('127.0.0.1',int(os.environ['PORT'])),Handler)
# The bound port is reported to the fixture, so a port that was taken between
# reservation and bind is detected instead of silently pointing at a different
# server that happens to own the port.
pathlib.Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
'''

FENCE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


@pytest.fixture
def review_service(tmp_path, request):
    """An owned review supervisor that behaves like the isolated service."""
    from shine.comfyops.owned_process import OwnedProcessGroup

    script, marker = tmp_path / "review_server.py", tmp_path / "port.txt"
    script.write_text(REVIEW_SERVER, encoding="utf-8")
    with socket.socket() as reserving:
        reserving.bind(("127.0.0.1", 0))
        port = reserving.getsockname()[1]
    env = dict(os.environ, TOKEN=TOKEN, PORT=str(port))
    if getattr(request, "param", None) == "not_pristine":
        env["FENCE_STATUS"] = "409"
    from uuid import uuid4

    group = OwnedProcessGroup.launch([sys.executable, str(script), str(marker)], cwd=tmp_path, env=env,
                                     pinned_files=[sys.executable, script],
                                     job_name="Local\\ShineHandoff-" + uuid4().hex)
    deadline = time.monotonic() + 20
    listener = None
    while time.monotonic() < deadline:
        if marker.is_file():
            listeners = windows_port_listeners(port)
            if listeners:
                listener = listeners[0][1]
                break
        time.sleep(0.05)
    assert listener, "the review supervisor never bound its port"
    yield group, port, listener
    group.close()


def test_first_close_fence_binds_the_first_reservation(review_service, monkeypatch):
    """The fence is only created against an owned, still pristine supervisor."""
    from shine.quality import review_release

    group, port, listener = review_service
    monkeypatch.setattr(review_release, "probe_memory", lambda uuid, **kwargs: {
        "ready": True, "available_ram_bytes": 8 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16303, "free_mb": 14000}})
    monkeypatch.setattr(review_release, "evaluate_readiness", lambda **kwargs: {
        "ready": True, "available_ram_bytes": 8 * 1024**3, "gpu": kwargs.get("gpu"),
        "min_ram_mb": kwargs.get("min_ram_mb"), "min_vram_mb": kwargs.get("min_vram_mb")})
    proof = review_release.create_cold_review_fence(
        base_url=f"http://127.0.0.1:{port}", runtime_sha256="c" * 64,
        supervisor={"pid": listener, "birth": _birth(listener)}, gpu_uuid="GPU-test",
        min_ram_mb=1024, min_vram_mb=1024, handoff_token=TOKEN, owner_group=group)
    assert proof["fence_id"] == FENCE_ID
    assert proof["memory"]["ready"] is True
    assert proof["initial_stage"]["exitcode"] == 0
    assert "stage_id" not in proof["initial_stage"]


@pytest.mark.parametrize("review_service", ["not_pristine"], indirect=True)
def test_first_close_fence_refuses_a_service_that_is_not_pristine(review_service, monkeypatch):
    """A service that refuses to fence yields no fence and no admission."""
    from shine.quality import review_release

    group, port, listener = review_service
    with pytest.raises(ValueError, match="REVIEW_COLD_FENCE_UNCONFIRMED"):
        review_release.create_cold_review_fence(
            base_url=f"http://127.0.0.1:{port}", runtime_sha256="c" * 64,
            supervisor={"pid": listener, "birth": _birth(listener)}, gpu_uuid="GPU-test",
            min_ram_mb=1024, min_vram_mb=1024, handoff_token=TOKEN, owner_group=group)


def test_first_close_fence_refuses_an_unowned_supervisor(review_service):
    """A supervisor that is not in an owned group can never be fenced."""
    from shine.quality import review_release

    group, port, listener = review_service
    with pytest.raises(ValueError, match="REVIEW_SUPERVISOR_OWNERSHIP_UNVERIFIED"):
        review_release.create_cold_review_fence(
            base_url=f"http://127.0.0.1:{port}", runtime_sha256="c" * 64,
            supervisor={"pid": listener, "birth": _birth(listener)}, gpu_uuid="GPU-test",
            min_ram_mb=1024, min_vram_mb=1024, handoff_token=TOKEN, owner_group=None)


def _birth(pid):
    from shine.comfyops.installer import process_identity

    return process_identity(pid)


def _refusing_launch():
    def launch(*args, **kwargs):
        raise RuntimeError("launch refused")

    return launch
