# Shine视频工作台 · Shine Video Studio

> Local-first autonomous short-drama production workbench built around **MiniMax-H3**, with a configurable independent LLM Agent runtime.

**Current status: M0/M1 local development foundation implemented.** The backend, SQLite migration, React workbench, asset/style gates, persistent mock scheduler, and independent mock worker run without a GPU. The mock path never creates or presents a video. Real ComfyUI/MiniMax-H3 rendering, an LLM gateway, semantic QC, and Windows GPU acceptance remain unimplemented or pending local validation.

## Product boundaries
- **Video model:** locally deployed MiniMax-H3 via ComfyUI; no silent substitution with online video services or other models.
- **Deployment:** Windows local Web application; backend FastAPI, frontend React/TypeScript, persistent SQLite jobs and independent worker.
- **LLM/VLM:** provider-configurable API routes; Codex is a development assistant only, not a required runtime.
- **Assets:** user-supplied, user-approved master character/scene/prop/voice assets only.
- **Style:** film-wide camera language, lighting/color, editing rhythm and sound rules are reviewed and frozen by the user **before** unattended production.
- **Production:** shot planning, reference mapping, H3 prompt compilation, execution, technical/semantic QC, bounded per-shot repair and edit/delivery. All work auditable and recoverable.

## Authoritative project documents
- [V1.0 项目建设方案](docs/Shine视频工作台_V1.0_项目建设方案.md)
- [Codex 云端 Agent 首轮开发总提示词](docs/Codex云端Agent_建仓及首轮开发总提示词.md)
- [AGENTS.md — 开发规范](AGENTS.md)

## How to start Codex Cloud development
1. In Codex, select the existing GitHub repository `anjingdtl/shine-video-studio` and its `main` branch, or open an authorized feature branch.
2. Ask Codex to read `AGENTS.md` and both documents in `docs/`.
3. **Skip repository creation:** this private repo already exists. Start with upstream source/license audit and M0/M1 implementation.
4. Commit tested code and report actual commit/PR URL, test results, and tasks awaiting real Windows + GPU + H3 acceptance.

Suggested opening instruction:

```text
请在当前已连接的 anjingdtl/shine-video-studio 私有仓库中，先完整阅读 AGENTS.md 和 docs/ 下的两份 V1.0 建设文件。跳过建仓步骤；从上游源码与许可证审计开始，按方案实际开发并测试 M0 + M1。保持唯一状态库/任务调度器、人工资产锁定、Style Bible 冻结和本地 H3 唯一视频引擎等强约束；云端无 GPU 的环节只使用 Mock 测试并标注待本机验收。完成后提交代码，提供真实测试结果及下一阶段清单。
```

## Run locally

Requirements: Python 3.12, Astral `uv`, Node.js 20 or newer, and npm. FFmpeg is optional for the current M0/M1 workflow and required by later media validation and delivery work. No API key, GPU, ComfyUI, or Codex runtime dependency is needed for the mock workflow.

On Windows, open PowerShell in the repository and run:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
./scripts/check_environment.ps1
./scripts/start_shine.ps1 -Install
```

The script installs frozen Python/npm dependencies, applies the Alembic migration, and opens separate backend, worker, and frontend windows. Open `http://127.0.0.1:5173`; the backend health endpoint is `http://127.0.0.1:8000/health`.

For Linux/macOS development, run each command in its own terminal:

```bash
uv sync --project backend --locked
npm --prefix frontend ci
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn shine.main:app --host 127.0.0.1 --port 8000
uv run --project backend python -m shine.worker
npm --prefix frontend run dev -- --host 127.0.0.1
```

Create a project, upload your own authorized image/audio/video files, approve and lock each asset, fill all seven style fields, then approve and freeze a Style Bible. Create a shot and add a mock job. The worker updates its durable state and records mock QC; the UI labels this as simulated and reports that no video was created. A `fail` scenario can be retried at most twice; an `uncertain` submission moves to manual review.

`SHINE_DATA_DIR` controls the local database and asset directory; `SHINE_DATABASE_URL` optionally overrides the database URL. See [`config/environment.md`](config/environment.md).

## Implemented and pending

- **Implemented:** FastAPI project/asset/style/shot/job APIs, SQLite WAL and Alembic migration, content-addressed media uploads, explicit human approval/lock, immutable style snapshots, typed ShotSpec/RenderRequest/QC/RepairPlan schemas, persistent dependencies/attempts/leases/audit events, a schema-bounded independent Agent boundary, mock worker recovery and bounded repair, React controls wired to the API, and CI checks.
- **Mock verified:** style and asset gates, request-local reference numbering, ten independent jobs, dependency invalidation, pause/resume, worker recovery, no blind retry after uncertain submission, and two-retry budget.
- **Not implemented:** Real ComfyUI HTTP adapter and reconciliation, official H3 Prompt Compiler, LLM/provider profiles, media decode/QC and delivery assembly, user authentication, and real Agent tools.
- **Pending local validation:** Windows startup behavior, local checkpoint/node compatibility, GPU memory/performance, T2VA and Ref2VA outputs, and real ComfyUI crash reconciliation. Follow [`docs/local-h3-acceptance.md`](docs/local-h3-acceptance.md).

Run backend tests and lint with `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests` and `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests`. Build the UI with `npm --prefix frontend run build`.

See [`docs/upstream-audit.md`](docs/upstream-audit.md), [`docs/architecture.md`](docs/architecture.md), and [`docs/contracts.md`](docs/contracts.md) for source/license boundaries, state ownership, and API contracts.

## Security
The repo must not store `.env` values, provider API keys, private assets, checkpoint weights, output videos, local SQLite databases, or logs. Keep third-party repository audits and licenses traceable.
