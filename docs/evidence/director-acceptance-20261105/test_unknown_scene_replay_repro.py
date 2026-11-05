"""A01 regression, inverted after the fix.

The original acceptance script asserted that the defect was PRESENT: two
identical ``scenes/prepare`` requests produced two provider calls and two
UNKNOWN task records.  The repair makes the input identity durable and the
claim atomic, so the second request must return the ORIGINAL task instead of
paying for the same call twice.

Run it the same way the acceptance round did (it needs the backend test
fixtures, so it is executed from ``backend``):

    uv run --project backend --locked pytest -c backend/pyproject.toml \
        ../docs/evidence/director-acceptance-20261105/test_unknown_scene_replay_repro.py -q

The assertion below is the product contract:

* the provider is called exactly once;
* exactly one UNKNOWN task exists for the project;
* the second response carries that same task id, reports UNKNOWN and says
  that no automatic replay happened.
"""
import sys
from pathlib import Path

from sqlalchemy import select

# The backend test fixtures live with the backend suite; this evidence script
# lives with the acceptance record. Import them explicitly so the script stays
# runnable on its own instead of only when copied into backend/tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from shine.models import AgentTaskRecord  # noqa: E402
from tests.conftest import api  # noqa: E402,F401  # pytest fixture


def test_unknown_scene_is_not_replayed_on_second_request(api, monkeypatch):
    client, factory, _ = api
    monkeypatch.setenv('SHINE_AGENT_STEP_DELAY_MS', '0')
    project = client.post('/api/v1/projects', json={'title': 'acceptance-isolated'}).json()['id']
    profile = client.post('/api/v1/provider-profiles',
                           json={'name': 'review-writer', 'kind': 'mock', 'model': 'writer'}).json()['id']
    client.put('/api/v1/agent-routes', json={'role': 'scriptwriter', 'provider_profile_id': profile})
    doc = client.post(f'/api/v1/projects/{project}/documents',
                      files={'file': ('script.txt', '场景一：房间\n阿德：出发。'.encode(), 'text/plain')}).json()
    calls = []

    def responder(messages, schema):
        calls.append(1)
        raise RuntimeError('simulated-response-lost')

    client.app.state.mock_llm_responder = responder
    path = f'/api/v1/projects/{project}/material-preparation/scenes/prepare'
    body = {'document_id': doc['id'], 'expected_document_hash': doc['sha256']}
    first = client.post(path, json=body, headers={'x-shine-local-action': '1'})
    second = client.post(path, json=body, headers={'x-shine-local-action': '1'})

    with factory() as db:
        unknown = list(db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project, AgentTaskRecord.status == 'NEEDS_REVIEW')))

    print('REPRO', first.status_code, second.status_code,
          'provider_calls', len(calls), 'unknown_tasks', len(unknown))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # The provider was called once. The second request did not pay for it again.
    assert len(calls) == 1, 'an UNKNOWN outcome must never be replayed automatically'
    assert len(unknown) == 1, 'the unknown result must be preserved, not duplicated'

    first_scene = first.json()['scenes'][0]
    second_scene = second.json()['scenes'][0]
    assert first_scene['status'] == second_scene['status'] == 'NEEDS_REVIEW'
    assert second_scene['task_id'] == unknown[0].id
    assert second_scene['external_outcome'] == 'UNKNOWN'
    assert second_scene['automatic_replay'] is False