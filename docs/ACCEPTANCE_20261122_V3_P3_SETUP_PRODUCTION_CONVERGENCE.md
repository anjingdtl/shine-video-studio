# Shine Video Studio — V3 P3 Acceptance Report (Setup & Production Convergence)

Date: 2026-11-22  
Branch: `main` (built directly on top of P2 commit `c00848977270400e4e436737491a15b90ab30ba8`)  
Test GPU / Host: Windows 11, NVIDIA GeForce RTX 5080 (16,303 MiB VRAM), isolated data dir `.local-acceptance/v3-p3-20261122`  
External ComfyUI Reused (Zero Copy/Download): `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` (git revision `b0b743566f65daafc423b4fea8a2fbda94b3384a`, 1,281 `object_info` node classes)

---

## 1. Scope & Three Principles Compliance

Strictly scoped to V3 Phase 3 (`SHINE_V3_PHASE3_SETUP_PRODUCTION_CONVERGENCE_20261121.md`; no P4 Cinema Skills; `worker.py` & `preproduction.py` untouched so `client_review_identity` stays unchanged):

- **One Fact (`一套事实`)**:
  - `setup_summary.summary(db, settings)` and `foundation/environment.py::environment_snapshot` expose a unified `optional_capabilities` projection (`pdd`, `second_pass`, `video_reference`, `audio_reference`, `relayvsr`) derived solely from `sealed_optional_capabilities` and `relayvsr_machine_readiness`.
  - Top-level `setup_summary.summary["blockers"]` tracks only non-optional baseline blockers; optional advisories stay separated in `optional_advisories` and never block base 1-image H3 shooting.
  - `resource_discovery._catalogues()` includes `relayvsr-x2-windows-x64` and `resource_discovery.report(db)` buckets `h3` (strictly `required_h3_components`), `image`, `relayvsr`, `vlm`, and `asr` identically to `setup_summary._component_ids_for`.
  - `deployment_readiness` sets `production_gates["workflow_profiles_registered"]` to `True` when live service validation (`shine.ref2va.v1`) is `READY`, eliminating false profile-registration gates on V3 installs.
- **One Adjudicator (`一个裁决`)**:
  - `strategy.shot_reference_capability_blockers(db, settings, audio_count=..., video_count=...)` enforces per-shot $B \cup E(\text{shot}, \text{strategy}, \text{bindings})$ loader closure (`VHS_LoadVideo` / `VHS_LoadAudioUpload`) only when a shot actually binds video or audio references, scoping failures (`VIDEO_REFERENCE_BACKEND_UNAVAILABLE` / `AUDIO_REFERENCE_BACKEND_UNAVAILABLE`) strictly to that shot.
  - `foundation/environment.py` exposes `h3_strategy_options`, `h3_shot_reference_blockers`, and `h3_strategy_blockers` facades while respecting `test_isolation_static.py` boundaries, so 4-stage (`manual4/production/service.py` & `manual4/api/production_routes.py`) and 7-stage (`director/package.py` & `services.py`) call the exact same `strategy.py` adjudicator.
  - `environment.check_capability` defaults omitted `accelerator_id` to `native20` (`BASELINE_ACCELERATOR_ID`), resolves pinned catalog weights for the base recipe without failing `RESOURCE_PIN_MISSING` on unpinned `LATENT_UPSCALER` when `second_pass=True`, and evaluates full `strategy_blockers` + `shot_reference_capability_blockers`.
- **One Executor (`一个执行者`)**:
  - No second preparer, second check chain, second downloader, or second ComfyUI instance added.
  - `profile_gate._weight_blockers` passes `include_profiles=False` into `load_binding`, reading the active recipe without touching archived static workflow JSONs.

---

## 2. Implemented Changes

| Module | Summary |
| --- | --- |
| `backend/shine/strategy.py` | Extended `_optional_capability_blockers` to cover `video_reference`/`audio_reference`; added `shot_reference_capability_blockers` & `optional_capabilities_overview`; added `reference_loaders` & `optional_capabilities` to `strategy_options`; made `upscale_options` null-DB safe. |
| `backend/shine/director/package.py` | Wired `shot_reference_capability_blockers` into `_compiled_shot_blockers` so missing `VHS_LoadVideo`/`VHS_LoadAudioUpload` only blocks the specific shot/episode binding those references. |
| `backend/shine/services.py` | Enforced `shot_reference_capability_blockers` inside `make_render_request` before graph compile (`409 STRATEGY_BLOCKED`). |
| `backend/shine/foundation/environment.py` | Added `h3_strategy_options`, `h3_shot_reference_blockers`, and `h3_strategy_blockers` facades; added `optional_capabilities` to `environment_snapshot`; updated `check_capability` to default to `native20`, avoid false `RESOURCE_PIN_MISSING` on `second_pass`, and include `strategy_blockers` + `shot_reference_capability_blockers`. |
| `backend/shine/manual4/production/service.py`, `backend/shine/manual4/api/production_routes.py` | Threaded `platform_db` and `settings` through manual4 production state, snapshot preview, run creation, and retake endpoints, sharing the single `strategy.py` option matrix and refusal gates (`409 STRATEGY_BLOCKED` / shot-scoped `VIDEO_REFERENCE_BACKEND_UNAVAILABLE`, preserving `422 M4_ACCELERATOR_UNVERIFIED` for `turbo4`). |
| `backend/shine/comfyops/setup_summary.py`, `resource_discovery.py`, `adoption.py`, `profile_gate.py`, `deployment.py` | Unified `optional_capabilities`, separated optional advisories from top-level blockers, added `relayvsr-x2-windows-x64` discovery & consistent capability bucketing in `resource_discovery`, converged `_required_h3_files` onto `_required_capability_files`, avoided legacy static disk reads in `profile_gate`, and honored `services/validate` readiness in `workflow_profiles_registered`. |
| `frontend/src/components/SetupEnvironmentStatus.tsx`, `frontend/src/manual4/api/client.ts`, `frontend/src/manual4/pages/ScheduleProduceStagePage.tsx` | Rendered per-feature optional capability chips (`已就绪` / `可选未配置`), clarified external ComfyUI reuse wording (no duplicate install/weights), and reflected live `verified`/`supported` status for accelerators & `second_pass` in manual4 Stage 3. |
| `backend/tests/test_v3_p3_setup_production_convergence.py`, `frontend/src/test/setupOptionalCapabilities.test.tsx` | Added 10 integration/full P3 counterexample cases (`ce1..ce10`) covering RelayVSR optionality, shot-scoped video/audio loader refusal in 7-stage & 4-stage, `second_pass` without RelayVSR, zero-download discovery/reconcile/adoption, task deduplication, stalled-run/lease recovery, multi-episode isolation, candidate variant refusal, and 7/4-stage parity, plus Vitest coverage. |

---

## 3. Live RTX 5080 Hardware Verification (Isolated Stack `.local-acceptance/v3-p3-20261122`)

1. **Fresh Discovery, Zero-Download Verification & External Adoption**:
   - Started isolated stack with `-NoWorker` from empty `shine.db` pointing `SHINE_COMFY_PROBE_ROOTS` at `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`.
   - `POST /api/v1/setup/existing-installations/inspect` & `verify`: `status=INSTALLED_VERIFIED`, `verified_bytes=43,978,717,531`, `copied_bytes=0`.
   - `POST /api/v1/setup/existing-installations/adopt`: `status=ADOPTED`, `instance_id=d99e0095-4f35-5efb-ae86-7484305e5803`, `installed_h3_bytes=43,974,265,807`, `copied_bytes=0`, `downloaded_bytes=0`.
2. **Configuration, Live Service Validation & Activation**:
   - `POST /api/v1/setup/configuration`: `recipe_sha256=591ef56d0544e54e6032d41635ac31d8889005796e6f08ea9bfd69660bff98c9`.
   - `POST /api/v1/setup/services/validate`: `status=200 READY`, `node_count=1281`, `optional_capabilities`: `pdd=true`, `second_pass=true`, `video_reference=true`, `audio_reference=true`, `relayvsr=false` (`RELAYVSR_NODES_MISSING_IN_LIVE`); `advisory_reviews` VLM/ASR recorded as `advisory=true NOT_READY`.
   - `POST /api/v1/setup/runtime/activate`: `status=ACTIVE`; `GET /api/v1/setup/readiness`: `workflow_profiles_registered=true`, `services_verified=true`.
3. **Cross-Line Parity & Negative Refusal Gates**:
   - Verified `/setup/summary`, `/projects/{pid7}/director/strategy-options`, `/manual4/system/status`, and `/manual4/projects/{pid4}/production/state` report identical `comfyui_root` and optional capability verdicts.
   - 7-stage `POST /projects/{pid7}/jobs` with `upscale.enabled=true` -> HTTP `409 STRATEGY_BLOCKED` (`UPSCALE_BACKEND_UNAVAILABLE` -> `RELAYVSR_NODES_MISSING_IN_LIVE`).
   - 4-stage `POST /manual4/projects/{pid4}/production/snapshot/preview` with `variant_id="ref2va-lowend-candidate"` -> HTTP `409 STRATEGY_BLOCKED` (`MODEL_VARIANT_NOT_ACCEPTED`).
4. **Shared Real RTX 5080 H3 Renders (7-Stage + 4-Stage on Same Adopted Instance)**:
   - **7-Stage (`native20`, 1-image baseline)**: Job `1536dfd8-d71c-4d3d-874b-504ed533ba27` (Attempt `4520a57b-b08a-41c9-84e2-4a430d9c8bba`, Comfy prompt `4aec3aa0-1434-42f8-8c75-c855568a5e7c`), `status=PASSED`, `quality_verification="real"`; output `V3 P3 七阶基线出片验证_视频_001镜_未分集.mp4` (`2,091,710` bytes, SHA `786009f2732857c1d3ed99e991082046c38aaf5b3d731d853823bbbd6cbb7060`), `ffprobe` confirms `1344x768` `h264` (`124` frames, `5.165s`) + `aac` (`5.166s`), `ffmpeg -v error -i ... -f null -` exit `0`.
   - **4-Stage (`manual4`, `pdd8`, 1-image)**: Run `b430eb61-f7fd-429f-b68a-6ef928f80fe1` (`COMPLETED`), Job `5267419c-495c-42f3-8c98-9d93e3f1bb5e` (Attempt `6a05be98-41f1-4ab7-99be-71636eb608bd`, Comfy prompt `86a554bc-2376-459e-b1ca-8bb94fc92e02`), `status=PASSED`; output `3572786d9f17090306ef0a8e80d81bb22779aed814c761ff7f02caa7ff31f66c.mp4` (`1,183,677` bytes), `ffprobe` confirms `672x384` `h264` (`124` frames, `5.165s`) + `aac` (`5.166s`), `ffmpeg -v error -i ... -f null -` exit `0`.
5. **Clean Shutdown & Lease Release**:
   - `stop_shine.ps1` terminated API, Worker, Worker-M4, Web, and Supervisor cleanly; `resource_leases` ledger confirmed `[('RELEASED', 7)]` (`ACTIVE: 0`, `RECONCILE_REQUIRED: 0`).

---

## 4. Verification Gates

| Check | Scope / Command | Result |
| --- | --- | --- |
| Targeted P3 & Isolation | `backend\.venv\Scripts\python.exe -m pytest backend/tests/manual4/test_isolation_static.py backend/tests/test_v3_p3_setup_production_convergence.py -q` | Passed (`13` tests, `0` failures) |
| Ruff | `backend\.venv\Scripts\ruff.exe check backend` | Exit `0` (`All checks passed!`) |
| Copilot contracts | `.\.venv\Scripts\python.exe -m scripts.generate_shine_copilot_contracts --check` | Exit `0` (`discovered=475`) |
| Alembic | `.\.venv\Scripts\python.exe -m alembic check` | Exit `0` (`No new upgrade operations detected.`) |
| Frontend full | `npx vitest run --no-file-parallelism`, `npx tsc --noEmit`, `npm.cmd run build` | `94` test files / `472` passed; `tsc` & `vite build` exit `0` |
| Backend 8-shard full | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8` | `303` files across `chunk-0..chunk-7` all exit `0`, `shards_with_failure_markers=0` (`shards-20261122-020135-231`, wall `981.3s`) |

---

## 5. Honest Scope & Remaining Hardware Boundaries

- **BLOCKED on current ComfyUI `b0b743566`**: `RelayVSRConditioning` and `VSRDecode` nodes are absent on this host revision; `optional_capabilities["relayvsr"]` honestly reports `OPTIONAL_UNCONFIGURED` (`RELAYVSR_NODES_MISSING_IN_LIVE`) without blocking baseline shooting.
- **NOT_TESTED on hardware**: RTX 4070 / RTX 5090vD2, `W6A8` / `NVFP4` / `turbo4`, live RelayVSR 2X GPU upscaling, and live VLM/ASR semantic adjudication (`acceptance_status` stays `pending_review`).
