"""The authoritative production-mode summary: one state, honest evidence.

These tests pin the /api/v1/system/status ``production_mode`` contract the
director workbench badge and the submission gates read.  The core invariants:

* a stopped managed renderer never demotes READY (it is idle by design);
* mock jobs never flip the mode to RUNNING;
* uncertain external state (RECONCILE_REQUIRED leases) outranks everything;
* every blocked state carries a Chinese explanation plus a traceable code.
"""
from tests.conftest import create_project, create_shot, freeze_style
from tests.test_runtime_activation import activate, candidate, prep  # noqa: F401
from tests.test_v13_preparation import HEADERS  # noqa: F401


def _mode(client) -> dict:
    response = client.get('/api/v1/system/status')
    assert response.status_code == 200, response.text
    return response.json()['production_mode']


def _register_worker(factory, worker_id='mode-worker'):
    from shine.models import WorkerInstance, utcnow

    with factory.begin() as db:
        db.add(WorkerInstance(worker_id=worker_id, version='test', status='online',
                              started_at=utcnow(), last_heartbeat_at=utcnow()))


def _seed_service_evidence(factory):
    """Seal live-service evidence for the ACTIVE identity with no extra
    workflow/review contracts so service_evidence_status() reports READY."""
    from shine.comfyops.mode_capabilities import core_mode_node_types
    from shine.comfyops.runtime_configuration import ACTIVE_KEY, record_service_evidence
    from shine.models import AppSetting

    with factory.begin() as db:
        active = db.get(AppSetting, ACTIVE_KEY)
        assert active is not None, 'activation must exist before evidence'
        record_service_evidence(db, active.value['identity'], {
            'workflow_profiles': {}, 'review_profiles': {}, 'node_count': 1,
            'mode_capabilities': {'required_nodes': sorted(core_mode_node_types())},
        })
        db.commit()


def test_fresh_studio_reports_demo_mode_and_mock_jobs_do_not_change_it(studio):
    client, factory, _root, _settings = studio
    mode = _mode(client)
    assert mode['mode'] == 'DEMO'
    assert mode['label'] == '演示体验'
    assert mode['action'] == 'enable_real'
    assert mode['render_provider'] == 'mock'
    assert mode['runtime_status'] == 'NOT_ACTIVATED'

    # A finished mock task with a live worker heartbeat: still demo.  Demo
    # tasks must never be reported as real production activity.
    from shine.providers import MockH3Provider
    from shine.worker import run_one
    from tests.conftest import create_job

    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style['id'])
    job = create_job(client, project_id, shot['id'])
    assert run_one(factory, MockH3Provider(), 'demo-worker') == job['id']
    mode = _mode(client)
    assert mode['mode'] == 'DEMO'
    assert mode['running_jobs'] == 0


def test_candidate_without_activation_reports_setup_required(prep, candidate):  # noqa: F811
    client, _factory, _settings = prep
    mode = _mode(client)
    assert mode['mode'] == 'SETUP_REQUIRED'
    assert mode['action'] == 'enable_real'
    assert mode['code'] == 'REAL_RUNTIME_NOT_ACTIVATED'
    assert mode['runtime_status'] == 'NOT_ACTIVATED'


def test_activation_without_service_evidence_reports_validation_required(prep, candidate):  # noqa: F811
    client, _factory, _settings = prep
    assert activate(prep, candidate).status_code == 200
    mode = _mode(client)
    assert mode['mode'] == 'VALIDATION_REQUIRED'
    assert mode['action'] == 'check_fix'
    assert mode['runtime_status'] == 'ACTIVE'


def test_verified_active_runtime_is_ready_even_with_renderer_idle_stopped(prep, candidate, monkeypatch):  # noqa: F811
    """The managed renderer is idle-stopped between shots by design; a valid
    ACTIVE runtime stays READY with renderer_online False."""
    client, factory, _settings = prep
    assert activate(prep, candidate).status_code == 200
    _seed_service_evidence(factory)
    _register_worker(factory)
    from shine.comfy.client import ComfyUIClient
    monkeypatch.setattr(ComfyUIClient, 'system_stats',
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('idle-stopped')))
    mode = _mode(client)
    assert mode['mode'] == 'READY'
    assert mode['label'] == '真实制作已就绪'
    assert mode['action'] == 'start_making'
    assert mode['renderer_online'] is False
    assert mode['worker_online'] is True


def test_worker_offline_blocks_ready(prep, candidate):  # noqa: F811
    client, factory, _settings = prep
    assert activate(prep, candidate).status_code == 200
    _seed_service_evidence(factory)
    mode = _mode(client)
    assert mode['mode'] == 'BLOCKED'
    assert mode['code'] == 'WORKER_OFFLINE'
    assert '后台制作服务' in mode['detail']


def test_real_running_job_and_live_series_report_running(prep, candidate):  # noqa: F811
    client, factory, _settings = prep
    assert activate(prep, candidate).status_code == 200
    _seed_service_evidence(factory)
    _register_worker(factory)
    project_id = candidate[0]
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style['id'])

    from shine.models import Job, ProductionSeries
    with factory.begin() as db:
        db.add(Job(project_id=project_id, shot_id=shot['id'], shot_revision=1,
                   style_version_id=style['id'], request={}, idempotency_key='mode-running-1',
                   status='RUNNING', provider='comfyui'))
        db.add(ProductionSeries(project_id=project_id, status='QUEUED', shot_count=1))
    mode = _mode(client)
    assert mode['mode'] == 'RUNNING'
    assert mode['label'] == '正在生成影片'
    assert mode['action'] == 'view_progress'
    assert mode['running_jobs'] == 1
    assert mode['series_active'] == 1

    # A running MOCK job in an otherwise ready environment stays READY:
    # demo tasks are not real production activity.
    with factory.begin() as db:
        db.execute(Job.__table__.update()
                   .where(Job.idempotency_key == 'mode-running-1')
                   .values(status='PASSED'))
        db.execute(ProductionSeries.__table__.update()
                   .where(ProductionSeries.project_id == project_id)
                   .values(status='COMPLETED'))
        db.add(Job(project_id=project_id, shot_id=shot['id'], shot_revision=1,
                   style_version_id=style['id'], request={}, idempotency_key='mode-running-2',
                   status='RUNNING', provider='mock'))
    assert _mode(client)['mode'] == 'READY'


def test_changed_active_files_report_blocked_with_chinese_detail(prep, candidate):  # noqa: F811
    client, _factory, _settings = prep
    assert activate(prep, candidate).status_code == 200
    from pathlib import Path
    Path(candidate[3]['recipe']).write_bytes(Path(candidate[3]['recipe']).read_bytes() + b'\n ')
    mode = _mode(client)
    assert mode['mode'] == 'BLOCKED'
    assert mode['label'] == '真实制作暂不可用'
    assert mode['code'] == 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED'
    assert '重新检查' in mode['detail']
    assert mode['render_provider'] == 'unavailable'
    assert client.get('/api/v1/system/status').json()['render_mode'] == 'unavailable'


def test_reconcile_lease_outranks_everything(prep, candidate):  # noqa: F811
    client, factory, _settings = prep
    assert activate(prep, candidate).status_code == 200
    _seed_service_evidence(factory)
    _register_worker(factory)
    from datetime import timedelta

    from shine.comfyops.models import ResourceLease
    from shine.models import utcnow
    with factory.begin() as db:
        db.add(ResourceLease(owner='h3:uncertain', budget_mb=1, exclusive=True, priority=5,
                             status='RECONCILE_REQUIRED', heartbeat_at=utcnow(),
                             expires_at=utcnow() + timedelta(minutes=5)))
    mode = _mode(client)
    assert mode['mode'] == 'RECONCILE_REQUIRED'
    assert mode['label'] == '有任务待核对'
    assert mode['action'] == 'check_fix'
    assert '不会自动重试' in mode['detail']




def test_enabled_comfyui_without_saved_setup_is_not_described_as_saved(api):
    """A brand-new install must not be told its configuration was saved.

    The provider can be enabled by configuration before anything is installed;
    "已保存真实制作配置" would send the user looking for a configuration that
    does not exist yet.
    """
    from types import SimpleNamespace

    from shine.production_mode import summarize_production_mode

    _client, factory, settings = api
    with factory() as db:
        mode = summarize_production_mode(
            db, settings, effective=SimpleNamespace(render_provider='comfyui'),
            runtime_error=None, worker_online=False, renderer_online=False,
        )
    assert mode['mode'] == 'SETUP_REQUIRED'
    assert mode['code'] == 'REAL_RUNTIME_NOT_ACTIVATED'
    assert '还没有保存' in mode['detail']
