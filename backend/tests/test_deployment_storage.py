"""Directory selection changes actual installer paths, never moves existing data."""

# ruff: noqa: F811
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from shine.comfyops.deployment import load_default_catalog
from shine.comfyops.host_env import probe_writable
from shine.comfyops.instances import ProcessRunner
from shine.comfyops.managed_recipe import build_renderer_launch, build_review_launch, load_managed_recipe
from shine.comfyops.models import InstallationPlan
from shine.comfyops.storage import on_system_drive as detect_system_drive
from shine.comfyops.storage import preferences_path, process_environment
from tests.conftest import create_project
from tests.test_new_machine_setup import test_configuration_generation_and_recipe_validation as exercise_configuration
from tests.test_v13_instances import install_fixture, operate
from tests.test_v13_preparation import HEADERS, approve, post, prep  # noqa: F401


def test_storage_selection_is_authenticated_and_shared_with_a_new_worker(prep, tmp_path):
    client, _, settings = prep
    root = tmp_path / '中文目录 with spaces' / 'runtime'
    before_db = settings.database_url
    assert not preferences_path(settings.data_dir).exists()
    response = client.put('/api/v1/setup/storage', json={'path': str(root)})
    assert response.status_code == 403
    assert not root.exists()
    response = client.put('/api/v1/setup/storage', json={'path': str(root)}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert Path(response.json()['root']) == root.resolve()
    assert settings.comfyops_dir == root.resolve()
    restarted_worker_settings = replace(settings)
    assert restarted_worker_settings.comfyops_dir == root.resolve()
    assert settings.database_url == before_db
    assert response.json()['files_moved'] is False
    assert Path(response.json()['paths']['download_cache']).is_relative_to(root)


def test_system_drive_download_is_blocked_until_explicit_selection(prep, tmp_path, monkeypatch):
    client, _, settings = prep
    monkeypatch.setattr('shine.comfyops.storage.on_system_drive', lambda path: True)
    report = client.get('/api/v1/setup/storage').json()
    assert report['requires_selection'] is True
    project = create_project(client)
    response = client.post('/api/v1/setup/install-plan', json={
        'project_id': project, 'components': ['comfyui'], 'disk_free': 10**15,
    })
    assert response.status_code == 409 and 'CHOOSE_DEPLOYMENT_DIRECTORY' in response.text
    target = tmp_path / 'explicit'
    response = client.put('/api/v1/setup/storage', json={'path': str(target)}, headers=HEADERS)
    assert response.status_code == 409
    response = client.put('/api/v1/setup/storage', json={
        'path': str(target), 'allow_system_drive': True,
    }, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()['requires_selection'] is False
    assert settings.comfyops_dir == target


def test_directory_change_cancels_approved_plan_without_reusing_its_grant(prep, tmp_path):
    client, factory, settings = prep
    project = create_project(client)
    row = post(client, '/setup/install-plan', {
        'project_id': project, 'components': ['comfyui'], 'disk_free': 10**15,
    })
    plan = approve(client, {'id': row['plan_id']}, 'install',
                   f"/comfyops/install-plans/{row['plan_id']}/approve")
    response = client.put('/api/v1/setup/storage', json={'path': str(tmp_path / 'new-download-volume')},
                          headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()['cancelled_plans'] == [row['plan_id']]
    with factory() as db:
        cancelled = db.get(InstallationPlan, row['plan_id'])
        assert cancelled.status == 'CANCELLED_STORAGE_CHANGED'
        assert cancelled.sha256 == plan['sha256']
    fresh = post(client, '/setup/install-plan', {
        'project_id': project, 'components': ['comfyui'], 'disk_free': 10**15,
    })
    with factory() as db:
        assert db.get(InstallationPlan, fresh['plan_id']).payload['storage_root'] == str(settings.comfyops_dir)


def test_selected_directory_drives_cache_package_and_instance_locations(prep, tmp_path):
    client, _, settings = prep
    selected = tmp_path / 'user-selected-volume'
    response = client.put('/api/v1/setup/storage', json={'path': str(selected)}, headers=HEADERS)
    assert response.status_code == 200, response.text
    source = install_fixture(prep, 'selected-source', {'main.py': b'# synthetic entrypoint'}, kind='source')
    package_dir = selected / 'isolated-packages'
    assert (package_dir / 'selected-source/main.py').is_file()
    assert (package_dir / '.cache').is_dir()
    project = create_project(client)
    _, created = operate(client, project, 'CREATE', source_package_id=source['id'])
    assert Path(created['payload']['result']['root']).is_relative_to(selected / 'fake-instances')
    assert not (settings.data_dir / 'comfyops').exists()
    assert client.get('/api/v1/setup/storage').json()['root'] == str(selected)


def test_existing_downloads_are_never_moved_or_deleted(prep, tmp_path):
    client, _, settings = prep
    marker = settings.comfyops_dir / 'isolated-packages/.cache/existing-partial'
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b'preserve unfinished download')
    response = client.put('/api/v1/setup/storage', json={'path': str(tmp_path / 'another-root')}, headers=HEADERS)
    assert response.status_code == 409 and 'NO_FILES_MOVED' in response.text
    assert marker.read_bytes() == b'preserve unfinished download'
    assert not preferences_path(settings.data_dir).exists()


def test_target_must_be_dedicated_absolute_and_empty(prep, tmp_path):
    client, _, _ = prep
    response = client.put('/api/v1/setup/storage', json={'path': 'relative-downloads'}, headers=HEADERS)
    assert response.status_code == 422
    root = tmp_path / 'existing-unowned'
    root.mkdir()
    marker = root / 'keep.txt'
    marker.write_text('user data')
    response = client.put('/api/v1/setup/storage', json={'path': str(root)}, headers=HEADERS)
    assert response.status_code == 409
    assert marker.read_text() == 'user data'


def test_actual_child_python_uses_selected_temp_and_cache_directories(tmp_path):
    selected = tmp_path / 'runtime-on-selected-disk'
    selected.mkdir()
    output = selected / 'actual-child-environment.json'
    runner = ProcessRunner()
    runner.environment = process_environment(selected)
    runner.run([sys.executable, '-c',
                'import os,tempfile,json,sys; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(json.dumps({"temp":tempfile.gettempdir(), '
                '"pip":os.environ["PIP_CACHE_DIR"], "hf":os.environ["HF_HOME"], '
                '"torch":os.environ["TORCH_HOME"], "triton":os.environ["TRITON_CACHE_DIR"], '
                '"cuda":os.environ["CUDA_CACHE_PATH"], "extensions":os.environ["TORCH_EXTENSIONS_DIR"], '
                '"pycache":sys.pycache_prefix}))', str(output)], cwd=selected)
    report = json.loads(output.read_text())
    assert all(Path(value).is_relative_to(selected) for value in report.values())


def test_full_deployment_budget_includes_environment_and_snapshots(prep):
    client, factory, _ = prep
    project = create_project(client)
    catalog = load_default_catalog()
    components = [c['component_id'] for c in catalog['components']]
    only_download_and_cache = catalog['total_bytes'] * 2
    response = client.post('/api/v1/setup/install-plan', json={
        'project_id': project, 'components': components, 'disk_free': only_download_and_cache,
    })
    assert response.status_code == 409 and 'INSUFFICIENT_DISK' in response.text
    result = post(client, '/setup/install-plan', {
        'project_id': project, 'components': components, 'disk_free': 10**15,
    })
    assert result['disk_required'] > only_download_and_cache
    with factory() as db:
        plan = db.get(InstallationPlan, result['plan_id'])
        measured_reserve = catalog['dependency_policy']['runtime_disk']['runtime_reserve_bytes']
        assert plan.payload['runtime_reserve_bytes'] == measured_reserve
        assert plan.payload['disk_required'] == only_download_and_cache + plan.payload['runtime_reserve_bytes']


def test_environment_probe_uses_the_selected_disk(prep, tmp_path):
    client, _, settings = prep
    chosen = tmp_path / 'selected-deployment-disk'
    response = client.put('/api/v1/setup/storage', json={'path': str(chosen)}, headers=HEADERS)
    assert response.status_code == 200, response.text
    report = client.get('/api/v1/setup/environment').json()
    assert Path(report['disk']['path']) == chosen
    assert Path(report['deployment_dir']['path']) == chosen
    assert Path(report['data_dir']['path']) == settings.data_dir


def test_concurrent_write_probes_use_independent_temporary_files(tmp_path):
    target = tmp_path / 'deployment-root'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: probe_writable(target), range(40)))
    assert all(r.get('writable') is True for r in results), results
    assert not list(target.iterdir())


def test_insufficient_space_on_actual_target_is_rejected_even_with_large_client_budget(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.setattr('shine.comfyops.storage.disk_info', lambda _: {
        'free_bytes': 1, 'total_bytes': 1000, 'free_gb': 0,
    })
    project = create_project(client)
    response = client.post('/api/v1/setup/install-plan', json={
        'project_id': project, 'components': ['comfyui'], 'disk_free': 10**15,
    })
    assert response.status_code == 409 and 'INSUFFICIENT_DISK_ON_HOST' in response.text


def test_native_system_drive_detection_does_not_require_existing_files(tmp_path):
    if os.name == 'nt':
        system = os.getenv('SystemDrive', 'C:')
        assert detect_system_drive(Path(system + '/ShineDrivePolicyProof'))
        other = next(letter + ':' for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ' if letter + ':' != system.upper())
        assert not detect_system_drive(Path(other + '/ShineDrivePolicyProof'))
    else:
        assert not detect_system_drive(tmp_path)


def test_managed_renderer_and_review_launches_override_ambient_compiler_caches(prep, monkeypatch):
    exercise_configuration(prep, monkeypatch)
    _, _, settings = prep
    recipe = load_managed_recipe(settings.comfyops_dir / 'setup/managed_recipe.json')
    monkeypatch.setenv('TRITON_CACHE_DIR', 'C:/ambient-cache-must-not-be-used')
    monkeypatch.setenv('TORCH_EXTENSIONS_DIR', 'C:/ambient-cache-must-not-be-used')
    _, _, renderer_env, _, _, _ = build_renderer_launch(recipe['renderer'])
    launches = [(renderer_env, Path(recipe['renderer']['root']))]
    for role in ('VLM', 'ASR'):
        _, _, env, _, _ = build_review_launch(recipe['reviews'][role])
        launches.append((env, Path(recipe['reviews'][role]['runtime_packages'])))
    for env, expected_root in launches:
        for key in process_environment(expected_root):
            if key == 'PIP_DISABLE_PIP_VERSION_CHECK':
                continue
            assert Path(env[key]).is_relative_to(expected_root), (key, env[key])
            assert Path(env[key]).is_dir(), (key, env[key])
