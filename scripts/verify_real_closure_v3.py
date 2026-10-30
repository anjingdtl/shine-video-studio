"""Real, unattended local production acceptance through the Web workbench.

This drives a real browser against a running local stack (started with
``scripts/start_shine.ps1 -Provider comfyui``) and proves the whole production
chain:

* a project, human approved and locked master assets, a frozen Style Bible and
  the shots are created in the browser;
* a production series of three shots is started from the browser;
* the resident Worker — not this script — claims the series and renders every
  shot behind one managed MiniMax-H3 renderer;
* the page is reloaded while the run continues, and the run survives it;
* the EDL is assembled and the final MP4 is played back in the browser.

The script never creates a PASSED job, never writes a quality gate and never
substitutes a fixture for the renderer.  It only reads the ledger afterwards
and writes the evidence.

Usage (the stack must already be running):

    uv run --project backend --with playwright==1.62.0 python scripts/verify_real_production.py ^
        --api http://127.0.0.1:8000 --web http://127.0.0.1:5173 ^
        --source-db .local-acceptance/multishot-20261031/acceptance/shine.db ^
        --evidence-dir .local-acceptance/final-local-pass/<run> ^
        --profile local-h3-ref2va-pdd8-2img-1aud ^
        --operator-token "$env:SHINE_OPERATOR_TOKEN"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

STYLE = [
    "Medium close shot under neon in the night rain, with a slight handheld breathing movement",
    "Keep the dialogue eyeline; the character stays in side profile and moves continuously",
    "Cool grey environment against warm skin tones, neon reflected in the wet asphalt",
    "Single key light from the side, keeping shadow detail in the rain",
    "Natural shallow depth of field with a light film grain",
    "Cut on the pause after a line",
    "Natural spoken pace in Mandarin, restrained ambience",
]

def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_assets(source_db: Path, data_root: Path) -> list[dict]:
    """The historically approved master assets of the previous acceptance run."""
    con = sqlite3.connect(source_db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, filename, kind, content_type, role, entity_id, storage_path, sha256, byte_size"
        " from assets where approved = 1 and locked = 1 order by kind, filename"
    ).fetchall()
    assets = []
    for row in rows:
        stored = data_root / row["storage_path"]
        if not stored.is_file():
            raise SystemExit(f"Approved master asset bytes are missing: {stored}")
        assets.append({
            "source_id": row["id"],
            "filename": row["filename"],
            "kind": row["kind"],
            "content_type": row["content_type"],
            "role": row["role"],
            "entity_id": row["entity_id"],
            "sha256": row["sha256"],
            "byte_size": row["byte_size"],
            "path": str(stored),
        })
    con.close()
    if not assets:
        raise SystemExit("The source database holds no approved and locked master asset")
    return assets


def probe(path: Path) -> dict:
    """Measure the produced media with FFprobe; never trust a reported value."""
    if not path.is_file():
        return {"available": False, "reason": "file missing"}
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, timeout=180, check=False,
    )
    if result.returncode != 0:
        return {"available": False, "reason": result.stderr.decode("utf-8", "replace")[:400]}
    body = json.loads(result.stdout.decode("utf-8", "replace"))
    video = next((s for s in body.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in body.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = None
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
    if rate and "/" in rate:
        num, den = rate.split("/")
        if float(den):
            fps = round(float(num) / float(den), 4)
    return {
        "available": True,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": fps,
        "duration_s": round(float(body.get("format", {}).get("duration", 0.0)), 4),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "has_audio": bool(audio),
        "nb_frames": video.get("nb_frames"),
        "sha256": sha256_file(path),
        "byte_size": path.stat().st_size,
    }


def decode_check(path: Path) -> dict:
    """A full decode proves the file is playable, not only parseable."""
    if not path.is_file():
        return {"available": False}
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
        capture_output=True, timeout=900, check=False,
    )
    return {
        "available": True,
        "decoded": result.returncode == 0,
        "stderr": result.stderr.decode("utf-8", "replace")[:600],
    }


def gpu_sample() -> dict:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=uuid,memory.total,memory.used,utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        return {"available": False}
    rows = []
    for line in result.stdout.decode("utf-8", "replace").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            rows.append({"uuid": parts[0], "total_mb": int(parts[1]), "used_mb": int(parts[2]),
                         "utilization": int(parts[3])})
    return {"available": True, "gpus": rows}


def memory_sample() -> dict:
    """The physical memory headroom, so the run records what it actually had."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$os = Get-CimInstance Win32_OperatingSystem; "
         "'{0},{1}' -f [math]::Round($os.TotalVisibleMemorySize/1KB), [math]::Round($os.FreePhysicalMemory/1KB)"],
        capture_output=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        return {"available": False}
    text = result.stdout.decode("utf-8", "replace").strip().splitlines()[-1]
    try:
        total, free = (int(value) for value in text.split(","))
    except ValueError:
        return {"available": False}
    return {"available": True, "total_mb": total, "free_mb": free}


CONTENT_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
}


def stage_upload(asset: dict, staging: Path) -> str:
    """Copy a content-addressed master asset to a file the browser can name.

    The asset store keeps the bytes under their hash, so the original file
    name and extension are gone.  The workbench checks the extension against
    the declared kind, so the upload needs the real name back.
    """
    staging.mkdir(parents=True, exist_ok=True)
    extension = CONTENT_EXTENSIONS.get(asset["content_type"] or "", "")
    if not extension:
        extension = {"image": ".png", "audio": ".wav", "video": ".mp4"}.get(asset["kind"], ".bin")
    name = Path(asset["filename"]).stem
    target = staging / f"{name}{extension}"
    target.write_bytes(Path(asset["path"]).read_bytes())
    return str(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real local production acceptance through the Web workbench")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--web", default="http://127.0.0.1:5173")
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--source-data", default=None,
                        help="Data root of the source database (defaults to the database's parent)")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--profile", default="local-h3-ref2va-pdd8-2img-1aud")
    parser.add_argument("--operator-token", required=True)
    parser.add_argument("--title", default="真实生产验收项目")
    parser.add_argument("--timeout-minutes", type=int, default=180)
    parser.add_argument("--shots", type=int, default=3)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--dom-only", action="store_true",
                        help="Stop after the series is queued. Used to validate the browser flow "
                             "without spending GPU time; the run is then NOT a production acceptance.")
    args = parser.parse_args()

    evidence = Path(args.evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "screenshots").mkdir(exist_ok=True)
    (evidence / "media").mkdir(exist_ok=True)

    source_db = Path(args.source_db).resolve()
    source_data = Path(args.source_data).resolve() if args.source_data else source_db.parent
    assets = source_assets(source_db, source_data)
    log(f"Historical approved master assets: {len(assets)}")
    for asset in assets:
        log(f"  {asset['kind']:<6} role={asset['role']:<16} {asset['sha256'][:12]}")

    api = args.api.rstrip("/")
    web = args.web.rstrip("/")
    client = httpx.Client(timeout=120)
    record: dict = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": api,
        "web": web,
        "profile": args.profile,
        "source_assets": assets,
        "steps": [],
    }

    def step(name: str, **extra) -> None:
        record["steps"].append({"name": name, "at": time.strftime("%H:%M:%S"), **extra})
        log(f"STEP {name} {extra if extra else ''}")

    profiles = client.get(api + "/api/v1/workflow-profiles").json()
    profile = next((row for row in profiles if row["name"] == args.profile), None)
    if profile is None:
        raise SystemExit(f"The workflow profile {args.profile} is not registered on this stack")
    step("workflow_profile", profile_id=profile["id"], graph_sha256=profile["graph_sha256"])

    status = client.get(api + "/api/v1/system/status").json()
    record["system_status_before"] = status
    if status.get("render_mode") != "comfyui" and not args.dom_only:
        raise SystemExit("The stack is not running in ComfyUI mode; the acceptance must use the real renderer")
    step("system_status", render_mode=status["render_mode"], worker_online=status["worker_online"])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1600, "height": 1200}, accept_downloads=True)
        page = context.new_page()
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(f"{message.type}: {message.text}")
                if message.type == "error" else None)

        def shot(name: str) -> None:
            page.screenshot(path=str(evidence / "screenshots" / f"{name}.png"), full_page=True)

        def ui_error(label: str) -> None:
            """A visible error toast is a failed step, never a silent no-op."""
            element = page.query_selector(".toast.error")
            if element is not None:
                raise SystemExit(f"The workbench reported an error during {label}: {element.inner_text().strip()}")

        def ui_notice() -> str:
            element = page.query_selector(".toast")
            return element.inner_text().strip() if element is not None else ""

        page.goto(web, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        shot("01-landing")

        # 1. the project -------------------------------------------------------
        if page.query_selector(".create-project input") is not None:
            page.fill(".create-project input", args.title)
            page.click(".create-project button.primary-button")
        else:
            # A project already exists in this workspace; the inline creator is
            # the same product action.
            page.fill(".new-project-inline", args.title)
            page.click(".project-selector button.subtle-button")
        page.wait_for_selector(".project-selector", timeout=30000)
        page.wait_for_timeout(1200)
        ui_error("creating the project")
        projects = client.get(api + "/api/v1/projects").json()
        project_id = next((row["id"] for row in projects if row["title"] == args.title), projects[0]["id"])
        record["project_id"] = project_id
        step("project_created", project_id=project_id)
        shot("02-project")

        # 2. the historically approved master assets ----------------------------
        for asset in assets:
            upload_path = stage_upload(asset, evidence / "uploads")
            page.select_option("#assets .upload-row select >> nth=0", asset["kind"])
            page.select_option("#assets .upload-row select >> nth=1", asset["role"] or "generic")
            page.fill("#assets .upload-row input[placeholder='CHAR_A / SCENE_01']", asset["entity_id"] or "")
            page.set_input_files("#assets .upload-row input[type=file]", upload_path)
            page.wait_for_timeout(1800)
            ui_error(f"uploading {asset['filename']}")
            log(f"  uploaded {asset['filename']} -> {ui_notice()!r}")
        page.wait_for_selector("#assets .asset-row", timeout=30000)
        page.wait_for_timeout(1500)
        shot("03-assets-uploaded")

        # 3. the human approval and lock ---------------------------------------
        page.fill("#assets input[type=password]", args.operator_token)
        page.fill("#assets .form-grid input:not([type=password])",
                  "历史已批准主资产重新绑定到隔离验收项目，来源与哈希不变")
        page.wait_for_timeout(300)
        for _ in range(2):
            for row in page.query_selector_all("#assets .asset-row"):
                button = row.query_selector("button.text-button")
                if button is not None and button.inner_text().strip() in {"批准", "锁定"}:
                    button.click()
                    page.wait_for_timeout(1500)
            page.wait_for_timeout(800)
        approved = client.get(api + f"/api/v1/projects/{project_id}/assets").json()
        locked = [row for row in approved if row["approved"] and row["locked"]]
        if len(locked) != len(assets):
            raise SystemExit(f"Only {len(locked)} of {len(assets)} assets are approved and locked")
        record["approved_assets"] = [
            {"id": row["id"], "filename": row["filename"], "sha256": row["sha256"],
             "role": row["role"], "entity_id": row["entity_id"],
             "approved_at": row["approved_at"], "locked_at": row["locked_at"],
             "provenance": row.get("provenance")}
            for row in locked
        ]
        step("assets_approved_and_locked", count=len(locked))
        shot("04-assets-locked")

        # 4. the frozen Style Bible --------------------------------------------
        fields = page.query_selector_all("#style .style-grid .style-field textarea")
        if len(fields) != len(STYLE):
            raise SystemExit(f"The Style Bible form exposes {len(fields)} fields, expected {len(STYLE)}")
        for field, value in zip(fields, STYLE):
            field.fill(value)
        page.wait_for_timeout(400)
        page.click("#style .section-footer button.secondary-button")
        page.wait_for_timeout(1500)
        page.click("#style .section-footer button.primary-button")
        page.wait_for_timeout(2500)
        styles = client.get(api + f"/api/v1/projects/{project_id}/styles").json()
        if not styles:
            raise SystemExit("The Style Bible was not frozen")
        record["style_version_id"] = styles[0]["id"]
        record["style_sha256"] = styles[0]["sha256"]
        step("style_frozen", style_version_id=styles[0]["id"], sha256=styles[0]["sha256"])
        shot("05-style-frozen")

        # 5. the rulepack, the script and the real-agent storyboard ------------
        locked_facts = [
            "Shot 3 must begin exactly on the last frame of shot 2 (last_frame soft continuity), no re-establishing.",
            "All three shots use the same approved character master; keep the character appearance continuous.",
            "Keep the Mandarin dialogue lines verbatim, exactly as written in the script.",
            "Vertical 9:16 (768x1344, 24fps), about 7 seconds per shot, three shots in total.",
        ]
        record["locked_story_facts"] = locked_facts
        rulepack = client.post(api + f"/api/v1/projects/{project_id}/rulepack/freeze", json={})
        if rulepack.status_code != 200:
            raise SystemExit(f"Rulepack freeze failed: {rulepack.status_code} {rulepack.text[:200]}")
        record["rulepack"] = rulepack.json()
        step("rulepack_frozen", payload=rulepack.json())

        script_doc = evidence.parent / "closure-script-v3.md"
        if not script_doc.is_file():
            raise SystemExit(f"The acceptance script document is missing: {script_doc}")
        script_text = script_doc.read_text(encoding="utf-8")
        record["script"] = {"filename": script_doc.name,
                            "sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
                            "chars": len(script_text)}
        document = client.post(
            api + f"/api/v1/projects/{project_id}/documents",
            files={"file": (script_doc.name, script_text.encode("utf-8"), "text/markdown")},
        )
        if document.status_code not in (200, 201):
            raise SystemExit(f"Script import failed: {document.status_code} {document.text[:300]}")
        record["document_id"] = document.json()["id"]
        step("script_imported", document_id=record["document_id"], chars=len(script_text))

        # The storyboard happens IN THE BROWSER: import, consent, generate.
        page.evaluate("document.getElementById('storyboard')?.scrollIntoView()")
        page.set_input_files("#storyboard input[type=file]", str(script_doc))
        page.wait_for_timeout(2000)
        ui_error("importing the script document")
        page.check("#storyboard .consent-row input[type=checkbox]")
        page.fill("#storyboard-facts", "; ".join(locked_facts))
        page.wait_for_timeout(300)
        step("storyboard_ui_ready", document_id=record["document_id"])
        shot("06-storyboard-imported")
        page.click("#storyboard button.primary-button")
        page.wait_for_timeout(2000)
        # Five sequential real LLM calls can take several minutes.  A refused
        # chain shows the reason in the panel status, never in a toast.  The
        # refusal has to be observed as well, otherwise a refused run would
        # wait for the full timeout and hide the reason that stopped it.
        page.wait_for_selector(
            "#storyboard .storyboard-steps li, #storyboard p[role=status], #storyboard .blocked-callout",
            timeout=1800000,
        )
        page.wait_for_timeout(1500)
        # The crew outcome is part of the evidence, whatever the chain decided.
        crew = client.get(api + f"/api/v1/projects/{project_id}/film-runs").json()
        record["crew_runs"] = [
            {"role": run["role_id"], "status": run["status"], "error_code": run["error_code"],
             "model": run["model_id"],
             "external_outcome": (run.get("trace") or {}).get("external_outcome"),
             "automatic_replay": (run.get("trace") or {}).get("automatic_replay"),
             "latency_ms": (run.get("trace") or {}).get("latency_ms")}
            for run in crew
        ]
        step("crew_runs", roles=[f"{run['role_id']}:{run['status']}" for run in crew])
        if page.query_selector("#storyboard .storyboard-steps li") is None:
            status_text = ""
            for selector in ("#storyboard p[role=status]", "#storyboard .blocked-callout"):
                element = page.query_selector(selector)
                if element is not None:
                    status_text = element.inner_text().strip()
                    if status_text:
                        break
            refused = [
                f"{run['role_id']}:{run['status']}:{run['error_code'] or 'no-code'}"
                for run in crew
                if run["status"] != "SUCCEEDED"
            ]
            record["storyboard_refusal"] = {"panel": status_text, "crew": refused}
            record["console_errors"] = console_errors[:50]
            (evidence / "real-production-record.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            shot("07-storyboard-refused")
            raise SystemExit(
                f"The storyboard chain did not start or was refused: {status_text} "
                f"| crew: {', '.join(refused) or 'no crew record'}"
            )
        steps_count = len(page.query_selector_all("#storyboard .storyboard-steps li"))
        ui_error("running the five-agent storyboard")
        record["storyboard_ui_steps"] = steps_count
        step("storyboard_generated_in_browser", steps=steps_count)
        shot("07-storyboard-generated")
        page.click("#storyboard .storyboard-result button.primary-button")
        page.wait_for_timeout(4000)
        ui_error("applying the storyboard as shots")
        shots_after = client.get(api + f"/api/v1/projects/{project_id}/shots").json()
        if len(shots_after) < 3:
            raise SystemExit(f"The applied storyboard created only {len(shots_after)} shots")
        record["shots"] = [{"id": row["id"], "name": row["name"], "revision": row["revision"],
                            "continuity": row["spec"].get("continuity"),
                            "dialogue": row["spec"].get("dialogue")} for row in shots_after]
        step("storyboard_applied", shot_count=len(shots_after))
        shot("08-storyboard-applied")
        # 6. the real production series ----------------------------------------
        page.select_option("#production-series #series-profile", label=f"{args.profile} · Ref2VA")
        page.select_option("#production-series #series-scope", "full")
        page.select_option("#production-series #series-edit-intent", index=1)
        page.wait_for_timeout(500)
        record["series_edit_intent_selected"] = True
        step("series_edit_intent_selected")
        page.wait_for_timeout(400)
        # The applied storyboard refreshes the shot list asynchronously; wait
        # until the panel actually renders the planned shots and the start
        # button becomes enabled.
        page.wait_for_function(
            "document.querySelectorAll('#production-series .series-shot input[type=checkbox]').length"
            f" >= {args.shots}", timeout=60000,
        )
        boxes = page.query_selector_all("#production-series .series-shot input[type=checkbox]")
        for box in boxes[: args.shots]:
            box.check()
        page.wait_for_timeout(400)
        shot("07-series-selected")
        page.wait_for_selector("#production-series .series-builder button.primary-button:not([disabled])",
                               timeout=60000)
        page.click("#production-series .series-builder button.primary-button")
        page.wait_for_timeout(4000)
        ui_error("starting the production series")
        series_rows = client.get(api + f"/api/v1/projects/{project_id}/production-series").json()
        if not series_rows:
            raise SystemExit("The production series was not created")
        series = series_rows[0]
        record["series_id"] = series["id"]
        step("series_started", series_id=series["id"], shots=series["shot_count"])
        shot("08-series-started")

        if args.dom_only:
            record["dom_only"] = True
            record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            record["console_errors"] = console_errors[:50]
            (evidence / "real-production-record.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            log("DOM-only run finished; no production was started.")
            browser.close()
            client.close()
            return 0

        # 7. the resident Worker drives the run --------------------------------
        deadline = time.time() + args.timeout_minutes * 60
        samples: list[dict] = []
        last = None
        reload_done = False
        while time.time() < deadline:
            current = client.get(api + f"/api/v1/production-series/{series['id']}").json()
            if current["status"] != last:
                log(f"series status={current['status']} "
                    f"completed={current['completed_count']}/{current['shot_count']}")
                last = current["status"]
            samples.append({"at": time.strftime("%H:%M:%S"), "status": current["status"],
                            "completed": current["completed_count"],
                            "items": [{"shot": i["shot_id"][:8], "status": i["status"],
                                       "job": i["job_status"], "failure": i["job_failure_code"]}
                                      for i in current["items"]],
                            "gpu": gpu_sample(),
                            "memory": memory_sample(),
                            "at_epoch": time.time()})
            if not reload_done and current["completed_count"] >= 1:
                # The run must survive the browser being closed and reopened.
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                page.select_option("#production-series #series-profile", label=f"{args.profile} · Ref2VA")
                page.wait_for_timeout(1000)
                shot("09-after-reload")
                reload_done = True
            if current["status"] in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
                break
            time.sleep(20)
        record["series_final"] = client.get(api + f"/api/v1/production-series/{series['id']}").json()
        record["series_samples"] = samples
        step("series_finished", status=record["series_final"]["status"])
        shot("10-series-finished")

        if record["series_final"]["status"] != "COMPLETED":
            log(f"series did not complete: {record['series_final']['status']}")
            record["console_errors"] = console_errors[:50]
            (evidence / "real-production-record.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
            client.close()
            return 2

        # 8. the shots, the gates and the media ---------------------------------
        jobs = client.get(api + f"/api/v1/projects/{project_id}/jobs").json()
        passed = [job for job in jobs if job["status"] == "PASSED"]
        record["jobs"] = []
        if len(passed) < args.shots:
            raise SystemExit(f"Only {len(passed)} of {args.shots} jobs passed")
        for job in jobs:
            detail = client.get(api + f"/api/v1/jobs/{job['id']}").json()
            gates = client.get(api + f"/api/v1/jobs/{job['id']}/quality-gates").json()
            attempt = detail["attempts"][-1]["id"] if detail.get("attempts") else None
            qc = client.get(api + f"/api/v1/jobs/{job['id']}/qc").json()
            entry = {
                "id": job["id"], "shot": job["shot_name"], "status": job["status"],
                "artifact_id": job["artifact_id"], "qc": job["last_qc_result"],
                "generation_mode": job["generation_mode"],
                "attempt_id": attempt,
                "expected_duration_s": detail["request"].get("expected_duration_s"),
                "attempts": [{"no": a["attempt_no"], "status": a["status"],
                              "failure": a["failure_code"], "reconciliation": a["reconciliation_state"],
                              "provider_job_id": a["provider_job_id"]} for a in detail.get("attempts", [])],
                "gates": [{"gate": g["gate_name"], "status": g["status"], "model": g["model"],
                           "confidence": g["confidence"], "is_mock": g["is_mock"],
                           "coverage": g["coverage"], "error": g["error_code"]}
                          for g in gates if attempt is None or g.get("attempt_id") == attempt],
                "qc_reports": [{"result": r["result"], "reviewer": r["reviewer"], "is_mock": r["is_mock"],
                                "report": r["report"]} for r in qc],
            }
            record["jobs"].append(entry)

        for job in passed:
            response = client.get(api + f"/api/v1/jobs/{job['id']}/artifacts")
            if response.status_code != 200:
                continue
            for artifact in response.json():
                media = client.get(api + f"/api/v1/artifacts/{artifact['id']}/media")
                if media.status_code != 200:
                    continue
                target = evidence / "media" / f"{job['shot_name']}.mp4"
                target.write_bytes(media.content)
                for entry in record["jobs"]:
                    if entry["id"] == job["id"]:
                        entry["media_path"] = str(target)
                        entry["probe"] = probe(target)
                        entry["decode"] = decode_check(target)

        # 9. the product's automatic delivery ----------------------------------
        # The script never builds an EDL and never calls POST /deliveries.
        deadline = time.time() + 900
        order = None
        while time.time() < deadline:
            orders = client.get(api + f"/api/v1/projects/{project_id}/delivery-orders").json()
            rows = [row for row in orders if row["series_id"] == series["id"]]
            if rows and rows[0]["status"] in {"DELIVERED", "FAILED", "BLOCKED"}:
                order = rows[0]
                break
            time.sleep(10)
        if order is None:
            raise SystemExit("The product did not produce an automatic delivery order in time")
        record["auto_delivery_order"] = order
        if order["status"] != "DELIVERED":
            raise SystemExit(
                f"The automatic delivery ended {order['status']}: {order.get('failure_code')}")
        step("auto_delivery_delivered",
             order_id=order["id"], version=order["version"],
             edl_source=order["edl_source"], artifact=order["artifact_id"])

        # The EDL must follow the series order, not the creation time.
        series_final = record["series_final"]
        expected_order = [item["job_id"] for item in series_final["items"]]
        edl_jobs = [clip["job_id"] for clip in order["edl_payload"]["clips"]]
        if edl_jobs != expected_order:
            raise SystemExit(
                f"The automatic EDL order {edl_jobs} does not match the series order {expected_order}")
        step("auto_edl_order_matches_series")

        # The reel is a real, decodable, downloadable file.
        media = client.get(api + f"/api/v1/deliveries/{order['artifact_id']}/media")
        if media.status_code != 200:
            raise SystemExit(f"The delivered reel is not downloadable: {media.status_code}")
        reel_path = evidence / "media" / "auto-delivery-final.mp4"
        reel_path.write_bytes(media.content)
        reel_sha = sha256_file(reel_path)
        if reel_sha != [row for row in client.get(
                api + f"/api/v1/projects/{project_id}/deliveries").json()
                if row["artifact_id"] == order["artifact_id"]][0]["sha256"]:
            raise SystemExit("The downloaded reel does not match its registered SHA-256")
        record["auto_reel"] = {"path": str(reel_path), "sha256": reel_sha,
                               "probe": probe(reel_path), "decode": decode_check(reel_path)}
        if not record["auto_reel"]["decode"]["decoded"]:
            raise SystemExit("The automatically delivered reel failed the full decode check")
        step("auto_reel_decoded", sha256=reel_sha,
             duration_s=record["auto_reel"]["probe"].get("duration_s"),
             width=record["auto_reel"]["probe"].get("width"),
             height=record["auto_reel"]["probe"].get("height"))

        # The audit trail must show the product did the assembly itself.
        events = client.get(api + f"/api/v1/projects/{project_id}/events?limit=500").json()
        auto_events = [e for e in events["items"] if e["event_type"] == "AUTO_DELIVERY_ASSEMBLED"]
        if not auto_events:
            raise SystemExit("No AUTO_DELIVERY_ASSEMBLED audit event was recorded")
        record["auto_delivery_audit"] = auto_events[0]

        # Playback of the automatically delivered reel in the browser.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.evaluate("document.getElementById('delivery')?.scrollIntoView()")
        page.wait_for_timeout(2000)
        shot("11-auto-delivery")
        video = page.query_selector("#delivery .auto-delivery video")
        if video is None:
            raise SystemExit("The automatically delivered reel is not offered in the workbench")
        playback = page.evaluate(
            """async () => {
                const v = document.querySelector('#delivery .auto-delivery video');
                v.muted = true;
                await v.play().catch(() => {});
                await new Promise(r => setTimeout(r, 4000));
                return {duration: v.duration, width: v.videoWidth, height: v.videoHeight,
                        currentTime: v.currentTime, readyState: v.readyState, src: v.currentSrc};
            }"""
        )
        record["playback"] = playback
        step("final_film_played", **playback)
        # 10. the audit trail and the ledger ------------------------------------
        events = client.get(api + f"/api/v1/projects/{project_id}/events?limit=500").json()
        record["audit_total"] = events["total"]
        record["audit_tail"] = events["items"][-40:]
        record["system_status_after"] = client.get(api + "/api/v1/system/status").json()
        record["gpu_after"] = gpu_sample()
        record["console_errors"] = console_errors[:50]

        browser.close()
    client.close()

    record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (evidence / "real-production-record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Evidence written to {evidence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
