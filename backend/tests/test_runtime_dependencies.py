"""Production lifecycle with synthetic source/wheel bytes and a fake OS build.

Real third-party builds/imports are separate local acceptance evidence. These
tests prove approval binding, derived identity checks and failure persistence.
"""

# ruff: noqa: F811

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from shine.comfyops.deployment import load_default_catalog
from shine.comfyops.installer import TRUSTED_DOWNLOAD_HOSTS, PackageSpec, SourceWheelBuild, check_url
from shine.comfyops.instances import FakeCommandRunner
from shine.comfyops.runtime_dependencies import prepare_build_directories, verified_built_wheels, wheel_identity
from tests.conftest import create_project
from tests.test_v13_instances import install_fixture, operate
from tests.test_v13_preparation import HEADERS, approve, post, prep  # noqa: F401


def wheel(name, version):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as zipped:
        for path, data in {
            f'{name}-{version}.dist-info/METADATA': f'Name: {name}\r\nVersion: {version}\r\n',
            f'{name}-{version}.dist-info/WHEEL': 'Wheel-Version: 1.0\nTag: py3-none-any\n',
        }.items():
            zipped.writestr(zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0)), data)
    return output.getvalue()


class BuildRunner(FakeCommandRunner):
    built_name = 'sox'
    built_version = '1.5.0'

    def run(self, args, *, cwd):
        super().run(args, cwd=cwd)
        if args[1:4] == ['-m', 'pip', 'wheel']:
            output = Path(args[args.index('--wheel-dir') + 1])
            (output / f'{self.built_name}-{self.built_version}-py3-none-any.whl').write_bytes(
                wheel(self.built_name, self.built_version),
            )


def source_fixture(prep):
    source = install_fixture(prep, 'comfy-source', {
        'main.py': b'# synthetic entrypoint', 'requirements.txt': b'sox==1.5.0\n',
    }, kind='source')
    dependencies = install_fixture(prep, 'test-runtime', {
        'setuptools-84.2.1-py3-none-any.whl': wheel('setuptools', '84.2.1'),
        'wheel-0.45.1-py3-none-any.whl': wheel('wheel', '0.45.1'),
        'sox-1.5.0.tar.gz': b'synthetic source; no setup script is executed',
    }, kind='wheel', source_builds=[{
        'relative_path': 'sox-1.5.0.tar.gz', 'name': 'sox', 'version': '1.5.0',
        'license_id': 'TEST-ONLY',
    }])
    return source, dependencies


def test_approved_source_build_is_hash_locked_and_audited(prep):
    client, _, _ = prep
    source, dependencies = source_fixture(prep)
    client.app.state.instance_runner = runner = BuildRunner()
    project = create_project(client)
    plan, created = operate(client, project, 'CREATE', source_package_id=source['id'],
                            package_ids=[dependencies['id']])
    root = Path(created['payload']['result']['root'])
    record = json.loads((root / '.shine-source-builds.json').read_text())
    built = record['wheels'][0]
    expected_sha = hashlib.sha256(wheel('sox', '1.5.0')).hexdigest()
    assert built['sha256'] == expected_sha
    assert built['source_sha256'] == hashlib.sha256(b'synthetic source; no setup script is executed').hexdigest()
    assert f'sox==1.5.0 --hash=sha256:{expected_sha}' in (root / '.shine-requirements.txt').read_text()
    build = next(c for c in plan['payload']['commands'] if c[1:4] == ['-m', 'pip', 'wheel'])
    assert {'--no-index', '--no-deps', '--no-build-isolation', '--no-cache-dir'} <= set(build)
    install_built = next(c for c in plan['payload']['commands'] if '--force-reinstall' in c)
    assert '--require-hashes' in install_built and '--no-deps' in install_built
    assert Path(install_built[install_built.index('-r') + 1]).name == '.shine-built-requirements.txt'
    assert (root / '.shine-built-requirements.txt').read_text().strip() == (
        f'sox==1.5.0 --hash=sha256:{expected_sha}'
    )
    assert runner.calls[-1][1:] == ['-m', 'pip', 'check']
    assert created['payload']['is_mock'] is True
    assert created['payload']['result']['source_builds'] == record


@pytest.mark.parametrize('name,version', [('unexpected', '1.5.0'), ('sox', '9.9.9')])
def test_mismatched_built_identity_requires_review_and_never_installs(prep, name, version):
    client, _, _ = prep
    source, dependencies = source_fixture(prep)
    runner = BuildRunner()
    runner.built_name, runner.built_version = name, version
    client.app.state.instance_runner = runner
    project = create_project(client)
    plan = post(client, '/comfyops/instance-plans', {
        'project_id': project, 'name': 'badbuild', 'action': 'CREATE',
        'source_package_id': source['id'], 'package_ids': [dependencies['id']], 'risk_confirmed': True,
    }, operator=True)
    plan = approve(client, plan, 'install', f"/comfyops/install-plans/{plan['id']}/approve")
    response = client.post(f"/api/v1/comfyops/instance-plans/{plan['id']}/execute",
                           json={'plan_hash': plan['sha256']}, headers=HEADERS)
    assert response.status_code == 409, response.text
    attempts = client.get(f"/api/v1/comfyops/install-plans/{plan['id']}/attempts").json()
    assert attempts[0]['status'] == 'NEEDS_REVIEW'
    assert attempts[0]['payload']['reason'] == 'BUILT_WHEEL_IDENTITY_CHANGED'
    installs = [c for c in runner.calls if c[1:4] == ['-m', 'pip', 'install']]
    assert len(installs) == 1 and '--no-deps' in installs[0]
    assert not any(c[1:] == ['-m', 'pip', 'check'] for c in runner.calls)


def test_existing_package_serialization_preserves_legacy_identity():
    spec = PackageSpec(name='legacy', kind='wheel', revision='1', license_id='TEST', usage='research',
                       files=[{'url': 'https://files.pythonhosted.org/x.whl', 'relative_path': 'x.whl',
                               'sha256': 'a' * 64, 'size': 1}])
    assert 'source_builds' not in spec.model_dump()
    assert 'source_builds' not in json.loads(spec.model_dump_json())


def test_default_catalog_urls_are_accepted_by_the_actual_installer():
    for component in load_default_catalog()['components']:
        for file in component['files']:
            check_url(file['url'], TRUSTED_DOWNLOAD_HOSTS)
    with pytest.raises(ValueError, match='UNTRUSTED_DOWNLOAD_URL'):
        check_url('https://download-r2.pytorch.org.untrusted.example/x.whl', TRUSTED_DOWNLOAD_HOSTS)


def test_source_mutation_after_build_cannot_borrow_the_original_hash(tmp_path):
    source = tmp_path / 'sox-1.5.0.tar.gz'
    source.write_bytes(b'original')
    output = tmp_path / '.built-wheels/fixture/source'
    build = {'source': str(source), 'source_sha256': hashlib.sha256(b'original').hexdigest(),
             'source_size': len(b'original'), 'output': str(output), 'name': 'sox', 'version': '1.5.0'}
    prepare_build_directories(tmp_path, [build])
    (output / 'sox-1.5.0-py3-none-any.whl').write_bytes(wheel('sox', '1.5.0'))
    source.write_bytes(b'mutation')
    with pytest.raises(ValueError, match='SOURCE_BUILD_INPUT_CHANGED'):
        verified_built_wheels(tmp_path, [build])


def test_source_build_requires_an_identified_license():
    with pytest.raises(ValueError, match='SOURCE_LICENSE_NOT_APPROVED'):
        SourceWheelBuild(relative_path='sox.tar.gz', name='sox', version='1.5.0', license_id='unknown')


def test_vendored_metadata_does_not_replace_the_primary_wheel_identity(tmp_path):
    path = tmp_path / 'setuptools-84.2.1-py3-none-any.whl'
    path.write_bytes(wheel('setuptools', '84.2.1'))
    with zipfile.ZipFile(path, 'a') as zipped:
        zipped.writestr('setuptools/_vendor/wheel-0.46.3.dist-info/METADATA', 'Name: wheel\nVersion: 0.46.3\n')
    assert wheel_identity(path) == ('setuptools', '84.2.1')
    with zipfile.ZipFile(path, 'a') as zipped:
        zipped.writestr('other-1.0.dist-info/METADATA', 'Name: other\nVersion: 1.0\n')
    with pytest.raises(ValueError, match='WHEEL_METADATA_INVALID'):
        wheel_identity(path)
