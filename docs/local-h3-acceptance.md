# Local MiniMax-H3 acceptance — PENDING_LOCAL_H3

The repository now contains a real ComfyUI HTTP adapter, official-format prompt compiler, Fake ComfyUI lifecycle tests, technical QC, semantic-review protocol, and bounded repair engine. None of that is evidence of real H3 output quality. Close this checklist only on the user's Windows machine with a supported GPU, installed ComfyUI, and locally available MiniMax-H3 checkpoints.

Do not upload private assets or model weights to cloud services during acceptance.

## Current status

| Area | Status |
| --- | --- |
| ComfyUI HTTP client, upload, `/prompt`, `/queue`, `/history`, `/view`, interrupt | `IMPLEMENTED` / `MOCK_VERIFIED` with Fake ComfyUI |
| Per-family reference media transfer (extension, upload path, loader capability) | `IMPLEMENTED` / `TESTED_OFFLINE` |
| Intent-first submission and conservative reconciliation (no duplicate render) | `IMPLEMENTED` / `TESTED_OFFLINE` |
| Automatic collection and re-check of finished external tasks | `IMPLEMENTED` / `TESTED_OFFLINE` |
| Named quality gates (`technical`/`semantic`/`audio`/`style`/`final`) and the per-project policy | `IMPLEMENTED` / `TESTED_OFFLINE` |
| Automatic repair that produces a different request revision | `IMPLEMENTED` / `TESTED_OFFLINE` |
| Workflow Profile schema and strict template validation | `IMPLEMENTED` / `TESTED_OFFLINE` |
| Real H3 API workflow mapping for the user's nodes/checkpoints | `BLOCKED` — no exported workflow JSON was available |
| Real video/audio media transport for the user's H3 loader nodes | `BLOCKED` — the nodes are unknown; the profile must declare `media_transfers` |
| H3 prompt compiler T2VA/I2VA/FL2VA/L2VA/Ref2VA | `IMPLEMENTED` / `TESTED_OFFLINE` against the pinned official skill format |
| Real T2VA/Ref2VA generation, identity/lip-sync/quality | `PENDING_LOCAL_H3` |
| FFprobe decode / FFmpeg silence QC | `PENDING_LOCAL_H3` if tools are not installed |
| Real VLM/ASR review | `IMPLEMENTED` protocol; `BLOCKED` without an authorized API profile |
| Long-shot continuation | Data contract + dependency invalidation only; real H3 long-shot experiment not run |

## Record the local setup

Before testing, capture this non-secret manifest in the local acceptance report:

```json
{
  "os": "Windows version and build",
  "gpu": "GPU model and available VRAM",
  "comfyui": {"version": "", "commit": "", "listen_address": "127.0.0.1"},
  "h3_nodes": ["exact ComfyUI node class names and versions"],
  "checkpoints": [
    {"family": "H3-Base-FL2VA or H3-Base-Ref2VA", "filename": "", "sha256": ""}
  ],
  "workflow_profiles": [
    {"mode": "T2VA/FL2VA/Ref2VA", "name": "", "graph_sha256": "", "path": "local path"}
  ],
  "ffmpeg": "version string or unavailable",
  "prompt_rule_version": "h3-prompt-writing@d21241f0a4b3",
  "prompt_skill_hashes": {
    "SKILL.md": "a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0",
    "base-en.txt": "2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc",
    "ref-en.txt": "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7"
  }
}
```

Do not include API keys, absolute source-media paths that disclose personal information, or model weights. Review the MiniMax H3 Community License and any separate node licenses before downloading or distributing model assets.

## Acceptance procedure

1. **Environment and migration:** Set `SHINE_DATA_DIR`, run `scripts/check_environment.ps1`, `alembic upgrade head`, and `scripts/start_shine.ps1 -Provider mock`. Confirm `/health` reports `h3_connected:false`, and `/api/v1/system/status` distinguishes API, Worker, Mock, and H3. Do not expose the API beyond loopback.
2. **ComfyUI probe:** Start ComfyUI locally. Enter its URL in Settings → ComfyUI Connection → Test. Record `/system_stats`, the installed H3 node class names from `/object_info`, checkpoint filenames and SHA-256, and the exact ComfyUI commit.
3. **Register the real workflow:** Export the user's ComfyUI **API workflow JSON**. Register it as a Workflow Profile with:
   - mode `T2VA`, `FL2VA`, or `Ref2VA`;
   - the exact required node class names;
   - a `parameter_map` mapping `positive_prompt`, `seed`, `duration`, and any reference slots (`reference_picture_1`, `reference_subject_1`, `reference_video_1`, `reference_audio_1`, …) to real `node.inputs.field` paths. **A missing reference slot blocks the render with `PENDING_WORKFLOW_MAPPING`; it is never skipped.**
   - a `media_transfers` declaration for the `video` and `audio` families, naming the upload path, the multipart field, the subfolder and the loader node classes the files need. Image uses ComfyUI's documented `/upload/image` default; **video and audio have no default** and the render is blocked until they are declared.
   - the intended H3-Base checkpoint family.
   The generic template shipped in the UI is **not** an H3-validated mapping. Replace it before any real job. Then call `POST /api/v1/workflow-profiles/{id}/validate` and record the result.
4. **Assets and style:** Upload only original, user-authorized assets. Set role/entity/tags, approve, and lock them. Fill all seven Style Bible dimensions, freeze V1, and confirm every new shot references the current frozen version.
5. **T2VA smoke test:** Create a 4–6 second T2VA shot with no references. Preview the compiled prompt and verify `integrated_multimodal_description`, `overall_soundscape`, and `non_diegetic_music` are present. Select `provider=comfyui`, create the job, and keep the worker running. Record the durable submit-intent ID, ComfyUI `prompt_id`, request/prompt/workflow hashes, and output path.
6. **Probe the output:** Run FFprobe and technical QC. Verify a decodable video, expected audio stream, 24 FPS, 4–15 second duration, intended aspect ratio, and local H3-Base resolution. A local H3-Base result must not be described as the full hosted H3-Context-IR + H3-Regenerate-2K workflow. If FFprobe is missing, QC must remain `needs_review`, never `passed`.
7. **Ref2VA test:** Bind an approved character subject, a first/last frame picture, a video reference, and an audio reference as applicable. Verify each `<Subject/Picture/Video/Audio N>` label maps to its own file hash; enforce 9 images, 3 videos, 3 audio, 12 total, and 15 seconds per media type. A global asset ID must never substitute for a request-local label. Add one dialogue line and verify the speaker maps to a bound subject.
8. **Uncertain submission:** Stop ComfyUI after a persisted submit intent and before acknowledgement. Restart Shine and reconcile against `/history` and `/queue`. Demonstrate that an unknown result becomes `NEEDS_REVIEW` and is never blindly resubmitted. Repeat after acknowledgement and verify recovery resumes QC without a second ComfyUI job. Verify the ComfyUI queue/history shows exactly **one** external task for that logical attempt.
9. **Revision during render:** Revise a shot while ComfyUI is rendering. Verify the worker discards the stale result, does not write it into the passed library, and marks downstream continuity jobs stale while independent shots remain intact.
10. **Repair:** Force a deterministic technical failure (for example, a wrong duration or missing audio stream). Verify technical QC fails, the repair engine classifies it, the semantic budget stops automatic retries, and the job escalates rather than claiming success. When an automatic repair does run, verify that the new attempt has a different `request_fingerprint` and a different `prompt_sha256` and that `request_revision` advanced.
11. **Quality gates:** Configure a real VLM/ASR profile, enable `allow_remote_review` for the project, and verify `GET /api/v1/jobs/{id}/quality-gates` shows a covered `semantic` and `audio` gate with the real model name and `is_mock=false`. Verify that a shot with a passing technical gate but no semantic review stays `needs_review`.
12. **Retain evidence:** Keep only non-sensitive evidence: environment manifest, workflow/checkpoint hashes, prompt mode + hash, compiler/rule version, ComfyUI task IDs, FFprobe output, test scene result, QC checks, the gate ledger, and audit events. Mark acceptance `PASS`, `FAIL`, or `BLOCKED`.

## Do not claim

- Do not claim H3 identity/lip-sync/quality acceptance from Fake ComfyUI or Mock jobs.
- Do not claim the full short drama can run unattended.
- Do not claim the generic workflow template is the user's real H3 graph.
- Do not download or replace model weights, modify the user's ComfyUI workflow files, or run GPU inference as part of an offline build task.
