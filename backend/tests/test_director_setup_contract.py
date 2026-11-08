"""Shipped TypeScript -> real FastAPI/Pydantic/SQLite, external executor ports only isolated."""
# ruff: noqa: F811
import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from shine.comfy.workflow import graph_hash
from shine.comfyops import instances
from shine.comfyops.models import InstallationPlan
from shine.models import utcnow
from tests.conftest import create_project
from tests.test_setup_cold_start import seed
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

LOCAL = {'X-Shine-Local-Action': '1'}
REPO = Path(__file__).resolve().parents[2]


def bridge(client, plan):
    process = subprocess.Popen(['node', str(REPO / 'scripts/setup_contract_bridge.mjs'), json.dumps(plan)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8')
    requests = []
    try:
        for line in process.stdout:
            message = json.loads(line)
            if 'request' not in message:
                assert 'error' not in message, message
                return message['result'], requests
            req = message['request']
            requests.append(req)
            response = client.request(req['method'], req['url'], json=req['body'], headers=req['headers'])
            process.stdin.write(json.dumps({'status': response.status_code, 'body': response.json()}) + '\n')
            process.stdin.flush()
        raise AssertionError(process.stderr.read())
    finally:
        process.stdin.close()
        process.wait(timeout=15)
        assert process.returncode == 0, process.stderr.read()


def install_plan(prep):
    client, _, _ = prep
    response = client.post('/api/v1/setup/install-plan', json={
        'project_id': create_project(client), 'components': ['comfyui'], 'disk_free': 10**15})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, plan):
    grant = client.post('/api/v1/preparation/approval-grants', headers=LOCAL,
                        json={'action': 'install', 'target_id': plan['plan_id']}).json()
    response = client.post(f"/api/v1/comfyops/install-plans/{plan['plan_id']}/approve", headers=LOCAL,
                           json={k: grant[k] for k in ('grant_id', 'snapshot')} | {'evidence': '本机合同测试明确批准'})
    assert response.status_code == 200, response.text
    return response.json()


def test_shipped_service_uses_real_install_contract(prep, monkeypatch):
    client, factory, _ = prep
    monkeypatch.setenv('SHINE_ENABLE_LOCAL_EXECUTION', '1')

    class IsolatedInstaller:
        def install(self, spec):
            return {'status': 'AVAILABLE', 'files': [f.model_dump() for f in spec.files]}

    monkeypatch.setattr('shine.comfyops.installer.LocalInstaller', lambda *args: IsolatedInstaller())
    plan = install_plan(prep)
    result, requests = bridge(client, plan)
    # The installation is a persistent task: the shipped TypeScript queues it
    # and re-attaches by attempt id instead of waiting inside one request.
    assert result['status'] == 'QUEUED' and result['attempt_id']
    assert [r['url'].split('/')[-1] for r in requests] == ['execution-context', 'approval-grants', 'approve', 'execute']
    assert requests[1]['body']['action'] == 'install'
    assert set(requests[2]['body']) == {'grant_id', 'snapshot', 'evidence'}
    with factory() as db:
        row = db.get(InstallationPlan, plan['plan_id'])
        assert row.payload['approved_by'] == 'local-user'
        assert requests[-1]['body'] == {'plan_hash': row.sha256, 'executor': 'local'}
        assert row.sha256 != plan['sha256']
    # A repeated execute is the same receipt: queueing is re-attachable, so a
    # lost response never becomes a second physical installation.
    replay = client.post(requests[-1]['url'], json=requests[-1]['body'], headers=LOCAL)
    assert replay.status_code == 202, replay.text
    assert replay.json()['attempt_id'] == result['attempt_id']


def test_shipped_service_uses_real_instance_approval_and_state_machine(prep, monkeypatch):
    client, factory, _ = prep
    owner = {'pid': 99999999, 'birth': 'synthetic-port-only'}
    project, _, _ = seed(prep, process=owner)
    monkeypatch.setenv('SHINE_ENABLE_LOCAL_EXECUTION', '1')
    # No process is stopped: isolate only the OS stop and HTTP idle probe ports.
    monkeypatch.setattr(instances.ProcessRunner, 'stop', lambda self, value: None)
    monkeypatch.setattr(instances, 'require_idle', lambda *args: {})
    response = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project})
    assert response.status_code == 200, response.text
    plan = response.json() | {'kind': 'INSTANCE_LIFECYCLE'}
    again = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project}).json()
    assert again['plan_id'] == plan['plan_id']
    result, requests = bridge(client, plan)
    assert result['status'] == 'COMPLETED'
    assert result['payload']['result']['process'] is None
    assert '/instance-plans/' in requests[-1]['url']
    assert set(requests[-1]['body']) == {'plan_hash'}
    assert requests[-1]['body']['plan_hash'] != plan['sha256']
    with factory() as db:
        assert db.get(InstallationPlan, plan['plan_id']).status == 'STOPPED'


@pytest.mark.parametrize('invalid', ['action', 'evidence', 'hash', 'old_hash', 'permission', 'actor', 'expired'])
def test_real_routes_reject_bad_install_contract(prep, monkeypatch, invalid):
    client, factory, _ = prep
    monkeypatch.setenv('SHINE_ENABLE_LOCAL_EXECUTION', '1')
    plan = install_plan(prep)
    if invalid == 'action':
        response = client.post('/api/v1/preparation/approval-grants', headers=LOCAL,
                               json={'action': 'install_approve', 'target_id': plan['plan_id']})
        assert response.status_code == 422
        return
    if invalid == 'evidence':
        grant = client.post('/api/v1/preparation/approval-grants', headers=LOCAL,
                            json={'action': 'install', 'target_id': plan['plan_id']}).json()
        response = client.post(f"/api/v1/comfyops/install-plans/{plan['plan_id']}/approve", headers=LOCAL,
                               json={k: grant[k] for k in ('grant_id', 'snapshot')})
        assert response.status_code == 422
        return
    approved = approve(client, plan)
    if invalid == 'permission':
        monkeypatch.delenv('SHINE_ENABLE_LOCAL_EXECUTION')
    if invalid == 'expired':
        with factory.begin() as db:
            row = db.get(InstallationPlan, plan['plan_id'])
            row.payload = {**row.payload, 'execute_before': (utcnow() - timedelta(minutes=1)).isoformat()}
            row.sha256 = graph_hash(row.payload)
            approved['sha256'] = row.sha256
    body = {'executor': 'local'}
    if invalid != 'hash':
        body['plan_hash'] = plan['sha256'] if invalid == 'old_hash' else approved['sha256']
    response = client.post(f"/api/v1/comfyops/install-plans/{plan['plan_id']}/execute", json=body,
                           headers=HEADERS if invalid == 'actor' else LOCAL)
    assert response.status_code == (422 if invalid == 'hash' else 409), response.text


@pytest.mark.parametrize('status', ['AWAITING_APPROVAL', 'NEEDS_REVIEW', 'INSTALLING'])
def test_instance_cannot_execute_without_approval_or_replay_unknown(prep, status):
    client, factory, _ = prep
    project, _, _ = seed(prep, process={'pid': 99999999, 'birth': 'synthetic'})
    plan = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project}).json()
    with factory.begin() as db:
        db.get(InstallationPlan, plan['plan_id']).status = status
    response = client.post(f"/api/v1/comfyops/instance-plans/{plan['plan_id']}/execute", headers=LOCAL,
                           json={'plan_hash': plan['sha256']})
    assert response.status_code == 409
    if status != 'AWAITING_APPROVAL':
        assert client.post('/api/v1/setup/next-instance-plan', json={'project_id': project}).status_code == 409
