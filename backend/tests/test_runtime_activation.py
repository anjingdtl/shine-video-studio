"""Persistent configuration contracts; all install receipts/files are synthetic."""
# ruff: noqa: F811
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shine.comfy.workflow import graph_hash
from shine.comfyops import deployment
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan, ResourceLease
from shine.comfyops.runtime_configuration import ACTIVE_KEY, resolve_runtime
from shine.models import AppSetting, utcnow
from tests.conftest import create_job, create_shot, freeze_style
from tests.test_setup_cold_start import seed
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401


@pytest.fixture
def candidate(prep, monkeypatch):
    project, root, identity = seed(prep)
    client, factory, settings = prep
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
    monkeypatch.setattr(deployment, 'ENV_PATH', settings.data_dir / 'isolated-env')
    monkeypatch.setattr('shine.comfyops.host_env.probe_gpu', lambda: {'status': 'OK', 'uuid': 'GPU-TEST'})
    response = client.post('/api/v1/setup/configuration', json={'project_id': project}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return project, root, identity, response.json()


def activate(prep, candidate, *, headers=HEADERS, sha=None):
    client, _, _ = prep
    return client.post('/api/v1/setup/runtime/activate', headers=headers, json={
        'project_id': candidate[0], 'recipe_sha256': sha or candidate[3]['recipe_sha256']})


def test_generation_does_not_activate_and_operator_activation_survives_restart(prep, candidate, monkeypatch):
    client, factory, settings = prep
    assert client.get('/api/v1/setup/runtime').json()['status'] == 'NOT_ACTIVATED'
    assert client.get('/health').json()['render_provider'] == 'mock'
    assert 'SHINE_RENDER_PROVIDER=' not in deployment.ENV_PATH.read_text(encoding='utf-8')
    assert activate(prep, candidate, headers={}).status_code in {401, 403}
    assert activate(prep, candidate, sha='0' * 64).status_code == 409
    response = activate(prep, candidate)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'ACTIVE'
    assert response.json()['services_verified'] is False
    assert activate(prep, candidate).json()['configuration_sha256'] == response.json()['configuration_sha256']
    with factory() as db:
        effective = resolve_runtime(db, settings)
    assert settings.render_provider == 'mock'
    assert effective.render_provider == 'comfyui'
    assert effective.managed_recipe_path == candidate[3]['recipe']
    assert effective.managed_recipe_sha256 == candidate[3]['recipe_sha256']
    assert client.get('/health').json()['render_provider'] == 'comfyui'
    assert client.get('/api/v1/comfy/config').json()['render_mode'] == 'comfyui'
    # Activation is also the operator's statement that this host renders for real,
    # so the choice is persisted for the next START_APP.cmd instead of relying on
    # the launcher flag that created today's process.
    assert 'SHINE_RENDER_PROVIDER=comfyui' in deployment.ENV_PATH.read_text(encoding='utf-8')
    from shine.comfy.client import ComfyUIClient
    def no_renderer(*args):
        raise RuntimeError('No real renderer exists in this contract test')
    monkeypatch.setattr(ComfyUIClient, 'system_stats', no_renderer)
    status = client.get('/api/v1/system/status').json()
    assert status['render_mode'] == 'comfyui' and status['h3_connected'] is False
    # A fresh settings object still inherits the durable binding, not the
    # launcher's old Mock flag. No process-global settings mutation is used.
    with factory() as db:
        assert resolve_runtime(db, replace(settings)).managed_recipe_sha256 == effective.managed_recipe_sha256


def test_resident_worker_reloads_activation_before_its_next_iteration(prep, candidate, monkeypatch):
    from shine import worker
    client, factory, settings = prep
    observed = []
    transport = object()
    monkeypatch.setattr(worker, '_renderer_client', lambda settings: transport)
    monkeypatch.setattr(worker, '_run_configured_once', lambda *args, **kw: observed.append(kw))
    worker.run_configured_once(factory, object(), 'contract-worker', settings=settings)
    assert observed[-1]['settings'].render_provider == 'mock'
    assert activate(prep, candidate).status_code == 200
    worker.run_configured_once(factory, object(), 'contract-worker', settings=settings, comfy_client=object())
    assert observed[-1]['settings'].render_provider == 'comfyui'
    assert observed[-1]['settings'].managed_recipe_path == candidate[3]['recipe']
    assert observed[-1]['comfy_client'] is transport


@pytest.mark.parametrize('changed', ['recipe', 'manifest', 'entrypoint', 'lock', 'instance'])
def test_changed_identity_blocks_activation_and_active_worker_without_mock_fallback(
        prep, candidate, changed, monkeypatch):
    from shine import worker
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    if changed == 'instance':
        with factory.begin() as db:
            row = db.get(ComfyInstance, candidate[2])
            row.payload = {**row.payload, 'dependency_hash': 'changed'}
            row.sha256 = graph_hash(row.payload)
    else:
        from pathlib import Path
        path = {'recipe': Path(candidate[3]['recipe']),
                'manifest': Path(candidate[3]['review_manifests']['ASR']),
                'entrypoint': candidate[1] / 'main.py', 'lock': candidate[1] / '.shine-requirements.txt'}[changed]
        path.write_bytes(path.read_bytes() + b'\n ')
    assert activate(prep, candidate).status_code == 409
    assert client.get('/api/v1/setup/runtime').json()['status'] == 'INVALID_REQUIRES_REVIEW'
    assert client.get('/health').json()['render_provider'] == 'unavailable'
    calls = []
    monkeypatch.setattr(worker, '_run_configured_once', lambda *a, **kw: calls.append(kw))
    with pytest.raises(HTTPException, match='ACTIVE_RUNTIME_'):
        worker.run_configured_once(factory, object(), 'blocked-worker', settings=settings)
    assert calls == []
    assert client.get('/api/v1/system/status').json()['render_mode'] == 'unavailable'


@pytest.mark.parametrize('blocker', ['ACTIVE', 'RECONCILE_REQUIRED', 'queued_job', 'fake_receipt', 'missing_receipt'])
def test_activation_requires_idle_reconciled_real_installation(prep, candidate, blocker):
    client, factory, settings = prep
    if blocker == 'queued_job':
        style = freeze_style(client, candidate[0])
        shot = create_shot(client, candidate[0], style['id'])
        create_job(client, candidate[0], shot['id'])
    else:
        with factory.begin() as db:
            if blocker in {'ACTIVE', 'RECONCILE_REQUIRED'}:
                db.add(ResourceLease(owner='contract-lease', budget_mb=1, exclusive=True, priority=1,
                                     status=blocker, expires_at=utcnow() - timedelta(seconds=1)))
            else:
                row = db.get(ComfyInstance, candidate[2])
                receipt = db.get(InstallationAttempt, row.payload['last_attempt'])
                receipt.payload = {**receipt.payload, 'is_mock': True}
                receipt.sha256 = graph_hash(receipt.payload)
                if blocker == 'missing_receipt':
                    db.delete(receipt)
    response = activate(prep, candidate)
    assert response.status_code == 409, response.text
    with factory() as db:
        assert db.get(AppSetting, ACTIVE_KEY) is None
        assert resolve_runtime(db, settings).render_provider == 'mock'


def test_real_activation_is_incompatible_with_offline_simulation(prep, candidate):
    _, factory, settings = prep
    from shine.comfyops.runtime_configuration import activate_runtime
    with factory() as db, pytest.raises(HTTPException, match='OFFLINE_SIMULATION'):
        activate_runtime(db, replace(settings, offline_simulation=True), candidate[0],
                         candidate[3]['recipe_sha256'], 'test-operator')
    assert activate(prep, candidate).status_code == 200
    with factory() as db, pytest.raises(HTTPException, match='OFFLINE_SIMULATION'):
        resolve_runtime(db, replace(settings, offline_simulation=True))


def test_real_resident_worker_observes_activation_and_blocks_changed_files(prep, candidate):
    """A real OS Worker reloads SQLite while alive; no models, jobs or renderer."""
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from sqlalchemy import select

    from shine.models import Job, WorkerInstance

    _, factory, settings = prep
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith('SHINE_')}
    environment.update({'SHINE_DATA_DIR': str(settings.data_dir), 'SHINE_DATABASE_URL': settings.database_url,
                        'SHINE_RENDER_PROVIDER': 'mock', 'SHINE_MANAGED_RECIPE': '',
                        'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
    log_path = settings.data_dir / 'resident-worker-activation.log'
    with log_path.open('wb') as log:
        child = subprocess.Popen([sys.executable, '-m', 'shine.worker'], env=environment,
                                 stdout=log, stderr=subprocess.STDOUT)
        def wait_phase(expected):
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                assert child.poll() is None, log_path.read_text(encoding='utf-8', errors='replace')
                with factory() as db:
                    row = db.scalar(select(WorkerInstance))
                    if row and row.current_phase == expected:
                        return row.worker_id
                time.sleep(.1)
            raise AssertionError(log_path.read_text(encoding='utf-8', errors='replace'))
        try:
            worker_id = wait_phase('idle')
            assert activate(prep, candidate).status_code == 200
            original = (candidate[1] / 'main.py').read_bytes()
            (candidate[1] / 'main.py').write_bytes(original + b'\n# changed after activation')
            assert wait_phase('configuration_blocked') == worker_id
            (candidate[1] / 'main.py').write_bytes(original)
            assert wait_phase('idle') == worker_id
            with factory() as db:
                assert db.scalar(select(Job)) is None
                assert len(db.scalars(select(WorkerInstance)).all()) == 1
        finally:
            child.terminate()
            child.wait(timeout=15)
