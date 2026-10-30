"""Assemble the EDL and play the final film in the Web workbench.

This continues a finished production series: it reads the passed shots from the
ledger, types the editor's proposal into the delivery panel, assembles it with
FFmpeg and plays the result back in the browser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble and play the final film in the Web workbench")
    parser.add_argument("--api", default="http://127.0.0.1:8140")
    parser.add_argument("--web", default="http://127.0.0.1:5183")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--shots", type=int, default=3)
    args = parser.parse_args()

    evidence = Path(args.evidence_dir).resolve()
    (evidence / "screenshots").mkdir(parents=True, exist_ok=True)
    (evidence / "media").mkdir(parents=True, exist_ok=True)
    api = args.api.rstrip("/")
    web = args.web.rstrip("/")
    client = httpx.Client(timeout=300)

    jobs = client.get(api + f"/api/v1/projects/{args.project_id}/jobs").json()
    passed = [job for job in jobs if job["status"] == "PASSED"]
    if len(passed) < args.shots:
        raise SystemExit(f"Only {len(passed)} of {args.shots} jobs passed")
    clips = []
    for job in passed:
        detail = client.get(api + f"/api/v1/jobs/{job['id']}").json()
        attempt = detail["attempts"][-1]
        media = client.get(api + f"/api/v1/jobs/{job['id']}/artifacts").json()
        artifact = media[0]
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(
                evidence / "media" / f"{job['shot_name']}.mp4")],
            capture_output=True, text=True, check=False,
        )
        duration = 7.0
        if probe.returncode == 0:
            duration = round(float(json.loads(probe.stdout)["format"]["duration"]), 3)
        clips.append({
            "job_id": job["id"],
            "attempt_id": attempt["id"],
            "artifact_id": artifact["id"],
            "in_s": 0.0,
            "out_s": duration,
            "transition": "CUT",
            "reason": "Real H3 shot that passed every required quality gate",
        })
    log(f"EDL clips: {len(clips)}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.goto(web, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        # Select the acceptance project.
        page.select_option("select[aria-label='切换项目']", value=args.project_id)
        page.wait_for_timeout(2500)
        page.evaluate("document.getElementById('delivery')?.scrollIntoView()")
        page.wait_for_timeout(800)
        page.fill("#delivery textarea", json.dumps({"clips": clips}, ensure_ascii=False, indent=2))
        page.wait_for_timeout(400)
        page.screenshot(path=str(evidence / "screenshots" / "11-edl-typed.png"), full_page=True)
        page.click("#delivery button:has-text('校验并装配成片')")
        page.wait_for_timeout(6000)
        message = page.query_selector("#delivery p[role='status']")
        text = message.inner_text().strip() if message else ""
        log(f"delivery message: {text}")
        page.screenshot(path=str(evidence / "screenshots" / "12-delivery-assembled.png"), full_page=True)
        video = page.query_selector("#delivery video")
        if video is None:
            raise SystemExit("The final film is not offered for playback in the workbench")
        playback = page.evaluate(
            """async () => {
                const v = document.querySelector('#delivery video');
                v.muted = true;
                await v.play().catch(() => {});
                await new Promise(r => setTimeout(r, 5000));
                return {duration: v.duration, width: v.videoWidth, height: v.videoHeight,
                        currentTime: v.currentTime, readyState: v.readyState, src: v.currentSrc};
            }"""
        )
        log(f"playback: {playback}")
        page.screenshot(path=str(evidence / "screenshots" / "13-final-film-playback.png"), full_page=True)
        browser.close()

    deliveries = client.get(api + f"/api/v1/projects/{args.project_id}/deliveries").json()
    if not deliveries:
        raise SystemExit("No delivery artifact was recorded")
    delivery = deliveries[0]
    media_response = client.get(api + f"/api/v1/deliveries/{delivery['artifact_id']}/media")
    if media_response.status_code != 200:
        raise SystemExit(f"The delivery media is not readable: HTTP {media_response.status_code}")
    target = evidence / "media" / "final-delivery.mp4"
    target.write_bytes(media_response.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)],
        capture_output=True, text=True, check=False,
    )
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(target), "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    body = json.loads(probe.stdout) if probe.returncode == 0 else {}
    video_stream = next((s for s in body.get("streams", []) if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in body.get("streams", []) if s.get("codec_type") == "audio"), {})
    record = {
        "project_id": args.project_id,
        "clips": clips,
        "delivery": {
            "artifact_id": delivery["artifact_id"],
            "sha256": delivery["sha256"],
            "byte_size": delivery["byte_size"],
            "provenance": delivery.get("provenance"),
        },
        "media_path": str(target),
        "media_sha256": sha256_file(target),
        "probe": {
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "fps": video_stream.get("avg_frame_rate"),
            "duration_s": round(float(body.get("format", {}).get("duration", 0.0)), 4),
            "video_codec": video_stream.get("codec_name"),
            "audio_codec": audio_stream.get("codec_name"),
        },
        "full_decode": decoded.returncode == 0,
        "decode_stderr": decoded.stderr[:400],
        "playback": playback,
        "delivery_message": text,
    }
    (evidence / "delivery-record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"delivery record written: {evidence / 'delivery-record.json'}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
