"""Synthetic files only: no installed ComfyUI execution, downloads or model inference."""

# ruff: noqa: F811
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from shine.comfyops import deployment
from shine.comfyops import existing_installation as existing
from tests.conftest import create_project
from tests.test_v13_preparation import post, prep  # noqa: F401


def installation(tmp_path):
    root = tmp_path / 'portable' / 'ComfyUI'
    (root / 'comfy').mkdir(parents=True)
    (root / 'main.py').write_text('raise RuntimeError("must never execute")')
    (root / 'models' / 'unet').mkdir(parents=True)
    return root


def catalog(data=b'correct model'):
    return {'catalog_id': 'test', 'catalog_version': '1', 'components': [{
        'component_id': 'model', 'kind': 'model', 'revision': 'test-revision',
        'license_id': 'TEST-ONLY', 'usage': 'research', 'dependencies': [],
        'files': [{
            'relative_path': 'weights.safetensors', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'url': 'https://huggingface.co/fixture/model/resolve/test-revision/weights.safetensors',
            'redirect_hosts': [],
        }],
    }]}


def test_discovery_finds_portable_at_common_depth_with_bounded_scan(tmp_path):
    parent = tmp_path / 'AIGC2026' / 'ComfyUI_bundle'
    root = installation(parent)
    # Put the portable root in the shape found in common Windows bundles.
    (parent / 'portable').rename(parent / 'ComfyUI_renamed')
    root = parent / 'ComfyUI_renamed' / 'ComfyUI'
    result = existing.discover([str(root.parent)], drives=[tmp_path])
    assert str(root) in result['installations']
    assert result['coverage'] == 'BOUNDED_RECURSIVE_SCAN'
    assert result['runtime_verified'] is False
    assert result['scan']['truncated'] is False
    # Direct portable layout is found automatically without a configured root.
    simple = tmp_path / 'AIGC2026' / 'ComfyUI_simple'
    (simple / 'ComfyUI' / 'comfy').mkdir(parents=True)
    (simple / 'ComfyUI' / 'main.py').touch()
    assert str(simple / 'ComfyUI') in existing.discover([], drives=[tmp_path])['installations']


def test_discovery_finds_deep_download_layout(tmp_path):
    """A download nested several levels down is still found by the bounded walk."""
    root = tmp_path / 'Downloads' / 'AIGC' / 'ComfyUI_windows_portable' / 'ComfyUI'
    (root / 'comfy').mkdir(parents=True)
    (root / 'main.py').write_text('raise RuntimeError("must never execute")')
    result = existing.discover([], drives=[tmp_path])
    assert str(root) in result['installations']
    assert result['scan']['directories_visited'] >= 4
    # A declared root is the person's explicit instruction, so it is walked too.
    assert str(root) in existing.discover([str(tmp_path / 'Downloads')], drives=[])['installations']


def test_discovery_skips_system_and_cache_trees(tmp_path):
    root = tmp_path / 'Windows' / 'ComfyUI'
    (root / 'comfy').mkdir(parents=True)
    (root / 'main.py').touch()
    assert existing.discover([], drives=[tmp_path])['installations'] == []


def test_discovery_endpoint_searches_the_chosen_root(prep, tmp_path):
    client, _, _ = prep
    root = tmp_path / 'Downloads' / 'bundle' / 'ComfyUI'
    (root / 'comfy').mkdir(parents=True)
    (root / 'main.py').touch()
    response = client.get('/api/v1/setup/existing-installations',
                          params={'root': str(tmp_path / 'Downloads')})
    assert response.status_code == 200, response.text
    body = response.json()
    assert str(root) in body['installations']
    assert body['coverage'] == 'BOUNDED_RECURSIVE_SCAN'


def test_inspection_is_read_only_and_size_never_means_verified(tmp_path):
    root = installation(tmp_path)
    source = root / 'models' / 'unet' / 'renamed.safetensors'
    source.write_bytes(b'wrong!! model')
    before = sorted(str(p) for p in tmp_path.rglob('*'))
    report = existing.inspect(str(root.parent), catalog())
    assert report['matches'][0]['status'] == 'PENDING_HASH'
    assert report['runtime_verified'] is False
    assert report['software']['installed'] is True
    assert report['software']['version_status'] == 'INSTALLED_VERSION_UNKNOWN'
    assert sorted(str(p) for p in tmp_path.rglob('*')) == before
    assert source.read_bytes() == b'wrong!! model'


def test_inspection_scans_model_roots_declared_by_the_installation(tmp_path):
    """Weights kept outside ComfyUI but declared in extra_model_paths.yaml are found."""
    root = installation(tmp_path)
    external = tmp_path / 'weights' / 'unet'
    external.mkdir(parents=True)
    (external / 'outside.safetensors').write_bytes(b'correct model')
    (root / 'extra_model_paths.yaml').write_text(
        'shared:\n'
        f'    base_path: {tmp_path / "weights"}\n'
        '    unet: unet\n',
        encoding='utf-8',
    )
    report = existing.inspect(str(root), catalog())
    assert report['matches'][0]['status'] == 'PENDING_HASH'
    assert str(external) in report['model_roots']


def test_inspection_ignores_declared_network_roots(tmp_path):
    root = installation(tmp_path)
    (root / 'extra_model_paths.yaml').write_text(
        'remote:\n    unet: \\\\server\\share\\unet\n', encoding='utf-8',
    )
    assert existing.inspect(str(root), catalog())['matches'] == []


def test_verify_confirms_matching_models_in_place_without_cache_copy(tmp_path):
    root = installation(tmp_path)
    source = root / 'models' / 'unet' / 'renamed.safetensors'
    source.write_bytes(b'correct model')
    report = existing.inspect(str(root), catalog())
    before = source.stat()

    result = existing.verify_models(report, report['snapshot'])

    assert result['status'] == 'INSTALLED_VERIFIED'
    assert result['verified_bytes'] == len(b'correct model')
    assert result['copied_bytes'] == 0
    assert result['verified'][0]['status'] == 'INSTALLED_HASH_MATCH'
    assert source.read_bytes() == b'correct model'
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    assert not (tmp_path / 'isolated-packages' / '.cache').exists()


def test_existing_comfyui_source_version_is_reported_without_running_python(tmp_path, monkeypatch):
    root = installation(tmp_path)
    (root / '.git').mkdir()
    expected = 'a' * 40

    def fake_run(args, **kwargs):
        if 'rev-parse' in args:
            return SimpleNamespace(returncode=0, stdout=expected + '\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(existing.subprocess, 'run', fake_run)
    report = existing.inspect(str(root), {
        **catalog(),
        'components': catalog()['components'] + [{
            'component_id': 'comfyui', 'kind': 'source', 'revision': expected, 'files': [],
        }],
    })

    assert report['software']['installed'] is True
    assert report['software']['version_status'] == 'INSTALLED_VERSION_MATCH'
    assert report['software']['actual_revision'] == expected
    assert report['software']['node_revisions_verified'] is False


def test_existing_comfyui_tracked_edits_are_not_reported_as_version_match(tmp_path, monkeypatch):
    root = installation(tmp_path)
    (root / '.git').mkdir()
    expected = 'b' * 40

    def fake_run(args, **kwargs):
        if 'rev-parse' in args:
            return SimpleNamespace(returncode=0, stdout=expected + '\n', stderr='')
        return SimpleNamespace(returncode=1, stdout='', stderr='')

    monkeypatch.setattr(existing.subprocess, 'run', fake_run)
    raw = catalog()
    raw['components'].append({'component_id': 'comfyui', 'kind': 'source', 'revision': expected, 'files': []})
    report = existing.inspect(str(root), raw)

    assert report['software']['version_status'] == 'INSTALLED_VERSION_MODIFIED'


def test_import_hashes_copied_bytes_rejects_mismatch_and_preserves_originals(prep, tmp_path):
    _, _, settings = prep
    root = installation(tmp_path)
    source = root / 'models' / 'unet' / 'renamed.safetensors'
    source.write_bytes(b'wrong!! model')
    report = existing.inspect(str(root), catalog())
    result = existing.import_models(settings, report, report['snapshot'])
    assert result['reused_bytes'] == 0
    assert result['rejected'][0]['status'] == 'HASH_MISMATCH'
    cache = settings.comfyops_dir / 'isolated-packages' / '.cache'
    assert list(cache.iterdir()) == []
    source.write_bytes(b'correct model')
    report = existing.inspect(str(root), catalog())
    result = existing.import_models(settings, report, report['snapshot'])
    assert result['reused_bytes'] == len(b'correct model')
    assert source.read_bytes() == b'correct model'
    assert (cache / report['matches'][0]['sha256']).read_bytes() == source.read_bytes()
    assert result['status'] == 'CACHED_NOT_INSTALLED'
    assert result['runtime_verified'] is False


def test_import_rejects_changed_snapshot(prep, tmp_path):
    _, _, settings = prep
    root = installation(tmp_path)
    report = existing.inspect(str(root), catalog())
    (root / 'models' / 'unet' / 'added').write_bytes(b'correct model')
    changed = existing.inspect(str(root), catalog())
    with pytest.raises(HTTPException, match='409'):
        existing.import_models(settings, changed, report['snapshot'])
    assert not (settings.comfyops_dir / 'isolated-packages' / '.cache').exists()


@pytest.mark.parametrize('path', ['relative/path', '//server/share', '\\\\server\\share'])
def test_network_and_relative_roots_rejected(path):
    with pytest.raises(HTTPException, match='422'):
        existing.local_path(path)


def test_endpoints_require_local_user_and_import_audit(prep, tmp_path, monkeypatch):
    client, factory, settings = prep
    root = installation(tmp_path)
    source = root / 'models' / 'unet' / 'weights'
    source.write_bytes(b'correct model')
    monkeypatch.setattr(deployment, 'load_default_catalog', catalog)
    post(client, '/setup/existing-installations/inspect', {'path': str(root)}, expected=403)
    report = post(client, '/setup/existing-installations/inspect', {'path': str(root)}, operator=True)
    response = client.post('/api/v1/setup/existing-installations/import-models', json={
        'path': str(root), 'snapshot': report['snapshot'],
    }, headers={'X-Shine-Local-Action': '1'})
    assert response.status_code == 200, response.text
    assert response.json()['reused_bytes'] == 13
    from sqlalchemy import select

    from shine.models import AuditEvent
    with factory() as db:
        assert any(row.event_type == 'EXISTING_COMFY_MODELS_CACHED' for row in db.scalars(select(AuditEvent)))


def test_verify_endpoint_requires_local_user_and_never_copies(prep, tmp_path, monkeypatch):
    client, _, _ = prep
    root = installation(tmp_path)
    (root / 'models' / 'unet' / 'weights').write_bytes(b'correct model')
    monkeypatch.setattr(deployment, 'load_default_catalog', catalog)
    report = post(client, '/setup/existing-installations/inspect', {'path': str(root)}, operator=True)

    response = client.post('/api/v1/setup/existing-installations/verify', json={
        'path': str(root), 'snapshot': report['snapshot'],
    }, headers={'X-Shine-Local-Action': '1'})
    assert response.status_code == 200, response.text
    assert response.json()['verified_bytes'] == 13
    assert response.json()['copied_bytes'] == 0
    assert response.json()['verified'][0]['status'] == 'INSTALLED_HASH_MATCH'
    assert response.json()['verification_reused'] is False

    def should_not_hash_again(*_args, **_kwargs):
        pytest.fail('a matching audit result must be reused without reading model bytes again')

    monkeypatch.setattr(existing, 'verify_models', should_not_hash_again)
    repeated = client.post('/api/v1/setup/existing-installations/verify', json={
        'path': str(root), 'snapshot': report['snapshot'],
    }, headers={'X-Shine-Local-Action': '1'})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['verified_bytes'] == 13
    assert repeated.json()['copied_bytes'] == 0
    assert repeated.json()['verification_reused'] is True


def test_verify_endpoint_reports_inflight_work_without_duplicate_hash(prep, tmp_path, monkeypatch):
    client, _, _ = prep
    root = installation(tmp_path)
    (root / 'models' / 'unet' / 'weights').write_bytes(b'correct model')
    monkeypatch.setattr(deployment, 'load_default_catalog', catalog)
    report = post(client, '/setup/existing-installations/inspect', {'path': str(root)}, operator=True)
    from shine import api_setup

    active_key = (report['root'].casefold(), report['snapshot'])
    monkeypatch.setattr(api_setup, '_active_installation_verifications', {active_key})

    def should_not_hash(*_args, **_kwargs):
        pytest.fail('an active verification must be polled instead of duplicated')

    monkeypatch.setattr(existing, 'verify_models', should_not_hash)
    response = client.post('/api/v1/setup/existing-installations/verify', json={
        'path': str(root), 'snapshot': report['snapshot'],
    }, headers={'X-Shine-Local-Action': '1'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'VERIFYING'
    assert response.json()['retry_after_ms'] == 5000


def test_install_plan_excludes_only_hash_verified_cache_from_download(prep):
    client, _, settings = prep
    project = create_project(client)
    component = deployment.load_default_catalog()['components'][0]
    spec = component['files'][0]
    # Use a small valid catalog package instead of allocating real archives.
    from unittest.mock import patch

    raw = deployment.load_default_catalog()
    raw['components'][0]['files'][0].update(size=13, sha256=hashlib.sha256(b'correct model').hexdigest())
    cache = settings.comfyops_dir / 'isolated-packages' / '.cache'
    cache.mkdir(parents=True)
    target = cache / raw['components'][0]['files'][0]['sha256']
    target.write_bytes(b'wrong!! model')
    with patch.object(deployment, 'load_deployment_catalog', return_value=raw):
        body = {'components': [component['component_id']], 'project_id': project, 'disk_free': 10**12}
        assert post(client, '/setup/install-plan', body)['download_bytes'] == 13
        target.write_bytes(b'correct model')
        assert post(client, '/setup/install-plan', body)['download_bytes'] == 0
    assert spec['size'] > 13


def test_a_non_git_install_reports_an_observed_version_with_its_source(tmp_path):
    """ComfyUI 0.39.0 without a Git checkout is not "version unknown"."""
    root = installation(tmp_path)
    (root / 'comfyui_version.py').write_text('__version__ = "0.39.0"\n', encoding='utf-8')
    report = existing.inspect(str(root), catalog())
    software = report['software']
    assert software['version_status'] == 'INSTALLED_VERSION_OBSERVED'
    assert software['actual_version'] == '0.39.0'
    assert software['version_source'] == 'comfyui_version.py'
    assert software['version_confidence'] == 'MEDIUM'
    assert software['actual_revision'] is None
    # A release number is evidence of the software, never of the pinned commit.
    assert software['version_evidence']['version_is_not_a_commit'] is True
    assert software['version_evidence']['pinned_revision_compared'] is False


def test_custom_node_inventory_reports_only_evidence_it_really_has(tmp_path):
    root = installation(tmp_path)
    declared = root / 'custom_nodes' / 'ComfyUI-KJNodes'
    declared.mkdir(parents=True)
    (declared / 'pyproject.toml').write_text('[project]\nversion = "1.4.2"\n', encoding='utf-8')
    unknown = root / 'custom_nodes' / 'SomeUnversionedNode'
    unknown.mkdir(parents=True)
    (unknown / '__init__.py').write_text('# test only\n', encoding='utf-8')
    (root / 'custom_nodes' / 'shine_h3_sampling_mode.py').write_text('# test only\n', encoding='utf-8')

    report = existing.inspect(str(root), catalog())
    packages = {row['name']: row for row in report['node_packages']['packages']}

    assert packages['ComfyUI-KJNodes']['version_status'] == 'VERSION_DECLARED'
    assert packages['ComfyUI-KJNodes']['version'] == '1.4.2'
    assert packages['SomeUnversionedNode']['version_status'] == 'VERSION_UNKNOWN'
    assert packages['SomeUnversionedNode']['version'] is None
    assert packages['ComfyUI-KJNodes']['node_class_mapping'] == 'REQUIRES_RUNNING_SERVICE'
    assert report['node_packages']['node_class_mapping_verified'] is False
    assert 'shine_h3_sampling_mode.py' in packages
