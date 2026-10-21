# AGENTS.md — Shine Video Studio

## Project authority
Before editing code, read in order:
1. [V1.0 产品与工程基线](docs/Shine视频工作台_V1.0_项目建设方案.md)
2. [Codex Cloud 首轮开发任务](docs/Codex云端Agent_建仓及首轮开发总提示词.md)
3. Existing source, tests, docs, and current Git history.

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
- The LLM gateway must keep provider keys outside SQLite/Git/logs/frontend responses. Role routing is explicit; do not silently fall back to another provider. Remote VLM/ASR requires project consent and derived evidence only.
- Per-shot bindings are authoritative. A job may not substitute a different asset set. Request-local H3 labels are remapped deterministically; global asset IDs are never label numbers.
- Worker writes require status, `state_version`, lease owner, and shot-revision checks. Expired/ambiguous external submissions are reconciled; never blind-resubmit.
- Deterministic technical QC and probabilistic semantic QC are separate. Missing FFprobe/FFmpeg yields `unavailable`, not `pass`. Low-confidence/uncertain findings require review. Mock success never means real video quality.
- Repairs are typed, bounded, and must protect passed versions. Do not claim unattended full-drama completion or real H3 acceptance from offline tests.
- Keep `/api/v1/system/status` distinctions (API, Worker, Mock, real H3) honest. Keep the API loopback-only until authentication exists.

## Working method
1. Inspect current repo and upstream implementations/commit IDs before reuse; write `docs/upstream-audit.md`.
2. Keep feature changes small, testable and recoverable. Do not alter locked product decisions without user confirmation.
3. Run backend tests and frontend build/lint where possible. Report exact commands and failures, including unavailable external services.
4. Use feature branches/PRs for substantial changes where available; do not destroy history or silently force-push.
5. Report implemented, mock-verified, pending Windows/H3 hardware validation, and blocked items separately.
