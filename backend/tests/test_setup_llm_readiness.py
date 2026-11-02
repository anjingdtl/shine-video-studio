"""Core LLM configuration uses real CRUD/routes and isolates only the provider probe."""
import pytest

from shine.comfyops.llm_readiness import TEXT_ROLES
from shine.llm.base import LLMError
from shine.llm.gateway import LLMGateway


def configured(api, monkeypatch, kind='openai_compatible'):
    client, _, _ = api
    profile = client.post('/api/v1/provider-profiles', json={'name': 'core text', 'kind': kind,
        'base_url': 'http://127.0.0.1:9/v1', 'model': 'fixture-model'}).json()
    for role in TEXT_ROLES:
        response = client.put('/api/v1/agent-routes', json={'role': role, 'provider_profile_id': profile['id']})
        assert response.status_code == 200, response.text
    monkeypatch.setattr(LLMGateway, 'test_profile', lambda *args, **kwargs: {
        'available': True, 'model_listed': True, 'json_mode': True})
    return client, profile


def test_empty_machine_requires_llm_without_creating_or_routing_any_provider(api):
    client, _, _ = api
    report = client.get('/api/v1/setup/llm-readiness').json()
    assert report['status'] == 'BLOCKED'
    assert {row['role'] for row in report['roles']} == set(TEXT_ROLES)
    assert all(row['code'] == 'LLM_ROUTE_REQUIRED' for row in report['roles'])
    assert client.get('/api/v1/provider-profiles').json() == []
    assert client.get('/api/v1/agent-routes').json() == []


def test_route_and_connection_tests_are_both_required_and_no_secrets_returned(api, monkeypatch):
    client, profile = configured(api, monkeypatch)
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'BLOCKED'
    assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()['ok']
    report = client.get('/api/v1/setup/llm-readiness').json()
    assert report['status'] == 'READY'
    assert report['verification'] == 'CONNECTION_ONLY_NOT_GENERATION'
    assert all(row['code'] is None for row in report['roles'])
    assert 'api_key' not in str(report)
    client.delete('/api/v1/agent-routes/director')
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'BLOCKED'


@pytest.mark.parametrize('change', [{'model': 'changed'}, {'base_url': 'http://127.0.0.1:8/v1'}, {'enabled': False}])
def test_changed_configuration_invalidates_prior_connection_evidence(api, monkeypatch, change):
    client, profile = configured(api, monkeypatch)
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'READY'
    assert client.put(f"/api/v1/provider-profiles/{profile['id']}", json=change).status_code == 200
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'BLOCKED'


def test_mock_and_missing_model_cannot_complete_real_preparation(api, monkeypatch):
    client, profile = configured(api, monkeypatch, kind='mock')
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'BLOCKED'
    client.put(f"/api/v1/provider-profiles/{profile['id']}", json={'kind': 'openai_compatible'})
    monkeypatch.setattr(LLMGateway, 'test_profile', lambda *args, **kwargs: {'available': True, 'model_listed': False})
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'BLOCKED'


def test_failed_retest_clears_previous_success(api, monkeypatch):
    client, profile = configured(api, monkeypatch)
    client.post(f"/api/v1/provider-profiles/{profile['id']}/test")
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'READY'
    def unavailable(*args, **kwargs):
        raise LLMError('isolated provider offline')
    monkeypatch.setattr(LLMGateway, 'test_profile', unavailable)
    assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()['ok'] is False
    assert client.get('/api/v1/setup/llm-readiness').json()['status'] == 'BLOCKED'
