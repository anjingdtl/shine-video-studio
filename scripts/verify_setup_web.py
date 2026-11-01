"""Isolated setup UI regression: durable plans and approved-plan resume.

Uses the real local API/SQLite/approval endpoints. Only the final execution
request is intercepted: this test never downloads packages or installs models.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

import verify_v13_web as harness


def exercise(api_base, ui_base, screenshot, temporary, browser_path=None):
    del temporary
    base = api_base + '/api/v1'
    headers = {'Authorization': 'Bearer ' + harness.TEST_CREDENTIAL}
    with httpx.Client(timeout=30) as client:
        def post(path, body):
            response = client.post(base + path, json=body, headers=headers)
            response.raise_for_status()
            return response.json()

        project = post('/projects', {'title': 'Isolated setup resume QA'})['id']
        other = post('/projects', {'title': 'Isolated empty project QA'})['id']
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=str(browser_path) if browser_path else None,
                args=['--no-sandbox', '--disable-gpu'],
            )
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                page.goto(ui_base, wait_until='networkidle')
                page.locator('select').first.select_option(project)
                chosen_root = ((screenshot.parent if screenshot else Path(__file__).resolve().parents[1]
                                / '.local-acceptance') / f'selected-下载目录-{project}').resolve()
                storage = page.locator('#deployment-storage')
                storage.get_by_label('下载安装目录', exact=True).fill(str(chosen_root))
                storage.get_by_label('保存目录的操作员凭据', exact=True).fill(harness.TEST_CREDENTIAL)
                with page.expect_response(lambda r: r.url.endswith('/setup/storage') and r.request.method == 'PUT') as saved:
                    storage.get_by_role('button', name='保存下载安装目录', exact=True).click()
                assert saved.value.status == 200, saved.value.text()
                assert Path(saved.value.json()['root']) == chosen_root
                plan = post('/setup/install-plan', {
                    'project_id': project, 'components': ['comfyui'], 'disk_free': 10**15,
                })
                page.reload(wait_until='networkidle')
                page.locator('select').first.select_option(project)
                expect(storage.get_by_label('下载安装目录', exact=True)).to_have_value(str(chosen_root))
                if screenshot:
                    storage.screenshot(path=str(screenshot.with_name('deployment-storage-settings.png')))
                setup = page.locator('#setup')
                expect(setup.get_by_text(plan['plan_id'], exact=False)).to_be_visible()
                page.reload(wait_until='networkidle')
                page.locator('select').first.select_option(project)
                expect(setup.get_by_text(plan['plan_id'], exact=False)).to_be_visible()

                grant = post('/preparation/approval-grants', {'action': 'install', 'target_id': plan['plan_id']})
                approved = post(f"/comfyops/install-plans/{plan['plan_id']}/approve", {
                    'grant_id': grant['grant_id'], 'snapshot': grant['snapshot'],
                    'evidence': 'Explicit isolated UI regression approval; no execution',
                })
                assert approved['sha256'] != plan['sha256']
                page.reload(wait_until='networkidle')
                page.locator('select').first.select_option(project)
                expect(setup.get_by_role('button', name='执行已批准计划', exact=True)).to_be_visible()
                requests = []
                page.on('request', lambda r: requests.append(r) if r.method == 'POST' else None)
                executed = []

                def intercept(route):
                    executed.append(route.request.post_data_json)
                    route.fulfill(status=200, content_type='application/json',
                                  body=json.dumps({'is_mock': True, 'test_only': 'execution intercepted'}))

                page.route(f"**/comfyops/install-plans/{plan['plan_id']}/execute", intercept)
                setup.locator('input[type="password"]').fill(harness.TEST_CREDENTIAL)
                setup.get_by_role('button', name='执行已批准计划', exact=True).click()
                expect(setup.get_by_text('INSTALL 执行完成', exact=False)).to_be_visible()
                assert executed == [{'plan_hash': approved['sha256'], 'executor': 'local'}]
                assert not any('/approve' in r.url or '/approval-grants' in r.url for r in requests)
                page.wait_for_load_state('networkidle')
                if screenshot:
                    setup.screenshot(path=str(screenshot))
                page.locator('select').first.select_option(other)
                expect(setup.get_by_text(plan['plan_id'], exact=False)).to_have_count(0)
                # Activation UI contract only. API activation and the live
                # resident Worker are exercised by test_runtime_activation.py.
                runtime_state = {'status': 'NOT_ACTIVATED', 'render_provider': 'mock', 'error': None,
                                 'candidate': {'project_id': project, 'recipe_sha256': 'f' * 64}}
                page.route('**/setup/runtime', lambda route: route.fulfill(
                    status=200, content_type='application/json', body=json.dumps(runtime_state)))
                activation_requests = []
                def intercept_activation(route):
                    activation_requests.append(route.request.post_data_json)
                    route.fulfill(status=200, content_type='application/json', body=json.dumps({
                        **runtime_state, 'status': 'ACTIVE', 'render_provider': 'comfyui'}))
                page.route('**/setup/runtime/activate', intercept_activation)
                page.reload(wait_until='networkidle')
                page.locator('select').first.select_option(other)
                expect(setup.get_by_role('button', name='确认并激活运行配置', exact=True)).to_be_disabled()
                page.locator('select').first.select_option(project)
                setup.locator('input[type="password"]').fill(harness.TEST_CREDENTIAL)
                setup.get_by_role('button', name='确认并激活运行配置', exact=True).click()
                expect(setup.get_by_text('运行配置：已激活', exact=False)).to_be_visible()
                expect(setup.get_by_role('button', name='确认并激活运行配置', exact=True)).to_be_enabled(timeout=30000)
                page.wait_for_load_state('networkidle')
                assert activation_requests == [{'project_id': project, 'recipe_sha256': 'f' * 64}]
                if screenshot:
                    setup.screenshot(path=str(screenshot.with_name('runtime-activation-ui-contract.png')))
                print(json.dumps({'result': 'PASS_UI_CONTRACT_ONLY', 'restored_plan': plan['plan_id'],
                                  'approved_hash_used': True, 'duplicate_approval': False,
                                  'project_isolation': True, 'activation_intercepted': True,
                                  'real_install': 'NOT_RUN'}))
            finally:
                browser.close()


if __name__ == '__main__':
    harness.exercise = exercise
    harness.main()
