"""Real-machine acceptance of the first managed startup.

This test only runs when the operator points it at a local ComfyUI install and
sets ``SHINE_REAL_COMFY_PROBE=1``. It starts the real renderer through the
managed startup path, proves the durable intent, the owned process group, the
closed start, the ownership proof and the physical baseline, and then stops the
owned group. It never submits a prompt, never loads a model and never touches a
production database.

Required environment:
  SHINE_REAL_COMFY_PROBE=1
  SHINE_REAL_COMFY_PYTHON   absolute path to the ComfyUI interpreter
  SHINE_REAL_COMFY_ROOT     absolute path to the ComfyUI root (contains main.py)
  SHINE_REAL_COMFY_BASE     absolute path to the ComfyUI base directory
  SHINE_REAL_COMFY_GPU_UUID expected GPU UUID, for the physical baseline
"""

import hashlib
import os
import shutil
import socket
from pathlib import Path

import pytest

from shine.comfyops.handoff_startup import STARTUP_PREFIX, start_managed_renderer
from shine.comfyops.models import ComfyInstance, ResourceLease
from shine.comfyops.scheduler import ResourceScheduler
from shine.models import AppSetting, Job, JobAttempt, utcnow

pytestmark = [
    pytest.mark.full,
    pytest.mark.external_service,
    pytest.mark.gpu_required,
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("SHINE_REAL_COMFY_PROBE") != "1",
        reason="SHINE_REAL_COMFY_PROBE=1 and a local ComfyUI install are required",
    ),
]

REPO = Path(__file__).resolve().parents[2]


def _required(name):
    value = os.environ.get(name, "").strip()
    if not value or not Path(value).is_absolute() or not Path(value).exists():
        pytest.skip(f"{name} must point at an existing absolute path")
    return value


@pytest.fixture
def real_recipe(tmp_path):
    """The operator's renderer recipe, with the adapter bytes pinned."""
    python = Path(_required("SHINE_REAL_COMFY_PYTHON"))
    root = Path(_required("SHINE_REAL_COMFY_ROOT"))
    base = Path(_required("SHINE_REAL_COMFY_BASE"))
    adapter = base / "custom_nodes" / "shine_comfy_handoff"
    if not (adapter / "__init__.py").is_file():
        shutil.copytree(REPO / "integrations" / "shine_comfy_handoff", adapter,
                        ignore=shutil.ignore_patterns("__pycache__"))
    parts = []
    for name in ("__init__.py", "barrier.py"):
        parts.append(hashlib.sha256((adapter / name).read_bytes()).digest())
    digest = hashlib.sha256(b"".join(parts)).hexdigest()
    with socket.socket() as reserving:
        reserving.bind(("127.0.0.1", 0))
        port = reserving.getsockname()[1]
    bootstrap = REPO / "scripts" / "managed_comfy_bootstrap.py"
    return {
        "instance_id": "real-managed-startup",
        "python": str(python),
        "root": str(root),
        "base_directory": str(base),
        "bootstrap": str(bootstrap),
        "adapter_dir": str(adapter),
        "adapter_sha256": digest,
        "gpu_uuid": os.environ.get("SHINE_REAL_COMFY_GPU_UUID", "").strip(),
        "min_ram_mb": 1024,
        "min_vram_mb": 1024,
        "argv": [str(python), str(bootstrap), "--base-directory", str(base), "--cpu",
                 "--disable-all-custom-nodes", "--whitelist-custom-nodes", "shine_comfy_handoff",
                 "--disable-partner-nodes", "--disable-auto-launch", "--listen", "127.0.0.1",
                 "--port", str(port)],
        "pinned_files": [str(python), str(root / "main.py"), str(adapter / "__init__.py"),
                         str(adapter / "barrier.py"), str(bootstrap)],
        "port": port,
    }


def test_real_comfyui_starts_behind_the_managed_startup(api, real_recipe):
    """The real renderer comes up closed, owned and physically measured."""
    from datetime import timedelta

    from shine.comfyops.managed_recipe import build_renderer_launch
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
        row.status, row.lease_owner = "SUBMITTING", "worker-real"
        row.lease_expires_at = utcnow() + timedelta(seconds=600)
        row.state_version += 1
        attempt = JobAttempt(job_id=row.id, attempt_no=1, request_fingerprint="a" * 64,
                             status="SUBMIT_INTENT", lease_owner="worker-real",
                             lease_expires_at=utcnow() + timedelta(seconds=600),
                             prompt_graph_sha256="b" * 64)
        db.add(attempt)
        db.flush()
    with factory() as db:
        assert ResourceScheduler().admit(db, lease_id)

    argv, cwd, env, pins, token, port = build_renderer_launch(real_recipe)
    renderer = None
    try:
        renderer = start_managed_renderer(
            factory, settings,
            instance_id="real-managed-startup", lease_id=lease_id, job_id=job["id"],
            worker_id="worker-real", argv=argv, cwd=cwd, env=env, pinned_files=pins,
            port=port, handoff_token=token, adapter_sha256=real_recipe["adapter_sha256"],
            gpu_uuid=real_recipe["gpu_uuid"], min_ram_mb=1024, min_vram_mb=1024,
        )
        with factory() as db:
            journal = db.get(AppSetting, STARTUP_PREFIX + "real-managed-startup")
            assert journal.value["stage"] == "RUNNING"
            assert token not in str(journal.value)
            instance = db.get(ComfyInstance, "real-managed-startup")
            assert instance.status == "RUNNING_PENDING_HEALTH"
            assert instance.payload["managed"] is True
            assert instance.payload["is_mock"] is False
            assert instance.payload["process"]["pid"] > 0
            assert instance.payload["process"]["birth"].isdigit()
            assert instance.payload["process"]["job_name"].startswith("Local\\ShineHandoff-")
            assert db.get(ResourceLease, lease_id).status == "ACTIVE"
        assert renderer.memory["ready"] is True
        assert renderer.supervisor.verify_drained_binding(renderer.url, renderer.process)
    finally:
        if renderer is not None:
            stopped = renderer.group.terminate()
            assert stopped["active_processes"] == 0
            renderer.group.close()


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    raise SystemExit(pytest.main([__file__, "-q", "-o", "addopts="]))
