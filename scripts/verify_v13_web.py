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


def exercise(api_base, ui_base, screenshot, temporary):
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
            executable_path=shutil.which("chromium") or None,
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
                "隔离实例操作（CREATE / INSTALL_NODE / BIND_MODELS / START / STOP / SNAPSHOT / RESTORE）"
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
        assert TEST_CREDENTIAL not in page.evaluate(
            "JSON.stringify(localStorage) + JSON.stringify(sessionStorage)"
        )
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
                    "offline package cache and real file installer",
                    "scope configuration inspection",
                    "instance create/start/stop/snapshot/restore/health via UI",
                    "legacy asset approval and lock grants",
                    "mode selection preserves Fake provenance",
                    "candidate promotion",
                    "explicit voice listening and freeze",
                    "TTS/ASR/alignment",
                    "SRT download",
                    "no browser credential persistence",
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
        backend = [uv, "run", "--project", str(repo / "backend")]
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
            exercise(api_base, ui_base, args.screenshot, temporary)
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
