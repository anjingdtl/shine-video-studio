# Shine视频工作台 · Shine Video Studio

> 安装盘可在设置中选择；首次引导先选择非系统盘，工具、Python 与依赖缓存不再默认落入 C 盘。使用方法和验证边界见 [下载与安装目录](docs/LOCAL_STORAGE_SETTINGS.md)。

> 2026-11-01 P0 收口：新机向导新增真实服务与工作流验证；启动已安装的 ComfyUI、VLM、ASR 读取 live `object_info` 和审片协议后停止，L4 改由密封证据计算，审片路由按实际安装模型（8B/0.6B）自动绑定；同时修复 `TMPDIR` 和磁盘/包数文档。完整 60.8GB 干净机下载与真实 H3 推理仍未执行，见 [修复记录](docs/NEW_MACHINE_DEPLOYMENT_FIX_20261101.md) 和 [验收清单](docs/NEW_MACHINE_ACCEPTANCE_CHECKLIST.md)。

> V1.3 对抗审查的软件修复与收尾实现已补齐，按 **TESTED_OFFLINE / MOCK_VERIFIED** 验收；
> 最终提交 CI、逐项证据及真机边界见 [对抗审查与重验](docs/V1.3_ADVERSARIAL_REVIEW.md)。真实 GPU/视听质量不由 Fake 结果代替。

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
- **Assets:** Agents may create candidates. Masters require authenticated human promotion and locking; Agents cannot approve, replace or mutate them. Fake candidates stay offline-only.
- **Style:** the user freezes a seven-dimension Style Bible before production. New shots bind to the current frozen version; historical shots keep their original version. A new version invalidates unstarted work that used the old style.
- **Production:** structured ShotSpec → deterministic H3 prompt compilation → durable job/attempt state → ComfyUI or explicitly labelled Mock → technical/semantic QC → bounded repair or human review.
- **Multi-shot production:** one production series is the unit of a real H3 run. It owns an ordered list of shots, is claimed by exactly one resident worker, keeps one managed renderer and one exclusive GPU lease across all of its shots, and releases both exactly once at the end.

## What is implemented
- **Shine Assets (standalone workspace):** this feature adds a project-independent asset home, image workshop, sound workshop, global library and project assignments at `#a/home`. Global candidates require technical checks, human review and promotion; project assignment creates a separate project-scoped identity. Qwen edit, inpaint, multi-reference, native alpha, 2K, voice inference and RTX 5080/H3 scheduling remain gated on local evidence. See the [user guide](docs/SHINE_ASSETS_USER_GUIDE_20261108.md), [API/workflow contract](docs/SHINE_ASSETS_API_WORKFLOW_CONTRACT_20261108.md) and [local Qwen validation procedure](docs/SHINE_ASSETS_LOCAL_QWEN_VALIDATION_20261108.md).
- **M1 hardening:** strict style-version gates, frozen-style impact analysis, per-shot binding table and API, project-scoped audit pagination/filters, worker heartbeat table and `/system/status`, worker result-version guards, conditional leases, and crash reconciliation.
- **M2 offline:** OpenAI-compatible/Anthropic/Ollama/Mock LLM adapters, DPAPI (or `0600` file) key storage, role routing, capability probes, script parsing (TXT/Markdown/JSON/DOCX/PDF), deterministic and LLM planners, official `h3-prompt-writing` compiler with pinned rule hashes, ComfyUI HTTP client/adapter, strict workflow profiles, Fake ComfyUI server, and continuity dependency contracts.
- **M3 offline:** structural media probing, optional FFprobe/FFmpeg technical QC, pluggable VLM/ASR reviewer protocol with evidence, confidence-aware fusion, root-cause classification, bounded RepairPlan engine, and passed-job protection.
- **M1/M2/M3 final hardening:** strict per-family ComfyUI reference-media transfer (no silent reference loss, content-type based file extensions, explicit video/audio transport adapters), an intent-first submission protocol that never duplicates an external render, five named quality gates (`technical`, `semantic`, `audio`, `style`, `final`) with a per-project quality policy, automatic repair that produces a genuinely different request revision, automatic collection and re-check of external tasks that already finished, and a repeatable Offline Production E2E.
- **Web workbench:** project status, asset approval/lock/metadata, independent shot bindings, Style Bible freeze/version history, Prompt preview, provider/role settings, ComfyUI test and workflow registration, worker/queue status, QC evidence, per-gate quality ledger, repair suggestions, and audit history.
- **Unattended production series:** the workbench can queue an ordered run of shots for the real renderer. The resident worker claims the series, renders every shot behind one managed renderer, keeps the exclusive GPU lease across the whole run, and releases the renderer and the lease exactly once when the run ends. The series can be paused, resumed and cancelled, and a dependent shot is only created after its source job really passed.

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

### Unattended multi-shot production

A real H3 run is an ordered **production series**. In the workbench: open the
*多镜头无人值守生产* panel, choose a registered Ref2VA workflow profile, tick the
shots in production order and press *开始真实生产*. The resident Worker then
claims the series, starts the managed MiniMax-H3 renderer, renders every shot
behind that one renderer, reviews each shot with the local VLM and ASR, creates
the next dependent shot only after its source really passed, and releases the
renderer and the exclusive GPU lease once the run ends. Closing or reloading the
browser does not stop the run.

To drive the same chain from a script against a local stack:

```powershell
./scripts/run_real_acceptance.ps1 -RunName <run-name> -ComfyRoot <本机 ComfyUI 目录> `
  -Profile local-h3-ref2va-pdd8-2img-1aud -Shots 3
```

It prepares the reviewer routes, starts the normal launcher in ComfyUI mode,
registers the real H3 workflow profiles, drives a real browser through the whole
production chain and writes the evidence to `.local-acceptance/final-local-pass/`.
The scripts need PowerShell 7.

`run_real_acceptance.ps1` / `run_real_closure_v3.ps1` / `run_asr_qualification.ps1` are
**maintainer** tools: they intentionally have no default ComfyUI path, because a package
never knows where a tester keeps one. Pass `-ComfyRoot` / `-ReviewPython` explicitly; a
managed new-machine install keeps ComfyUI under
`<SHINE_DATA_DIR>\comfyops\instances\<id>\ComfyUI`.

## Tests and checks

```powershell
# L1 quick
uv run --project backend python scripts/run_tests.py quick

# L2 integration
uv run --project backend python scripts/run_tests.py integration

# L3 full suite on this machine
uv run --project backend python scripts/run_tests.py full

# Slowest tests and modules
uv run --project backend python scripts/run_tests.py profile

npm --prefix frontend run build
```

Read [the three-tier strategy](docs/testing/THREE_TIER_TEST_STRATEGY.md) for marker rules, diff selection, platform boundaries, and CI triggers. L3 CI runs all backend tests on Linux and Windows and retains the browser E2E job. A local `full` run covers the current OS only; it is not evidence of Windows, GPU, real-model, or browser acceptance. `imageio-ffmpeg` is a development-only dependency for synthetic test media, never real H3 generation.

## V1.1 professional film crew

After migrating, open **影视制片团队** and **H3 官方规范中心**. Configure each role against an existing provider profile (all roles may share one), then explicitly freeze the project rule pack. Request a style suggestion or freeze your Style Bible, run the scriptwriter, select its artifact for the director, then select the director artifact for the cinematographer. The continuity supervisor reviews both plans. A user may then apply the camera plan, which performs deterministic preflight and creates shots in the existing production system. Editor EDL proposals only use eligible current artifacts; mock/unverified sources require an explicitly labelled offline preview.

Descriptions submitted under V1.1 rules must be English; dialogue remains in its original language. Chinese style/camera descriptions are reported as blockers, never silently translated. Official source files are not redistributed: import legally acquired, fixed-revision files through the local cache controls if desired. Missing originals and real-H3 capability status remain visible. No real LLM or H3 GPU acceptance is implied by the offline suite.

## V1.3: controlled ComfyOps + multi-modal pre-production

V1.3 adds isolated instance lifecycle and read-only external backup, pinned offline wheel/node/model installation, in-process Qwen voice and direct Comfy image adapters, structured critical requirements and typed semantic review, per-subcall persistent model tasks and confirmed resource release, six API-backed preparation workspaces, an eighth controlled ComfyOps proposal role, scoped Windows probes, package/dependency plans, authenticated one-use human grants, a tested local downloader/atomic installer, workflow capabilities, candidate import/generation/QC/promotion, frozen Voice Profiles/TTS/ASR/alignment/subtitles, pre-production manifests and shared GPU leases. The cloud end-to-end path uses explicitly Fake image/voice/ComfyUI/GPU capabilities. Real model inference, Windows host integration and audiovisual quality remain local acceptance items. Historical H3 workflows and review contracts are preserved. See the [cloud implementation report](docs/V1.3_CLOUD_IMPLEMENTATION_REPORT.md) and [local checklist](docs/V1.3_LOCAL_INTEGRATION_CHECKLIST.md) for exact evidence and limitations.

- [V1.3 product and technical blueprint](docs/Shine视频工作台_V1.3_ComfyOps与全资产预制作系统建设方案.md)
- [V1.3 cloud Codex full implementation brief](docs/V1.3_云端Codex实施总提示词.md)

## Documentation
- [2026-10-25 本机最终收尾补充报告（含仍未关闭的产品验收）](docs/LOCAL_FINAL_CLOSEOUT_20261025.md)
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

## 新机部署（第一套正式支持的配置）

**支持范围**：Windows 10/11 x64 · NVIDIA ≥15.3GiB 显存（RTX 5080 16GB 级）· ≥30GiB RAM · ≥180GiB 空闲磁盘。
完整报告与边界见 [docs/NEW_MACHINE_DEPLOYMENT.md](docs/NEW_MACHINE_DEPLOYMENT.md)。

**分发包入口（发给测试用户时用）**：双击包内的 `一键安装并启动.cmd`（文件名乱码时用 `SETUP.cmd`）。
它调用 `scripts/install_once.ps1`，自动挑选剩余空间最大的非系统盘，
按固定版本装工具链与依赖、启动 API/Worker/Web，个人本机使用无需操作员密码，
并直接打开 `http://127.0.0.1:<端口>/#setup` 进入下方的新机设置向导。重复双击是安全的：已安装就只启动，
已在运行就直接开向导。`START_APP.cmd` 重开、`STOP_APP.cmd` 停止、`CHECK_ENVIRONMENT.cmd` 只读体检。
分发包由 `scripts/build_test_package.ps1 -OutDir <目录> -Verify` 从某个已提交的树重建，
产出 zip + SHA-256 + 逐文件清单，不包含模型、数据库、密钥、证据与虚拟环境。

**唯一推荐入口（在仓库里直接装，系统自带 PowerShell 即可，无需预装任何工具）**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
```

它按固定版本+SHA-256 安装缺失的 uv / Node 20 / PowerShell 7 / FFmpeg（用户级，不改系统），
之后 `./scripts/start_shine.ps1 -Install` 照常启动。然后打开 `http://127.0.0.1:5173` 的
**新机设置**面板：点击「检测本机环境」，检查目录与空间，再确认「下载缺项并自动配置」。
系统会依次下载校验、准备隔离实例、登记工作流、生成配置、验证服务并激活；详细维护入口在高级详情中。
检测本身不下载、不生成视频。基础工具缺失时重新运行一键安装入口补齐；硬件不足需先处理。
素材只需上传后点击「确认使用」，不再手填密码或核验依据；角色名称和标签按需填写。
本机免密仍保留来源与跨站检查、版本绑定的单次授权、文件校验与不确定状态对账。
流程、测试和验收边界见 [个人部署简化说明](docs/PERSONAL_LOCAL_SIMPLIFICATION_20261101.md)。

**自动安装内容**：Shine 自身依赖（uv sync/npm ci）、隔离 ComfyUI 实例（固定 commit b0b74356）、
154 个哈希锁定的 Windows 发行包、5 个节点包、H3 Ref2VA INT8 模型栈（UNet/文本编码器/双 VAE/LoRA/PDD），
以及本地 VLM/ASR 审片模型（Qwen3-VL-8B-FP8 / Qwen3-ASR-0.6B）。清单明细见
`config/deployment/h3-ref2va-windows-x64.json`（14 组件；完整目录 60.8GB，默认生产组件 48.3GB，可选审片组件另计 12.5GB）。

**必须的人工作业**：每张安装/实例计划的一次性批准；LLM provider 凭据录入（DPAPI/0600 本机存储）；
主资产上传与人工晋升；音色/风格冻结；审片 needs_review 的人工裁决与异常对账。
系统不代替用户做资产批准、风格冻结或最终艺术判断。

**恢复/卸载**：所有部署物都在 `SHINE_DATA_DIR` 下（`comfyops/isolated-packages`、`comfyops/instances`、`setup/`）；
删除这些目录 + `backend/.venv` + `frontend/node_modules` + `%LOCALAPPDATA%\Shine` 即恢复原状，
已有外部 ComfyUI 实例从不被触碰（既有保护语义不变）。

**真实验证范围**：本轮为 IMPLEMENTED / TESTED_OFFLINE（含真实小文件下载算法验证与 37MB 归档实测哈希）。
真实全量下载（60.8GB）、干净 Windows 安装、真实 ComfyUI 启动与真实 H3 推理均为 NOT_RUN，需用户授权后执行。
