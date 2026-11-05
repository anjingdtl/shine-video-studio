"""Human-paced walkthrough of the Shine workbench in a real Chromium.

This is a *journey* check, not an API contract test: it clicks and types the way
a person would, takes a screenshot at every stage, and reports the first thing
that blocks the journey instead of asserting internals.

Run:
    uv run --with playwright==1.64.1 python scripts/journey_longform_browser.py

It drives whatever is already serving http://127.0.0.1:5173 (vite -> API) and
creates its own project.  Screenshots land in .local-acceptance/journey-shots/.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local-acceptance" / "journey-shots"
OUT.mkdir(parents=True, exist_ok=True)

PLAYWRIGHT_CACHE = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright" if os.environ.get("LOCALAPPDATA") else None


def _find_chromium() -> str | None:
    if not PLAYWRIGHT_CACHE or not PLAYWRIGHT_CACHE.is_dir():
        return None
    candidates = sorted(PLAYWRIGHT_CACHE.glob("chromium-*/chrome-win64/chrome.exe"))
    return str(candidates[-1]) if candidates else None


CHROMIUM = _find_chromium()
RESULTS: list[tuple[str, str, str]] = []

STAGES = ["创意与剧本", "剧本统筹与素材", "镜头拆解", "导演包", "视频生产", "后期成片"]

PROFILE_BODY = {
    "name": "旅程验收 Ref2VA",
    "mode": "Ref2VA",
    "graph": {
        "1": {
            "class_type": "H3BaseSampler",
            "inputs": {
                "positive_prompt": "",
                "seed": 0,
                "duration": 6.0,
                "reference_picture_1": "",
            },
        },
        "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
    },
    "parameter_map": {
        "positive_prompt": "1.inputs.positive_prompt",
        "seed": "1.inputs.seed",
        "duration": "1.inputs.duration",
        "reference_picture_1": "1.inputs.reference_picture_1",
    },
    "required_nodes": ["H3BaseSampler"],
}


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)


def record(step: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((step, "PASS" if ok else "BLOCKED", detail))
    print(f"[{'PASS' if ok else 'BLOCK'}] {step}" + (f" — {detail}" if detail else ""))


def try_step(page: Page, name: str, fn) -> bool:
    try:
        detail = fn()
        record(name, True, detail or "")
        return True
    except PlaywrightTimeout as exc:
        record(name, False, str(exc).splitlines()[0][:200])
        shot(page, f"blocked-{name}")
        return False
    except AssertionError as exc:
        record(name, False, f"断言失败：{exc}")
        shot(page, f"blocked-{name}")
        return False


def png(path: Path, size: int = 512, rgb: tuple[int, int, int] = (120, 140, 170)) -> None:
    """A real PNG with no third-party dependency."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    fixture = OUT / "fixture-character.png"
    png(fixture)

    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.set_default_timeout(15000)
        # A person clicks "OK" on the confirm dialogs the product uses for
        # irreversible local actions; Playwright dismisses them by default.
        page.on("dialog", lambda dialog: dialog.accept())
        console_errors: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        failed_calls: list[str] = []
        def note_failure(response):
            if response.status < 400 or "/api/" not in response.url:
                return
            try:
                body = response.text()[:160]
            except Exception:  # noqa: BLE001 - a body may already be consumed
                body = ""
            failed_calls.append(f"{response.status} {response.url} {body}")

        page.on("response", note_failure)

        title = f"长片旅程 {time.strftime('%H%M%S')}"

        try_step(page, "01-打开工作台", lambda: (
            page.goto(BASE, wait_until="networkidle"),
            page.locator(".topbar").first.wait_for(),
            shot(page, "01-home"),
            "标题=" + page.title(),
        )[-1])

        def create_project():
            # A project may already be selected on this machine; the selector keeps
            # its own "new project" input, and the first-run card only exists when
            # the database is empty.  Drive whichever one the screen really shows.
            inline = page.locator("input.new-project-inline")
            if inline.count():
                inline.first.fill(title)
                page.get_by_role("button", name="新建作品").click()
            else:
                page.get_by_placeholder("给作品起个名字").fill(title)
                page.get_by_role("button", name="创建作品").click()
            page.get_by_text(title).first.wait_for()
            page.wait_for_timeout(1000)
            shot(page, "02-project-created")
            return title

        try_step(page, "02-新建作品", create_project)

        try_step(page, "03-六阶段导航", lambda: (
            [page.get_by_role("button", name=f"第 {index + 1} 阶段：{label}").wait_for()
             for index, label in enumerate(STAGES)],
            shot(page, "03-six-stages"),
            "六个阶段按钮都在",
        )[-1])

        try_step(page, "04-进入素材页", lambda: (
            page.get_by_role("button", name="第 2 阶段：剧本统筹与素材").click(),
            page.wait_for_timeout(700),
            shot(page, "04-assets"),
            "已进入素材页",
        )[-1])

        try_step(page, "05-上传素材", lambda: (
            page.locator(".upload-button input[type=file]").first.set_input_files(str(fixture)),
            page.wait_for_timeout(1600),
            shot(page, "05-uploaded"),
            "素材已上传",
        )[-1])

        def confirm_asset():
            page.get_by_role("checkbox", name=f"选择上传素材 {fixture.name}").check()
            button = page.get_by_role("button", name="确认使用所选待确认素材")
            button.wait_for(state="visible")
            for _ in range(3):
                if button.is_enabled():
                    break
                page.wait_for_timeout(300)
            assert button.is_enabled(), "勾选后确认按钮仍是禁用状态"
            button.click()
            try:
                page.locator(".asset-state").get_by_text("可用于分镜").first.wait_for(timeout=10000)
            except PlaywrightTimeout:
                raise AssertionError("确认素材后未出现『可用于分镜』：" + " | ".join(failed_calls[-3:])) from None
            page.wait_for_timeout(400)
            shot(page, "06-asset-confirmed")
            return "素材已确认并锁定"

        try_step(page, "06-确认素材", confirm_asset)

        def freeze_style():
            preset = page.locator("#style-preset")
            preset.wait_for()
            preset.select_option(index=1)
            page.wait_for_timeout(700)
            # The first freeze also approves and binds the official rule pack, so
            # that button reads 「批准并冻结」; later freezes read 「冻结为新版本」.
            label = "批准并冻结" if page.get_by_role("button", name="批准并冻结").count() else "冻结为新版本"
            button = page.get_by_role("button", name=label).first
            diagnostic = {
                "label": label,
                "matches": page.get_by_role("button", name=label).count(),
                "visible": button.is_visible(),
                "enabled": button.is_enabled(),
                "preset": preset.input_value(),
            }
            assert diagnostic["enabled"], f"冻结按钮不可用：{diagnostic}"
            button.click()
            page.get_by_text("已冻结 V", exact=False).first.wait_for(timeout=12000)
            page.wait_for_timeout(700)
            shot(page, "07-style-frozen")
            return "风格已冻结"

        try_step(page, "07-冻结风格", freeze_style)

        def add_shots():
            page.get_by_role("button", name="第 3 阶段：镜头拆解").click()
            page.wait_for_timeout(700)
            # 镜头拆解 opens on the script→AI storyboard tab; hand-built shots live
            # under 排产制作.
            page.get_by_role("button", name="② 排产制作").click()
            page.wait_for_timeout(600)
            # 手工建分镜收在「单镜头制作与任务明细（高级）」折叠里，点开它。
            page.locator("summary", has_text="单镜头制作与任务明细").first.click()
            page.wait_for_timeout(500)
            beat = page.locator('textarea[placeholder="描述画面、动作和关键剧情事实"]')
            beat.wait_for()
            for text in ("雨夜码头，林澈翻开账册", "灯下特写，账页被风吹动"):
                beat.fill(text)
                page.locator(".binding-row select").first.select_option(index=1)
                page.get_by_role("button", name="保存分镜").click()
                page.wait_for_timeout(1200)
                beat.fill("")
            page.wait_for_timeout(600)
            shot(page, "08-shots")
            count = page.locator(".shot-row").count()
            assert count >= 2, f"只看到 {count} 个镜头"
            return f"{count} 个镜头"

        try_step(page, "08-建立分镜", add_shots)

        try_step(page, "09-生成分集草案", lambda: (
            page.get_by_role("button", name="第 4 阶段：导演包").click(),
            page.wait_for_timeout(600),
            page.get_by_role("button", name="生成分集草案").click(),
            page.get_by_role("button", name="确认分集方案").wait_for(timeout=12000),
            page.wait_for_timeout(500),
            shot(page, "09-plan"),
            "分集草案已生成",
        )[-1])

        try_step(page, "10-确认分集方案", lambda: (
            page.get_by_role("button", name="确认分集方案").click(),
            page.get_by_text("分集方案已确认").first.wait_for(),
            page.wait_for_timeout(600),
            shot(page, "10-plan-confirmed"),
            "分集方案已确认",
        )[-1])

        profile_id = ""

        def register_profile():
            nonlocal profile_id
            body = {**PROFILE_BODY, "name": f"旅程验收 Ref2VA {time.strftime('%H%M%S')}"}
            response = page.request.post(
                f"{BASE}/api/v1/workflow-profiles",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Shine-Local-Action": "1"},
            )
            assert response.ok, f"{response.status} {response.text()[:200]}"
            profile_id = response.json()["id"]
            return f"profile={profile_id[:8]}"

        try_step(page, "11-登记生成方案", register_profile)

        try_step(page, "12-生成导演包", lambda: (
            page.reload(wait_until="networkidle"),
            page.get_by_role("button", name="第 4 阶段：导演包").click(),
            page.wait_for_timeout(900),
            page.locator(".run-binding-row select").select_option(profile_id),
            page.get_by_role("button", name="生成导演包").click(),
            page.wait_for_timeout(1500),
            shot(page, "12-package"),
            "导演包已生成",
        )[-1])

        try_step(page, "13-确认导演包", lambda: (
            page.get_by_role("button", name="确认导演包").click(),
            page.wait_for_timeout(1500),
            shot(page, "13-package-confirmed"),
            "导演包已确认",
        )[-1])

        try_step(page, "14-启动整部作品", lambda: (
            page.get_by_role("button", name="启动整部作品生产").click(),
            page.wait_for_timeout(1800),
            shot(page, "14-run-started"),
            "已点击启动",
        )[-1])

        try_step(page, "15-进入全片生产", lambda: (
            page.get_by_role("button", name="第 5 阶段：视频生产").click(),
            page.wait_for_timeout(1500),
            shot(page, "15-run"),
            "运行页已打开",
        )[-1])

        try_step(page, "16-进入后期成片", lambda: (
            page.get_by_role("button", name="第 6 阶段：后期成片").click(),
            page.wait_for_timeout(1500),
            shot(page, "16-post"),
            "成片页已打开",
        )[-1])

        page.set_viewport_size({"width": 390, "height": 844})
        try_step(page, "17-窄屏布局", lambda: (
            page.wait_for_timeout(800),
            shot(page, "17-narrow"),
            "窄屏截图已保存",
        )[-1])
        page.set_viewport_size({"width": 1440, "height": 900})
        shot(page, "99-final")

        if console_errors:
            print("\nconsole errors:")
            for line in console_errors[:10]:
                print("  -", line[:200])
        if failed_calls:
            print("\nfailed API calls:")
            for line in failed_calls[:10]:
                print("  -", line[:200])
        browser.close()

    print("\n== 结果 ==")
    blocked = [row for row in RESULTS if row[1] != "PASS"]
    for step, status, detail in RESULTS:
        print(f"{status:8} {step} {detail}")
    print(f"\n{len(RESULTS) - len(blocked)}/{len(RESULTS)} 通过，截图在 {OUT}")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
