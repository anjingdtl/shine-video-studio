"""Isolated V1.3 browser acceptance. Every media output and approval is a test fixture.

Requires Playwright 1.62.0, Chromium, uv, npm and the installed project dependencies.
Starts its own API/Vite on unused loopback ports with a temporary database and a
public test-only operator credential. Never connects to an existing Shine/ComfyUI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

TEST_CREDENTIAL = "isolated-v13-test-operator-credential-12345"


def exercise(api_base, ui_base, screenshot, temporary, browser_path=None):
    base = api_base + "/api/v1"
    headers = {"Authorization": "Bearer " + TEST_CREDENTIAL}
    c = httpx.Client(timeout=20)

    def post(path, body=None, privileged=False):
        r = c.post(base + path, json=body or {}, headers=headers if privileged else {})
        assert r.status_code in (200, 201), (path, r.status_code, r.text)
        return r.json()

    def approve(row, action, path):
        g = post(
            "/preparation/approval-grants",
            {"action": action, "target_id": row["id"]},
            True,
        )
        return post(
            path,
            {k: g[k] for k in ("grant_id", "snapshot")}
            | {
                "offline": True,
                "evidence": "Explicit isolated browser QA fixture review",
            },
            True,
        )

    p = post("/projects", {"title": "V1.3 Browser QA fixture"})["id"]
    # Only tiny synthetic source bytes are copied. The exact production file installer
    # runs with a cache-only transport; no third-party install or inference is executed.
    source_bytes = b"# isolated Fake lifecycle source - never execute\n"
    source = post(
        "/comfyops/packages",
        {
            "name": "browser-instance-source",
            "kind": "node",
            "revision": "a" * 40,
            "license_id": "TEST-ONLY",
            "usage": "research",
            "files": [
                {
                    "url": "https://raw.githubusercontent.com/fixture/main.py",
                    "relative_path": "main.py",
                    "size": len(source_bytes),
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                }
            ],
        },
    )
    imported_source = c.post(
        base + f"/comfyops/packages/{source['id']}/offline-files",
        headers=headers,
        data={"relative_path": "main.py"},
        files={"file": ("main.py", source_bytes)},
    )
    imported_source.raise_for_status()
    file_plan = post(
        "/comfyops/install-plans",
        {
            "project_id": p,
            "package_ids": [source["id"]],
            "disk_free": 1000,
            "executor": "local",
        },
    )
    file_plan = approve(
        file_plan, "install", f"/comfyops/install-plans/{file_plan['id']}/approve"
    )
    from shine.comfyops.local import execute_approved
    from shine.comfyops.installer import LocalInstaller, ControlledDownloader
    from shine.config import Settings
    from shine.db import make_engine, make_session_factory

    class NoNetwork:
        def stream(self, *args):
            raise AssertionError(
                "Browser QA installer must only use the uploaded cache"
            )

    qa_settings = Settings(
        data_dir=Path(temporary),
        database_url="sqlite:///" + (Path(temporary) / "qa.db").as_posix(),
    )
    qa_engine = make_engine(qa_settings.database_url)
    qa_factory = make_session_factory(qa_engine)
    execute_approved(
        qa_factory,
        qa_settings,
        file_plan["id"],
        file_plan["sha256"],
        "v13-browser-test-operator",
        LocalInstaller(
            Path(temporary) / "comfyops" / "isolated-packages",
            ControlledDownloader({"raw.githubusercontent.com"}, NoNetwork()),
        ),
    )
    # A normal failed production-installer path, using a transport that forbids
    # network. The browser must reconcile it without silently executing again.
    failed_package = post("/comfyops/packages", {
        "name": "browser-failed-package", "kind": "model", "revision": "fixture-v1",
        "license_id": "TEST-ONLY", "usage": "research",
        "files": [{"url": "https://huggingface.co/fixture/data", "relative_path": "data.bin",
                   "size": 4, "sha256": hashlib.sha256(b"data").hexdigest()}],
    })
    failed_plan = post("/comfyops/install-plans", {
        "project_id": p, "package_ids": [failed_package["id"]], "disk_free": 100, "executor": "local",
    })
    failed_plan = approve(failed_plan, "install", f"/comfyops/install-plans/{failed_plan['id']}/approve")
    try:
        execute_approved(qa_factory, qa_settings, failed_plan["id"], failed_plan["sha256"],
                         "v13-browser-test-operator", LocalInstaller(
                             Path(temporary) / "comfyops" / "isolated-packages",
                             ControlledDownloader({"huggingface.co"}, NoNetwork())))
    except AssertionError as exc:
        assert "must only use the uploaded cache" in str(exc)
    else:
        raise AssertionError("Failed-install fixture unexpectedly completed")
    qa_engine.dispose()

    from shine.comfyops.assets import fake_image

    imported = c.post(
        base + f"/projects/{p}/assets",
        data={"asset_kind": "image"},
        files={
            "file": (
                "isolated-browser-upload.png",
                fake_image("browser-legacy-approval"),
                "image/png",
            ),
        },
    )
    imported.raise_for_status()
    legacy_asset_id = imported.json()["id"]
    fields = [
        "photography_language",
        "shot_blocking",
        "color_palette",
        "lighting_tone",
        "lens_texture",
        "editing_rhythm",
        "sound_performance",
    ]
    r = c.put(
        base + f"/projects/{p}/style/draft",
        json={f: "Restrained natural cinematic treatment" for f in fields},
    )
    r.raise_for_status()
    doc = c.post(
        base + f"/projects/{p}/documents",
        files={
            "file": ("script.txt", "BrowserCharacter：你好。".encode(), "text/plain")
        },
    ).json()
    post(f"/projects/{p}/preproduction/analyze", {"document_id": doc["id"]})
    right = post(
        "/preparation/rights",
        {
            "project_id": p,
            "scope": "asset",
            "license_id": "TEST-ONLY",
            "purpose": "offline browser QA",
            "evidence": "Synthetic fixtures only",
        },
    )
    right = approve(
        right, "rights_approve", f"/preparation/rights/{right['id']}/approve"
    )
    wf = post(
        "/workflow-capabilities/assemble",
        {"template": "fake-image-v1", "name": "browser Fake image"},
    )
    post(
        f"/workflow-capabilities/{wf['id']}/validate",
        {"object_info": {"ShineFake": {"input": {"required": {"prompt": ["STRING"]}}}}},
    )
    post(f"/workflow-capabilities/{wf['id']}/fake-execute")
    asset = post(
        f"/projects/{p}/candidate-assets/generate",
        {
            "family": "character",
            "entity_id": "BrowserCharacter",
            "rights_id": right["id"],
            "prompt": "Synthetic browser fixture",
            "workflow_id": wf["id"],
        },
    )
    post(f"/candidate-assets/{asset['id']}/qc")
    vr = post(
        "/preparation/rights",
        {
            "project_id": p,
            "scope": "voice_design",
            "license_id": "TEST-ONLY",
            "purpose": "offline browser QA",
            "evidence": "Synthetic fixture voice only",
        },
    )
    vr = approve(vr, "rights_approve", f"/preparation/rights/{vr['id']}/approve")
    voice = post(
        f"/projects/{p}/voice-profiles/design",
        {
            "character": "BrowserVoice",
            "description": "Original fixture test tone",
            "text": "这是用于离线软件验证的音色试听。",
            "rights_id": vr["id"],
            "asset_rights_id": right["id"],
        },
    )
    vc = post(f"/candidate-assets/{voice['payload']['candidate_id']}/qc")
    approve(vc, "promote", f"/candidate-assets/{vc['id']}/promote")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=str(browser_path) if browser_path else shutil.which("chromium") or None,
            headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        failures = []
        page.on(
            "response",
            lambda response: failures.append((response.status, response.url))
            if response.status >= 400 and "/api/" in response.url
            else None,
        )
        page.goto(ui_base, wait_until="networkidle")
        page.locator("select").first.select_option(p)
        page.get_by_label("V1.3 风格冻结操作员凭据（仅当前页面内存）").fill(
            TEST_CREDENTIAL
        )
        page.locator("#style").get_by_role(
            "button", name="批准并冻结", exact=True
        ).click()
        expect(page.locator("#style")).to_contain_text("已冻结 V1", timeout=15000)
        masters = page.locator("#assets")
        masters.get_by_label("主资产审批凭据").fill(TEST_CREDENTIAL)
        masters.get_by_label("主资产核验依据").fill(
            "Explicit isolated browser master review"
        )
        master = masters.locator(".asset-row").filter(
            has_text="isolated-browser-upload.png"
        )
        master.get_by_role("button", name="批准", exact=True).click()
        expect(master).to_contain_text("已批准", timeout=15000)
        master.get_by_role("button", name="锁定", exact=True).click()
        expect(master).to_contain_text("已锁定", timeout=15000)
        prep = page.locator("#preproduction")
        prep.get_by_label("操作员凭据", exact=True).fill(TEST_CREDENTIAL)
        prep.get_by_label("人工核验 / 试听 / 权利证据").fill(
            "Explicit isolated browser lifecycle approval"
        )
        prep.get_by_role("button", name="读取执行器与授权扫描范围", exact=True).click()
        expect(
            prep.get_by_label("选择扫描范围 JSON（仅可缩小管理员白名单）")
        ).to_have_value('{\n  "roots": [],\n  "ports": []\n}')

        def result_json():
            return json.loads(prep.locator("details[open] pre").last.inner_text())

        def lifecycle(action, snapshot_id=None):
            prep.get_by_role("button", name="环境中心", exact=True).click()
            operation = {"name": "browser-studio", "action": action, "port": 8190}
            if action == "CREATE":
                operation["source_package_id"] = source["id"]
            if snapshot_id:
                operation["snapshot_id"] = snapshot_id
            prep.get_by_label(
                "隔离实例操作（CREATE / INSTALL_NODE / BIND_MODELS / START / STOP / SNAPSHOT / RESTORE / ROLLBACK）"
            ).fill(json.dumps(operation))
            prep.get_by_role(
                "button",
                name="生成实例操作计划（到模型与节点中心审批执行）",
                exact=True,
            ).click()
            page.wait_for_function(
                "() => document.querySelector('#preproduction details[open] pre')?.textContent?.includes('AWAITING_APPROVAL')"
            )
            plan_id = result_json()["id"]
            prep.get_by_role("button", name="模型与节点", exact=True).click()
            card = prep.locator("article").filter(has_text=plan_id)
            card.get_by_role("button", name="批准此哈希计划", exact=True).click()
            expect(card).to_contain_text("APPROVED", timeout=15000)
            card.get_by_role(
                "button", name="模拟执行（无系统变更）", exact=True
            ).click()
            expect(card).to_contain_text("MOCK_VERIFIED", timeout=15000)
            return result_json()

        prep.get_by_role("button", name="操作员保存受限扫描范围", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('#preproduction details[open] pre')?.textContent?.includes('configuration_hash')"
        )
        assert result_json()["roots"] == [] and result_json()["ports"] == []
        prep.get_by_role("button", name="模型与节点", exact=True).click()
        prep.get_by_label("Package ID", exact=True).fill(source["id"])
        prep.get_by_label("离线文件在固定清单中的相对路径", exact=True).fill("main.py")
        prep.get_by_label(
            "导入固定摘要的离线模型 / 节点 / wheel", exact=True
        ).set_input_files(
            {"name": "main.py", "mimeType": "text/x-python", "buffer": source_bytes}
        )
        page.wait_for_function(
            "() => document.querySelector('#preproduction details[open] pre')?.textContent?.includes('USER_OFFLINE_IMPORT')"
        )
        assert result_json()["sha256"] == hashlib.sha256(source_bytes).hexdigest()

        created_instance = lifecycle("CREATE")
        managed_id = created_instance["payload"]["instance_id"]
        lifecycle("START")
        lifecycle("STOP")
        saved = lifecycle("SNAPSHOT")
        lifecycle("RESTORE", saved["id"])
        prep.get_by_role("button", name="环境中心", exact=True).click()
        prep.get_by_label("受管实例 ID", exact=True).fill(managed_id)
        prep.get_by_role("button", name="核验实例健康与队列", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('#preproduction details[open] pre')?.textContent?.includes('ONLINE')"
        )
        assert result_json()["is_mock"] is True
        prep.get_by_role("button", name="全资产工厂", exact=True).click()
        prep.get_by_label("操作员凭据", exact=True).fill(TEST_CREDENTIAL)
        prep.get_by_label("人工核验 / 试听 / 权利证据").fill(
            "Explicit isolated browser QA fixture review"
        )
        card = prep.locator("article").filter(has_text="BrowserCharacter")
        expect(card).to_contain_text("READY_FOR_REVIEW")
        prep.get_by_label("操作模式").select_option("local")
        prep.get_by_label("我已核对服务器配置的范围、来源、许可和具体操作风险").check()
        expect(card).to_contain_text("READY_FOR_REVIEW")
        # Merely selecting local mode cannot relabel the existing Fake candidate.
        assert asset["payload"]["provenance"]["is_mock"]
        prep.get_by_label("操作模式").select_option("fake")
        card.get_by_role("button", name="按选定模式人工晋升", exact=True).click()
        expect(card).to_contain_text("PROMOTED", timeout=15000)
        prep.get_by_role("button", name="Voice Studio", exact=True).click()
        vcard = prep.locator("article").filter(has_text="BrowserVoice")
        expect(vcard).to_contain_text("PREVIEW")
        vcard.get_by_role("button", name="操作员确认试听并冻结", exact=True).click()
        expect(vcard).to_contain_text("FROZEN", timeout=15000)
        vcard.get_by_role(
            "button", name="生成对白 / ASR / 逐字对齐 / SRT / ASS", exact=True
        ).click()
        expect(prep.get_by_role("link", name="下载 SRT")).to_be_visible(timeout=15000)
        href = prep.get_by_role("link", name="下载 SRT").get_attribute("href")
        assert "-->" in c.get(api_base + href).text
        prep.get_by_role("button", name="模型与节点", exact=True).click()
        failed_card = prep.locator("article").filter(has_text=failed_plan["id"])
        failed_card.get_by_role("button", name="查看安装尝试", exact=True).click()
        expect(failed_card).to_contain_text("FAILED_RECONCILE_REQUIRED")
        # Fake request mode cannot authorize real filesystem reconciliation.
        expect(failed_card.get_by_role("button", name="核对安装失败（不重试）", exact=True)).to_be_disabled()
        prep.get_by_label("操作模式").select_option("local")
        prep.get_by_label("我已核对服务器配置的范围、来源、许可和具体操作风险").check()
        failed_card.get_by_role("button", name="核对安装失败（不重试）", exact=True).click()
        expect(failed_card).to_contain_text("RECONCILED_NO_RETRY", timeout=15000)
        expect(failed_card).to_contain_text("继续安装须新建并批准计划")
        prep.get_by_label("安装磁盘预算（字节，须核对空间）").fill("100")
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/comfyops/install-plans")) as fresh_response:
            failed_card.get_by_role("button", name="从原清单新建待批准计划", exact=True).click()
        fresh_plan = fresh_response.value.json()
        assert fresh_plan["status"] == "AWAITING_APPROVAL"
        assert fresh_plan["payload"]["packages"] == failed_plan["payload"]["packages"]
        attempts = c.get(base + f"/comfyops/install-plans/{failed_plan['id']}/attempts").json()
        assert len(attempts) == 1 and attempts[0]["status"] == "RECONCILED_NO_RETRY"
        assert c.get(base + f"/comfyops/install-plans/{fresh_plan['id']}/attempts").json() == []
        assert not (Path(temporary) / "comfyops/isolated-packages/browser-failed-package").exists()
        assert TEST_CREDENTIAL not in page.evaluate(
            "JSON.stringify(localStorage) + JSON.stringify(sessionStorage)"
        )
        # UI-only modes: intercept every POST before it reaches the API. These
        # profiles are deliberately not registered or presented as real evidence.
        styles = c.get(base + f"/projects/{p}/styles").json()
        routing_shot = post(f"/projects/{p}/shots", {
            "name": "Mode routing UI fixture",
            "spec": {"story_beat": "Synthetic UI routing only", "duration_target_s": 5,
                     "style_version_id": styles[0]["id"], "camera": {"shot_size": "medium"}},
        })
        profiles = [
            {"id": f"ui-only-{mode}", "name": f"UI fixture NO SUBMISSION {mode}", "mode": mode,
             "parameter_map": {}, "required_nodes": [], "checkpoint_id": "UI_ONLY",
             "graph_sha256": "0" * 64, "validated_against": None, "created_at": "2026-10-25"}
            for mode in ("T2VA", "Ref2VA")
        ]
        page.route("**/api/v1/workflow-profiles?*", lambda route: route.fulfill(json=profiles))
        submissions = []

        def capture_submission(route):
            if route.request.method == "POST":
                submissions.append(route.request.post_data_json)
                route.fulfill(status=201, json={"test_only": "NOT_SUBMITTED"})
            else:
                route.continue_()

        page.route(f"**/api/v1/projects/{p}/jobs", capture_submission)
        page.reload(wait_until="networkidle")
        page.locator("select").first.select_option(p)
        page.get_by_label("提交方式").select_option("comfyui")
        # The default basic self-review scope checks the expected output size, so
        # the workbench only offers the real-generation confirmation once the
        # operator declares the width and height. Fill them exactly as an
        # operator would before expecting the confirmation dialog.
        page.get_by_label("预期输出宽度").fill("768")
        page.get_by_label("预期输出高度").fill("1344")
        queue = page.get_by_role("button", name="加入队列", exact=True).first
        messages = []
        for mode in ("T2VA", "Ref2VA"):
            page.get_by_label("真实工作流 Profile").select_option(f"ui-only-{mode}")
            page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
            queue.click()
            assert f"提交真实 {mode} 生成" in messages[-1]
            assert len(submissions) == (0 if mode == "T2VA" else 1), submissions
            page.once("dialog", lambda dialog: dialog.accept())
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(f"/projects/{p}/jobs")):
                queue.click()
            assert submissions[-1]["generation_mode"] == mode
            assert submissions[-1]["workflow_profile_id"] == f"ui-only-{mode}"
        assert c.get(base + f"/projects/{p}/jobs").json() == [], "UI routing probe must create no backend jobs"
        page.get_by_label("提交方式").select_option("mock")
        # Hold an old project's asset response until a different project has
        # finished loading. A late callback must not replace the new selection.
        # Protocol-only UI fixtures. These requests never become real capabilities or jobs.
        workflow_fixtures = [
            {"id": "ui-native-draft", "status": "DRAFT", "sha256": "a" * 64,
             "payload": {"name": "Mock native draft", "category": "VLM_REVIEW", "native_provider": {"fixture": True}}},
            {"id": "ui-native-evidence", "status": "STATIC_VALIDATED", "sha256": "b" * 64,
             "payload": {"name": "Mock native calibration", "category": "ASR", "native_provider": {"fixture": True}}},
            {"id": "ui-h3-evidence", "status": "STATIC_VALIDATED", "sha256": "c" * 64,
             "payload": {"name": "Mock H3 job evidence", "category": "H3_T2VA"}},
        ]
        registry_url = "**/api/v1/workflow-capabilities"
        page.route(registry_url, lambda route: route.fulfill(json={
            "categories": ["VLM_REVIEW", "ASR", "H3_T2VA"], "revisions": workflow_fixtures, "legacy_profiles": []}))
        grants_url = "**/api/v1/preparation/approval-grants"
        page.route(grants_url, lambda route: route.fulfill(json={"grant_id": "ui-only-grant", "snapshot": "a" * 64}))
        workflow_checks = []

        def workflow_request(route):
            data = route.request.post_data_json
            workflow_checks.append({"path": route.request.url.split("/api/v1")[-1], "body": data})
            assert route.request.headers.get("authorization") == "Bearer " + TEST_CREDENTIAL
            route.fulfill(json={"status": "MOCK_VERIFIED", "is_mock": True, "no_backend_execution": True})

        workflow_paths = [
            "**/api/v1/workflow-capabilities/ui-native-draft/validate-native",
            "**/api/v1/workflow-capabilities/ui-native-evidence/native-execution-evidence",
            "**/api/v1/workflow-capabilities/ui-h3-evidence/local-evidence",
        ]
        for path in workflow_paths:
            page.route(path, workflow_request)
        prep.get_by_role("button", name="工作流能力", exact=True).click()
        prep.get_by_label("操作模式").select_option("local")
        prep.get_by_label("操作员凭据", exact=True).fill(TEST_CREDENTIAL)
        prep.get_by_label("人工核验 / 试听 / 权利证据").fill("Mock UI protocol check only; no real acceptance")
        prep.get_by_label("我已核对服务器配置的范围、来源、许可和具体操作风险").check()
        native_draft = prep.locator("article").filter(has_text="ui-native-draft")
        native_evidence = prep.locator("article").filter(has_text="ui-native-evidence")
        h3_evidence = prep.locator("article").filter(has_text="ui-h3-evidence")
        expect(native_draft.get_by_role("button", name="使用 Fake 节点目录验证", exact=True)).to_have_count(0)
        expect(native_evidence.get_by_role("button", name="Fake HTTP 执行契约", exact=True)).to_have_count(0)
        expect(native_draft.get_by_label("工作流验证实例 ID")).to_have_count(0)
        with page.expect_response(lambda response: response.url.endswith("/ui-native-draft/validate-native")):
            native_draft.get_by_role("button", name="读取独立审片 / ASR 执行器验证", exact=True).click()
        page.wait_for_load_state("networkidle")
        prep.get_by_label("本地执行证据 JSON", exact=True).fill(json.dumps({"samples": [{"call_id": "ui-only", "result": {"is_mock": True}}]}))
        with page.expect_response(lambda response: response.url.endswith("/ui-native-evidence/native-execution-evidence")):
            native_evidence.get_by_role("button", name="核验已完成的审片 / ASR 校准记录", exact=True).click()
        page.wait_for_load_state("networkidle")
        prep.get_by_label("本地执行证据 JSON", exact=True).fill(json.dumps({"job_id": "ui-only-h3", "graph_sha256": "d" * 64}))
        with page.expect_response(lambda response: response.url.endswith("/ui-h3-evidence/local-evidence")):
            h3_evidence.get_by_role("button", name="提交并核验本地执行证据", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert len(workflow_checks) == 3
        assert workflow_checks[0]["body"] == {}
        assert workflow_checks[1]["body"]["project_id"] == p
        assert workflow_checks[1]["body"]["samples"][0]["result"]["is_mock"] is True
        assert workflow_checks[2]["body"]["job_id"] == "ui-only-h3"
        assert all(check["body"].get("offline") is False for check in workflow_checks[1:])
        if screenshot:
            prep.screenshot(path=str(screenshot.with_name(screenshot.stem + "-native.png")))
        for path in workflow_paths + [grants_url, registry_url]:
            page.unroute(path)
        # Text-only critic UI protocol. No remote model call or real proposal.
        roles_url = "**/api/v1/film-agents"
        lock_url = f"**/api/v1/projects/{p}/rulepack"
        critic_url = f"**/api/v1/projects/{p}/film-agents/qc_reviewer/run"
        page.route(roles_url, lambda route: route.fulfill(json=[{
            "role": "qc_reviewer", "label": "Input critic UI fixture", "model": "UI_ONLY",
            "profile_id": "ui-only-text", "output_kind": "StyleInputAssessment", "required_inputs": [],
            "permissions": "Mock protocol fixture only", "tool_allowlist": ["propose"],
        }]))
        page.route(lock_url, lambda route: route.fulfill(json={"rulepack_id": "UI_ONLY"}))
        critic_calls = []

        def capture_critic(route):
            critic_calls.append(route.request.post_data_json)
            route.fulfill(status=201, json={"scope": "MOCK_UI_NO_MODEL_CALL"})

        page.route(critic_url, capture_critic)
        page.reload(wait_until="networkidle")
        crew = page.locator("#film-crew")
        crew.get_by_label("执行工种").select_option("qc_reviewer")
        expect(crew.get_by_role("button", name="运行工种", exact=True)).to_be_disabled()
        crew.get_by_label("检查镜头输入").select_option(routing_shot["id"])
        consent = crew.get_by_label("允许本次工种向所选远端模型发送", exact=False)
        consent.check()
        with page.expect_response(lambda response: response.url.endswith("/film-agents/qc_reviewer/run")):
            crew.get_by_role("button", name="运行工种", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert len(critic_calls) == 1
        assert critic_calls[0]["shot_spec"] == routing_shot["spec"]
        assert critic_calls[0]["input_revision_ids"] == [] and critic_calls[0]["allow_remote_text"] is True
        expect(consent).not_to_be_checked()
        runs_url = f"**/api/v1/projects/{p}/film-runs"
        abandon_url = f"**/api/v1/projects/{p}/film-runs/ui-unknown-text-task/abandon-remote-response"
        page.route(runs_url, lambda route: route.fulfill(json=[{
            "id": "ui-unknown-text-task", "role_id": "qc_reviewer", "status": "NEEDS_REVIEW",
            "model_id": "UI_ONLY", "error_code": "LLM_TIMEOUT", "rulepack_id": "UI_ONLY", "trace": {},
        }]))
        abandonment_calls, abandonment_grants = [], []

        def capture_text_grant(route):
            abandonment_grants.append(route.request.post_data_json)
            assert route.request.headers.get("authorization") == "Bearer " + TEST_CREDENTIAL
            assert route.request.headers.get("content-type") == "application/json"
            route.fulfill(json={"grant_id": "ui-text-only-grant", "snapshot": "a" * 64})

        def capture_abandonment(route):
            abandonment_calls.append(route.request.post_data_json)
            assert route.request.headers.get("authorization") == "Bearer " + TEST_CREDENTIAL
            assert route.request.headers.get("content-type") == "application/json"
            route.fulfill(json={"status": "ABANDONED", "external_outcome": "UNKNOWN", "automatic_replay": False})

        page.route(grants_url, capture_text_grant)
        page.route(abandon_url, capture_abandonment)
        page.reload(wait_until="networkidle")
        crew.locator("details").filter(has_text="ui-unknown-text-task").locator("summary").click()
        abandon_button = crew.get_by_role("button", name="放弃远端未知响应（不重试）", exact=True)
        expect(abandon_button).to_be_disabled()
        crew.get_by_label("远端响应对账凭据").fill(TEST_CREDENTIAL)
        crew.get_by_label("远端响应对账说明").fill("Mock UI protocol only; no model invocation or quality approval")
        page.once("dialog", lambda dialog: dialog.dismiss())
        abandon_button.click()
        assert not abandonment_calls and not abandonment_grants
        expect(crew.get_by_label("远端响应对账凭据")).to_have_value("")
        crew.get_by_label("远端响应对账凭据").fill(TEST_CREDENTIAL)
        crew.get_by_label("远端响应对账说明").fill("Mock UI protocol only; no model invocation or quality approval")
        page.once("dialog", lambda dialog: dialog.accept())
        with page.expect_response(lambda response: response.url.endswith("/abandon-remote-response")):
            abandon_button.click()
        page.wait_for_load_state("networkidle")
        assert len(abandonment_calls) == len(abandonment_grants) == 1
        assert abandonment_grants[0] == {"action": "model_reconcile", "target_id": "ui-unknown-text-task"}
        assert abandonment_calls[0]["grant_id"] == "ui-text-only-grant"
        expect(crew.get_by_label("远端响应对账凭据")).to_have_value("")
        expect(crew.get_by_label("远端响应对账说明")).to_have_value("")
        assert TEST_CREDENTIAL not in page.evaluate("JSON.stringify(localStorage) + JSON.stringify(sessionStorage)")
        for path in [runs_url, grants_url, abandon_url]:
            page.unroute(path)
        for path in [roles_url, lock_url, critic_url]:
            page.unroute(path)
        settings = page.locator("#settings")
        settings.get_by_placeholder("配置名称", exact=True).fill("UI bounded text fixture")
        settings.locator(".provider-form select").first.select_option("openai_compatible")
        settings.get_by_placeholder("Base URL", exact=True).fill("http://127.0.0.1:9999/v1")
        settings.get_by_placeholder("Model ID", exact=True).fill("UI_ONLY_NO_MODEL_CALL")
        settings.get_by_label("最大输出 token", exact=True).fill("8192")
        settings.get_by_label("思考深度", exact=True).select_option("low")
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/provider-profiles")) as bounded_response:
            settings.get_by_role("button", name="添加", exact=True).click()
        assert bounded_response.value.status == 201
        assert bounded_response.value.json()["generation_options"] == {"max_completion_tokens": 8192, "reasoning_effort": "low"}
        bounded_card = settings.locator(".profile-row").filter(has_text="UI bounded text fixture")
        expect(bounded_card).to_contain_text("输出上限 8192 token")
        expect(bounded_card).to_contain_text("思考深度 low")
        settings.locator(".provider-form select").first.select_option("anthropic")
        expect(settings.get_by_label("最大输出 token", exact=True)).to_have_count(0)
        other = post("/projects", {"title": "Late-response isolation fixture"})["id"]
        page.unroute("**/api/v1/workflow-profiles?*")
        page.reload(wait_until="networkidle")
        selector = page.locator("select").first
        selector.select_option(other)
        page.wait_for_load_state("networkidle")
        expect(page.locator("#assets .asset-row")).to_have_count(0)
        delayed = []
        old_assets_url = f"**/api/v1/projects/{p}/assets"
        page.route(old_assets_url, lambda route: delayed.append(route))
        with page.expect_request(lambda request: request.url.endswith(f"/projects/{p}/assets")):
            selector.select_option(p)
        with page.expect_response(lambda response: response.url.endswith(f"/projects/{other}/jobs")):
            selector.select_option(other)
        assert delayed, "Old project request was not held"
        for route in delayed:
            route.fulfill(json=c.get(base + f"/projects/{p}/assets").json())
        page.wait_for_load_state("networkidle")
        expect(selector).to_have_value(other)
        expect(page.locator("#assets .asset-row")).to_have_count(0)
        expect(page.get_by_text("Mode routing UI fixture", exact=True)).to_have_count(0)
        page.unroute(old_assets_url)
        if screenshot:
            page.screenshot(path=str(screenshot), full_page=True)
        browser.close()
        assert not failures, failures
    styles = c.get(base + f"/projects/{p}/styles").json()
    assets = c.get(base + f"/projects/{p}/assets").json()
    voices = c.get(base + f"/projects/{p}/voice-profiles").json()
    assert styles[0]["approved_by"] == "v13-browser-test-operator"
    assert any(
        a["entity_id"] == "BrowserCharacter" and a["approved"] and a["locked"]
        for a in assets
    )
    assert next(a for a in assets if a["id"] == legacy_asset_id)["locked"]
    assert voices[0]["status"] == "FROZEN" and voices[0]["payload"]["is_mock"]
    print(
        json.dumps(
            {
                "status": "MOCK_VERIFIED",
                "project_id": p,
                "flows": [
                    "authenticated style freeze",
                    "native reviewer validation/calibration routing, no Fake node controls (intercepted)",
                    "H3 job_id evidence uses the stronger local-evidence route (intercepted)",
                    "offline package cache and real file installer",
                    "scope configuration inspection and grant-authorized save",
                    "offline file import through browser control",
                    "instance create/start/stop/snapshot/restore/health via UI",
                    "legacy asset approval and lock grants",
                    "mode selection preserves Fake provenance",
                    "candidate promotion",
                    "explicit voice listening and freeze",
                    "TTS/ASR/alignment",
                    "SRT download",
                    "no browser credential persistence",
                    "T2VA/Ref2VA selection and cancel/confirm payload routing (intercepted; zero backend jobs)",
                    "failed-install history, authenticated reconciliation, fresh unapproved plan; no replay or package writes",
                    "late old-project responses cannot overwrite selected-project assets/shots",
                    "independent text critic requires selected shot, routes exact spec and resets per-call consent (intercepted)",
                    "remote text response abandonment cancel/confirm, exact authenticated grant and credential clearing (intercepted; no model call)",
                    "explicit text output budget and reasoning depth persist from UI without a model call",
                ],
                "api_errors": failures,
            }
        )
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url):
    for _ in range(80):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("Isolated QA server did not start")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--browser-path", type=Path, help="Use an already installed Chromium executable")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    npm = shutil.which("npm")
    if not uv or not npm:
        raise RuntimeError("uv and npm are required")
    api_port, ui_port = free_port(), free_port()
    while ui_port == api_port:
        ui_port = free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    ui_base = f"http://127.0.0.1:{ui_port}"
    children = []
    with tempfile.TemporaryDirectory(prefix="shine-v13-web-qa-") as temporary:
        environment = dict(os.environ)
        environment.update(
            {
                "SHINE_DATA_DIR": temporary,
                "SHINE_DATABASE_URL": "sqlite:///"
                + (Path(temporary) / "qa.db").as_posix(),
                "SHINE_OPERATOR_ID": "v13-browser-test-operator",
                "SHINE_OPERATOR_TOKEN_SHA256": hashlib.sha256(
                    TEST_CREDENTIAL.encode()
                ).hexdigest(),
                "SHINE_COMFYUI_URL": "http://127.0.0.1:9",
                "SHINE_ALLOW_REMOTE_COMFYUI": "0",
                "SHINE_RENDER_PROVIDER": "mock",
                "SHINE_LOCAL_IMAGE_URL": "",
                "SHINE_LOCAL_VOICE_URL": "",
                "SHINE_COMFY_PROBE_ROOTS": "[]",
                "SHINE_COMFY_PROBE_PORTS": "[]",
                "SHINE_OFFLINE_SIMULATION": "0",
                "SHINE_VITE_API_TARGET": api_base,
            }
        )
        backend = [uv, "run", "--project", str(repo / "backend"), "--frozen"]
        subprocess.run(
            backend
            + ["alembic", "-c", str(repo / "backend/alembic.ini"), "upgrade", "head"],
            cwd=repo,
            env=environment,
            check=True,
            capture_output=True,
        )
        try:
            commands = [
                backend
                + [
                    "uvicorn",
                    "shine.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                [
                    npm,
                    "--prefix",
                    str(repo / "frontend"),
                    "run",
                    "dev",
                    "--",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(ui_port),
                    "--strictPort",
                ],
            ]
            for command in commands:
                children.append(
                    subprocess.Popen(
                        command,
                        cwd=repo,
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=os.name != "nt",
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                        if os.name == "nt"
                        else 0,
                    )
                )
            wait_http(api_base + "/health")
            wait_http(ui_base)
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            exercise(api_base, ui_base, args.screenshot, temporary, args.browser_path)
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                            check=False,
                            capture_output=True,
                        )
                    else:
                        os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=20)


if __name__ == "__main__":
    main()
