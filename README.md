# Shine视频工作台 · Shine Video Studio

> Local-first short-drama production workbench built around **MiniMax-H3 via local ComfyUI**, with an independent, provider-configurable LLM Agent runtime.

**Current status: M1 hardened; M2 and M3 offline foundations implemented and tested.** The backend, SQLite migrations, React workbench, per-shot asset bindings, frozen style versions, concurrency-safe worker, project audit, worker health, independent LLM gateway, Agent planning runtime, official-format H3 prompt compiler, ComfyUI HTTP adapter, Fake ComfyUI server, technical QC, semantic QC protocol, and bounded repair engine run without a GPU. Mock and fake-HTTP evidence never counts as real MiniMax-H3 validation.

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
- **Web workbench:** project status, asset approval/lock/metadata, independent shot bindings, Style Bible freeze/version history, Prompt preview, provider/role settings, ComfyUI test and workflow registration, worker/queue status, QC evidence, repair suggestions, and audit history.

## Explicitly not claimed
- Real MiniMax-H3 rendering, GPU performance, T2VA/Ref2VA visual acceptance, lip-sync, identity consistency, and full long-shot continuation are **PENDING_LOCAL_H3**.
- No real H3 workflow JSON was available. The ComfyUI adapter validates a generic API workflow and requires a project-specific parameter mapping. Do not treat a mock/fake-server result as a real workflow mapping.
- FFprobe/FFmpeg may be absent on the current machine. Technical QC then reports `unavailable`, never `pass`, for decode and silence checks.
- No user authentication. Keep the API on loopback.

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

## Documentation
- [V1.0 项目建设方案](docs/Shine视频工作台_V1.0_项目建设方案.md)
- [AGENTS.md — 开发规范](AGENTS.md)
- [architecture.md](docs/architecture.md)
- [contracts.md](docs/contracts.md)
- [upstream-audit.md](docs/upstream-audit.md)
- [local-h3-acceptance.md](docs/local-h3-acceptance.md)
- [LOCAL_AGENT_BUILD_REPORT.md](docs/LOCAL_AGENT_BUILD_REPORT.md)

## Security
The repository must not contain `.env` values, provider keys, private assets, model weights, generated videos, local databases, or logs. Provider keys are stored outside SQLite through Windows DPAPI or a permission-restricted local file and never returned by the API. Remote semantic review requires explicit project consent. Third-party source/license audits and the GPL plugin boundary remain traceable.
