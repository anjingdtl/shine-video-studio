# Shine视频工作台 · Shine Video Studio

> Local-first short-drama production workbench built around **MiniMax-H3 via local ComfyUI**, with an independent, provider-configurable LLM Agent runtime.

**Current status: M1 hardened; M2/M3 offline foundations implemented and tested; the offline production loop is closed and reproducible.** The backend, SQLite migrations, React workbench, per-shot asset bindings, frozen style versions, concurrency-safe worker, project audit, worker health, independent LLM gateway, Agent planning runtime, official-format H3 prompt compiler, ComfyUI HTTP adapter with strict reference-media transfer, Fake ComfyUI server, five named quality gates, request-revision repair engine, automatic recovery of finished external tasks, and the Offline Production E2E all run without a GPU. Mock and fake-HTTP evidence never counts as real MiniMax-H3 validation. See [`docs/OFFLINE_FINAL_ACCEPTANCE_REPORT.md`](docs/OFFLINE_FINAL_ACCEPTANCE_REPORT.md).

## V1.2.1 Ref2VA hardening

Two local Ref2VA INT8 ConvRot workflow profiles are registered; three existing real-H3
480×864 artifacts were rechecked for hashes and full decoding. Their low-resolution visual
quality is **not accepted for production**. The user confirmed normal voice, verbatim dialogue
and lip-sync; this feedback is separate from the authenticated per-item approval ledger.
Last-frame image reference is **SOFT_REFERENCE_CONTINUITY**. Hard anchoring and real video
reference transfer remain pending and fail closed. No new GPU render was run.

V1.2.1 adds authenticated loopback operator review, current Attempt/Artifact/hash snapshots,
concurrency and replay protection, graph-derived model identity, conservative continuity gates,
and Web video preview/review. See [the closeout report](docs/H3_REF2VA_V1.2.1_FINAL_CLOSEOUT.md)
and [operator setup](docs/operator-review.md). Original data and successful sampling graphs
are preserved. This is not a declaration of final film-quality acceptance.

## Product boundaries
- **Video model:** only locally deployed MiniMax-H3 through ComfyUI. No silent substitution with another video model, online service, or H3 generation mode.
- **Deployment:** Windows local Web application; FastAPI, React/TypeScript, SQLite WAL, and a separately restartable worker. API/Vite bind to `127.0.0.1`.
- **LLM/VLM:** independent runtime with configurable provider profiles and role routes. Codex is a development tool, never a runtime dependency.
- **Assets:** only user-supplied, user-approved master character/scene/prop/voice assets. The Agent cannot create, replace, approve, or mutate them.
- **Style:** the user freezes a seven-dimension Style Bible before production. New shots bind to the current frozen version; historical shots keep their original version. A new version invalidates unstarted work that used the old style.
- **Production:** structured ShotSpec → deterministic H3 prompt compilation → durable job/attempt state → ComfyUI or explicitly labelled Mock → technical/semantic QC → bounded repair or human review.

## What is implemented
- **M1 hardening:** strict style-version gates, frozen-style impact analysis, per-shot binding table and API, project-scoped audit pagination/filters, worker heartbeat table and `/system/status`, worker result-version guards, conditional leases, and crash reconciliation.
- **M2 offline:** OpenAI-compatible/Anthropic/Ollama/Mock LLM adapters, DPAPI (or `0600` file) key storage, role routing, capability probes, script parsing (TXT/Markdown/JSON/DOCX/PDF), deterministic and LLM planners, official `h3-prompt-writing` compiler with pinned rule hashes, ComfyUI HTTP client/adapter, strict workflow profiles, Fake ComfyUI server, and continuity dependency contracts.
- **M3 offline:** structural media probing, optional FFprobe/FFmpeg technical QC, pluggable VLM/ASR reviewer protocol with evidence, confidence-aware fusion, root-cause classification, bounded RepairPlan engine, and passed-job protection.
- **M1/M2/M3 final hardening:** strict per-family ComfyUI reference-media transfer (no silent reference loss, content-type based file extensions, explicit video/audio transport adapters), an intent-first submission protocol that never duplicates an external render, five named quality gates (`technical`, `semantic`, `audio`, `style`, `final`) with a per-project quality policy, automatic repair that produces a genuinely different request revision, automatic collection and re-check of external tasks that already finished, and a repeatable Offline Production E2E.
- **Web workbench:** project status, asset approval/lock/metadata, independent shot bindings, Style Bible freeze/version history, Prompt preview, provider/role settings, ComfyUI test and workflow registration, worker/queue status, QC evidence, per-gate quality ledger, repair suggestions, and audit history.

## Explicitly not claimed
- Three local Ref2VA renders exist. Production visual quality is blocked; other generation modes and hard continuity have no acceptance. User playback feedback and authenticated database approvals are distinct evidence sources.
- Two real Ref2VA workflow JSONs are in `config/workflows`, with two image slots and optional one audio slot. No dynamic slot expansion or verified video-reference transfer is claimed. Mock/Fake HTTP results remain software-only evidence.
- Video and audio references have **no default transport**. A workflow profile must declare how those families reach the H3 nodes, otherwise the render is blocked with `PENDING_WORKFLOW_MAPPING` instead of silently dropping the file.
- FFprobe/FFmpeg may be absent on the current machine. Technical QC then reports `unavailable`, never `pass`, for the decode, stream-metadata and silence checks.
- A job that passed every gate with a mock reviewer or a mock ASR is labelled `mock_verified`; it is not a video-quality claim.
- General API authentication is not implemented; operator review has separate server-validated local credentials. Keep the API on loopback. The ComfyUI renderer URL must point at this machine unless `SHINE_ALLOW_REMOTE_COMFYUI=1` is set explicitly, because reference bytes are uploaded to it.

## Run locally

Requirements: Python 3.12, Astral `uv`, Node.js 20+ and npm. FFmpeg/FFprobe are optional but required for decode and silence QC. No API key, GPU, ComfyUI, or Codex runtime is needed for the Mock workflow.

On Windows, open PowerShell in the repository:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
./scripts/check_environment.ps1
./scripts/start_shine.ps1 -Install
./scripts/check_health.ps1
```

`start_shine.ps1` performs the Alembic migration and opens separate API, worker, and Web windows. Use `-Provider comfyui` only after local H3 acceptance. Open `http://127.0.0.1:5173`; API health is `http://127.0.0.1:8000/health`, and system/worker status is `http://127.0.0.1:8000/api/v1/system/status`.

To verify the whole local stack without touching an existing project database:

```powershell
./scripts/verify_local_stack.ps1
```

It uses a throwaway data directory, starts the API and the worker in mock mode, creates a project, freezes a Style Bible, runs one mock job to `PASSED` and prints the audit trail.

Manual commands (Linux/macOS or separate terminals):

```bash
uv sync --project backend --locked
npm --prefix frontend ci
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn shine.main:app --host 127.0.0.1 --port 8000
uv run --project backend python -m shine.worker
npm --prefix frontend run dev -- --host 127.0.0.1
```

First workflow: create a project → upload your authorized assets with role/entity metadata → approve and lock each asset → fill and freeze all seven Style Bible dimensions → create a shot with independent bindings → preview the compiled H3 prompt → add a Mock job. The worker records durable attempts and mock QC. `PASSED` for a Mock job means only that scheduling and mock QC passed; `video_created=false` and `artifact_kind="none"` remain visible.

`SHINE_DATA_DIR` controls the database, assets, artifacts, and secret files. See [`config/environment.md`](config/environment.md).

## Tests and checks

```powershell
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
npm --prefix frontend run build
```

The suite covers style-version gates, binding isolation, concurrent revision, two-worker claims, crash recovery, uncertain submission, project audit, worker health, LLM retry/schema/auth classification, Agent output rejection, H3 label/section validation, Fake ComfyUI lifecycle, continuity, technical QC fallback, semantic fusion, repair budgets, path traversal, disk-full handling, and Alembic fresh/M1 upgrades.

The V1.1 offline suite has **200 tests** (174 existing + 26 film-crew/rulebook cases). It includes the Fake ComfyUI crew collaboration/repair/EDL loop. `imageio-ffmpeg` is a development-only dependency for synthetic test media, never real H3 generation. See the V1.1 implementation report for exact validation results and scope.

## V1.1 professional film crew

After migrating, open **影视制片团队** and **H3 官方规范中心**. Configure each role against an existing provider profile (all roles may share one), then explicitly freeze the project rule pack. Request a style suggestion or freeze your Style Bible, run the scriptwriter, select its artifact for the director, then select the director artifact for the cinematographer. The continuity supervisor reviews both plans. A user may then apply the camera plan, which performs deterministic preflight and creates shots in the existing production system. Editor EDL proposals only use eligible current artifacts; mock/unverified sources require an explicitly labelled offline preview.

Descriptions submitted under V1.1 rules must be English; dialogue remains in its original language. Chinese style/camera descriptions are reported as blockers, never silently translated. Official source files are not redistributed: import legally acquired, fixed-revision files through the local cache controls if desired. Missing originals and real-H3 capability status remain visible. No real LLM or H3 GPU acceptance is implied by the offline suite.

## V1.3 planned: ComfyOps + multi-modal pre-production

V1.3 is an **approved architecture and implementation plan, not implemented functionality**. It adds a controlled ComfyOps environment/model/node/workflow management Agent, candidate visual/audio asset production and user imports, VoiceOps voice design/TTS/ASR, human promotion of formal assets, and a pre-production readiness DAG. Real ComfyUI/model installation and GPU acceptance remain local-only. The current V1.2.1 master-asset policy remains enforced until the V1.3 candidate/promotion boundary is implemented and tested.

- [V1.3 product and technical blueprint](docs/Shine视频工作台_V1.3_ComfyOps与全资产预制作系统建设方案.md)
- [V1.3 cloud Codex full implementation brief](docs/V1.3_云端Codex实施总提示词.md)

## Documentation
- [V1.1 产品修订方案](docs/V1.1_影视专业Agent与H3官方规范中心_修订方案.md)
- [V1.1 contracts and API](docs/v1.1-contracts.md)
- [V1.1 实现与离线验收报告](docs/V1.1_实现与离线验收报告.md)
- [V1.0 项目建设方案](docs/Shine视频工作台_V1.0_项目建设方案.md)
- [AGENTS.md — 开发规范](AGENTS.md)
- [architecture.md](docs/architecture.md)
- [contracts.md](docs/contracts.md)
- [upstream-audit.md](docs/upstream-audit.md)
- [local-h3-acceptance.md](docs/local-h3-acceptance.md)
- [LOCAL_AGENT_BUILD_REPORT.md](docs/LOCAL_AGENT_BUILD_REPORT.md)
- [OFFLINE_FINAL_ACCEPTANCE_REPORT.md](docs/OFFLINE_FINAL_ACCEPTANCE_REPORT.md)

## Security
The repository must not contain `.env` values, provider keys, private assets, model weights, generated videos, local databases, or logs. Provider keys are stored outside SQLite through Windows DPAPI or a permission-restricted local file and never returned by the API. Remote semantic review requires explicit project consent. Third-party source/license audits and the GPL plugin boundary remain traceable.
