# Shine Video Studio — V3 P2 Acceptance Report (Optional Nodes & Multimodal Ref2VA)

Date: 2026-11-21 -> 2026-11-22  
Branch: `main` (built directly on top of P1 commit `5cea98137f99c741adc14811bb590faece09e7ba`)  
Test GPU / Host: Windows 11, NVIDIA GeForce RTX 5080, isolated data dir `.local-acceptance/v3-p2-20261121`  
Local ComfyUI: `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` (git revision `b0b743566f65daafc423b4fea8a2fbda94b3384a`, 1281 `object_info` node classes)

---

## 1. Scope & Three Principles Compliance

Strictly scoped to V3 Phase 2 (no P3 setup redesign, no P4 Cinema Skills):

- **Single source of truth**:
  - `shine/comfyops/mode_capabilities.py::probe_optional_capabilities(object_info)` inspects live `object_info` once during `POST /api/v1/setup/services/validate` and records `optional_capabilities` into `deployment-service-validation`.
  - `shine/comfyops/setup_summary.py::sealed_optional_capabilities(db, settings)` and `relayvsr_machine_readiness` read that single sealed evidence (resolving `ACTIVE_KEY` identity when called from DB-only domain paths without `settings`).
  - `shine/strategy.py` (`accelerator_options`, `strategy_options`, `strategy_blockers`) derives all readiness & blockers from that single source + on-disk optional weight verification.
- **Single adjudicator**:
  - Both UI option projection (`GET /api/v1/projects/{id}/director/strategy-options`) and submission enforcement (`services.make_render_request`, `director/package.py`, `production/runner.py`) call `strategy.strategy_blockers` / `strategy_options`. Selected-but-unavailable capabilities fail explicitly (`PDD_BACKEND_UNAVAILABLE`, `SECOND_PASS_BACKEND_UNAVAILABLE`, `UPSCALE_BACKEND_UNAVAILABLE`) with HTTP `409 STRATEGY_BLOCKED`, never silently downgraded.
  - Hard continuity remains explicitly refused (`PENDING_HARD_CONTINUITY_TEST`).
- **Single executor**:
  - Both 7-stage (`shine/worker.py`) and 4-stage (`shine/manual4/worker.py`) call `shine/foundation/h3.py::ComfyH3Executor` -> `shine/comfy/graph_compiler.py::compile_ref2va_workflow`.
  - `episode_input_digest` (`shine/production/episode_delivery.py`) hashes RelayVSR 2X artifacts (`relay_artifact_for_job`) into episode delivery identity.

---

## 2. Implemented Changes

| Module | Summary |
| --- | --- |
| `backend/shine/comfy/graph_compiler.py` | Verified `VIDEO_TRANSFER` via `VHS_LoadVideo` (`nodes 26..28`, `force_rate=24.0`, `format="None"`, up to 3 videos; total refs `image + video + audio <= 12`, `image >= 1`). Wired only `ref_videos.ref_video_{i}` (deliberately omitting `ref_video_audio_{i}` so silent shots stay non-vocal and audio ordinals never shift). Included `"video_count"` in `recipe_payload` only when `video_count > 0`, preserving 100% of P1 `recipe_sha256` / `graph_sha256`. |
| `backend/shine/comfy/capabilities.py` | Reported `VIDEO_REFERENCE_CONTINUITY` and `"video_reference": "VERIFIED_TRANSFER"` when profiles/compiled graphs carry `reference_video_*` and `media_transfers["video"]`. |
| `backend/shine/foundation/h3.py` | Bound `reference_video_{vid_idx}` uploads in `ComfyH3Executor.execute`. |
| `backend/shine/manual4/worker.py` | Added video file existence + SHA-256 verification (`M4_REFERENCE_FILE_MISSING` / `M4_REFERENCE_SHA_MISMATCH`) and `reference_video_{idx+1}` binding for 4-stage execution. |
| `backend/shine/comfyops/mode_capabilities.py` | Added `probe_optional_capabilities(object_info)` covering `pdd`, `second_pass`, `video_reference`, `audio_reference`, and `relayvsr`. |
| `backend/shine/comfyops/service_validation.py` | Executed `probe_optional_capabilities(object_info)` during live validation and returned/sealed `optional_capabilities`. |
| `backend/shine/comfyops/setup_summary.py` | Added `sealed_optional_capabilities(db, settings)` and merged live `RELAYVSR_NODES_MISSING_IN_LIVE` blockers into `relayvsr_machine_readiness`. |
| `backend/shine/strategy.py` | Wired `_optional_capability_blockers` into `accelerator_options`, `strategy_options`, and `strategy_blockers` (`PDD_BACKEND_UNAVAILABLE`, `SECOND_PASS_BACKEND_UNAVAILABLE`, `UPSCALE_BACKEND_UNAVAILABLE`). |
| `backend/shine/services.py` | Threaded `settings` through `make_render_request` and `create_render_job` so `POST /jobs` enforces the exact same `strategy_blockers` as `/director/strategy-options`. |
| `backend/shine/production/episode_delivery.py` | Included passed `relay_artifact_for_job(db, job.id).id` in `episode_input_digest`. |
| `frontend/src/api.ts`, `frontend/src/components/DirectorShootPanel.tsx` | Exposed accelerator blockers/reasons and disabled `secondPass` when unsupported. |
| `backend/tests/test_graph_compiler.py`, `backend/tests/test_v3_p2_optional_multimodal.py` | Updated video compiler specs and added 33 P2 integration/full tests (matrix combinations, missing-backend refusal, HTTP 409 gate, 7-stage & 4-stage video binding, sound anti-regression, episode digest). |

---

## 3. Live RTX 5080 Hardware Verification (Isolated Stack)

Executed via two-phase isolated run `.local-acceptance/v3-p2-20261121/run-p2-live.ps1` (`start_shine.ps1 -Provider comfyui -NoWorker` -> setup/validate/activate -> `stop_shine.ps1` -> `start_shine.ps1 -Provider comfyui` -> renders -> `stop_shine.ps1`):

1. **Live Service Validation (`POST /api/v1/setup/services/validate`)**:
   - `configuration_sha256`: `781c1f7bc27dca282a2b16d3f04bad770ed2b173697d8950a0e4d6c635e71a52` (`node_count=1281`).
   - `optional_capabilities`:
     - `pdd`: `available=true` (`MiniMaxH3PDDAccApply`, `MiniMaxH3PDDAccScheduler`, `ShineH3SamplingMode`, weight present)
     - `second_pass`: `available=true` (`MinimaxH3LatentUpscaler3DRefineHandoff`, `VRAM_Debug`, `MiniMaxH3PDDAccScheduler`, `latent_upscaler` present)
     - `video_reference`: `available=true` (`VHS_LoadVideo` present)
     - `audio_reference`: `available=true` (`VHS_LoadAudioUpload` present)
     - `relayvsr`: `available=false`, `status="BLOCKED"`, `RELAYVSR_NODES_MISSING_IN_LIVE` (`missing_nodes=["RelayVSRConditioning", "VSRDecode"]` on ComfyUI `b0b743566`, even though RelayVSR weight files exist on disk).
2. **Honest Refusal Gate (`POST /api/v1/projects/{pid}/jobs`)**:
   - Requesting `upscale={"enabled": true, "engine": "relayvsr", "variant": "x2"}` returned HTTP `409 STRATEGY_BLOCKED` with `UPSCALE_BACKEND_UNAVAILABLE` -> `RELAYVSR_NODES_MISSING_IN_LIVE` (zero silent downgrade, zero queued jobs).
3. **Real RTX 5080 Render 1 — `pdd8` + `second_pass=True` + 1 Image + `VHS_LoadVideo` Video Reference (Silent)**:
   - Job `46b83e9a-958c-4ee5-8194-f83a262c94e2`, Attempt `a0117aa1-cf88-4560-a53c-bf251e36d607`, Prompt ID `c731e27f-f1dc-4006-bc02-75574852ebe3`, Status `PASSED`, `quality_verification="real"`.
   - Submitted `prompt_graph_sha256`: `c6190575f0aa9aedb0f61e0cba15e4e15e21886b5f45c7709b5cabdb88921f7b` (`workflow_profile_hash`: `59fa41f9a638409a9b0f5efa41f28154ff0dc00de0742a1ae749896354436fc0`).
   - Verified submitted ComfyUI graph nodes: includes `MiniMaxH3PDDAccApply`, `MiniMaxH3PDDAccScheduler`, `VHS_LoadVideo` (`force_rate=24.0`, `format="None"`), `MinimaxH3LatentUpscaler3DRefineHandoff`, `VRAM_Debug`; binds `ref_images.ref_image_0` + `ref_videos.ref_video_0`; omits `ref_video_audio_0` and `VHS_LoadAudioUpload`.
   - Sound contract: includes `"This shot has no dialogue: the soundtrack is entirely non-vocal."` and `"Use the visual track only."`.
   - Artifact `15c431c1-1525-404a-a015-ba91164c3a24` (`is_mock=false`, SHA `53ecc881a82cbd375dc71a7cb61c7e6f4591a1af8ddc9b4fe639dea636c68131`, `2,442,532` bytes): `ffprobe` confirms `1344x768` `h264` (`5.165s`) + `aac` (`5.166s`), `ffmpeg -v error -i ... -f null -` exit code `0`.
4. **Real RTX 5080 Render 2 — `native20` Baseline + `second_pass=False` + 1 Image + `VHS_LoadAudioUpload` (Chinese Dialogue)**:
   - Job `36ed4555-787a-4d62-adbd-bb3de28d2929`, Attempt `6088eebb-037a-4b3d-9aff-76519b04ba3b`, Prompt ID `fa36c1df-eb31-4e52-af0a-d60744dd0a3a`, Status `PASSED`, `quality_verification="real"`.
   - Submitted `prompt_graph_sha256`: `65b746af649257b28469cb00e5caa67d8c3c03967cd029f1a799fc8c74990cdc` (`workflow_profile_hash`: `49d068f8cdb3581c8c18e6a2a347a1a3f34e203047c0ecb3b7a04ac8ac98038e`).
   - Verified minimal execution graph (`23` nodes): excludes `MiniMaxH3PDDAccApply`, `MiniMaxH3PDDAccScheduler`, `VHS_LoadVideo`, `MinimaxH3LatentUpscaler3DRefineHandoff`, `VRAM_Debug`; binds `ref_images.ref_image_0` + `ref_audios.ref_audio_0` (`VHS_LoadAudioUpload`).
   - Sound contract: includes `"Every voice in this shot speaks Chinese."` + `"<d>[Chinese] 今晚雨下得很大。</d>"`.
   - Artifact `5f7a1dab-7c02-4840-89ee-7cb8a9f6ee8c` (`is_mock=false`, SHA `d3e96890f20b02de2c526ce9edd831d3fed6ad420c7fa369722f7810231b36bf`, `2,068,881` bytes): `ffprobe` confirms `1344x768` `h264` (`5.165s`) + `aac` (`5.166s`), `ffmpeg -v error -i ... -f null -` exit code `0`.
5. **Cleanup**: `stop_shine.ps1` stopped API, Worker, Worker-M4, Web, and Supervisor cleanly (`run/processes.json` removed; `resource_leases` shows `RELEASED: 31`, `ACTIVE: 0`, `RECONCILE_REQUIRED: 0`).

---

## 4. Verification Gates (Final Code State)

| Check | Scope / Command | Result |
| --- | --- | --- |
| Ruff | `backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests` | Exit `0` (`All checks passed!`) |
| Copilot contracts | `backend\.venv\Scripts\python.exe backend/scripts/generate_shine_copilot_contracts.py --check` | Exit `0` (`discovered=475`) |
| Alembic | `backend\.venv\Scripts\python.exe -m alembic current` & `check` | `f180f0010008 (head)`, `No new upgrade operations detected.` |
| Frontend full | `npx vitest run --no-file-parallelism`, `tsc --noEmit`, `npm.cmd run build` | `93` test files / `471` passed; `tsc` & `vite build` exit `0` |
| Backend 8-shard suite | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8` | `302` files across `chunk-0..chunk-7` all exit `0`, `shards_with_failure_markers=0` (`shards-20261122-004649-218`, wall `914.1s`) |
| Windows test package | `pwsh -NoProfile -File scripts\build_test_package.ps1 -OutDir .local-acceptance\v3-p2-20261121\package -Verify` | Verified after commit (see git commit below) |

---

## 5. Honest Scope & Unverified / Blocked Boundaries

- **BLOCKED on current machine (`RelayVSR 2X` real GPU render)**: Local ComfyUI revision `b0b743566f65daafc423b4fea8a2fbda94b3384a` lacks `RelayVSRConditioning` and `VSRDecode` (introduced at upstream `2f8943324e1eae89a5ee6c7db93602299246b5f1`). Live `validate_services` and `strategy-options` honestly mark RelayVSR `BLOCKED` (`RELAYVSR_NODES_MISSING_IN_LIVE`), and `POST /jobs` refuses it (`409 STRATEGY_BLOCKED`). RelayVSR graph/EDL logic is verified offline in pytest only (`test_relay_upscale_stage.py`, `test_relayvsr_and_profiles.py`, `test_v3_p2_optional_multimodal.py`), NOT rendered on GPU.
- **NOT_VERIFIED on live hardware**:
  - Live `>= 3` video references, `9` images, or `12`-mixed-reference GPU execution (verified at compiler/executor test level only).
  - Live 4-stage (`manual4`) GPU run with `video` reference (4-stage P2 changes verified via `test_manual4_worker_video_reference_sha_and_graph_binding` and `backend/tests/manual4`).
  - RTX 4070 / RTX 5090vD2, `W6A8`, `NVFP4`, `turbo4`, and external cloud VLM/ASR semantic adjudication (`acceptance_status` remained `pending_review` because VLM/ASR advisory models are absent on this host).
