# AGENTS.md — Shine Video Studio

## Project authority
Before editing code, read in order:
1. [V1.0 产品与工程基线](docs/Shine视频工作台_V1.0_项目建设方案.md)
2. [Codex Cloud 首轮开发任务](docs/Codex云端Agent_建仓及首轮开发总提示词.md)
3. [V1.1 影视专业 Agent 与 H3 官方规范中心](docs/V1.1_影视专业Agent与H3官方规范中心_修订方案.md)
4. Existing source, tests, docs, and current Git history.

**Repository already exists:** `anjingdtl/shine-video-studio`, private, default branch `main`. Do not attempt to create another repository or overwrite existing work. Codex is used for development only; it must **not** become a runtime dependency.

## Product invariants
- Local MiniMax-H3 through ComfyUI is the only official video-generation execution path. Mock adapters are for development/testing, never presented as real video.
- Windows local-first Web UI: FastAPI + React/TypeScript + SQLite, with a separately restartable worker.
- Independent Agent Runtime; configurable LLM/VLM API providers and capability detection.
- User-uploaded and user-approved character, location, prop and voice assets only. Agents must not silently create, replace, approve or mutate master assets. Technical derivatives are permitted with provenance, but cannot be promoted automatically.
- A user-approved immutable Style Bible must be frozen before production. All shots, renders and repairs must reference its version/hash.
- One authoritative project/asset database, one task orchestrator, one H3 prompt compiler, one audit trail.
- Job attempts are separate from logical shots; persist idempotency and ComfyUI task IDs, retry budgets, dependency invalidation, and crash reconciliation. Uncertain submissions require reconciliation, never blind re-submission.
- QC distinguishes deterministic errors and probabilistic VLM/ASR judgments. Hard errors never auto-pass; low-confidence issues require review, and independent shots should continue.
- Never copy third-party GPL sources into the main project without legal review. Myang remains an optional separately installed ComfyUI plugin; verify all upstream licenses and exact revisions.
- Never commit model weights, generated videos, personal source material, secrets or API keys. Default backend binding is localhost; cloud transmission of asset bytes requires explicit user permission.

## First development milestone
Deliver actual **M0 + M1**, not design-only outputs. Prioritize working FastAPI APIs, React screens wired to APIs, migrations, asset approval/lock, frozen style versions, persistent job state machine, mock renderer, worker recovery and adversarial tests. Create upstream audit and local-H3 acceptance instructions. Never claim local GPU/H3 validation from a cloud-only test.

## M2/M3 offline additions (current branch)

- `H3PromptCompiler` is the only prompt output path. Do not submit free-form agent text directly to H3. Keep the pinned `h3-prompt-writing` rule version/hashes and official section order/label families.
- ComfyUI jobs require a registered API workflow profile with an explicit `parameter_map`. Never fabricate a "validated" H3 workflow or silently change generation mode/checkpoint. Unmapped or missing workflow JSON is a documented blocker, not a reason to invent one.
- Every reference in a request owns a deterministic logical slot. A slot without a mapping, or a video/audio family without a declared `media_transfers` adapter, blocks the render with `PENDING_WORKFLOW_MAPPING`. **Never drop a reference silently.**
- The LLM gateway must keep provider keys outside SQLite/Git/logs/frontend responses. Role routing is explicit; do not silently fall back to another provider. Remote VLM/ASR requires project consent and derived evidence only.
- Per-shot bindings are authoritative. A job may not substitute a different asset set. Request-local H3 labels are remapped deterministically; global asset IDs are never label numbers.
- Worker writes require status, `state_version`, lease owner, and shot-revision checks. Expired/ambiguous external submissions are reconciled; never blind-resubmit.
- The submit intent (including the prompt-graph hash) is persisted **before** the external call, and the external prompt id immediately after it. An uncertain submission is `NEEDS_REVIEW`, never an automatic retry.
- Deterministic technical QC and probabilistic semantic QC are separate. Missing FFprobe/FFmpeg yields `unavailable`, not `pass`. Probing metadata is not a decode.
- Five named quality gates (`technical`, `semantic`, `audio`, `style`, `final`) are recorded per attempt. `PASSED` means the production attempt completed under the project policy; it is not a claim that every advisory model review passed. `acceptance_status` is `accepted` only when the latest final gate really passed. A pending final gate is recorded as `pending_review`, never relabelled as accepted.
- Advisory model reviews (`advisory_model_reviews`, always true) never sit in the managed render critical path: a reviewer that is missing, incompatible, slow or failing is recorded as `not_run`/`needs_review` advisory evidence and must not block a shot, series or delivery. Reviewer probes must not load weights or claim READY without an import check; a setup-owned reviewer route whose executor cannot import is retired, never used by the worker.
- Repairs are typed, bounded, and must protect passed versions. An automatic repair must produce a **different** request revision (recompiled prompt, rebuilt manifest, or changed parameters). Do not claim unattended full-drama completion or real H3 acceptance from offline tests.
- Upstream re-renders invalidate their dependants so downstream validity is recomputed; passed shot versions and their artifacts are never overwritten.
- The ComfyUI renderer URL must be loopback unless `SHINE_ALLOW_REMOTE_COMFYUI=1` is set explicitly, because reference bytes are uploaded to it.
- Keep `/api/v1/system/status` distinctions (API, Worker, Mock, real H3) honest. Keep the API loopback-only until authentication exists.
- The offline production loop is covered by `backend/tests/test_offline_e2e.py`; run it after any change to the worker, the gates or the repair engine.

## Production series (real multi-shot H3 production)

- A **production series** is the durable unit of a real H3 run. It owns an ordered list of shots, its own claim, its own heartbeat and its own state. The resident Worker claims exactly one series at a time.
- The first shot of a series owns a fresh managed startup; every following shot continues the **same** fenced renderer through the idle keeper's lease transfer. The exclusive GPU lease is therefore held across the whole run and released **exactly once**, at the end. Two serial H3 model stages must never overlap.
- A dependent shot is only created after its source job really **passed**, is not stale and still matches the current shot revision. A continuity relation always points at a real job; it is never inferred.
- The lease release and the series state transition commit in **one** transaction. A release that already committed is idempotent, not a second release. A renderer that cannot be stopped keeps its lease for reconciliation.
- A worker that stops renewing leaves a **claimable** series, not a locked one, and the run resumes at its first unfinished shot. An uncertain external submission is never resubmitted by the recovery path.
- The ordinary job queue keeps its own semantics and is driven behind the same managed renderer, so a single queued shot no longer holds the lease forever.
- The series must be reachable from the Web workbench, not only from a test harness. A run that a script drives by hand is not an unattended production acceptance.

## Working method

V1.1 keeps professional Agent outputs as typed, versioned proposals on the existing Gateway
and database. Only user actions may apply style/camera suggestions. Rule-pack locks are
immutable per project; unsupported source/rule changes require code review and tests.
Never bundle official full-text guides without redistribution permission. Run
`backend/tests/test_film_crew.py` alongside the offline E2E after crew/rulebook changes.

1. Inspect current repo and upstream implementations/commit IDs before reuse; write `docs/upstream-audit.md`.
2. Keep feature changes small, testable and recoverable. Do not alter locked product decisions without user confirmation.
3. Run backend tests and frontend build/lint where possible. Report exact commands and failures, including unavailable external services.
4. Use feature branches/PRs for substantial changes where available; do not destroy history or silently force-push.
5. Report implemented, mock-verified, pending Windows/H3 hardware validation, and blocked items separately.

## Distribution package (tester hand-off)

- Build releases only from a committed tree: `pwsh -File scripts\build_test_package.ps1 -OutDir <dir> -Verify`.
  The script refuses a dirty working tree, takes files from `git ls-files`, excludes models/databases/secrets/
  `docs/evidence`/virtualenvs, and writes `BUILD_INFO.json` plus a per-file `PACKAGE_MANIFEST.json`.
- One tester entry only: `一键安装并启动.cmd` (ASCII fallback `SETUP.cmd`) → `scripts/install_once.ps1`.
  It must stay idempotent (installed → start, running → open the wizard), pick a non-system drive with
  ≥180 GiB free, and land on `#setup`. Personal desktop setup requires no operator password.
- Encoding is a correctness requirement, not style: `.cmd` entry files stay pure ASCII (cmd.exe uses the
  active code page), and every `scripts/*.ps1` containing non-ASCII text must be UTF-8 **with BOM**
  (Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI and fails to parse). `test_package_entrypoints.py`
  enforces both.
- Never package weights, generated media, SQLite files, `.env`, `operator-credentials.txt` or acceptance
  evidence. Testers get the installer, not the data.

## V1.3 amendment (supersedes V1.0's source-only restriction)

- AI may generate `CandidateAsset` rows. Only explicit local-user grants may promote new
  `Asset` masters or freeze Voice Profiles. Do not use legacy approve/lock to promote candidates.
- Retain `offline_only` on Fake masters. Never turn Fake workflow/model/voice/QC evidence into
  real verification. `SHINE_OFFLINE_SIMULATION` is for isolated test data only.
- An analyzed project must pass `comfyops.preproduction.enforce` at both Job creation and Worker
  submission. Never disable it to make a test pass. Existing H3/Style/Rule/continuity gates remain.
- Preparation and GPU leases use the same SQLite database. Expired resources remain blocked
  until reconciled; a clock timeout does not prove models were unloaded.
- Run `test_v13_preparation.py`, `test_v13_migrations.py`, `test_v13_e2e.py`, the old offline E2E,
  film crew, complete backend suite, Ruff, Alembic and TypeScript/Vite. Preserve Windows CI.
- See `docs/V1.3_CLOUD_IMPLEMENTATION_REPORT.md` for evidence and explicit local boundaries.

## Personal desktop simplification (2026-11-01, user-authorized)

- No password or manually typed rights evidence is required for uploaded-master confirmation.
  Same-origin loopback UI actions use a CSRF marker, with the actor recorded as `local-user`.
  This is a personal-machine trust boundary, not authenticated proof of human identity.
- Keep action/snapshot-bound one-use grants, file integrity checks, candidate promotion gates,
  style freezing, cloud consent, and uncertain-submission reconciliation.
- One explicit download confirmation may cover the displayed pinned deployment and its ordered
  instance/configuration steps. Stop on failure or changed page/project; never blind-retry.
- Normal installers do not generate or request an operator password. Preserve existing secrets
  and explicit legacy provisioning callers without rewriting their files.
