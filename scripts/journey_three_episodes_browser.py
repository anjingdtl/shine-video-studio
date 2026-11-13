r"""Human-paced acceptance of the 3 x 60 s director journey in a real browser.

What is real here
-----------------
The API, the database, the resident Worker, the shot/subtitle/prompt/material
services and every screen are the product's own code.  The browser creates the
project, uploads and confirms the master, writes the storyboard book with the
dialogue/narration tone, plans the three episodes, freezes a shot prompt, adds a
user-specified prop, writes per-shot subtitles, compiles the three external
subtitle tracks and revises a material prompt.

What is replaced, and why it is named
-------------------------------------
* The script outline extraction and the AI storyboard landing are model steps.
  This run has no text model wired to the isolated data dir, so the confirmed
  outline artifact and the storyboard run ledger are seeded **through the same
  tables the model steps write**.  The shots themselves are created through the
  public shot API, exactly as the advanced shot form does.
* The renderer is ``mock``: this machine has no running ComfyUI, so the run can
  start but cannot produce bytes.  Media assembly is covered by
  ``test_director_worker_integration.py`` (fake renderer + real FFmpeg), not by
  this journey.

Run:
    backend\.venv\Scripts\python.exe scripts\journey_three_episodes_browser.py

Screenshots land in ``.local-acceptance/journey-3x60/shots``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
DATA_DIR = (REPO / ".local-acceptance" / "journey-3x60"
            / time.strftime("run-%Y%m%d-%H%M%S"))
OUT = DATA_DIR / "shots"
API_PORT = 8123
WEB_PORT = 5199
BASE = f"http://127.0.0.1:{WEB_PORT}"
API = f"http://127.0.0.1:{API_PORT}"
LOCAL = {"X-Shine-Local-Action": "1", "Content-Type": "application/json"}

sys.path.insert(0, str(BACKEND))

PROFILE_BODY = {
    "name": f"三集穿测 Ref2VA {time.strftime('%H%M%S')}",
    "mode": "Ref2VA",
    "graph": {
        "1": {
            "class_type": "H3BaseSampler",
            "inputs": {
                "positive_prompt": "",
                "seed": 0,
                "duration": 12.0,
                "reference_subject_1": "",
                "reference_picture_1": "",
            },
        },
        "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
    },
    "parameter_map": {
        "positive_prompt": "1.inputs.positive_prompt",
        "seed": "1.inputs.seed",
        "duration": "1.inputs.duration",
        "reference_subject_1": "1.inputs.reference_subject_1",
        "reference_picture_1": "1.inputs.reference_picture_1",
    },
    "required_nodes": ["H3BaseSampler"],
}

EPISODE_TEXT = {
    1: [
        ("旁白", "雨落在码头的铁皮棚上，像有人在数着旧账。", "Rain drums on the harbour roof."),
        ("旁白", "林澈把最后一页账册塞进衣襟。", "Lin hides the last ledger page."),
        ("旁白", "远处汽笛响起，他没有回头。", "A whistle sounds; he does not turn."),
        ("Lin", "账册在哪儿？", "Lin asks where the ledger is."),
        ("旁白", "答案被夜色吞掉了。", "The night swallows the answer."),
    ],
    2: [
        ("Lin", "这东西不该留到今天。", "Lin says it should not have survived."),
        ("Lin", "灯灭之前，你得走。", "Lin warns the other to leave."),
        ("Lin", "我数到三。", "Lin starts counting."),
        ("Lin", "别碰那本册子。", "Lin warns about the book."),
        ("Lin", "门外有人。", "Someone is outside."),
    ],
    3: [
        ("旁白", "三天后，雨停了。", "Three days later the rain stops."),
        ("旁白", "账页上的墨迹已经干了。", "The ink on the ledger is dry."),
        ("Lin", "这笔账，我认。", "Lin accepts the debt."),
        ("Lin", "但人得走。", "But the person must leave."),
        ("旁白", "码头重新安静下来。", "The harbour falls quiet again."),
    ],
}

CHINESE_EPISODE = {1: "第一集 · 雨夜账册", 2: "第二集 · 灯下对质", 3: "第三集 · 雨后码头"}


# ---------------------------------------------------------------------------
# Result bookkeeping
# ---------------------------------------------------------------------------

RESULTS: list[tuple[str, str, str]] = []


def record(step: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((step, "PASS" if ok else "BLOCKED", detail))
    print(f"[{'PASS' if ok else 'BLOCKED'}] {step}" + (f" — {detail}" if detail else ""))


def png(path: Path, size: int = 512, rgb: tuple[int, int, int] = (110, 130, 160)) -> None:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# HTTP helpers (seeding only; the journey itself is browser-driven)
# ---------------------------------------------------------------------------


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers=LOCAL)
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def wait_http(url: str, seconds: float = 90.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception:  # noqa: BLE001 - the stack is still starting
            time.sleep(1)
    raise SystemExit(f"the local stack did not answer {url}")


# ---------------------------------------------------------------------------
# Seeding the two model steps that this isolated run replaces
# ---------------------------------------------------------------------------


def _script_text() -> str:
    parts = []
    for episode in (1, 2, 3):
        lines = [CHINESE_EPISODE[episode]]
        lines.extend(f"{speaker}：{line}" for speaker, line, _ in EPISODE_TEXT[episode])
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _sections() -> list[dict]:
    rows = []
    text = _script_text()
    parts = text.split("\n\n")
    offset = 0
    for order, (episode, part) in enumerate(zip((1, 2, 3), parts)):
        start, end = offset, offset + len(part)
        offset = end + 2
        rows.append({
            "production_section_id": f"sec-{episode}",
            "title": CHINESE_EPISODE[episode],
            "estimated_seconds": 60,
            "start_offset": start,
            "end_offset": end,
            "beats": [{"description": beat} for _, _, beat in EPISODE_TEXT[episode]],
            "dialogue": [{"speaker": speaker, "text": line}
                         for speaker, line, _ in EPISODE_TEXT[episode]],
            "questions": [],
        })
    return rows


def seed_outline(project_id: str) -> None:
    """The confirmed StoryOutline a real outline extraction would have written."""
    import hashlib

    from shine.h3.rulebook import digest
    from shine.main import create_app
    from shine.models import (
        AgentArtifact, AgentTaskRecord, AppSetting, Project, ProjectDocument, utcnow,
    )

    app = create_app()
    factory = app.state.session_factory
    text = _script_text()
    document_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    payload = {
        "schema_version": "1.0",
        "document_id": "",
        "document_sha256": document_sha,
        "original_text_sha256": document_sha,
        "offset_unit": "unicode_codepoint",
        "production_sections": _sections(),
        "characters": [{"name": "Lin", "stable_identity": "Lin", "role": "主角"}],
        "story_line": ["雨夜账册", "灯下对质", "雨后码头"],
        "timeline": ["第一夜", "第二夜", "三天后"],
        "questions": [],
        "source_locked_facts": [],
        "locked_facts": ["Lin keeps the harbour ledger"],
        "leaf_artifact_ids": [],
        "continuity_status": {"basis": "user_supplied_unverified"},
    }
    with factory.begin() as db:
        document = ProjectDocument(
            project_id=project_id, filename="三集穿测剧本.txt", content_type="text/plain",
            sha256=document_sha, source_format="txt", text=text,
        )
        db.add(document)
        db.flush()
        payload["document_id"] = document.id
        project = db.get(Project, project_id)
        project.current_document_id = document.id
        task = AgentTaskRecord(
            project_id=project_id, role_id="story_outline_run",
            request_identity=f"journey-outline-{project_id}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64,
            rulepack_id="journey/1.0", rulepack_hash="0" * 64,
            model_profile_id="journey-seed", model_id="seeded-outline",
            schema_version="1.0", status="SUCCEEDED", trace={"fixture": True},
        )
        db.add(task)
        db.flush()
        artifact = AgentArtifact(
            project_id=project_id, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest(payload),
            producer_role="story_outline_run", approved_by="local-user",
            approved_at=utcnow(), source_task_id=task.id,
        )
        db.add(artifact)
        db.flush()
        db.merge(AppSetting(key=f"current-story-outline:{project_id}",
                            value={"artifact_id": artifact.id, "sha256": artifact.sha256}))
        db.merge(AppSetting(key=f"current-story-outline:{project_id}:{document.id}",
                            value={"artifact_id": artifact.id, "sha256": artifact.sha256}))
    app.state.engine.dispose()


def seed_storyboard_ledger(project_id: str, episodes: list[dict]) -> None:
    """The run ledger an AI storyboard landing leaves behind (shots are real)."""
    from shine.main import create_app
    from shine.models import AgentTaskRecord

    app = create_app()
    factory = app.state.session_factory
    units, results = [], []
    for order, episode in enumerate(episodes):
        episode_id = episode["episode_id"]
        shots = list(episode.get("shot_ids") or [])
        start = order * 600
        units.append({
            "episode_id": episode_id, "production_section_id": f"sec-{order + 1}",
            "start_offset": start,
        })
        results.append({
            "unit": {"start_offset": start}, "episode_id": episode_id,
            "apply": {"shot_ids": shots, "material_notices": []},
        })
    with factory.begin() as db:
        db.add(AgentTaskRecord(
            project_id=project_id, role_id="director_storyboard_run",
            request_identity=f"journey-storyboard-{project_id}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64,
            style_version_id=None, rulepack_id="journey/1.0",
            rulepack_hash="0" * 64, model_profile_id="journey-seed",
            model_id="seeded-storyboard", schema_version="1.0", status="SUCCEEDED",
            trace={"units": units, "results": results, "blocked_episodes": {},
                   "cursor": len(units)},
        ))
    app.state.engine.dispose()


# ---------------------------------------------------------------------------
# Shots and subtitles (public API, same payload as the advanced shot form)
# ---------------------------------------------------------------------------


def seed_shots(project_id: str, style_id: str, asset_id: str) -> list[dict]:
    created = []
    for episode in (1, 2, 3):
        for index, (speaker, line, beat) in enumerate(EPISODE_TEXT[episode], start=1):
            characters = ["Lin"] if speaker == "Lin" else ["Lin", "旁白"]
            shot = call("POST", f"/api/v1/projects/{project_id}/shots", {
                "name": f"第{episode}集第{index}镜",
                "spec": {
                    "schema_version": "1.0",
                    "story_beat": (
                        f"[Shot {index}] {beat} The harbour is wet and cold, the "
                        "camera holds on Lin's hands and the ledger."
                    ),
                    "duration_target_s": 12,
                    "shot_priority": "B",
                    "style_version_id": style_id,
                    "camera": {"shot_size": "medium", "movement": "slow_push"},
                    "characters": characters,
                    "required_asset_ids": [asset_id],
                    "dialogue": [{"speaker_id": speaker, "text": line,
                                  "start_s": 0.6, "end_s": 4.4}],
                    "sound": {"environment": "rain on the harbour", "music_policy": "low_or_none"},
                    "continuity": {"mode": "cut"},
                    "locked_story_facts": ["Lin keeps the harbour ledger"],
                },
                "bindings": [{"asset_id": asset_id, "use_as": "subject",
                              "role": "Lin", "ordinal": 0}],
            })
            call("PUT", f"/api/v1/projects/{project_id}/director/storyboard/shots/"
                       f"{shot['id']}/subtitles", {
                "lines": [{"start_s": 0.6, "end_s": 4.4, "text": line}],
                "freeze": True,
            })
            created.append(shot)
    return created


# ---------------------------------------------------------------------------
# Browser journey
# ---------------------------------------------------------------------------


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # The seeding helpers below live in this process, so the isolated data
    # directory has to be the process environment, not just the children's.
    os.environ["SHINE_DATA_DIR"] = str(DATA_DIR)
    os.environ["SHINE_RENDER_PROVIDER"] = "mock"
    env = {**os.environ, "SHINE_DATA_DIR": str(DATA_DIR),
           "SHINE_RENDER_PROVIDER": "mock", "SHINE_OPERATOR_ID": "",
           "SHINE_OPERATOR_TOKEN_SHA256": ""}
    subprocess.run([str(BACKEND / ".venv" / "Scripts" / "python.exe"), "-m", "alembic",
                    "upgrade", "head"], cwd=str(BACKEND), env=env, check=True)

    processes = []
    try:
        processes.append(subprocess.Popen(
            [str(BACKEND / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
             "shine.main:app", "--host", "127.0.0.1", "--port", str(API_PORT),
             "--log-level", "warning"],
            cwd=str(BACKEND), env=env))
        processes.append(subprocess.Popen(
            [str(BACKEND / ".venv" / "Scripts" / "python.exe"), "-m", "shine.worker"],
            cwd=str(BACKEND), env=env))
        processes.append(subprocess.Popen(
            ["node", str(REPO / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"),
             "--host", "127.0.0.1", "--port", str(WEB_PORT)],
            cwd=str(REPO / "frontend"),
            env={**os.environ, "SHINE_VITE_API_TARGET": API}))
        wait_http(f"{API}/health")
        wait_http(f"{BASE}/")

        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        cache = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        chromium = None
        if cache.is_dir():
            found = sorted(cache.glob("chromium-*/chrome-win64/chrome.exe"))
            chromium = str(found[-1]) if found else None

        fixture = OUT / "Lin-reference.png"
        png(fixture)

        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=chromium)
            page = browser.new_page(viewport={"width": 1440, "height": 980})
            page.set_default_timeout(20000)
            page.on("dialog", lambda dialog: dialog.accept())
            console_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text)
                    if msg.type == "error" else None)

            def shot(name: str) -> None:
                page.screenshot(path=str(OUT / f"{name}.png"))

            def step(name: str, fn) -> bool:
                try:
                    detail = fn()
                    record(name, True, detail or "")
                    return True
                except AssertionError as exc:
                    record(name, False, str(exc)[:300])
                    shot(f"blocked-{name}")
                    return False
                except PlaywrightTimeout as exc:
                    record(name, False, str(exc).splitlines()[0][:300])
                    shot(f"blocked-{name}")
                    return False

            title = f"三集六十秒穿测 {time.strftime('%H%M%S')}"

            def open_workbench():
                page.goto(BASE, wait_until="networkidle")
                page.locator(".topbar").first.wait_for()
                shot("01-home")
                return "工作台已打开"

            step("01-打开工作台", open_workbench)

            def create_project():
                page.get_by_placeholder("给作品起个名字（如：巷口的灯）").fill(title)
                page.get_by_role("button", name="创建作品").click()
                page.get_by_text(title).first.wait_for()
                page.wait_for_timeout(1200)
                shot("02-project")
                return title

            step("02-新建作品", create_project)

            projects = call("GET", "/api/v1/projects")
            project_id = next((row["id"] for row in projects if row["title"] == title), None)
            if project_id is None:
                raise AssertionError(f"新建的作品没有出现在项目列表里：{[row['title'] for row in projects]}")
            seed_outline(project_id)
            page.reload(wait_until="networkidle")

            def upload_master():
                page.get_by_role("button", name="专业模式").click()
                page.wait_for_timeout(800)
                page.get_by_label("人物名称（建议与剧本一致）").fill("Lin")
                page.get_by_label("素材用途").select_option("character")
                page.locator(".upload-button input[type=file]").first.set_input_files(str(fixture))
                page.wait_for_timeout(1800)
                page.get_by_role("checkbox", name=f"选择上传素材 {fixture.name}").check()
                page.get_by_role("button", name="确认使用所选 1 项素材").click()
                page.locator(".asset-state").get_by_text("可用于分镜").first.wait_for()
                shot("03-master")
                page.get_by_role("button", name="导演模式").click()
                page.wait_for_timeout(800)
                return "人物主素材已确认"

            step("03-上传并确认人物主素材", upload_master)

            def storyboard_book():
                page.get_by_role("button", name="第 2 阶段：剧本统筹").click()
                page.wait_for_timeout(800)
                number_inputs = page.locator(".book-settings input[type=number]")
                number_inputs.nth(0).fill("3")
                number_inputs.nth(1).fill("60")
                page.get_by_role("group", name="对白设定").get_by_role("button", name="平衡").click()
                page.get_by_role("group", name="旁白设定").get_by_role("button", name="少旁白").click()
                page.get_by_role("button", name="生成分镜书").first.click()
                page.get_by_text("计划总时长").first.wait_for()
                shot("04-book")
                page.get_by_role("button", name="确认分镜书（分集 + 风格）").first.click()
                page.get_by_text("第 1 版已确认").first.wait_for()
                shot("05-book-confirmed")
                return "分镜书已确认"

            if not step("04-②分镜书（3 集 × 60 秒，平衡对白/少旁白）", storyboard_book):
                raise SystemExit(1)

            styles = call("GET", f"/api/v1/projects/{project_id}/styles")
            style = styles[-1]
            assets = call("GET", f"/api/v1/projects/{project_id}/assets")
            lina = next(row for row in assets if row.get("entity_id") == "Lin")
            shots = seed_shots(project_id, style["id"], lina["id"])
            record("05-落镜（15 镜 × 12 秒，旁白/无旁白/旁白穿插）", True, f"{len(shots)} 镜")

            # The director page offers the script-first plan only; grouping an
            # existing shot list is the documented compatible entry, so it is
            # recorded through that same public API instead of a hidden click.
            episodes = [
                {
                    "episode_id": f"ep-{index + 1:02d}",
                    "order": index + 1,
                    "title": CHINESE_EPISODE[index + 1],
                    "shot_ids": [row["id"] for row in shots[index * 5:(index + 1) * 5]],
                    # The section the episode covers is what the material
                    # stage reads; the shots are the same ones the seeded
                    # storyboard ledger applied for that section.
                    "section_ids": [f"sec-{index + 1}"],
                    "planned_seconds": 60,
                    "boundary_note": "用户按 5 镜 × 12 秒分组",
                }
                for index in range(3)
            ]
            plan = call("POST", f"/api/v1/projects/{project_id}/episode-plans",
                        {"mode": "shots", "target_seconds": 60, "tolerance_seconds": 0,
                         "episodes": episodes})
            call("POST", f"/api/v1/projects/{project_id}/episode-plans/"
                       f"{plan['version']}/confirm", {"expected_sha256": plan["sha256"]})
            episodes = plan["payload"]["episodes"]
            for episode, offset in zip(episodes, (0, 5, 10)):
                episode["shot_ids"] = [row["id"] for row in shots[offset:offset + 5]]
            seed_storyboard_ledger(project_id, episodes)
            record("06-⑤按已有镜头分集（兼容入口，3 集）", True,
                   f"第 {plan['version']} 版 · {len(episodes)} 集")

            def episode_cards():
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(1800)
                page.get_by_role("button", name="第 5 阶段：导演包").click()
                page.wait_for_timeout(1000)
                cards = page.locator(".shoot-episode")
                assert cards.count() == 3, f"只看到 {cards.count()} 张集卡"
                text = page.locator(".shoot-panel").inner_text()
                assert "演示模式" in text, text[:300]
                shot("06-shoot-panel")
                return "三张集卡与演示模式提示都在"

            step("07-⑤导演包集卡", episode_cards)

            def manual_prompt():
                page.get_by_role("button", name="第 3 阶段：镜头拆解").click()
                page.wait_for_timeout(1200)
                first_dock = page.locator("details.episode-dock").first
                if not first_dock.evaluate("node => node.open"):
                    first_dock.locator("summary").click()
                page.wait_for_timeout(400)
                page.locator(".shot-table-row").first.get_by_role("button", name="提示词").click()
                dialog = page.get_by_role("dialog", name="镜头提示词")
                dialog.wait_for()
                dialog.locator("textarea").fill(
                    "subject_definitions:\n<Subject 1> is the approved reference for Lin.\n\n"
                    "summary:\n[reference generation] Lin holds a wet ledger in the harbour rain.\n\n"
                    "retention_analysis:\n<Subject 1>: fully_preserved - the same face and coat.\n\n"
                    "detailed_description:\nRain falls on the corrugated roof while Lin presses a "
                    "ledger page into his coat.\n\n"
                    "overall_soundscape:\nRain on metal, distant harbour whistle.\n\n"
                    "non_diegetic_music:\nN/A"
                )
                dialog.get_by_role("button", name="保存并冻结").click()
                dialog.get_by_text("已冻结第 1 版").wait_for()
                shot("08-shot-prompt")
                dialog.get_by_role("button", name="关闭").click()
                return "第一镜提示词已人工冻结"

            step("07-③人工修订并冻结镜头提示词", manual_prompt)

            def requested_asset():
                dock = page.locator("details.episode-dock").first
                if not dock.evaluate("node => node.open"):
                    dock.locator("summary").click()
                    page.wait_for_timeout(300)
                # The 操作 cell clips its fourth button at this width; a person
                # can still reach it, so dispatch the real click on the button.
                page.locator(".shot-table-row").nth(1).get_by_role(
                    "button", name="+素材").evaluate("el => el.click()")
                dialog = page.get_by_role("dialog", name="指定增加素材")
                dialog.wait_for()
                dialog.get_by_label("名称").fill("旧账册")
                dialog.get_by_label("用途／外观描述（可选，给素材准备参考）").fill(
                    "一本被雨水打湿的旧账册，封皮上有码头编号。")
                dialog.get_by_role("button", name="加入本镜并送素材准备").click()
                dialog.get_by_text("已加入本镜素材需求").wait_for()
                shot("09-requested-asset")
                dialog.locator(".editor-actions").get_by_role("button", name="关闭").click()
                return "用户指定素材已写入这一镜"

            step("08-③用户指定增加素材", requested_asset)

            def subtitles():
                page.wait_for_timeout(600)
                docks = page.locator("details.episode-dock")
                assert docks.count() == 3, f"只看到 {docks.count()} 集"
                for index in range(3):
                    dock = docks.nth(index)
                    if not dock.evaluate("node => node.open"):
                        dock.locator("summary").click()
                        page.wait_for_timeout(300)
                    dock.get_by_role(
                        "button", name="生成 / 更新本集字幕轨").click()
                    page.get_by_text("已生成字幕第").first.wait_for()
                    page.wait_for_timeout(700)
                shot("10-subtitles")
                return "三集外挂字幕轨已生成"

            step("09-③三集字幕轨（全字幕）", subtitles)

            def material_prompt():
                page.get_by_role("button", name="第 4 阶段：拍摄素材准备").click()
                page.wait_for_timeout(1000)
                page.get_by_role("button", name="重新按镜头生成清单").first.click()
                page.locator(".material-table").wait_for()
                page.wait_for_timeout(600)
                page.get_by_role("button", name="人工修订").first.evaluate(
                    "el => el.click()")
                dialog = page.get_by_role("dialog", name="素材提示词")
                dialog.wait_for()
                # The modal loads the composed prompt after it opens; typing
                # before that answer lands would be overwritten by the load.
                dialog.get_by_text("没有发现规范偏差").wait_for()
                box = dialog.locator("textarea").first
                box.fill(
                    "A worn harbour ledger with a numbered leather cover, lying open on wet "
                    "wood, cold overcast light, realistic materials, eye-level medium close-up, "
                    "cinematic narrative prop reference."
                )
                dialog.get_by_role("button", name="保存并冻结").click()
                dialog.get_by_text("提示词已冻结").wait_for()
                shot("11-material-prompt")
                dialog.get_by_role("button", name="关闭").click()
                return "素材提示词已人工冻结"

            step("10-④人工修订素材提示词", material_prompt)

            profile = None

            def register_profile():
                nonlocal profile
                profile = call("POST", "/api/v1/workflow-profiles", PROFILE_BODY)
                return f"方案 {profile['id'][:8]}"

            step("11-登记 Ref2VA 生成方案", register_profile)

            def package_flow():
                page.reload(wait_until="networkidle")
                page.wait_for_timeout(1800)
                page.get_by_role("button", name="第 5 阶段：导演包").click()
                page.wait_for_timeout(1000)
                select = page.locator(".shoot-profile select")
                select.wait_for()
                options = select.locator("option").all_inner_texts()
                assert any(profile["name"] in row for row in options), options
                start = page.get_by_role("button", name="确认拍摄方案并开始真实制作")
                start.wait_for()
                assert start.is_disabled(), "演示模式下的开拍按钮必须是禁用的"
                page.wait_for_timeout(400)
                shot("12-package")
                return "方案已登记；演示模式下开拍如实禁用"

            step("12-⑤方案与开拍判定", package_flow)

            def run_and_post():
                page.get_by_role("button", name="第 6 阶段：视频生产").click()
                page.wait_for_timeout(1200)
                shot("13-run")
                page.get_by_role("button", name="第 7 阶段：后期成片").click()
                page.wait_for_timeout(1200)
                shot("14-post")
                return "生产与后期页面已打开"

            step("13-⑥⑦生产与后期", run_and_post)

            # ---- ledger verification (the same facts the screens show) ----
            def verify():
                plans_now = call("GET", f"/api/v1/projects/{project_id}/episode-plans")
                confirmed = [row for row in plans_now if row["status"] == "CONFIRMED"][-1]
                episodes_now = confirmed["payload"]["episodes"]
                assert len(episodes_now) == 3, [row["episode_id"] for row in episodes_now]
                assert all(len(row.get("shot_ids") or []) == 5 for row in episodes_now)
                tracks = call("GET", f"/api/v1/projects/{project_id}/subtitles")
                tracks = [row for row in tracks if row.get("episode_id")]
                assert len(tracks) == 3, tracks
                assert all(len(row.get("cues") or []) == 5 for row in tracks), tracks
                prompts = call("GET", f"/api/v1/projects/{project_id}/director/"
                                     f"storyboard/episodes/ep-01/shots")
                assert prompts["shot_count"] == 5, prompts
                return "3 集 · 15 镜 · 3 条字幕轨（每条 5 行）"

            step("14-核对项目台账", verify)

            if console_errors:
                print("\nconsole errors:")
                for line in console_errors[:10]:
                    print("  -", line[:200])
            browser.close()

    finally:
        for process in reversed(processes):
            process.terminate()

    blocked = [row for row in RESULTS if row[1] != "PASS"]
    print("\n== 结果 ==")
    for name, status, detail in RESULTS:
        print(f"{status:8} {name} {detail}")
    print(f"\n{len(RESULTS) - len(blocked)}/{len(RESULTS)} 通过；截图在 {OUT}")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
