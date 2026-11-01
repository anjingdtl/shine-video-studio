"""Cold-start orchestration contracts; synthetic files, never production inference."""
# ruff: noqa: F811
from datetime import timedelta

import pytest
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops import deployment
from shine.comfyops.instances import instance_root
from shine.comfyops.models import ComfyInstance, ModelPackage, NodePackage, ResourceLease
from shine.models import utcnow
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401


def seed(prep, *, mode='local', port=8188, process=None):
    from uuid import NAMESPACE_URL, uuid5

    client, factory, settings = prep
    project = create_project(client)
    catalog = deployment.load_default_catalog()
    with factory() as db:
        deployment.register_catalog_packages(db, catalog, list(deployment._component_map(catalog)))
        for cls in (ModelPackage, NodePackage):
            for row in db.scalars(select(cls)):
                row.status = 'FILES_VERIFIED_PENDING_RUNTIME'
        root = instance_root(settings, 'shine-h3', mode)
        (root / '.venv/Scripts').mkdir(parents=True)
        (root / '.venv/Scripts/python.exe').write_bytes(b'SYNTHETIC - NEVER EXECUTE')
        (root / 'main.py').write_text('# contract fixture', encoding='utf-8')
        components = deployment._component_map(catalog)
        payload = {'managed': True, 'name': 'shine-h3', 'port': port, 'root': str(root),
                   'is_mock': mode == 'fake',
                   'node_versions': {cid: c['revision'] for cid, c in components.items() if c['kind'] == 'node'},
                   'model_bindings': {c['destination_family']: cid for cid, c in components.items()
                                      if c['kind'] == 'model' and c.get('destination_family')},
                   'process': process}
        identity = str(uuid5(NAMESPACE_URL, f'shine-comfy-instance:{mode}:http://127.0.0.1:{port}'))
        db.add(ComfyInstance(id=identity, payload=payload, sha256=graph_hash(payload),
                             status='RUNNING_PENDING_HEALTH' if process else 'STOPPED'))
        db.commit()
    return project, root, identity


def test_prepared_instance_does_not_start_an_unfenced_renderer(prep):
    project, _, _ = seed(prep)
    client, _, _ = prep
    response = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project})
    assert response.status_code == 200, response.text
    assert response.json()['complete'] is True
    assert response.json()['action'] is None
    assert response.json()['next_step'] == 'GENERATE_CONFIGURATION'


@pytest.mark.parametrize('mode,port', [('fake', 8188), ('local', 8199)])
def test_matching_name_on_another_identity_is_not_completed(prep, mode, port):
    project, _, _ = seed(prep, mode=mode, port=port, process={'is_mock': True})
    client, _, _ = prep
    response = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project})
    if mode == 'local':
        # The same directory already belongs to another port. Refuse to reuse
        # its identity or replace its files; this is not a second CREATE target.
        assert response.status_code == 409, response.text
        assert response.json()['detail'] == 'INSTANCE_IDENTITY_CHANGED'
        return
    assert response.status_code == 200, response.text
    assert response.json()['complete'] is False
    assert response.json()['action'] == 'CREATE'


def test_legacy_running_instance_gets_an_owned_stop_plan(prep):
    project, _, _ = seed(prep, process={'pid': 12345, 'birth': 'fixture-not-a-live-process'})
    client, _, _ = prep
    response = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project})
    assert response.status_code == 200, response.text
    assert response.json()['complete'] is False
    assert response.json()['action'] == 'STOP'


@pytest.mark.parametrize('change,action', [('missing_node', 'INSTALL_NODE'), ('missing_model', 'BIND_MODELS')])
def test_partial_preparation_is_not_complete_and_retains_pinned_steps(prep, change, action):
    project, _, identity = seed(prep)
    client, factory, _ = prep
    from shine.comfyops.models import InstallationPlan
    with factory.begin() as db:
        row = db.get(ComfyInstance, identity)
        payload = dict(row.payload)
        key = 'node_versions' if change == 'missing_node' else 'model_bindings'
        values = dict(payload[key])
        removed = next(iter(values))
        del values[removed]
        payload[key] = values
        row.payload, row.sha256 = payload, graph_hash(payload)
    response = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project})
    assert response.status_code == 200, response.text
    assert response.json()['action'] == action and not response.json()['complete']
    if change == 'missing_node':
        with factory() as db:
            plan = db.get(InstallationPlan, response.json()['plan_id'])
            assert plan.payload['operation']['package_ids'] == [deployment.WHEEL_COMPONENT_ID, removed]


@pytest.mark.parametrize('change', ['wrong_revision', 'wrong_binding', 'needs_review', 'wrong_root'])
def test_changed_or_ambiguous_instance_does_not_borrow_completion(prep, change):
    project, _, identity = seed(prep)
    client, factory, settings = prep
    with factory.begin() as db:
        row = db.get(ComfyInstance, identity)
        payload = dict(row.payload)
        if change == 'wrong_revision':
            payload['node_versions'] = {**payload['node_versions'], 'ComfyUI-KJNodes': 'unapproved-revision'}
        elif change == 'wrong_binding':
            payload['model_bindings'] = {**payload['model_bindings'], 'diffusion_models': 'another-model'}
        elif change == 'wrong_root':
            payload['root'] = str(settings.data_dir / 'other-directory')
        else:
            row.status = 'NEEDS_REVIEW'
        row.payload, row.sha256 = payload, graph_hash(payload)
    response = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project})
    assert response.status_code == 409, response.text


@pytest.mark.parametrize('blocker', ['process', 'ACTIVE', 'RECONCILE_REQUIRED', 'fake'])
def test_configuration_cannot_mutate_a_running_or_fake_runtime(prep, monkeypatch, blocker):
    project, root, _ = seed(prep, mode='fake' if blocker == 'fake' else 'local',
                            process={'pid': 12345, 'birth': 'fixture'} if blocker == 'process' else None)
    client, factory, settings = prep
    monkeypatch.setattr(deployment, 'ENV_PATH', settings.data_dir / 'isolated-env')
    monkeypatch.setattr('shine.comfyops.host_env.probe_gpu', lambda: {'status': 'OK', 'uuid': 'GPU-TEST'})
    if blocker in {'ACTIVE', 'RECONCILE_REQUIRED'}:
        with factory.begin() as db:
            db.add(ResourceLease(owner='test-runtime', gpu='gpu-0', budget_mb=1, exclusive=True,
                                 priority=1, status=blocker, expires_at=utcnow() - timedelta(seconds=1)))
    response = client.post('/api/v1/setup/configuration', json={'project_id': project}, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert not (root / 'custom_nodes/shine_comfy_handoff').exists()
    assert not (settings.comfyops_dir / 'setup/managed_recipe.json').exists()
    assert not deployment.ENV_PATH.exists()
