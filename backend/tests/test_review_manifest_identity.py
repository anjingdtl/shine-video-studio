
import hashlib
import importlib.util
from pathlib import Path

import pytest

from tests.review_fixtures import fixture_style_input_checks


@pytest.fixture
def executor():
    spec = importlib.util.spec_from_file_location(
        'review_manifest_identity', Path(__file__).resolve().parents[2] / 'scripts/local_review_server.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(tmp_path):
    files = []
    for name in ['config.json', 'model.safetensors', 'tokenizer.json']:
        content = ('synthetic:' + name).encode()
        (tmp_path / name).write_bytes(content)
        files.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
    return {'model_root': str(tmp_path), 'model_id': 'fixture-only', 'files': files, 'sha256': 'untrusted-label'}


def test_identity_uses_verified_files_not_supplied_label_or_absolute_root(executor, tmp_path):
    spec = manifest(tmp_path)
    actual = executor.verified_model_identity(spec)
    spec['sha256'] = 'a' * 64
    spec['files'].reverse()
    assert executor.verified_model_identity(spec) == actual
    assert actual['model_id'] == 'fixture-only'
    assert len(actual['files_sha256']) == 64
    assert actual['file_count'] == 3
    assert str(tmp_path) not in str(actual)
    assert 'untrusted-label' not in str(actual)


def test_identity_changes_for_a_new_verified_model_revision(executor, tmp_path):
    spec = manifest(tmp_path)
    before = executor.verified_model_identity(spec)
    content = b'X' * (tmp_path / 'model.safetensors').stat().st_size
    (tmp_path / 'model.safetensors').write_bytes(content)
    with pytest.raises(ValueError, match='MODEL_HASH_CHANGED'):
        executor.verified_model_identity(spec)
    entry = next(f for f in spec['files'] if f['path'] == 'model.safetensors')
    entry.update(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
    assert executor.verified_model_identity(spec)['files_sha256'] != before['files_sha256']


def test_declared_size_is_checked_not_just_echoed(executor, tmp_path):
    spec = manifest(tmp_path)
    spec['files'][0]['bytes'] += 1
    with pytest.raises(ValueError, match='MODEL_SIZE_CHANGED'):
        executor.verified_model_identity(spec)


def test_ambiguous_file_aliases_are_rejected(executor, tmp_path):
    spec = manifest(tmp_path)
    spec['files'].append({**spec['files'][0], 'path': './config.json'})
    with pytest.raises(ValueError, match='DUPLICATE_MODEL_FILE'):
        executor.verified_model_identity(spec)


@pytest.mark.parametrize('role', ['VLM', 'ASR'])
@pytest.mark.parametrize('proof_kind', [
    'valid', 'missing', 'wrong-model', 'malformed', 'mock-envelope', 'wrong-envelope-model', 'mock-no-proof',
])
def test_local_executor_response_identity_is_bound_to_completed_call(api, tmp_path, monkeypatch, role, proof_kind):
    import json

    import httpx
    from sqlalchemy import select

    from shine.comfyops.model_calls import TrackedProvider
    from shine.comfyops.models import ResourceLease
    from shine.llm.base import ProviderConfig
    from shine.models import AgentTaskRecord
    from shine.quality.semantic import LocalASRReviewer, LocalVisionReviewer, ReceivedReviewError
    from tests.conftest import create_project
    from tests.test_semantic_qc import context

    client, factory, _ = api
    project = create_project(client)
    identity = dict(schema_version='1', model_id='fixture-only', files_sha256='a'*64,
                    runtime_sha256='b'*64, file_count=3, bytes=200)
    if proof_kind == 'wrong-model':
        identity['model_id'] = 'another-checkpoint'
    if proof_kind == 'malformed':
        identity['runtime_sha256'] = 'operator-supplied-label'
    body = {'is_mock': proof_kind in {'mock-envelope', 'mock-no-proof'}, 'model': 'fixture-only'}
    if proof_kind == 'wrong-envelope-model':
        body['model'] = 'different-model'
    if proof_kind not in {'missing', 'mock-no-proof'}:
        body['executor_identity'] = identity
    if role == 'VLM':
        body['choices'] = [{'finish_reason': 'stop', 'message': {'content': json.dumps(
            dict(reviewer='fixture', is_mock=False, input_sha256='a'*64, findings=[],
                 verdict='passed', overall_confidence=1, raw_summary='fixture only',
                 style_input_checks=fixture_style_input_checks()))}}]
    else:
        body.update(text='fixture transcript', segments=[])
    calls = []

    def respond(request):
        if request.url.path == '/free':
            return httpx.Response(200, json={'released': True, 'allocated_bytes': 0})
        calls.append(request)
        return httpx.Response(200, json=body)

    original = httpx.Client
    monkeypatch.setattr('shine.quality.semantic.httpx.Client',
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    config = ProviderConfig(profile_id='fixture-profile', name='fixture', kind='openai_compatible',
                            base_url='http://127.0.0.1:8116', model='fixture-only', api_key=None)
    provider = (LocalVisionReviewer if role == 'VLM' else LocalASRReviewer)(config)
    wrapped = TrackedProvider(provider, factory, project)
    source = tmp_path / 'synthetic-audio.wav'
    source.write_bytes(b'fake audio for mocked transport only')
    action = (lambda: wrapped.review(context(tmp_path))) if role == 'VLM' else (lambda: wrapped.transcribe(source))
    invalid = proof_kind in {'wrong-model', 'malformed', 'mock-envelope', 'wrong-envelope-model'}
    if invalid:
        with pytest.raises(ReceivedReviewError, match='EXECUTOR_IDENTITY_INVALID'):
            action()
    else:
        action()
    assert len(calls) == 1
    with factory() as db:
        task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.role_id.like('model_call:%')))
        assert db.get(ResourceLease, task.trace['resource_id']).status == 'RELEASED'
        assert task.status == ('FAILED' if invalid else 'COMPLETED')
        if not invalid:
            assert task.trace['executor_identity'] == (None if proof_kind in {'missing', 'mock-no-proof'} else identity)
            assert task.trace['is_mock'] == (proof_kind == 'mock-no-proof')
        else:
            assert task.trace['received_response']['retryable'] is False


def test_optional_identity_never_reuses_previous_response_proof():
    from shine.llm.base import ProviderConfig
    from shine.quality.semantic import LocalASRReviewer

    config = ProviderConfig(profile_id='p', name='fixture', kind='openai_compatible',
                            base_url='http://127.0.0.1:8116', model='fixture-only', api_key=None)
    provider = LocalASRReviewer(config)
    identity = dict(schema_version='1', model_id='fixture-only', files_sha256='a'*64,
                    runtime_sha256='b'*64, file_count=3, bytes=200)
    provider.record_executor_identity(
        {'is_mock': False, 'model': 'fixture-only', 'executor_identity': identity}, 'a'*64
    )
    assert provider.last_executor_identity == identity
    provider.record_executor_identity({}, 'b'*64)
    assert provider.last_executor_identity is None


@pytest.mark.parametrize('role', ['VLM', 'ASR'])
@pytest.mark.parametrize('mutation', ['changed-weight', 'undeclared-tokenizer'])
def test_cold_load_rechecks_bytes_after_startup_before_loading_any_model(
    executor, tmp_path, monkeypatch, role, mutation
):
    import importlib.metadata
    import sys
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    image = SimpleNamespace(width=4, height=4)
    image.convert = lambda _: image
    monkeypatch.setitem(sys.modules, 'PIL', SimpleNamespace(Image=SimpleNamespace(open=lambda _: image)))
    spec = manifest(tmp_path)
    asr = tmp_path / 'asr'
    asr.mkdir()
    spec['asr'] = manifest(asr)
    spec['asr']['model_id'] = 'asr-fixture'
    loads = []

    def forbidden_load(*args, **kwargs):
        loads.append(1)
        raise AssertionError('Changed weights must be rejected before model loading')

    fake_transformers = SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=forbidden_load),
        Qwen3VLForConditionalGeneration=SimpleNamespace(from_pretrained=forbidden_load),
        pipeline=forbidden_load,
    )
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(mem_get_info=lambda: (16*1024**3, 16*1024**3)))
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    monkeypatch.setitem(sys.modules, 'transformers', fake_transformers)
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: 'fixture-only')
    from shine.quality.semantic import review_schema_hash, vision_review_schema
    schema = vision_review_schema()
    monkeypatch.setattr(executor, 'decoder_runtime_identity', lambda: {
        'protocol': 'local-review-json-schema-v1', 'decoder_sha256': 'd'*64,
        'schema_sha256': review_schema_hash(schema),
    })
    app = executor.build_app(spec)
    client = TestClient(app)
    assert client.get("/identity").status_code == 200
    changed = tmp_path if role == 'VLM' else asr
    if mutation=='changed-weight':
        (changed / 'model.safetensors').write_bytes(b'changed-after-startup')
    else:
        (changed / 'added_tokens.json').write_text('{}',encoding='utf8')
    assert client.get("/identity").status_code == 409
    if role == 'VLM':
        import base64

        # The decoder and model are mocked; this verifies file-integrity admission only.
        payload = {'model': 'fixture-only', 'response_format': {'type': 'json_schema',
                   'json_schema': {'strict': True, 'schema': schema}}, 'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' +
                                              base64.b64encode(b'mocked image bytes').decode()}}
        ]}]}
        def action():
            return client.post('/v1/chat/completions', json=payload)
    else:
        outputs = iter([SimpleNamespace(stdout=b'{"format":{"duration":"1"}}'), SimpleNamespace(stdout=b'')])
        monkeypatch.setattr(executor.subprocess, 'run', lambda *args, **kwargs: next(outputs))
        monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace())
        def action():
            return client.post('/v1/audio/transcriptions', data={'model': 'asr-fixture'},
                               files={'file': ('fixture.wav', b'fake mocked audio', 'audio/wav')})
    error='MODEL_(SIZE|HASH)_CHANGED' if mutation=='changed-weight' else 'UNDECLARED_MODEL_RUNTIME_FILE'
    with pytest.raises(ValueError, match=error):
        action()
    assert not loads


def sharded_manifest(tmp_path, *, weight_map=None, extras=()):
    import json

    mapping = weight_map if weight_map is not None else {
        'layer.a': 'model-00001-of-00002.safetensors',
        'layer.b': 'model-00002-of-00002.safetensors',
    }
    contents = {
        'config.json': b'{}', 'tokenizer.json': b'{}',
        'model-00001-of-00002.safetensors': b'synthetic first shard',
        'model-00002-of-00002.safetensors': b'synthetic second shard',
        'model.safetensors.index.json': json.dumps({'weight_map': mapping}).encode(),
        **{name: b'synthetic extra weight' for name in extras},
    }
    files = []
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
        files.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    return {'model_root': str(tmp_path), 'model_id': 'sharded-fixture-only', 'files': files}


def test_sharded_model_identity_covers_index_and_every_actual_shard(executor, tmp_path):
    spec = sharded_manifest(tmp_path)
    proof = executor.verified_model_identity(spec)
    assert proof['file_count'] == 5
    (tmp_path / 'model-00002-of-00002.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='MODEL_(SIZE|HASH)_CHANGED'):
        executor.verified_model_identity(spec)


@pytest.mark.parametrize('weight_map', [
    {}, {'layer': '../unchecked.safetensors'}, {'layer': 'missing.safetensors'},
    {'layer': 'config.json'}, {'layer': 12}, ['not-a-map'],
    {'layer': 'model-00001-of-00002.safetensors'},
])
def test_shard_index_cannot_load_unchecked_or_ambiguous_weight_files(executor, tmp_path, weight_map):
    spec = sharded_manifest(tmp_path, weight_map=weight_map)
    with pytest.raises(ValueError, match='MODEL_SHARD_INDEX_INVALID'):
        executor.verified_model_identity(spec)


def test_monolithic_and_sharded_weights_cannot_be_mixed(executor, tmp_path):
    spec = sharded_manifest(tmp_path, extras=('model.safetensors',))
    with pytest.raises(ValueError, match='MODEL_SHARD_INDEX_INVALID'):
        executor.verified_model_identity(spec)


def test_native_binding_accepts_a_complete_sharded_runtime_without_monolithic_weights():
    from shine.comfyops.native import NativeProviderBinding

    binding = NativeProviderBinding(
        profile_id='fixture-only', kind='openai_compatible', model='fixture-only',
        base_url_sha256='a' * 64, model_package_id='fixture-only',
        executor_identity={'schema_version': '1', 'model_id': 'fixture-only', 'runtime_sha256': 'b' * 64,
                           'files_sha256': 'c' * 64, 'file_count': 5, 'bytes': 200},
        runtime_files=['config.json', 'tokenizer.json', 'model.safetensors.index.json',
                       'model-00001-of-00002.safetensors', 'model-00002-of-00002.safetensors'])
    assert 'model.safetensors.index.json' in binding.runtime_files


@pytest.mark.parametrize('extra', ['model.safetensors', 'pytorch_model.bin', 'adapter_config.json',
                                   'added_tokens.json', 'chat_template.jinja', 'generation_config.json', 'VOCAB.TXT'])
def test_unlisted_root_file_cannot_override_checked_shards_or_processor(executor, tmp_path, extra):
    spec = sharded_manifest(tmp_path)
    (tmp_path / extra).write_bytes(b'unlisted loader input')
    with pytest.raises(ValueError, match='UNDECLARED_MODEL_RUNTIME_FILE'):
        executor.verified_model_identity(spec)


@pytest.mark.parametrize('extra', ['adapter_config.json', 'added_tokens.json', 'generation_config.json'])
def test_unlisted_runtime_configuration_also_blocks_monolithic_models(executor, tmp_path, extra):
    spec = manifest(tmp_path)
    (tmp_path / extra).write_bytes(b'unlisted loader input')
    with pytest.raises(ValueError, match='UNDECLARED_MODEL_RUNTIME_FILE'):
        executor.verified_model_identity(spec)


def test_local_audit_metadata_remains_outside_runtime_identity(executor, tmp_path):
    spec = manifest(tmp_path)
    before = executor.verified_model_identity(spec)
    (tmp_path / 'source-audit.json').write_text('{"scope":"local note, not loader configuration"}', encoding='utf8')
    assert executor.verified_model_identity(spec) == before


def test_declared_peft_redirect_cannot_escape_the_verified_full_model_scope(executor, tmp_path):
    import json

    spec = manifest(tmp_path)
    data = json.dumps({'base_model_name_or_path': '../unscoped-model'}).encode()
    (tmp_path / 'adapter_config.json').write_bytes(data)
    spec['files'].append({'path': 'adapter_config.json', 'sha256': hashlib.sha256(data).hexdigest(),
                          'bytes': len(data)})
    with pytest.raises(ValueError, match='UNSUPPORTED_MODEL_ADAPTER_LAYOUT'):
        executor.verified_model_identity(spec)
