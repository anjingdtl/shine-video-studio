"""Real Chromium -> Vite -> isolated ASGI routes/SQLite.

Synthetic tiny catalog/cache, OS command port and service probes are fixtures.
No API route interception, no real model downloads or H3 inference.
Run: uv run --project backend --frozen --with playwright==1.64.1 pytest scripts/test_director_browser.py -v
"""
# ruff: noqa: F811
import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'backend'))
from shine.comfyops import deployment, existing_installation, host_env, instances, service_validation  # noqa: E402
from shine.comfyops.installer import LocalInstaller, ControlledDownloader  # noqa: E402
from shine.comfy.client import ComfyUIClient  # noqa: E402
from shine.llm.gateway import LLMGateway  # noqa: E402
from shine.comfyops.llm_readiness import TEXT_ROLES  # noqa: E402
from shine.models import WorkerInstance, utcnow  # noqa: E402
from tests.conftest import fake_deployment_host, make_studio  # noqa: E402,F401
from tests.test_service_validation import _fake_object_info, _fake_review  # noqa: E402
from tests.test_runtime_dependencies import wheel  # noqa: E402


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize('scenario', ['missing', 'models_complete', 'instance_ready'])
def test_browser_setup_routes(tmp_path, monkeypatch, fake_deployment_host, scenario):
    app, factory, settings = make_studio(tmp_path)
    raw = copy.deepcopy(deployment.load_default_catalog())
    contents = {}
    for component in raw['components']:
        cid = component['component_id']
        if cid == 'comfyui':
            files = {'main.py': b'# synthetic source; never run', 'requirements.txt': b'# no real dependencies'}
        elif component['kind'] == 'wheel':
            files = {'fixture-1.0-py3-none-any.whl': wheel('fixture', '1.0')}
        elif component['kind'] == 'node':
            files = {'__init__.py': b'# synthetic node; never import'}
        else:
            files = {'model.bin': ('synthetic test fixture ' + cid).encode()}
        component['source_builds'] = []
        component['files'] = []
        for name, data in files.items():
            digest = hashlib.sha256(data).hexdigest()
            contents[digest] = data
            component['files'].append({'url': 'https://raw.githubusercontent.com/fixture/' + name,
                                      'relative_path': name, 'sha256': digest, 'size': len(data)})
    raw['total_bytes'] = sum(f['size'] for c in raw['components'] for f in c['files'])
    raw['dependency_policy']['runtime_disk']['runtime_reserve_bytes'] = 0
    monkeypatch.setattr(deployment, 'load_default_catalog', lambda: raw)
    monkeypatch.setattr(deployment, 'load_deployment_catalog', lambda *args: raw)
    monkeypatch.setattr(deployment, 'ENV_PATH', tmp_path / 'isolated.env')
    monkeypatch.setenv('SHINE_ENABLE_LOCAL_EXECUTION', '1')
    monkeypatch.setattr(LLMGateway, 'test_profile', lambda *args, **kwargs: {
        'available': True, 'model_listed': True, 'models': ['synthetic-text-model'], 'json_mode': True})
    monkeypatch.setattr(host_env, 'probe_gpu', lambda: {'status': 'OK', 'uuid': 'GPU-SYNTHETIC'})
    monkeypatch.setattr(host_env, 'probe_host_environment', lambda *args, **kwargs: {
        'tools': {}, 'gpu': {}, 'ram': {'status': 'OK'}, 'disk': {'status': 'OK', 'free_gb': 9999},
        'port': {'status': 'OK'}, 'data_dir': {'writable': True}})
    monkeypatch.setattr(host_env, 'setup_blockers', lambda _: [])
    # The isolated fixture has no real existing installation to reuse: the
    # synthetic package root must not look like one, and the smoke must not
    # depend on what this machine happens to have installed.
    monkeypatch.setattr(existing_installation, 'discover', lambda _roots: {'installations': []})
    fail_validation = {'once': scenario == 'missing'}
    def renderer_probe(recipe):
        info = _fake_object_info()
        if fail_validation['once']:
            fail_validation['once'] = False
            info = {}  # External live service reports an incomplete node contract once.
        return {'object_info': info, 'port': recipe['renderer']['port']}
    monkeypatch.setattr(service_validation, '_probe_renderer', renderer_probe)
    monkeypatch.setattr(service_validation, '_probe_review', _fake_review)
    def stopped_renderer(*args):
        raise RuntimeError('Synthetic renderer remains idle-stopped')
    monkeypatch.setattr(ComfyUIClient, 'system_stats', stopped_renderer)
    commands = []
    def isolated_run(self, argv, *, cwd):
        commands.append(argv)
        if argv[1:3] == ['-m', 'venv']:
            target = Path(argv[-1]) / 'Scripts/python.exe'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'SYNTHETIC EXECUTABLE; NEVER RUN')
    monkeypatch.setattr(instances.ProcessRunner, 'run', isolated_run)
    cache = settings.comfyops_dir / 'isolated-packages/.cache'
    cache.mkdir(parents=True)
    for digest, data in contents.items():
        (cache / digest).write_bytes(data)
    api_port, ui_port = port(), port()
    api_base = f'http://127.0.0.1:{api_port}'
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=api_port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    env = {**os.environ, 'SHINE_VITE_API_TARGET': api_base}
    ui = subprocess.Popen([shutil.which('npm'), '--prefix', str(REPO / 'frontend'), 'run', 'dev', '--',
                           '--host', '127.0.0.1', '--port', str(ui_port), '--strictPort'],
                          cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    requests = []
    responses = []
    try:
        for _ in range(100):
            if server.started:
                try:
                    if httpx.get(f'http://127.0.0.1:{ui_port}', timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
            time.sleep(.1)
        else:
            raise AssertionError('Isolated servers failed to start')
        with httpx.Client(base_url=api_base, headers={'X-Shine-Local-Action': '1'}, timeout=30) as client:
            project = client.post('/api/v1/projects', json={'title': 'Browser ' + scenario}).json()['id']
            if scenario != 'missing':
                profile = client.post('/api/v1/provider-profiles', json={
                    'name': 'Synthetic text provider', 'kind': 'openai_compatible',
                    'base_url': 'http://127.0.0.1:9/v1', 'model': 'synthetic-text-model'}).json()
                assert client.post(f"/api/v1/provider-profiles/{profile['id']}/test").json()['ok']
                for role in TEXT_ROLES:
                    assert client.put('/api/v1/agent-routes', json={
                        'role': role, 'provider_profile_id': profile['id']}).status_code == 200
            if scenario != 'missing':
                installer = LocalInstaller(settings.comfyops_dir / 'isolated-packages',
                                           ControlledDownloader({'raw.githubusercontent.com'}, None))
                with factory() as db:
                    packages = deployment.register_catalog_packages(db, raw, [c['component_id'] for c in raw['components']])
                    for row in packages.values():
                        from shine.comfyops.installer import PackageSpec
                        installer.install(PackageSpec.model_validate(row.payload))
                        row.status = 'FILES_VERIFIED_PENDING_RUNTIME'
                    db.commit()
            if scenario == 'instance_ready':
                for _ in range(8):
                    next_plan = client.post('/api/v1/setup/next-instance-plan', json={'project_id': project}).json()
                    if next_plan.get('complete'):
                        break
                    grant = client.post('/api/v1/preparation/approval-grants', json={
                        'action': 'install', 'target_id': next_plan['plan_id']}).json()
                    approved = client.post(f"/api/v1/comfyops/install-plans/{next_plan['plan_id']}/approve", json={
                        'grant_id': grant['grant_id'], 'snapshot': grant['snapshot'], 'evidence': 'Synthetic browser fixture preparation'}).json()
                    executed = client.post(f"/api/v1/comfyops/instance-plans/{next_plan['plan_id']}/execute", json={
                        'plan_hash': approved['sha256']})
                    assert executed.status_code == 200, executed.text
            with factory.begin() as db:
                db.add(WorkerInstance(worker_id='synthetic-browser-worker', version='test', status='online',
                                      started_at=utcnow(), last_heartbeat_at=utcnow()))
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, channel='chrome')
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                    page.on('dialog', lambda dialog: dialog.accept())
                    page.on('request', lambda req: requests.append({'url': req.url, 'method': req.method,
                            'body': req.post_data_json}) if '/api/v1/' in req.url and req.method == 'POST' else None)
                    page.on('response', lambda res: responses.append((res.url, res.status)) if '/api/v1/' in res.url else None)
                    page.goto(f'http://127.0.0.1:{ui_port}', wait_until='networkidle')
                    page.get_by_role('button', name='启用真实制作', exact=True).first.click()
                    # Reusing an existing installation is part of the current
                    # contract: the wizard waits for the person before it prepares
                    # anything, so the smoke has to make that decision.
                    reuse_button = page.get_by_role('button', name='核验并确认复用', exact=True)
                    try:
                        reuse_button.wait_for(state='visible', timeout=20000)
                        reuse_button.click()
                    except Exception:
                        pass  # no existing installation to reuse in this scenario
                    if scenario == 'missing':
                        wizard = page.get_by_role('dialog')
                        # The text models are a parallel preparation: the wizard
                        # names what is missing and keeps preparing H3 (J03).
                        expect(wizard.get_by_text('文本模型还有角色未配置', exact=False).first).to_be_visible(timeout=45000)
                        llm_shot = REPO / '.local-acceptance/director-browser/llm-first.png'
                        llm_shot.parent.mkdir(exist_ok=True)
                        page.screenshot(path=str(llm_shot), full_page=True, animations='disabled')
                        wizard.get_by_label('模型配置名称', exact=True).fill('Browser core LLM')
                        wizard.get_by_label('接口地址', exact=True).fill('http://127.0.0.1:9/v1')
                        wizard.get_by_label('Model ID', exact=True).fill('synthetic-text-model')
                        wizard.get_by_role('button', name='添加', exact=True).click()
                        wizard.get_by_role('button', name='测试', exact=True).click()
                        wizard.get_by_label('用于文本工种的模型', exact=False).select_option(label='Browser core LLM · synthetic-text-model')
                        wizard.get_by_role('button', name='用于全部文本工种', exact=True).click()
                        expect(wizard.get_by_text('LLM 准备：配置及连接检查通过（尚非文本生成验收）', exact=True)).to_be_visible()
                    if scenario == 'missing':
                        # The wizard finishes on its own: the first service
                        # validation fails once in this scenario.
                        expect(page.get_by_test_id('wizard-result')).to_contain_text('LIVE_WORKFLOW_CONTRACT_FAILED', timeout=60000)
                        assert any('/install-plan' in r['url'] for r in requests), (
                            'the missing text models must not block the H3 install path')
                        executed_before_retry = sum(r['url'].endswith('/execute') for r in requests)
                        page.get_by_role('dialog').get_by_role('button', name='重新检查', exact=True).click()
                    expect(page.get_by_test_id('wizard-result')).to_contain_text('真实制作已准备好', timeout=45000)
                    if scenario == 'missing':
                        assert sum(r['url'].endswith('/execute') for r in requests) == executed_before_retry
                    expect(page.get_by_role('button', name='返回作品，开始制作')).to_be_visible()
                    page.get_by_role('button', name='返回作品，开始制作').click()
                    expect(page.get_by_role('dialog')).to_have_count(0)
                    status = client.get('/api/v1/system/status').json()
                    assert status['production_mode']['mode'] == 'READY', status['production_mode']
                    assert status['production_mode']['renderer_online'] is False
                    failures = [(url, code) for url, code in responses if code >= 400]
                    if scenario == 'missing':
                        assert len(failures) == 1 and failures[0][0].endswith('/setup/services/validate') and failures[0][1] == 409, failures
                    else:
                        assert failures == [], failures
                    if scenario == 'missing':
                        assert any('/install-plans/' in r['url'] and r['url'].endswith('/execute') for r in requests)
                    if scenario != 'instance_ready':
                        assert any('/instance-plans/' in r['url'] and r['url'].endswith('/execute') for r in requests)
                    # Explicit demo preference persists across refresh and disables real series.
                    # The advanced fold lives inside the production dock; opening it
                    # needs the production stage of the workbench, which this
                    # smoke does not navigate to yet (known gap).
                    page.get_by_text('单镜头制作与任务明细（高级）', exact=True).click()
                    page.get_by_label('生成方式', exact=False).first.select_option('mock')
                    expect(page.get_by_role('button', name='开始制作影片', exact=False)).to_be_disabled()
                    page.reload(wait_until='networkidle')
                    page.get_by_role('button', name='AI导演制作', exact=False).click()
                    expect(page.get_by_text('当前选择演示；连续制作只支持真实 H3，演示态不能启动。')).to_be_visible()
                    # Reload keeps READY with an idle renderer and issues no installation writes.
                    assert not any(r['url'].endswith('/production-series') for r in requests)
                    expect(page.locator('.mode-entry')).to_contain_text('真实制作已就绪')
                    evidence = REPO / '.local-acceptance/director-browser'
                    evidence.mkdir(exist_ok=True)
                    page.screenshot(path=str(evidence / (scenario + '.png')), full_page=True, animations='disabled')
                    (evidence / (scenario + '.json')).write_text(json.dumps({
                        'scenario': scenario, 'browser_version': browser.version, 'requests': requests, 'responses': responses,
                        'production_mode': status['production_mode'], 'external_execution': 'ISOLATED_SYNTHETIC_PORTS',
                        'real_h3_inference': 'NOT_RUN'}, ensure_ascii=False, indent=2), encoding='utf-8')
                finally:
                    browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        if ui.poll() is None:
            subprocess.run(['taskkill', '/PID', str(ui.pid), '/T', '/F'], capture_output=True)
            ui.wait(timeout=15)
        app.state.engine.dispose()
