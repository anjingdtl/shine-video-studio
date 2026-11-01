"""The installation identity is bound to every production run inside one database.

The installation row is durable evidence and every production run owns its own
identity. A first run and every later run must both prove that binding, and
neither the installation evidence nor an earlier run's record may be deleted to
make a conflict disappear. Every port, file, receipt and process below is
synthetic; no model is ever loaded and no H3 inference happens here.
"""
# ruff: noqa: F811

import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shine.comfy.workflow import graph_hash
from shine.comfyops.handoff_startup import STARTUP_PREFIX, start_managed_renderer, verify_managed_startup
from shine.comfyops.managed_recipe import managed_run_binding, managed_run_identity
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan, ResourceLease
from shine.comfyops.owned_process import terminate_named_group
from shine.comfyops.renderer_supervisor import windows_port_listeners
from shine.comfyops.scheduler import ResourceScheduler
from shine.models import AppSetting, Job, JobAttempt, utcnow
from tests.conftest import create_comfy_job, create_shot, freeze_style
from tests.test_managed_startup import (  # noqa: F401
    DIGEST,
    TOKEN,
    launch_arguments,
    ready_memory,
    renderer,
)
from tests.test_runtime_activation import candidate  # noqa: F401
from tests.test_setup_cold_start import seed
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are required")


def _refusing_launch():
    def launch(*args, **kwargs):
        raise RuntimeError("launch refused")

    return launch


@pytest.fixture
def install(prep):
    """A stopped, real-looking installation with a completed install receipt."""

    def _install(port):
        client, factory, settings = prep
        project, root, identity = seed(prep, port=port)
        (root / '.shine-requirements.txt').write_text('# synthetic lock', encoding='utf-8')
        with factory.begin() as db:
            plan = InstallationPlan(project_id=project, payload={}, sha256=graph_hash({}), status='COMPLETED')
            db.add(plan)
            db.flush()
            row = db.get(ComfyInstance, identity)
            attempt_id = str(uuid4())
            payload = {**row.payload, 'last_attempt': attempt_id, 'dependency_hash': '1' * 64}
            row.payload, row.sha256 = payload, graph_hash(payload)
            receipt = {'is_mock': False, 'result': payload}
            db.add(InstallationAttempt(id=attempt_id, plan_id=plan.id, payload=receipt,
                                       sha256=graph_hash(receipt), status='COMPLETED'))
        with factory() as db:
            installed = db.get(ComfyInstance, identity)
        return {'project': project, 'root': root, 'identity': identity, 'port': port,
                'sha256': installed.sha256, 'payload': dict(installed.payload),
                'receipt': attempt_id}

    return _install


def claim(factory, client, project, style_id, *, worker="worker-a"):
    """A claimed ComfyUI job with one admitted exclusive lease and one attempt."""
    shot = create_shot(client, project, style_id)
    job = create_comfy_job(client, project, shot["id"])
    with factory.begin() as db:
        lease = ResourceScheduler().enqueue(db, "h3:" + job["id"], 16384, True, 100)
        db.flush()
        lease_id = lease.id
        row = db.get(Job, job["id"])
        row.status, row.lease_owner = "SUBMITTING", worker
        row.lease_expires_at = utcnow() + timedelta(seconds=120)
        row.state_version += 1
        attempt = JobAttempt(job_id=row.id, attempt_no=1, request_fingerprint="a" * 64,
                             status="SUBMIT_INTENT", lease_owner=worker,
                             lease_expires_at=utcnow() + timedelta(seconds=120),
                             prompt_graph_sha256="b" * 64)
        db.add(attempt)
        db.flush()
        attempt_id, version = attempt.id, row.state_version
    with factory() as db:
        assert ResourceScheduler().admit(db, lease_id)
    return job["id"], lease_id, attempt_id, version


def release(factory, lease_id):
    """Give the exclusive GPU admission back before the next run claims it."""
    with factory.begin() as db:
        db.get(ResourceLease, lease_id).status = "RELEASED"


def recipe_for(info):
    """The activated recipe shape: the installation identity plus its evidence."""
    return {'instance_id': info['identity'],
            'installation': {'id': info['identity'], 'sha256': info['sha256']}}


def run(factory, settings, renderer, claimed, info, *, binding=None, instance_id=None, **overrides):
    """Start one production run that is bound to the installation."""
    bound, runtime_id = managed_run_binding(recipe_for(info), claimed[2])
    arguments = launch_arguments(renderer, overrides.get("inventory"))
    arguments.update(overrides)
    # The renderer works in the installed tree, exactly as the generated recipe says.
    arguments["cwd"] = str(info["root"])
    job_id, lease_id, attempt_id, version = claimed
    return start_managed_renderer(
        factory, settings,
        instance_id=instance_id or runtime_id,
        lease_id=lease_id,
        job_id=job_id,
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
        installation=binding if binding is not None else bound,
    )


def stop(managed, port):
    """Stop the owned renderer and wait until its port is free again."""
    terminate_named_group(managed.group.job_name)
    managed.group.close()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not windows_port_listeners(port):
            return
        time.sleep(0.05)
    raise AssertionError("the renderer never released its port")


def test_first_run_binds_the_installation_and_its_own_run_identity(prep, install, renderer):
    """The first production run owns a run identity and leaves the evidence alone."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    binding, runtime_id = managed_run_binding(recipe_for(info), claimed[2])

    assert runtime_id == managed_run_identity(info['identity'], claimed[2])
    assert runtime_id != info['identity']
    managed = run(factory, settings, renderer, claimed, info)
    try:
        with factory() as db:
            journal = db.get(AppSetting, STARTUP_PREFIX + runtime_id)
            assert journal is not None
            assert journal.value["stage"] == "RUNNING"
            assert journal.value["installation"] == binding
            record = db.get(ComfyInstance, runtime_id)
            assert record is not None
            assert record.payload["installation"] == binding
            assert record.payload["managed"] is True
            assert record.payload["is_mock"] is False
            # The run is recorded under its own identity, never over the installation.
            installed = db.get(ComfyInstance, info['identity'])
            assert installed.id == info['identity']
            assert installed.sha256 == info['sha256']
            assert installed.payload == info['payload']
            assert installed.status == 'STOPPED'
    finally:
        stop(managed, renderer[2])


def test_a_later_production_run_uses_its_own_run_identity(prep, install, renderer):
    """A second production run starts beside the first one, without erasing it."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    first = claim(factory, client, info['project'], style['id'])
    binding_one, runtime_one = managed_run_binding(recipe_for(info), first[2])
    managed = run(factory, settings, renderer, first, info)
    stop(managed, renderer[2])
    release(factory, first[1])

    second = claim(factory, client, info['project'], style['id'])
    binding_two, runtime_two = managed_run_binding(recipe_for(info), second[2])
    assert runtime_two != runtime_one
    managed_two = run(factory, settings, renderer, second, info)
    try:
        with factory() as db:
            # Both runs keep their own record; neither replaced the other.
            for runtime_id, binding in ((runtime_one, binding_one), (runtime_two, binding_two)):
                journal = db.get(AppSetting, STARTUP_PREFIX + runtime_id)
                assert journal is not None and journal.value["stage"] == "RUNNING"
                assert journal.value["installation"] == binding
                record = db.get(ComfyInstance, runtime_id)
                assert record is not None and record.payload["installation"] == binding
            # The installation evidence is still the one that was proven.
            installed = db.get(ComfyInstance, info['identity'])
            assert installed.sha256 == info['sha256']
            assert installed.payload == info['payload']
    finally:
        stop(managed_two, renderer[2])


@pytest.mark.parametrize('damage', ['evidence_changed', 'receipt_missing', 'receipt_mock', 'instance_mock'])
def test_changed_installation_evidence_blocks_the_run(prep, install, renderer, damage):
    """A run may never use an installation whose proof has moved."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    with factory.begin() as db:
        row = db.get(ComfyInstance, info['identity'])
        if damage == 'evidence_changed':
            row.payload = {**row.payload, 'dependency_hash': 'changed'}
            row.sha256 = graph_hash(row.payload)
        elif damage == 'receipt_missing':
            db.delete(db.get(InstallationAttempt, info['receipt']))
        elif damage == 'receipt_mock':
            receipt = db.get(InstallationAttempt, info['receipt'])
            receipt.payload = {**receipt.payload, 'is_mock': True}
            receipt.sha256 = graph_hash(receipt.payload)
        else:
            row.payload = {**row.payload, 'is_mock': True}
            row.sha256 = graph_hash(row.payload)
    binding, runtime_id = managed_run_binding(recipe_for(info), claimed[2])
    with pytest.raises(HTTPException, match="COMFY_INSTALLATION_BINDING_CHANGED"):
        run(factory, settings, renderer, claimed, info, launch=_refusing_launch())
    with factory() as db:
        # Nothing was recorded and no process was created for a blocked run.
        assert db.get(AppSetting, STARTUP_PREFIX + runtime_id) is None
        assert db.get(ComfyInstance, runtime_id) is None
        assert db.get(ResourceLease, claimed[1]).status == "ACTIVE"


def test_a_running_installation_blocks_the_run(prep, install, renderer):
    """An installation that still owns a process cannot be started again."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    with factory.begin() as db:
        row = db.get(ComfyInstance, info['identity'])
        row.payload = {**row.payload, 'process': {'pid': 4242, 'birth': '1'}}
        row.sha256 = graph_hash(row.payload)
    binding, runtime_id = managed_run_binding(recipe_for(info), claimed[2])
    with pytest.raises(HTTPException, match="COMFY_INSTALLATION_BINDING_CHANGED"):
        run(factory, settings, renderer, claimed, info, launch=_refusing_launch())
    with factory() as db:
        assert db.get(AppSetting, STARTUP_PREFIX + runtime_id) is None


def test_the_binding_attempt_must_belong_to_the_run_job(prep, install, renderer):
    """A binding may not borrow another job's attempt."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    first = claim(factory, client, info['project'], style['id'])
    release(factory, first[1])
    second = claim(factory, client, info['project'], style['id'])
    binding, runtime_id = managed_run_binding(recipe_for(info), second[2])
    with pytest.raises(HTTPException, match="COMFY_INSTALLATION_BINDING_CHANGED"):
        run(factory, settings, renderer, second, info,
            binding={**binding, 'attempt_id': first[2]}, launch=_refusing_launch())
    with factory() as db:
        assert db.get(AppSetting, STARTUP_PREFIX + runtime_id) is None


def test_a_run_identity_must_match_its_own_attempt(prep, install, renderer):
    """The run identity is derived, never chosen by the caller."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    binding, runtime_id = managed_run_binding(recipe_for(info), claimed[2])
    with pytest.raises(HTTPException, match="COMFY_INSTALLATION_BINDING_CHANGED"):
        run(factory, settings, renderer, claimed, info,
            instance_id=managed_run_identity(info['identity'], uuid4()), launch=_refusing_launch())
    with factory() as db:
        assert db.get(AppSetting, STARTUP_PREFIX + runtime_id) is None


def test_the_same_attempt_never_starts_a_second_renderer(prep, install, renderer):
    """An uncertain run keeps its record; it is never relaunched."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    binding, runtime_id = managed_run_binding(recipe_for(info), claimed[2])
    with pytest.raises(RuntimeError, match="launch refused"):
        run(factory, settings, renderer, claimed, info, launch=_refusing_launch())
    with factory() as db:
        journal = db.get(AppSetting, STARTUP_PREFIX + runtime_id)
        assert journal.value["stage"] == "RECONCILE_REQUIRED"
        assert journal.value["installation"] == binding
    with pytest.raises(HTTPException, match="COMFY_STARTUP_ALREADY_RECORDED"):
        run(factory, settings, renderer, claimed, info)


def test_a_continued_run_reverifies_the_installation_binding(prep, install, renderer):
    """A running renderer stays bound to the installation it started from."""
    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    managed = run(factory, settings, renderer, claimed, info)
    try:
        verify_managed_startup(factory, settings, managed)
        with factory.begin() as db:
            row = db.get(ComfyInstance, info['identity'])
            row.payload = {**row.payload, 'dependency_hash': 'changed'}
            row.sha256 = graph_hash(row.payload)
        with pytest.raises(HTTPException, match="COMFY_INSTALLATION_BINDING_CHANGED"):
            verify_managed_startup(factory, settings, managed)
    finally:
        stop(managed, renderer[2])


def test_readiness_ignores_the_run_records(prep, install, renderer):
    """The installed environment layer still resolves the installation itself."""
    from shine.comfyops import deployment

    client, factory, settings = prep
    info = install(renderer[2])
    style = freeze_style(client, info['project'])
    claimed = claim(factory, client, info['project'], style['id'])
    managed = run(factory, settings, renderer, claimed, info)
    try:
        with factory() as db:
            report = deployment.deployment_readiness(db, settings)
        layer = next(x for x in report['layers'] if x['layer'] == 'L3_environment_installed')
        assert layer['instance'] == info['identity']
        assert layer['status'] == 'READY'
    finally:
        stop(managed, renderer[2])


def test_the_recipe_must_carry_a_valid_installation_binding(prep):
    """A bound recipe may not name another installation or a loose digest."""
    from shine.comfyops.managed_recipe import _renderer, adapter_identity

    client, factory, settings = prep
    root = settings.data_dir / 'recipe-root'
    (root / 'adapter').mkdir(parents=True, exist_ok=True)
    (root / 'bootstrap.py').write_text('# synthetic', encoding='utf-8')
    for name in ('__init__.py', 'barrier.py'):
        (root / 'adapter' / name).write_text('# synthetic', encoding='utf-8')
    base = {
        'instance_id': 'installation-id',
        'python': sys.executable,
        'root': str(root),
        'base_directory': str(root),
        'bootstrap': str(root / 'bootstrap.py'),
        'adapter_dir': str(root / 'adapter'),
        'adapter_sha256': adapter_identity(str(root / 'adapter')),
        'gpu_uuid': 'GPU-test',
        'argv': ['{python}', '{bootstrap}'],
        'pinned_files': ['{python}'],
    }
    with pytest.raises(HTTPException, match="COMFY_RECIPE_INSTALLATION_INVALID"):
        _renderer({**base, 'installation': {'id': 'other-installation', 'sha256': 'b' * 64}})
    with pytest.raises(HTTPException, match="COMFY_RECIPE_INSTALLATION_INVALID"):
        _renderer({**base, 'installation': {'id': 'installation-id', 'sha256': 'not-a-digest'}})
    with pytest.raises(HTTPException, match="COMFY_RECIPE_INSTALLATION_INVALID"):
        _renderer({**base, 'installation': {'id': 'installation-id'}})
    recipe = _renderer({**base, 'installation': {'id': 'installation-id', 'sha256': 'b' * 64}})
    assert recipe['installation'] == {'id': 'installation-id', 'sha256': 'b' * 64}


def test_activation_requires_the_installation_binding(prep, candidate):
    """A generated configuration without the binding is not an activatable identity."""
    from shine.comfyops.runtime_configuration import configuration_identity

    client, factory, settings = prep
    path = Path(candidate[3]['recipe'])
    with factory() as db:
        identity = configuration_identity(db, settings, path)
    assert identity['instance_id'] == candidate[2]
    raw = json.loads(path.read_text(encoding='utf-8'))
    without = {**raw, 'renderer': {k: v for k, v in raw['renderer'].items() if k != 'installation'}}
    path.write_text(json.dumps(without, indent=2), encoding='utf-8')
    with factory() as db, pytest.raises(HTTPException, match="RUNTIME_INSTALLATION_BINDING_REQUIRED"):
        configuration_identity(db, settings, path)
    stale = {**raw, 'renderer': {**raw['renderer'],
                                 'installation': {**raw['renderer']['installation'], 'sha256': '0' * 64}}}
    path.write_text(json.dumps(stale, indent=2), encoding='utf-8')
    with factory() as db, pytest.raises(HTTPException, match="RUNTIME_INSTALLATION_BINDING_REQUIRED"):
        configuration_identity(db, settings, path)


def test_a_legacy_recipe_without_a_binding_keeps_its_own_identity():
    """A recipe that was never bound to an installation still runs as it did."""
    binding, runtime_id = managed_run_binding({'instance_id': 'legacy-instance'}, 'attempt-1')
    assert binding is None
    assert runtime_id == 'legacy-instance'
