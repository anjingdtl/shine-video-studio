# Local MiniMax-H3 acceptance — PENDING_LOCAL_VALIDATION

The cloud Mock worker does not run ComfyUI and does not create video. Close this checklist only on the user's Windows machine with a supported GPU, installed ComfyUI, and locally available MiniMax-H3 checkpoints. Do not upload private assets or weights to cloud services during acceptance.

## Record the local setup

Before testing, capture this non-secret environment manifest in the local acceptance report:

```json
{
  "os": "Windows version and build",
  "gpu": "GPU model and available VRAM",
  "comfyui": {"version": "", "commit": "", "listen_address": "127.0.0.1"},
  "h3_nodes": ["exact node class names and versions"],
  "checkpoints": [
    {"family": "H3-Base-FL2VA or H3-Base-Ref2VA", "filename": "", "sha256": ""}
  ],
  "workflow_files": [
    {"mode": "T2VA/FL2VA/Ref2VA", "path": "local path", "sha256": ""}
  ],
  "ffmpeg": "version string",
  "prompt_skill_hashes": {
    "SKILL.md": "a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0",
    "base-en.txt": "2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc",
    "ref-en.txt": "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7"
  }
}
```

Do not include API keys, source-media paths that disclose personal information, or model weights in the report. Review the MiniMax H3 Community License Agreement and any separate node licenses before downloading or distributing model assets.

## Acceptance procedure

1. Run Shine's migration and API on `127.0.0.1`; confirm `/health` reports `h3_connected: false` until a real adapter is implemented. Keep the Mock worker available for scheduler regression only.
2. Start the locally installed ComfyUI separately. Save ComfyUI version, `/system_stats`, `/object_info` results for the installed H3 nodes, exact checkpoint names and SHA-256 hashes. Confirm the selected graph uses only the intended H3-Base family and does not fall back to another model or online video API.
3. Prepare an original, user-authorized synthetic scene and user-approved/locked reference assets. Do not use the upstream projects' example footage as production media. Use a 4–6 second T2VA request first, with no external VLM or upload.
4. After a RealComfyUIProvider and H3 Prompt Compiler are implemented, submit only after the frozen style hash and per-request reference manifest are visible in Shine. Capture the durable submit-intent ID, ComfyUI `prompt_id`, request fingerprint, workflow hash, checkpoint hash, and returned output path in local logs.
5. Probe the output with FFprobe. Verify a decodable video stream, expected audio stream, 24 FPS, 4–15 second duration, intended aspect ratio, and local H3-Base output resolution. A local H3-Base result must not be described as the full hosted H3-Context-IR + H3-Regenerate-2K workflow.
6. Repeat with one Ref2VA request after runtime capability detection. Verify each manifest label maps to its own approved local file hash; enforce no more than 9 images, 3 videos, 3 audio clips, 15 seconds of video/audio references per type, and 12 total files. Check that a global asset ID never substitutes for a request-local H3 label.
7. Stop ComfyUI after a persisted submit intent and before its acknowledgement. Restart Shine and reconcile against ComfyUI queue/history by idempotency metadata or `prompt_id`; demonstrate that an uncertain result becomes `NEEDS_REVIEW` and is never blindly resubmitted. Repeat after acknowledgement and verify recovery resumes QC without a second ComfyUI job.
8. Retain only non-sensitive evidence: environment manifest, workflow/checkpoint hashes, prompt mode, ComfyUI task IDs, FFprobe output, test scene result, and audit events. Mark acceptance `PASS`, `FAIL`, or `BLOCKED`; do not report mock evidence as local H3 validation.

The ComfyUI adapter, official prompt compiler, real media QC, and this complete procedure remain **PENDING_LOCAL_VALIDATION / NOT_IMPLEMENTED** until that implementation and the Windows GPU run exist.
