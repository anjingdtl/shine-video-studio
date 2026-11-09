"""CPU-only tests of ownership, queue drain and history preservation on Windows."""

import hashlib
import json
import os
import socket
import sys
import time

import pytest

from tests.test_comfy_handoff_journal import handoff  # noqa: F401

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
marker = pathlib.Path(sys.argv[1])
temporary = marker.with_suffix('.tmp')
temporary.write_text(str(server.server_port))
temporary.replace(marker)
server.serve_forever()
'''


@pytest.fixture
def server(tmp_path, request):
    from shine.comfyops.owned_process import OwnedProcessGroup

    script, marker = tmp_path / "server.py", tmp_path / "port.txt"
    script.write_text(SERVER, encoding="utf-8")
    token, digest = "t" * 40, "a" * 64
    with socket.socket() as reserving:
        reserving.bind(("127.0.0.1", 0))
        port = reserving.getsockname()[1]
    env = dict(os.environ, TOKEN=token, BARRIER_HASH=digest, PORT=str(port))
    if getattr(request, "param", False):
        env["SHINE_COMFY_START_DRAINED"] = "1"
    from uuid import uuid4

    job_name = "Local\\ShineHandoff-" + uuid4().hex if getattr(request, "param", None) == "named" else None
    group = OwnedProcessGroup.launch([sys.executable, str(script), str(marker)], cwd=tmp_path, env=env,
                                     pinned_files=[sys.executable, script], job_name=job_name)
    try:
        deadline = time.monotonic() + 10
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.is_file()
        if getattr(request, "param", False):
            import httpx

            response = httpx.post(f"http://127.0.0.1:{port}/shine-handoff/resume",
                                  headers={"Authorization": "Bearer " + token}, trust_env=False)
            assert response.status_code == 200
        yield group, f"http://127.0.0.1:{int(marker.read_text())}", token, digest, tmp_path
    finally:
        group.close()


def test_owned_drain_archives_history_before_stopping(server):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    evidence = supervisor.drain_and_archive(root / "history", "a" * 36)
    raw = evidence["history_path"].read_bytes()
    assert json.loads(raw) == {"historic-prompt": {"status": "complete"}}
    assert hashlib.sha256(raw).hexdigest() == evidence["history_sha256"]
    stopped = supervisor.stop_after_drain()
    assert stopped["active_processes"] == 0
    assert evidence["history_path"].read_bytes() == raw


@pytest.mark.parametrize("server", ["named"], indirect=True)
def test_named_renderer_binding_rejects_another_group_name(server):
    from shine.comfyops.installer import process_identity
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    process = {"pid": group.pid, "birth": process_identity(group.pid), "job_name": "Local\\ShineHandoff-" + "a" * 32}
    with pytest.raises(ValueError, match="OWNERSHIP_UNVERIFIED"):
        supervisor.verify_binding(url, process)
    process["job_name"] = group.job_name
    assert supervisor.verify_binding(url, process)


def test_restart_refuses_recipe_without_closed_start(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor.drain_and_archive(root / "history", "a" * 36)
    supervisor.stop_after_drain()
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", lambda **_kw: {"review_stage_id": "x"})
    monkeypatch.setattr(supervisor, "check_memory", lambda *args, **kwargs: {"ready": True})
    with pytest.raises(ValueError, match="COMFY_CLOSED_START_REQUIRED"):
        supervisor.restore_after_review({"gpu_uuid": "GPU-test"}, min_ram_mb=1, min_vram_mb=1)
    assert supervisor._replacement is None


@pytest.mark.parametrize("server", [True], indirect=True)
def test_renderer_activation_refuses_missing_physical_release(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops.handoff_activation import activate_renderer_for_attempt
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import Job, JobAttempt

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor._control("POST", "/shine-handoff/drain", "drained")
    with factory.begin() as db:
        db.get(Job, job_id).status = "RUNNING"
        attempt = db.get(JobAttempt, attempt_id)
        attempt.provider_job_id, attempt.status = None, "SUBMIT_INTENT"
    with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
        activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                      "worker-a", version)
    assert supervisor._control("GET", "/shine-handoff/status", "drained")


@pytest.mark.parametrize("server", [True], indirect=True)
def test_resume_is_bound_to_live_lease_and_durable_intent(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops.handoff_activation import ReviewReleaseBoundary, activate_renderer_for_attempt
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AppSetting, Job, JobAttempt

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor._control("POST", "/shine-handoff/drain", "drained")
    boundary = object.__new__(ReviewReleaseBoundary)
    monkeypatch.setattr(ReviewReleaseBoundary, "verify", lambda *args: {
        "reviews": ["synthetic"], "observed_resource_epoch": {"epoch": 0, "kind": None, "lease_id": None},
    })
    with factory.begin() as db:
        db.get(Job, job_id).status = "RUNNING"
        db.get(JobAttempt, attempt_id).provider_job_id = None
        db.get(JobAttempt, attempt_id).status = "SUBMIT_INTENT"
    original = supervisor._control

    def uncertain(method, path, expected, *args):
        if path == "/shine-handoff/resume":
            with factory() as db:
                assert db.get(AppSetting, "renderer-activation:" + attempt_id).value["stage"] == "INTENT"
            original(method, path, expected, *args)
            raise TimeoutError("lost acknowledgement")
        return original(method, path, expected, *args)

    monkeypatch.setattr(supervisor, "_control", uncertain)
    with pytest.raises(TimeoutError, match="lost acknowledgement"):
        activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                      "worker-a", version, physical_boundary=boundary)
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "renderer-activation:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"
    with pytest.raises(HTTPException):
        activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                      "worker-a", version, physical_boundary=boundary)


@pytest.mark.parametrize("server", [True], indirect=True)
def test_activity_after_physical_check_blocks_resume(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException
    from sqlalchemy import text

    from shine.comfyops.handoff_activation import ReviewReleaseBoundary, activate_renderer_for_attempt
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.comfyops.resource_epoch import advance_epoch, read_epoch
    from shine.models import AppSetting, Job, JobAttempt

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor._control("POST", "/shine-handoff/drain", "drained")
    boundary = object.__new__(ReviewReleaseBoundary)

    def changed_after_check(*args):
        with factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            observed = read_epoch(db)
            advance_epoch(db, lease_id, "model")
        return {"reviews": ["synthetic"], "observed_resource_epoch": observed}

    monkeypatch.setattr(ReviewReleaseBoundary, "verify", changed_after_check)
    with factory.begin() as db:
        db.get(Job, job_id).status = "RUNNING"
        db.get(JobAttempt, attempt_id).provider_job_id = None
        db.get(JobAttempt, attempt_id).status = "SUBMIT_INTENT"
    with pytest.raises(HTTPException):
        activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                      "worker-a", version, physical_boundary=boundary)
    assert supervisor._control("GET", "/shine-handoff/status", "drained")
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "renderer-activation:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"


@pytest.mark.parametrize("server", [True], indirect=True)
def test_confirmed_resume_is_not_repeated(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops.handoff_activation import ReviewReleaseBoundary, activate_renderer_for_attempt
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AppSetting, Job, JobAttempt

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor._control("POST", "/shine-handoff/drain", "drained")
    boundary = object.__new__(ReviewReleaseBoundary)
    monkeypatch.setattr(ReviewReleaseBoundary, "verify", lambda *args: {
        "reviews": ["synthetic"], "observed_resource_epoch": {"epoch": 0, "kind": None, "lease_id": None},
    })
    with factory.begin() as db:
        db.get(Job, job_id).status = "RUNNING"
        db.get(JobAttempt, attempt_id).provider_job_id = None
        db.get(JobAttempt, attempt_id).status = "SUBMIT_INTENT"
    owner = activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                          "worker-a", version, physical_boundary=boundary)
    with factory() as db:
        record = db.get(AppSetting, "renderer-activation:" + attempt_id).value
        assert record["stage"] == "CONFIRMED" and record["listener"] == owner
        assert "token" not in str(record).lower()
    with pytest.raises(HTTPException, match="COMFY_ACTIVATION_ALREADY_RECORDED"):
        activate_renderer_for_attempt(factory, settings, supervisor, job_id, attempt_id, lease_id, instance_id,
                                      "worker-a", version, physical_boundary=boundary)


def test_unowned_pid_response_never_stops_the_group(server, monkeypatch):
    from shine.comfyops.installer import process_identity
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.process_identity", lambda _pid: "wrong-birth")
    with pytest.raises(ValueError, match="OWNERSHIP_UNVERIFIED"):
        supervisor.drain_and_archive(root / "history", "b" * 36)
    assert not (root / "history").exists()
    assert group.owns(group.pid, process_identity(group.pid))


def test_changed_history_prevents_stopping(server):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    evidence = supervisor.drain_and_archive(root / "history", "c" * 36)
    evidence["history_path"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="HISTORY_CHANGED"):
        supervisor.stop_after_drain()


def test_listener_mismatch_refuses_drain_before_any_side_effect(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.windows_loopback_listener_pid", lambda _port: os.getpid())
    supervisor = RendererSupervisor(group, url, token, digest)
    with pytest.raises(ValueError, match="LISTENER"):
        supervisor.drain_and_archive(root / "history", "d" * 36)
    assert not (root / "history").exists()
    import httpx

    result = httpx.get(url + "/shine-handoff/status", headers={"Authorization": "Bearer " + token}).json()
    assert result["status"] == "accepting"


def test_control_never_uses_environment_proxy(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    supervisor = RendererSupervisor(group, url, token, digest)
    evidence = supervisor.drain_and_archive(root / "history", "e" * 36)
    assert evidence["history_path"].is_file()
    assert supervisor.stop_after_drain()["active_processes"] == 0


def test_memory_admission_requires_confirmed_process_exit(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    probes = []

    def probe(uuid, *, min_ram_mb, min_vram_mb):
        probes.append(uuid)
        return {"ready": False, "available_ram_bytes": 100, "gpu": {"uuid": uuid, "free_mb": 1},
                "min_ram_mb": min_ram_mb, "min_vram_mb": min_vram_mb}

    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", probe)
    with pytest.raises(ValueError, match="PROCESS_EXIT_UNCONFIRMED"):
        supervisor.check_memory("GPU-test", min_ram_mb=8000, min_vram_mb=14000)
    assert not probes
    supervisor.drain_and_archive(root / "history", "f" * 36)
    supervisor.stop_after_drain()
    with pytest.raises(ValueError, match="MEMORY_INSUFFICIENT"):
        supervisor.check_memory("GPU-test", min_ram_mb=8000, min_vram_mb=14000, timeout=1)
    # The admission waits for the stopped process to release its memory, so the
    # baseline is measured again instead of on the first sample.
    assert probes and set(probes) == {"GPU-test"}
    assert len(probes) > 1


def test_new_listener_after_stop_blocks_memory_admission(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor.drain_and_archive(root / "history", "f" * 36)
    supervisor.stop_after_drain()
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.windows_port_listeners", lambda _port: [("127.0.0.1", 123)])
    with pytest.raises(ValueError, match="LISTENER"):
        supervisor.check_memory("GPU-test", min_ram_mb=8000, min_vram_mb=14000)


@pytest.mark.parametrize("server", [True, "named"], indirect=True)
def test_owned_renderer_restart_requires_review_release_and_keeps_fixed_identity(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor.drain_and_archive(root / "history", "1" * 36)
    supervisor.stop_after_drain()
    checks = []
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", lambda **kw: checks.append(kw) or {
        "review_stage_id": "synthetic-stage", "memory": {"ready": True},
    })
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    new_supervisor = None
    try:
        new_supervisor, evidence = supervisor.restore_after_review(
            {"receipt": {"stage_id": "synthetic-stage"}, "gpu_uuid": "GPU-test"},
            min_ram_mb=8000, min_vram_mb=14000, timeout=15,
        )
        assert len(checks) == 1 and evidence["review_stage_id"] == "synthetic-stage"
        assert evidence["process"]["pid"] != group.pid
        if group.job_name:
            from shine.comfyops.owned_process import inspect_named_group

            process = evidence["process"]
            assert process["job_name"] != group.job_name
            assert inspect_named_group(process["job_name"], process["pid"], process["birth"])
        assert new_supervisor._control("GET", "/shine-handoff/status", "drained")
        with pytest.raises(ValueError, match="RESTART_ALREADY_ATTEMPTED"):
            supervisor.restore_after_review({}, min_ram_mb=8000, min_vram_mb=14000)
    finally:
        if new_supervisor:
            new_supervisor._group.terminate()
            new_supervisor._group.close()


@pytest.mark.parametrize("server", [True], indirect=True)
def test_restart_with_changed_source_never_spawns_or_blindly_retries(server, monkeypatch):
    from shine.comfyops.renderer_supervisor import RendererSupervisor

    group, url, token, digest, root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    supervisor.drain_and_archive(root / "history", "2" * 36)
    supervisor.stop_after_drain()
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", lambda **_kw: {
        "review_stage_id": "synthetic-stage", "memory": {"ready": True},
    })
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    with (root / "server.py").open("a", encoding="utf-8") as file:
        file.write("\n# changed after render\n")
    request = {"receipt": {"stage_id": "synthetic-stage"}, "gpu_uuid": "GPU-test"}
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        supervisor.restore_after_review(request, min_ram_mb=8000, min_vram_mb=14000)
    assert supervisor._replacement is None
    with pytest.raises(ValueError, match="RESTART_ALREADY_ATTEMPTED"):
        supervisor.restore_after_review(request, min_ram_mb=8000, min_vram_mb=14000)


def bind_handoff_output(api, handoff_state, server):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.installer import process_identity
    from shine.comfyops.models import ComfyInstance
    from shine.models import Job, JobAttempt, MediaArtifact

    _, _, settings = api
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff_state
    group, url, token, digest, root = server
    path = settings.data_dir / "test" / "output.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-video")
    file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with factory.begin() as db:
        instance = db.get(ComfyInstance, instance_id)
        instance.payload = {**instance.payload, "url": url,
                            "process": {"pid": group.pid, "birth": process_identity(group.pid)}}
        instance.sha256 = graph_hash(instance.payload)
        attempt = db.get(JobAttempt, attempt_id)
        attempt.output_sha256, attempt.output_bytes = file_digest, path.stat().st_size
        artifact = db.get(MediaArtifact, db.get(Job, job_id).artifact_id)
        artifact.sha256, artifact.byte_size = file_digest, path.stat().st_size
    return settings, path


def test_handoff_refuses_unfenced_restart_before_stopping_renderer(api, handoff, server):  # noqa: F811
    from shine.comfyops.handoff_preparation import prepare_review_handoff
    from shine.comfyops.installer import process_identity
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AppSetting

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    with pytest.raises(ValueError, match="COMFY_CLOSED_START_REQUIRED"):
        with prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, instance_id,
                                    "worker-a", version, supervisor, settings.data_dir / "history", "GPU-test",
                                    min_ram_mb=1, min_vram_mb=1):
            raise AssertionError("handoff must not begin")
    assert group.owns(group.pid, process_identity(group.pid))
    with factory() as db:
        assert db.get(AppSetting, "handoff:" + attempt_id) is None


@pytest.mark.parametrize("server", [True], indirect=True)
def test_preparation_keeps_lease_through_owned_drain_and_review_boundary(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops.handoff_preparation import prepare_review_handoff
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.comfyops.scheduler import CURRENT_RESOURCE, ResourceScheduler
    from shine.models import AppSetting

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, root = server
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    supervisor = RendererSupervisor(group, url, token, digest)
    with pytest.raises(HTTPException, match="HANDOFF_RESTART_NOT_IMPLEMENTED"):
        with prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, instance_id,
                                    "worker-a", version, supervisor, settings.data_dir / "history", "GPU-test",
                                    min_ram_mb=8000, min_vram_mb=14000):
            assert CURRENT_RESOURCE.get() == lease_id
            with factory.begin() as db:
                assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "REVIEW_INTENT"
                other = ResourceScheduler().enqueue(db, "other-stage", 1)
                other_id = other.id
            with factory() as db:
                assert not ResourceScheduler().admit(db, other_id)
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        journal = db.get(AppSetting, "handoff:" + attempt_id).value
        assert journal["last_confirmed_stage"] == "REVIEW_INTENT"
        assert os.path.isfile(journal["evidence"]["DRAINED"]["history_path"])


@pytest.mark.parametrize("server", [True], indirect=True)
def test_preproduction_change_after_drain_blocks_stop(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops.handoff_preparation import prepare_review_handoff
    from shine.comfyops.installer import process_identity
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AppSetting, Job

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    original = supervisor.drain_and_archive

    def drain_then_change(*args):
        evidence = original(*args)
        with factory.begin() as db:
            db.get(Job, job_id).preproduction_manifest_hash = "f" * 64
        return evidence

    monkeypatch.setattr(supervisor, "drain_and_archive", drain_then_change)
    with pytest.raises(HTTPException, match="PREPRODUCTION_OUTDATED"):
        with prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, instance_id,
                                    "worker-a", version, supervisor, settings.data_dir / "history", "GPU-test",
                                    min_ram_mb=8000, min_vram_mb=14000):
            raise AssertionError("review must not start")
    assert group.owns(group.pid, process_identity(group.pid))
    with factory() as db:
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
        assert db.get(AppSetting, "handoff:" + attempt_id).value["last_confirmed_stage"] == "DRAIN_INTENT"
    assert len(list((settings.data_dir / "history").glob("history-*.json"))) == 1


@pytest.mark.parametrize("server", [True], indirect=True)
def test_preproduction_change_after_check_before_stop_intent_blocks_stop(api, handoff, server, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from shine.comfyops import handoff_preparation
    from shine.comfyops.installer import process_identity
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import Job

    settings, _path = bind_handoff_output(api, handoff, server)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    check = handoff_preparation._verify_preproduction
    calls = []

    def change_after_check(*args):
        check(*args)
        calls.append(1)
        if len(calls) == 2:
            with factory.begin() as db:
                db.get(Job, job_id).preproduction_manifest_hash = "f" * 64

    monkeypatch.setattr(handoff_preparation, "_verify_preproduction", change_after_check)
    with pytest.raises(HTTPException, match="PREPRODUCTION_OUTDATED"):
        with handoff_preparation.prepare_review_handoff(
            factory, settings, job_id, attempt_id, lease_id, instance_id, "worker-a", version,
            supervisor, settings.data_dir / "history", "GPU-test", min_ram_mb=8000, min_vram_mb=14000,
        ):
            raise AssertionError("review must not start")
    assert group.owns(group.pid, process_identity(group.pid))


def test_caller_cannot_omit_required_visual_review(handoff, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from fastapi import HTTPException

    from shine.comfyops.handoff_preparation import HandoffSession

    factory, job_id, attempt_id, lease_id, _instance_id, _version = handoff
    guard = SimpleNamespace(check=lambda: None)
    session = HandoffSession(factory, SimpleNamespace(offline_simulation=True), job_id, attempt_id, lease_id,
                             guard, None, "GPU-test", 0, 0)
    session.accepted = {"ASR": {"is_mock": True}}
    monkeypatch.setattr("shine.comfyops.handoff_preparation.record_handoff_stage", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException, match="HANDOFF_REQUIRED_REVIEW_MISSING"):
        session.finish(required_kinds={"ASR"})


def test_restart_rechecks_all_prior_review_supervisors(handoff, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from shine.comfyops.handoff_preparation import HandoffSession

    factory, job_id, attempt_id, lease_id, _instance_id, _version = handoff
    restarted = []
    supervisor = SimpleNamespace(restore_after_review=lambda *args, **kwargs: restarted.append(True))
    session = HandoffSession(factory, SimpleNamespace(offline_simulation=True), job_id, attempt_id, lease_id,
                             SimpleNamespace(check=lambda: None), supervisor, "GPU-test", 0, 0)
    session.accepted = {"ASR": {"is_mock": True}, "VLM": {"is_mock": True}}
    session.review_requests = {"ASR": {"base_url": "old"}, "VLM": {"base_url": "new"}}
    from shine.comfyops.resource_epoch import read_epoch
    with factory() as db:
        session.last_verified_epoch = read_epoch(db)
    session.last_request = {"base_url": "new"}
    monkeypatch.setattr("shine.comfyops.handoff_preparation.record_handoff_stage", lambda *args, **kwargs: None)
    def failed_release(**args):
        if args["base_url"] == "old":
            raise ValueError("EARLIER_REVIEW_OWNER_CHANGED")
        return {}
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", failed_release)
    with pytest.raises(ValueError, match="EARLIER_REVIEW_OWNER_CHANGED"):
        session.finish(required_kinds={"ASR", "VLM"})
    assert not restarted


def test_next_review_rechecks_prior_release_before_reservation(handoff, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from shine.comfyops.handoff_preparation import HandoffSession

    factory, job_id, attempt_id, lease_id, _instance_id, _version = handoff
    session = HandoffSession(factory, SimpleNamespace(offline_simulation=True), job_id, attempt_id, lease_id,
                             SimpleNamespace(check=lambda: None), None, "GPU-test", 0, 0)
    session.accepted = {"ASR": {"is_mock": True}}
    session.review_requests = {"ASR": {"base_url": "old"}}
    from shine.comfyops.resource_epoch import read_epoch
    with factory() as db:
        session.last_verified_epoch = read_epoch(db)
    reserved = []
    monkeypatch.setattr("shine.comfyops.handoff_preparation.reserve_handoff_review",
                        lambda *args, **kwargs: reserved.append(True))
    def failed_release(**_args):
        raise ValueError("EARLIER_REVIEW_OWNER_CHANGED")
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", failed_release)
    with pytest.raises(ValueError, match="EARLIER_REVIEW_OWNER_CHANGED"):
        with session.review_stage("VLM"):
            pass
    assert not reserved


@pytest.mark.parametrize("server", [True], indirect=True)
@pytest.mark.parametrize("with_intervening_event", [False, True, "managed_worker", "managed_slow_prepare",
                                                     "managed_wrong_url", "managed_uncertain", "managed_fake_not_mock",
                                                     "managed_expire_before_submit", "managed_graph_changed",
                                     "managed_missing_required", "managed_wrong_data_dir",
                                     "managed_mock_real",
                                                     "reserve", "transfer", "transfer_rejected",
                                                     "idle_lost", "idle_expired", "idle_heartbeat", "idle_closed",
                                                     "cold_fence_boundary", "earlier_release_boundary"])
def test_synthetic_review_release_restores_owned_renderer_without_releasing_lease(
    api, handoff, server, monkeypatch, with_intervening_event  # noqa: F811
):
    from dataclasses import replace
    from types import SimpleNamespace

    from shine.comfyops.handoff_preparation import HandoffReviewProvider, prepare_review_handoff
    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.models import ComfyInstance, ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AgentTaskRecord, AppSetting, Job, Project
    from tests.test_review_isolation_receipt import response

    settings, _path = bind_handoff_output(api, handoff, server)
    settings = replace(settings, offline_simulation=True)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    receipt = response()["isolation_receipt"]
    review_url = "http://127.0.0.1:8116/v1"
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", lambda **kw: {
        "review_stage_id": kw["receipt"]["stage_id"], "memory": {"ready": True},
    })
    monkeypatch.setattr("shine.quality.review_release.verify_review_ready", lambda **_kw: {"ready": True})
    monkeypatch.setattr("shine.quality.review_release.reserve_review_service", lambda **_kw: None)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    replacement = None
    try:
        with prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, instance_id,
                                    "worker-a", version, supervisor, settings.data_dir / "history", "GPU-test",
                                    min_ram_mb=8000, min_vram_mb=14000) as session:
            with factory.begin() as db:
                job = db.get(Job, job_id)
                project_id = job.project_id
                db.get(Project, job.project_id).quality_policy = {
                    "require_semantic_review": False, "require_style_review": False,
                }
                task = AgentTaskRecord(project_id=job.project_id, role_id="model_call:ASR:transcribe",
                                       input_hash="a" * 64, asset_manifest_hash="b" * 64, rulepack_id="test",
                                       rulepack_hash="c" * 64, model_profile_id="test", model_id="test",
                                       status="COMPLETED", trace={
                                           "kind": "ASR", "resource_id": lease_id, "is_mock": True,
                                           "production_review": {"job_id": job_id},
                                           "review_provider": {"base_url_sha256": hashlib.sha256(
                                               review_url.encode()).hexdigest()}, "isolation_receipt": receipt,
                                       })
                db.add(task)
                db.flush()
                task_id = task.id
            request = {
                "task_id": task_id, "base_url": review_url, "receipt": receipt, "runtime_sha256": "b" * 64,
                "supervisor": {"pid": 777, "birth": "789"}, "gpu_uuid": "GPU-test",
                "handoff_token": "x" * 40,
            }
            from fastapi import HTTPException

            with pytest.raises(HTTPException, match="HANDOFF_REVIEW_INTENT_MISSING"):
                session.accept_review(request)
            from shine.comfyops.handoff_journal import bind_handoff_review_task

            class Provider:
                is_mock = True
                config = SimpleNamespace(profile_id="p", kind="local", model="test", base_url=review_url)
                last_isolation_receipt = receipt

                def transcribe(self, _value):
                    return {"is_mock": True}

                def generate(self, _value):
                    raise AssertionError("unexpected model method executed")

            monkeypatch.setattr("shine.comfyops.native.production_review_binding", lambda *args, **kwargs: None)
            tracked = TrackedProvider(Provider(), factory, project_id, reviewer_kind="ASR", production_job_id=job_id)
            wrapped = HandoffReviewProvider(session, tracked, "ASR", {
                key: value for key, value in request.items() if key not in {"task_id", "receipt"}
            })
            with pytest.raises(HTTPException, match="HANDOFF_REVIEW_METHOD_INVALID"):
                wrapped.generate("bypass")
            wrapped.transcribe("synthetic")
            with factory() as db:
                step = db.get(AppSetting, "handoff:" + attempt_id).value["review_steps"][-1]
                assert db.get(AgentTaskRecord, step["task_id"]).status == "COMPLETED"
                assert step["status"] == "VERIFIED"
            with pytest.raises(HTTPException, match="HANDOFF_REVIEW_TASK_BINDING_INVALID"):
                bind_handoff_review_task(factory, attempt_id, step["id"], task_id, settings=settings)
            replacement, evidence = session.finish(required_kinds={"ASR"})
            assert evidence["review_stage_id"] == receipt["stage_id"]
        with factory() as db:
            assert db.get(ResourceLease, lease_id).status == "ACTIVE"
            journal = db.get(AppSetting, "handoff:" + attempt_id).value
            assert journal["stage"] == "RESTARTED"
            assert journal["review_steps"][0]["status"] == "VERIFIED"
            assert db.get(ComfyInstance, instance_id).payload["process"] == evidence["process"]
            assert journal["evidence"]["REVIEW_FINISHED"]["is_mock"] is True
        from uuid import uuid4

        from shine.comfyops.handoff_activation import ReviewReleaseBoundary

        boundary = ReviewReleaseBoundary(session, min_ram_mb=8000, min_vram_mb=14000)
        next_attempt = str(uuid4())
        release = boundary.verify(factory, settings, instance_id, next_attempt, lease_id)
        assert release["reviews"][0]["review_stage_id"] == receipt["stage_id"]
        if with_intervening_event in {"cold_fence_boundary", "earlier_release_boundary"}:
            extra_config = {"gpu_uuid": "GPU-test", "base_url": "http://127.0.0.1:8117/v1"}
            if with_intervening_event == "cold_fence_boundary":
                calls = []
                monkeypatch.setattr("shine.quality.review_release.verify_cold_review_fence",
                                    lambda **kw: calls.append(kw) or {"memory": {"ready": True}})
                extended = ReviewReleaseBoundary(session, min_ram_mb=8000, min_vram_mb=14000,
                    cold_fences={"VLM": "fence-id"}, review_configs={"VLM": extra_config})
                assert len(extended.verify(factory, settings, instance_id, next_attempt, lease_id)["reviews"]) == 2
                assert calls[0]["fence_id"] == "fence-id"
                monkeypatch.setattr("shine.quality.review_release.verify_cold_review_fence",
                                    lambda **_kw: (_ for _ in ()).throw(ValueError("COLD_FENCE_LOST")))
                with pytest.raises(ValueError, match="COLD_FENCE_LOST"):
                    extended.verify(factory, settings, instance_id, next_attempt, lease_id)
            else:
                old = {**extra_config, "receipt": receipt}
                extended = ReviewReleaseBoundary(session, min_ram_mb=8000, min_vram_mb=14000,
                    review_configs={"VLM": extra_config}, prior_releases={"VLM": old})
                assert len(extended.verify(factory, settings, instance_id, next_attempt, lease_id)["reviews"]) == 2
                changed = ReviewReleaseBoundary(session, min_ram_mb=8000, min_vram_mb=14000,
                    review_configs={"VLM": {**extra_config, "gpu_uuid": "different"}}, prior_releases={"VLM": old})
                with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
                    changed.verify(factory, settings, instance_id, next_attempt, lease_id)
            return
        if with_intervening_event in {"managed_worker", "managed_slow_prepare", "managed_wrong_url",
                                      "managed_uncertain", "managed_fake_not_mock", "managed_expire_before_submit",
                                      "managed_graph_changed", "managed_missing_required", "managed_wrong_data_dir",
                                      "managed_mock_real"}:
            from copy import deepcopy
            from dataclasses import replace
            from uuid import uuid4

            from shine.comfy.provider import ComfyResult, ComfySubmission, PreparedSubmission
            from shine.comfy.workflow import graph_hash
            from shine.comfyops.handoff_idle import IdleHandoffKeeper
            from shine.comfyops.handoff_preparation import ManagedReviewCoordinator
            from shine.quality.semantic import Transcript
            from shine.worker import run_managed_once
            from tests.conftest import create_comfy_job

            with factory() as db:
                old = db.get(Job, job_id)
                project, shot = old.project_id, old.shot_id
            # The previous attempt is finished before the next one starts.
            with factory.begin() as db:
                old = db.get(Job, job_id)
                old.status, old.lease_owner, old.lease_expires_at = "NEEDS_REVIEW", None, None
            new_job_id = create_comfy_job(api[0], project, shot)["id"]
            new_receipt = deepcopy(receipt)
            new_receipt["stage_id"] = str(uuid4())

            class NewProvider:
                is_mock = True
                config = SimpleNamespace(profile_id="p", kind="local", model="test", base_url=review_url)
                last_isolation_receipt = new_receipt

                def transcribe(self, _path):
                    return Transcript(text="", is_mock=True, model="test")

            class SyntheticService:
                is_mock = with_intervening_event != "managed_fake_not_mock"
                client = SimpleNamespace(base_url="http://127.0.0.1:1" if with_intervening_event == "managed_wrong_url"
                                         else url)
                submissions = 0

                def prepare(self, request, profile, assets, **_kwargs):
                    assert replacement._control("GET", "/shine-handoff/status", "drained")
                    if with_intervening_event == "managed_slow_prepare":
                        time.sleep(3)
                    self.prepared = PreparedSubmission(prompt={}, prompt_graph_sha256=graph_hash({}),
                                                       workflow_hash="e" * 64, uploaded_references=0)
                    return self.prepared

                def submit_prepared(self, prepared, request):
                    assert replacement._control("GET", "/shine-handoff/status", "accepting")
                    with factory() as db:
                        attempts = db.query(JobAttempt).filter_by(job_id=new_job_id).all()
                        assert len(attempts) == 1 and attempts[0].prompt_graph_sha256 == prepared.prompt_graph_sha256
                        assert db.get(AppSetting, "renderer-activation:" + attempts[0].id).value["stage"] == "CONFIRMED"
                    self.submissions += 1
                    if with_intervening_event == "managed_uncertain":
                        from shine.comfy.client import ComfySubmissionUncertain

                        raise ComfySubmissionUncertain("synthetic lost acknowledgement")
                    return ComfySubmission(prompt_id="synthetic-next", workflow_hash=prepared.workflow_hash,
                                           uploaded_references=0, prompt_graph_sha256=prepared.prompt_graph_sha256)

                def poll(self, _prompt_id):
                    return ComfyResult(status="completed", content=b"synthetic-video", content_type="video/mp4")

            from shine.models import JobAttempt

            prior = ManagedReviewCoordinator(supervisor, instance_id, settings.data_dir / "history", "GPU-test",
                min_ram_mb=8000, min_vram_mb=14000, review_configs={"ASR": {
                    "base_url": review_url, "runtime_sha256": receipt["runtime_sha256"],
                    "supervisor": {"pid": 777, "birth": "789"}, "gpu_uuid": "GPU-test", "handoff_token": "x" * 40,
                }})
            prior.session, prior.replacement = session, replacement
            prior.keeper = IdleHandoffKeeper(factory, attempt_id, replacement).start()
            service = SyntheticService()
            if with_intervening_event == "managed_missing_required":
                with factory.begin() as db:
                    db.get(Project, project).quality_policy = {"require_semantic_review": True,
                                                              "require_style_review": False}
            if with_intervening_event in {"managed_expire_before_submit", "managed_graph_changed"}:
                from datetime import timedelta

                import shine.comfyops.handoff_activation as activation
                from shine.models import utcnow

                original_activate = activation.activate_renderer_for_attempt

                def expire_after_activation(*args, **kwargs):
                    result = original_activate(*args, **kwargs)
                    if with_intervening_event == "managed_expire_before_submit":
                        with factory.begin() as db:
                            db.get(Job, new_job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
                    else:
                        service.prepared.prompt["changed"] = {"class_type": "Untrusted", "inputs": {}}
                    return result

                monkeypatch.setattr(activation, "activate_renderer_for_attempt", expire_after_activation)
            next_coordinator = None
            try:
                effective_settings = replace(settings, ffmpeg_path="missing-ffmpeg", ffprobe_path="missing-ffprobe",
                    worker_lease_seconds=2 if with_intervening_event == "managed_slow_prepare" else 30,
                    data_dir=settings.data_dir / "other" if with_intervening_event == "managed_wrong_data_dir"
                    else settings.data_dir, offline_simulation=with_intervening_event != "managed_mock_real")
                if with_intervening_event in {
                    "managed_wrong_data_dir", "managed_mock_real"
                }:
                    with pytest.raises(HTTPException):
                        run_managed_once(factory, "worker-next", prior, settings=effective_settings,
                                         comfy_service=service, asr_reviewer=NewProvider())
                    with factory() as db:
                        assert db.get(ResourceLease, lease_id).status == "ACTIVE"
                        assert db.get(Job, new_job_id).status == "QUEUED"
                    assert service.submissions == 0
                    return
                result, next_coordinator = run_managed_once(factory, "worker-next", prior,
                    settings=effective_settings, comfy_service=service, asr_reviewer=NewProvider())
                assert result == new_job_id
                if with_intervening_event == "managed_missing_required":
                    # Semantic/Style model review is advisory, so the actual
                    # managed H3 submission still happens when that reviewer
                    # is absent. The test intentionally makes technical tools
                    # unavailable; that separate QC result remains pending.
                    assert service.submissions == 1
                    with factory() as db:
                        job = db.get(Job, new_job_id)
                        assert job.artifact_id
                        assert job.failure_code != "HANDOFF_REQUIRED_REVIEW_MISSING"
                    assert next_coordinator.keeper and next_coordinator.session.finished
                    return
                if with_intervening_event not in {"managed_worker", "managed_slow_prepare"}:
                    with factory() as db:
                        from sqlalchemy import select

                        job = db.get(Job, new_job_id)
                        new_lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:" + new_job_id))
                        assert job.status == "NEEDS_REVIEW" and new_lease.status == "RECONCILE_REQUIRED"
                        assert db.get(AppSetting, "handoff-reservation:" + new_job_id).value["stage"] == (
                            "RECONCILE_REQUIRED"
                        )
                        if with_intervening_event == "managed_uncertain":
                            assert service.submissions == 1 and job.failure_code == "COMFY_SUBMISSION_OUTCOME_UNKNOWN"
                        else:
                            assert service.submissions == 0
                    return
                assert service.submissions == 1
                with factory() as db:
                    new_job = db.get(Job, new_job_id)
                    assert new_job.artifact_id and new_job.status == "REPAIR_PENDING"
                    new_journal = db.get(AppSetting, "handoff:" + next_coordinator.session.attempt_id)
                    assert new_journal.value["stage"] == "RESTARTED"
                assert next_coordinator.keeper and next_coordinator.session.finished
            finally:
                prior.keeper.close()
                if next_coordinator and next_coordinator.keeper:
                    next_coordinator.keeper.close()
                if next_coordinator and next_coordinator.replacement:
                    next_coordinator.replacement._group.terminate()
                    next_coordinator.replacement._group.close()
            return
        if with_intervening_event == "reserve":
            from datetime import timedelta

            from shine.comfyops.handoff_activation import activate_renderer_for_attempt
            from shine.comfyops.handoff_idle import IdleHandoffKeeper
            from shine.models import JobAttempt, utcnow
            from tests.conftest import create_comfy_job

            with factory() as db:
                old = db.get(Job, job_id)
                project, shot = old.project_id, old.shot_id
            # The previous attempt is finished before the next one starts.
            with factory.begin() as db:
                old = db.get(Job, job_id)
                old.status, old.lease_owner, old.lease_expires_at = "NEEDS_REVIEW", None, None
            new_job_id = create_comfy_job(api[0], project, shot)["id"]
            keeper = IdleHandoffKeeper(factory, attempt_id, replacement).start()
            try:
                reserved = keeper.reserve(settings, boundary, new_job_id, instance_id)
            finally:
                keeper.close()
            with factory.begin() as db:
                assert db.get(ResourceLease, lease_id).status == "RELEASED"
                assert db.get(ResourceLease, reserved["lease_id"]).status == "ACTIVE"
                new_job = db.get(Job, new_job_id)
                assert new_job.status == "QUEUED"
                new_job.status, new_job.lease_owner = "SUBMITTING", "worker-next"
                new_job.lease_expires_at = utcnow() + timedelta(seconds=60)
                new_attempt = JobAttempt(job_id=new_job_id, attempt_no=1, request_fingerprint="a" * 64,
                                         status="SUBMIT_INTENT", lease_owner="worker-next",
                                         prompt_graph_sha256="c" * 64,
                                         lease_expires_at=utcnow() + timedelta(seconds=60))
                db.add(new_attempt)
                db.flush()
                new_attempt_id, new_version = new_attempt.id, new_job.state_version
            activate_renderer_for_attempt(factory, settings, replacement, new_job_id, new_attempt_id,
                                          reserved["lease_id"], instance_id, "worker-next", new_version,
                                          physical_boundary=boundary)
            with factory() as db:
                assert db.get(AppSetting, "renderer-activation:" + new_attempt_id).value["stage"] == "CONFIRMED"
            return
        if with_intervening_event in {"transfer", "transfer_rejected"}:
            from datetime import timedelta

            from shine.comfyops.handoff_activation import activate_renderer_for_attempt
            from shine.comfyops.handoff_idle import IdleHandoffKeeper
            from shine.models import JobAttempt, utcnow
            from tests.conftest import create_comfy_job

            client = api[0]
            with factory() as db:
                old_job = db.get(Job, job_id)
                project, shot = old_job.project_id, old_job.shot_id
            # The previous attempt is finished before the next one starts.
            with factory.begin() as db:
                old_job = db.get(Job, job_id)
                old_job.status, old_job.lease_owner, old_job.lease_expires_at = "NEEDS_REVIEW", None, None
            next_job_id = create_comfy_job(client, project, shot)["id"]
            with factory.begin() as db:
                job = db.get(Job, next_job_id)
                job.status, job.lease_owner = "SUBMITTING", "worker-next"
                job.lease_expires_at = utcnow() + timedelta(seconds=60)
                next_row = JobAttempt(job_id=job.id, attempt_no=1, request_fingerprint="a" * 64,
                    status="SUBMIT_INTENT", lease_owner="worker-next",
                    prompt_graph_sha256=None if with_intervening_event == "transfer_rejected" else "c" * 64,
                    lease_expires_at=utcnow() + timedelta(seconds=60))
                db.add(next_row)
                db.flush()
                next_attempt_id, next_version = next_row.id, job.state_version
            if with_intervening_event == "transfer":
                from time import sleep

                import shine.comfyops.handoff_transfer as transfer_module

                original_check = transfer_module._check

                def delayed_transfer_check(*args, **kwargs):
                    sleep(0.3)  # Force an idle heartbeat to contend with the transfer.
                    return original_check(*args, **kwargs)

                monkeypatch.setattr(transfer_module, "_check", delayed_transfer_check)
            keeper = IdleHandoffKeeper(factory, attempt_id, replacement, lease_seconds=3, interval=0.1).start()
            try:
                if with_intervening_event == "transfer_rejected":
                    with pytest.raises(HTTPException):
                        keeper.transfer(settings, boundary, next_job_id, next_attempt_id,
                                        instance_id, "worker-next", next_version)
                    with factory() as db:
                        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
                        assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"
                    return
                transferred = keeper.transfer(settings, boundary, next_job_id, next_attempt_id,
                                               instance_id, "worker-next", next_version)
            finally:
                keeper.close()
            with factory() as db:
                assert db.get(ResourceLease, lease_id).status == "RELEASED"
                assert db.get(ResourceLease, transferred["lease_id"]).status == "ACTIVE"
            assert boundary.verify(factory, settings, instance_id, next_attempt_id, transferred["lease_id"])["reviews"]
            activate_renderer_for_attempt(factory, settings, replacement, next_job_id, next_attempt_id,
                                          transferred["lease_id"], instance_id, "worker-next", next_version,
                                          physical_boundary=boundary)
            with factory() as db:
                assert db.get(AppSetting, "renderer-activation:" + next_attempt_id).value["stage"] == "CONFIRMED"
            assert replacement._control("GET", "/shine-handoff/status", "accepting")
            return
        if with_intervening_event in {"idle_lost", "idle_expired", "idle_heartbeat", "idle_closed"}:
            from datetime import timedelta
            from time import monotonic, sleep

            from shine.comfyops.handoff_idle import IdleHandoffKeeper
            from shine.models import utcnow

            keeper = IdleHandoffKeeper(factory, attempt_id, replacement, lease_seconds=3, interval=0.1).start()
            try:
                if with_intervening_event == "idle_closed":
                    from shine.comfyops.scheduler import ResourceScheduler

                    keeper.close()
                    with factory.begin() as db:
                        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
                        waiting = ResourceScheduler().enqueue(db, "idle-close-contender", 1)
                        contender_id = waiting.id
                    with factory() as db:
                        assert not ResourceScheduler().admit(db, contender_id)
                    return
                if with_intervening_event == "idle_heartbeat":
                    with factory.begin() as db:
                        db.get(ResourceLease, lease_id).expires_at = utcnow() + timedelta(seconds=1)
                    deadline = monotonic() + 2
                    while monotonic() < deadline:
                        with factory() as db:
                            renewed = db.get(ResourceLease, lease_id).expires_at > utcnow() + timedelta(seconds=2)
                        if renewed:
                            break
                        sleep(0.05)
                    assert renewed
                    return
                if with_intervening_event == "idle_lost":
                    def owner_lost(*_args):
                        raise ValueError("SYNTHETIC_OWNER_LOST")
                    monkeypatch.setattr(replacement, "verify_drained_binding", owner_lost)
                else:
                    with factory.begin() as db:
                        db.get(ResourceLease, lease_id).expires_at = utcnow() - timedelta(seconds=1)
                with pytest.raises((HTTPException, ValueError)):
                    keeper.check()
                with factory() as db:
                    assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
                    assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"
            finally:
                keeper.close()
            return
        if with_intervening_event:
            from sqlalchemy import text

            from shine.comfyops.resource_epoch import advance_epoch

            with factory.begin() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                advance_epoch(db, lease_id, "model")
            with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
                boundary.verify(factory, settings, instance_id, next_attempt, lease_id)
            return
        with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
            ReviewReleaseBoundary(session, min_ram_mb=8000, min_vram_mb=13999)
        with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
            boundary.verify(factory, replace(settings, offline_simulation=False), instance_id, next_attempt, lease_id)
        from datetime import timedelta

        from shine.models import utcnow

        with factory.begin() as db:
            waiting = ResourceLease(owner="next-queued-job", budget_mb=1, exclusive=True, priority=1,
                                    status="WAITING", heartbeat_at=utcnow() + timedelta(seconds=1),
                                    expires_at=utcnow() + timedelta(seconds=60))
            db.add(waiting)
            db.flush()
            waiting_id = waiting.id
        # A WAITING row alone does not prove it was never admitted by an older worker.
        # Until admission provenance is durable, this ambiguous row must block.
        with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
            boundary.verify(factory, settings, instance_id, next_attempt, lease_id)
        with factory.begin() as db:
            # An isolated synthetic queue row is retained for the rest of this test.
            db.get(ResourceLease, waiting_id).heartbeat_at = utcnow() - timedelta(days=1)
            db.get(ResourceLease, waiting_id).created_at = utcnow() - timedelta(days=1)
        with factory.begin() as db:
            later = ResourceLease(owner="later-model", budget_mb=1, exclusive=True, priority=1, status="RELEASED",
                                  heartbeat_at=utcnow() + timedelta(seconds=1), expires_at=utcnow())
            db.add(later)
            db.flush()
            later_id = later.id
        with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
            boundary.verify(factory, settings, instance_id, next_attempt, lease_id)
        with factory.begin() as db:
            db.get(ResourceLease, later_id).heartbeat_at = utcnow() - timedelta(days=1)
            db.get(ResourceLease, later_id).created_at = utcnow() - timedelta(days=1)
        with factory.begin() as db:
            journal_row = db.get(AppSetting, "handoff:" + attempt_id)
            original_journal = dict(journal_row.value)
            changed_steps = [dict(item) for item in original_journal["review_steps"]]
            changed_steps[0]["resource_epoch"] = {"epoch": 999, "kind": "model", "lease_id": lease_id}
            journal_row.value = {**original_journal, "review_steps": changed_steps}
        with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
            boundary.verify(factory, settings, instance_id, next_attempt, lease_id)
        with factory.begin() as db:
            db.get(AppSetting, "handoff:" + attempt_id).value = original_journal
        with factory.begin() as db:
            task = db.get(AgentTaskRecord, step["task_id"])
            task.trace = {**task.trace, "isolation_receipt": {**receipt, "stage_id": str(uuid4())}}
        with pytest.raises(HTTPException, match="COMFY_PHYSICAL_RELEASE_UNVERIFIED"):
            boundary.verify(factory, settings, instance_id, next_attempt, lease_id)
    finally:
        if replacement:
            replacement._group.terminate()
            replacement._group.close()


@pytest.mark.parametrize("server", [True], indirect=True)
@pytest.mark.parametrize("review_fails", [False, True])
def test_worker_collects_reviews_under_managed_handoff(api, handoff, server, monkeypatch, review_fails):  # noqa: F811
    from dataclasses import replace
    from types import SimpleNamespace

    from shine.comfyops.handoff_preparation import ManagedReviewCoordinator
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AppSetting, Job, Project
    from shine.quality.semantic import Transcript
    from shine.worker import _finalize_comfy_attempt
    from tests.test_review_isolation_receipt import response

    settings, _path = bind_handoff_output(api, handoff, server)
    settings = replace(settings, offline_simulation=True, ffprobe_path="missing-ffprobe", ffmpeg_path="missing-ffmpeg")
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    receipt = response()["isolation_receipt"]
    review_url = "http://127.0.0.1:8116/v1"
    calls = []

    class Provider:
        is_mock = True
        config = SimpleNamespace(profile_id="p", kind="local", model="test", base_url=review_url)
        last_isolation_receipt = receipt

        def transcribe(self, _value):
            calls.append("ASR")
            if review_fails:
                raise RuntimeError("synthetic review failure")
            return Transcript(text="", is_mock=True, model="test")

    monkeypatch.setattr("shine.comfyops.native.production_review_binding", lambda *args, **kwargs: None)
    monkeypatch.setattr("shine.quality.review_release.verify_review_release", lambda **_kw: {
        "review_stage_id": receipt["stage_id"], "memory": {"ready": True},
    })
    monkeypatch.setattr("shine.quality.review_release.verify_review_ready", lambda **_kw: {"ready": True})
    monkeypatch.setattr("shine.quality.review_release.reserve_review_service", lambda **_kw: None)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    coordinator = ManagedReviewCoordinator(supervisor, instance_id, settings.data_dir / "history", "GPU-test",
        min_ram_mb=8000, min_vram_mb=14000, review_configs={"ASR": {
            "base_url": review_url, "runtime_sha256": receipt["runtime_sha256"],
            "supervisor": {"pid": 777, "birth": "789"}, "gpu_uuid": "GPU-test", "handoff_token": "x" * 40,
        }})
    with factory.begin() as db:
        project = db.get(Project, db.get(Job, job_id).project_id)
        project.quality_policy = {"require_semantic_review": False, "require_style_review": False}
    service = SimpleNamespace(client=SimpleNamespace(free_models=lambda: pytest.fail("unsafe unload path")))
    try:
        if review_fails:
            with pytest.raises(RuntimeError, match="synthetic review failure"):
                _finalize_comfy_attempt(factory, job_id=job_id, attempt_id=attempt_id,
                    expected_state_version=version, worker_id="worker-a", settings=settings, service=service,
                    prompt_id="prompt-1", asr_reviewer=Provider(), managed_handoff=coordinator)
            with factory() as db:
                assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RECONCILE_REQUIRED"
                assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
                failed_job = db.get(Job, job_id)
                assert failed_job.artifact_id
                assert failed_job.status == "NEEDS_REVIEW"
                assert failed_job.failure_code == "COMFY_HANDOFF_RECONCILIATION_REQUIRED"
            assert calls == ["ASR"] and coordinator.replacement is None
            return
        assert _finalize_comfy_attempt(factory, job_id=job_id, attempt_id=attempt_id,
            expected_state_version=version, worker_id="worker-a", settings=settings, service=service,
            prompt_id="prompt-1", asr_reviewer=Provider(), managed_handoff=coordinator) == job_id
        assert calls == ["ASR"]
        with factory() as db:
            assert db.get(AppSetting, "handoff:" + attempt_id).value["stage"] == "RESTARTED"
            assert db.get(ResourceLease, lease_id).status == "ACTIVE"
            reviewed_job = db.get(Job, job_id)
            # Synthetic bytes are not a valid video; the hard error must not pass.
            assert reviewed_job.artifact_id and reviewed_job.status == "REPAIR_PENDING"
            assert reviewed_job.failure_code == "QC_FAILED"
        assert coordinator.session.finished and coordinator.replacement and coordinator.keeper
        with factory.begin() as db:
            from datetime import timedelta

            from shine.models import utcnow
            db.get(ResourceLease, lease_id).expires_at = utcnow() + timedelta(seconds=2)
        coordinator.keeper.check()
        with factory() as db:
            assert db.get(ResourceLease, lease_id).expires_at > utcnow() + timedelta(seconds=30)
    finally:
        if getattr(coordinator, "keeper", None):
            coordinator.keeper.close()
        if coordinator.replacement:
            coordinator.replacement._group.terminate()
            coordinator.replacement._group.close()


@pytest.mark.parametrize("server", [True], indirect=True)
def test_interrupted_review_intent_preserves_lease_and_never_restarts(api, handoff, server, monkeypatch):  # noqa: F811
    from dataclasses import replace

    from fastapi import HTTPException

    from shine.comfyops.handoff_preparation import prepare_review_handoff
    from shine.comfyops.models import ResourceLease
    from shine.comfyops.renderer_supervisor import RendererSupervisor
    from shine.models import AppSetting

    settings, _path = bind_handoff_output(api, handoff, server)
    settings = replace(settings, offline_simulation=True)
    factory, job_id, attempt_id, lease_id, instance_id, version = handoff
    group, url, token, digest, _root = server
    supervisor = RendererSupervisor(group, url, token, digest)
    monkeypatch.setattr("shine.comfyops.renderer_supervisor.probe_memory", lambda uuid, **kw: {
        "ready": True, "available_ram_bytes": 16 * 1024**3,
        "gpu": {"uuid": uuid, "total_mb": 16384, "free_mb": 15000}, **kw,
    })
    with pytest.raises(RuntimeError, match="SIMULATED_REVIEW_PROCESS_LOST"):
        with prepare_review_handoff(factory, settings, job_id, attempt_id, lease_id, instance_id,
                                    "worker-a", version, supervisor, settings.data_dir / "history", "GPU-test",
                                    min_ram_mb=8000, min_vram_mb=14000) as session:
            with session.review_stage("ASR"):
                raise RuntimeError("SIMULATED_REVIEW_PROCESS_LOST")
    with factory() as db:
        journal = db.get(AppSetting, "handoff:" + attempt_id).value
        assert journal["stage"] == "RECONCILE_REQUIRED"
        assert journal["last_confirmed_stage"] == "REVIEW_INTENT"
        assert journal["review_steps"][0]["status"] == "INTENT"
        assert db.get(ResourceLease, lease_id).status == "RECONCILE_REQUIRED"
    from shine.comfyops.handoff_journal import reserve_handoff_review

    with pytest.raises(HTTPException):
        reserve_handoff_review(factory, attempt_id, "ASR", settings=settings)
    assert supervisor._replacement is None
