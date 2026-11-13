# AGENTS.md — Shine Video Studio

## Project authority
Before editing code, read in order:
1. [V1.0 产品与工程基线](docs/Shine视频工作台_V1.0_项目建设方案.md)
2. [Codex Cloud 首轮开发任务](docs/Codex云端Agent_建仓及首轮开发总提示词.md)
3. [V1.1 影视专业 Agent 与 H3 官方规范中心](docs/V1.1_影视专业Agent与H3官方规范中心_修订方案.md)
4. Existing source, tests, docs, and current Git history.

**Repository already exists:** `anjingdtl/shine-video-studio`, **public**, default branch `main`. Do not attempt to create another repository or overwrite existing work. Codex is used for development only; it must **not** become a runtime dependency.

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

### 全量回归执行方式（固定规则：后端 8 分片并行）

后端全量 `pytest backend/tests -q` 单进程串行要 1.5 小时量级，**不允许**这样跑；本项目
的全量回归一律按测试文件均分成 **8 个分片并行**执行，单次约 10 分钟。

```bash
cd <worktree>
ls backend/tests/test_*.py > /tmp/all-tests.txt
split -n r/8 /tmp/all-tests.txt /tmp/chunk-          # 按文件均分，不按用例切
for f in /tmp/chunk-*; do
  backend/.venv/Scripts/python.exe -m pytest $(tr '\n' ' ' < "$f") -q -p no:cacheprovider \
    > "$f.log" 2>&1 &
done
wait
grep -H "^FAILED\|^ERROR" /tmp/chunk-*.log          # 无输出才算全绿
```

判定口径：

1. **8 个分片全部退出码 0，且日志里没有 `FAILED` / `ERROR` 行**才叫通过。
2. 本仓库 `conftest.py` 把 `[shine-module]` 逐模块摘要打在终端摘要位置，所以看不到
   "N passed" 汇总行；不要用汇总行判定，也不要因为它缺失就认为没跑。
3. 分片必须**按文件均分**（`split -n r/8`）。按用例切会让同一模块的 fixture 在多个进程
   里重复初始化，出现假失败。
4. 分片日志要留存（`.local-data/` 或 `/tmp`），报告里写清命令、分片数与各分片结果。
5. 前端全量仍是 `npx vitest run --no-file-parallelism`（tinypool worker 在本机会崩，
   与代码无关），这一条不受后端 8 分片影响。
6. **必须在命令前加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`**。执行环境自带的 safe-delete 垫片按
   "轮"统计删除次数，累计 50 次后开始拦截**每一次** `unlink`；pytest 清理 tmp 目录正好触发，
   表现为 `test_v13_instances` / `test_v13_preparation` / `test_security_windows` 等模块
   大片与代码无关的 `FAILED`/`ERROR`（实测守卫触发次数与失败数强相关，单独跑同一模块则全通过）。
   加这个变量是让 pytest 自己删临时文件，不是放宽被测代码的门禁；**不要**因此改测试或跳过模块。

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 bash -c 'for f in /tmp/chunk-*; do
  (backend/.venv/Scripts/python.exe -m pytest $(tr "\n" " " < "$f") -q -p no:cacheprovider \
    > "$f.log" 2>&1; echo "$f exit=$?" >> /tmp/shards-exit.txt) & done; wait'
```

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

## 2026-11-03 late supplement (real-production recovery)

- **Do not hold the SQLite writer transaction across an external ComfyUI submit.** The claim,
  preproduction and activation guards commit first; the upload/`/prompt` call runs outside the
  transaction. A managed heartbeat or series heartbeat that sees `database is locked/busy`
  defers to its next beat instead of declaring the lease lost.
- A series whose shots all passed but whose ownership proof was lost is reconciled, not left
  blocked. If the GPU lease is still `RECONCILE_REQUIRED/ACTIVE`, only a renderer proven by
  PID/birth/named Job Object may be stopped, and the lease release plus `COMPLETED/RELEASED`
  commit in one transaction. If the lease is already released, the series outcome is merged
  without touching any renderer. Never resubmit a shot for this path.
- An activation journal with `last_confirmed_stage=CONFIRMED` may reconcile a completed
  external prompt only while the recorded renderer is still the same live, owned process.
  Otherwise it remains a blocker. Operator abandon must also check startup/activation
  renderer ownership before releasing the lease.
- Guided advisory reviews must use `TrackedProvider` (which rebuilds `VerifiedInputEvidence`
  from the database pin). Never pass a raw `{artifact_id, sha256}` pin to the reviewer;
  a guided review that fails for this reason is an infrastructure defect, not a model verdict.
- `client_review_identity` pins `worker.py`, `preproduction.py`, the handoff modules and the
  quality client. Any change to those files invalidates an existing VLM revision by design:
  rerun real positive/negative calibration calls, publish a new VLM revision, deprecate the
  old one, analyze a new preproduction plan revision and re-freeze it. Old evidence is never
  reused.
- The formal project's frozen look was corrected after the first real render exposed a
  daylight-versus-rainy-night conflict: Style V2 `7c3ed722-7bd0-4a52-96e1-2e2c4c0a7624`,
  shot revision 4, frozen plan `3187c16d-3b80-4ea8-a3d7-f6047d2873eb`. Style V1 and the
  earlier renders remain as history; they are not rewritten.
- Current pending blocker (see `docs/PROGRESS_20261103_CONTINUATION.md`): shot `f0173fbe…`
  has a persisted artifact `469ee15a…` but the worker process was lost before QC. The GPU
  lease `96971172…` is `ACTIVE` and expired while the renderer is already gone. Recovery must
  prefer QC from the persisted artifact (artifact SHA + attempt output SHA + prompt graph
  hash) and must not blind-resubmit. The corrected current shot is **not yet accepted**.
  (Resolved in the 2026-11-03 closing round — see the supplement below.)
- After worker/quality changes run `backend/tests/test_offline_e2e.py`,
  `test_comfy_completion_window.py`, `test_production_series.py`, `test_advisory_queue.py`
  and the full backend suite. Run `npm.cmd run build` and `npm.cmd test` in `frontend/`.
  Build the Windows package only from the final committed tree with
  `pwsh -File scripts\build_test_package.ps1`.

## 2026-11-03 closing round (persisted-artifact recovery, dead-renderer lease, spoken language)

- **A persisted render artifact is authoritative when its whole identity chain matches.** Adopt
  an already-persisted artifact only if `artifact.sha256 == attempt.output_sha256`, the attempt
  carries a `prompt_graph_sha256`, the job/attempt/artifact agree on the external prompt id, and
  the artifact's attempt/job/project ids match and it is not a mock. Then re-run technical QC and
  the five gates from the stored bytes, record `reconciliation_state=adopted_persisted_artifact`
  and `resubmitted=false`, and never contact the renderer or resubmit H3. Bytes changed after
  persistence stay rejected (`ARTIFACT_HASH_MISMATCH`). External history is corroboration only.
- **Verify the stored bytes before recording the adoption.** The digest/size check on disk runs
  before `adopted_persisted_artifact`, the reconciliation row and the `RECONCILE_COMPLETE` journal
  are written. A tampered file must leave no adoption evidence behind, otherwise the attempt can
  never be reviewed again.
- A handoff journal already reconciled to `RECONCILE_COMPLETE` is history: the persisted-artifact
  path never rewrites it. The one exception is a render whose bytes are stored and whose gates
  never ran: no conclusion exists yet, so the gates may still continue on the stored bytes.
- **An exclusive GPU lease is a claim on a live process.** Release it only when PID/birth identity,
  the named Job Object (no live member) and a free port all prove the renderer is gone
  (`reconcile_dead_renderer_leases`). An unreadable process identity is uncertainty, never death.
  Otherwise the lease stays `RECONCILE_REQUIRED`. The lease release and the series
  `COMPLETED/RELEASED` transition commit in one transaction; a release that already committed is
  idempotent. A series with a blocked shot is never marked complete, and a completion that was
  merged before its shots were decided is explicitly corrected to `NEEDS_REVIEW`.
- Re-validating already-produced media against the current frozen contract is allowed only when
  the **render bindings** (style, rule pack, master assets, models, H3 workflow) are unchanged;
  a changed render binding is refused. Advisory-review capability revisions — including the
  models they run on — are not render bindings and may be re-bound explicitly through the
  operator action.
- **The H3 prompt must state the spoken-language contract explicitly.** With dialogue:
  `All spoken dialogue must be spoken in {project language}, matching the original line text
  exactly`. Without dialogue: `do not produce speech, voice-over or vocal sounds of any kind`.
  The language is written in its canonical English name: the official rule pack rejects CJK
  outside the dialogue tags, so a project setting written in another script is normalised
  (`shine/h3/spoken_language.py`) or refused with 422 at the API boundary — never copied into the
  prompt. The project `language` setting is the single source of truth and is exposed in the
  director UI; already-produced shots are never rewritten. Verifying the model's actual accent
  still requires a real re-render with spoken lines.
- After worker/quality changes in this area, rerun the real VLM positive/negative calibration,
  publish a new revision, deprecate the old one, re-analyze and re-freeze the plan. Current
  approved VLM revision: `03f32432-4c94-4476-8580-e1a14e9fc37f`; frozen plan
  `7832906a-d383-4eaa-a94d-0e0b510769d2`; the formal shot `f0173fbe…` is `PASSED` with
  `acceptance_status=accepted`, its series `a6b97b5e…` is `COMPLETED/RELEASED`, lease
  `96971172…` is `RELEASED`, and the delivery `ac7f0298…` is `DELIVERED`.
- After worker/quality changes run `backend/tests/test_offline_e2e.py`,
  `test_comfy_completion_window.py`, `test_production_series.py`, `test_advisory_queue.py`,
  `test_comfy_handoff_journal.py`, `test_v13_preparation.py`, `test_h3_compiler.py` and the full
  backend suite. Run `npm.cmd run build` and `npm.cmd test` in `frontend/`. Build the Windows
  package only from the final committed tree with `pwsh -File scripts\build_test_package.ps1`.

## 2026-11-04 renderer lifecycle round (ComfyUI belongs to the workbench)

- **ComfyUI and the local reviewers are started by this installation, so they belong to
  the workbench, not to the machine.** A renderer that outlives the stack keeps the model
  resident in GPU memory and blocks the next run. The launcher therefore releases leftover
  backends before a clean start (`scripts/release_residual_backends.py`, called by
  `scripts/start_shine.ps1`), and the workbench has one action that closes them:
  `POST /api/v1/setup/managed-processes/release` plus the「关闭本机后台并释放显存」button
  in the setup panel.
- **Ownership is proven twice or the process is not touched.** A managed renderer or reviewer
  is stopped only when its recorded PID/birth identity and the membership of its named
  `Local\ShineHandoff-<uuid>` Job Object agree (`shine/comfyops/renderer_release.py`). A
  renderer that serves a running attempt is never stopped; a process whose ownership cannot
  be proven is reported as `UNVERIFIED` and left alone. A leftover reviewer that no process
  record names any more is owned when its command line names this installation's own review
  script.
- **The startup record is the only ownership proof; a reconciliation may not drop it.**
  `reconcile_managed_startups` keeps the process identity, port and lease id when it marks an
  interrupted startup `RECONCILE_REQUIRED`. A record that loses that identity can neither be
  released nor matched to its lease, and the renderer becomes unstoppable.
- **The workbench never claims a renderer that is gone.** A managed record whose process is
  provably gone is written back as `STOPPED` and its lease is released as reconciled; the
  `comfy_instances` row follows. Stale `RUNNING_PENDING_HEALTH` rows are history, not a
  running renderer.
- Evidence: `docs/ACCEPTANCE_20261104_RENDERER_LIFECYCLE.md`,
  `backend/tests/test_managed_process_release.py`.

## 2026-11-04 crew prompt round (director chain: EDITORIAL_TIMING_CONFLICT and LLM_FORMAT)

- **The five crew prompts live in `shine/agents/crew_prompts.py`.** They state three things
  only: the role's authority, the official H3 constraints, and the deterministic numbers the
  server checks the answer against. Never let a prompt sentence contradict a server check:
  the 2026-11-04 director failures (`5cb3a8fc`/`170afa21` → `EDITORIAL_TIMING_CONFLICT`,
  `b6f2bda9`/`31ead07a` → `LLM_FORMAT`) came from a prompt that invited regrouping and a
  "natural tail" while the server required the cut table exactly.
- **The segmentation is computed, not negotiated.** `editorial_timing_plan`
  (`shine/agents/editorial_timing.py`) is the finished answer and is served to the crew in the
  context; the director prompt writes out every `duration_s` and every cut `start_s` it must
  copy. A role must never recompute a boundary the server already fixed.
- **A request no answer can satisfy is refused before the model call.**
  `EDITORIAL_TIMING_UNGROUPABLE` (the cut table cannot be grouped into 4–15 s segments) and
  `EDITORIAL_BEAT_COUNT_MISMATCH` (the scriptwriter's beat count cannot match the table) are
  server-side facts; both are recorded in `trace.editorial_refusal` and returned as
  `rejection_reason_code`. Never ask a model to solve them.
- **The grouping search falls back.** The three-segment search stays the primary answer; when
  it has no solution, a fallback minimises the longest segment and prefers more segments. The
  pinned 8-shot grouping (4.3 / 5.1 / 5.6 s) must not change.
- **A refusal must name its reason.** The chain returns `rejection_reason_code` for every
  rejection and the workbench shows it. `LLM_FORMAT` keeps `provider_schema_path` and
  `provider_schema_validator` (the field that broke), never provider text.
- **The official standard is the rule pack, not a style.** Every crew prompt carries the
  Ref2VA section order (`subject_definitions`, `summary`, `retention_analysis`,
  `detailed_description`, `overall_soundscape`, `non_diegetic_music`) and the rule ids
  `H3_ENGLISH` / `H3_TIMING` / `H3_DIALOGUE` / `H3_LIMITS` / `H3_LABELS`. The crew never
  writes the prompt; `H3PromptCompiler` is the only prompt output path.
- **A beat lists only the characters that appear in it.** Every listed character becomes a
  required subject reference, so repeating the whole cast in every beat fills the reference
  budget that the render needs.
- Evidence: `docs/ACCEPTANCE_20261104_CREW_PROMPT_REPAIR.md`,
  `backend/tests/test_crew_prompt_contract.py`. A real-model rerun of the failing script is
  still outstanding, and a project without a registered workflow profile stays blocked at
  `PENDING_WORKFLOW_MAPPING`.

## 2026-11-03 workbench usability round (naming, step gap, resume)

- **`entity_id` is the only join key** between a script character and an uploaded master
  (`shine/asset_identity.py`). A crew step that needs a character master resolves the name
  through `resolve_character_asset`, which matches `entity_id` (or an exact asset UUID) and
  nothing else. `tags` are never consulted.
- **The order is script first, material second.** The user imports the script; the
  scriptwriter names the characters; the server retrieves the masters and proposes a mapping
  (`POST /projects/{id}/storyboard/character-binding/proposal`); the user confirms every row;
  the confirmed names are written on the masters and the script breakdown is re-keyed into a
  **new** artifact (`.../character-binding/apply`); only then are the masters approved and
  locked. A proposal never approves, locks, creates or replaces a master. An ambiguous or
  duplicated name is refused (`CHARACTER_NAME_ALREADY_IN_USE`), because an ambiguous binding is
  a blocked shot.
- **A proposal is a reading, not a verdict.** Name matches outrank tag and filename matches;
  upload order is offered only as the weakest candidate and is labelled as needing a human.
  The human confirmation is the authority.
- **One crew step at a time, with a real gap.** `SHINE_AGENT_STEP_DELAY_MS` (default 1200ms)
  separates two real model calls. A failed role is retried from itself:
  `POST /projects/{id}/storyboard/generate` accepts `start_role` and `resume_steps`, and the
  server reuses an artifact only when it belongs to the project, carries the role's contract
  kind and is not stale. A rejected response carries `failed_role` and `resume_steps`.
- **Every action the user must take is loud.** A required action uses the same
  `.required-action` / `.must-button` block, and every module carries a short
  `.module-guide` that says what to do and why it is required. A raw error code is never the
  only thing on screen.
- Evidence: `docs/WORKBENCH_USABILITY_ROUND_20261103.md`,
  `backend/tests/test_character_binding.py`.

## 2026-11-04 round (voice masters, project workflow, continuous production)

- **The setup capability owns the local voice runtime and this project's H3 workflow.**
  `shine/voiceops/runtime_config.py` holds the configuration (role → verified package, interpreter,
  overlay); the setup panel writes it and the voice provider reads it, rebuilding the provider when
  the configuration changes. `GET /setup/voice-readiness` states per role whether the package is
  hash-verified, declares the capability and has a runtime. `POST /setup/profiles/validate`
  registers this project's Ref2VA profiles and verifies them on the real renderer.
- **A hash-verified package advances its registry status.** The install reconcile recorded
  `MATCHES_PINNED_FILES` but left the package `UNVERIFIED_ORIGIN` forever; it now advances to
  `FILES_VERIFIED_PENDING_RUNTIME` (files verified ≠ runtime verified) and may re-verify a
  reconciled attempt without network or deletion.
- **Only live work blocks a runtime re-validation.** A `NEEDS_REVIEW` series is blocked and
  unclaimable, so it must not freeze every re-validation of this machine.
- **A name list is split by every consumer.** A chorus line (`"S1, S2, …"`) is split by
  `split_character_names` in the binding rewrite, the director's character check, the `ShotSpec`
  speaker check and the rule pack's dialogue-removal regex — and it stays **one** `<d>` block
  (the rule pack requires one language-tagged block per original line).
- **A profile may offer more reference slots than a request binds.** The submitted graph drops the
  loader of every unused slot (deterministically); otherwise the placeholder file name made
  ComfyUI reject the whole prompt. The physical slot order follows the manifest order, so the
  shot bindings are ordered by family (subjects, pictures, video, audio).
- **A provider injected by the host is the host's to own** and is never replaced by the product.
- **The local H3 node's Autogrow group carries up to nine reference images**; the workbench's own
  profiles used two or three. A project whose shots bind more masters needs its own wider profile,
  built from the real node and validated on the real renderer.
- Evidence: `docs/ACCEPTANCE_20261104_VOICE_AND_REF2VA.md`,
  `backend/tests/test_setup_voice_readiness.py`, `test_setup_workflow_profiles.py`,
  `test_reference_slot_pruning.py`, `test_idle_configuration.py`.


## 2026-11-04 director-mode round (setup, output frame, cast identity, optional voice)

- **The director layer owns its own setup page.** `#d/setup` (topbar「基础配置」and the first-run
  card) renders the same `SetupPanel`, `SettingsPanel` and LLM configuration the expert layer uses;
  a new user can install/download, configure the text model and register a generation profile
  without switching layers. Installation plans and generation profiles stay bound to a project, so
  the page offers an inline "create the first work" step instead of silently creating one.
- **The output frame is a real render parameter, not a review expectation.** A production series
  records `output_spec` (width, height, 24 fps, optional clip length). `width`/`height` reach H3 only
  through an explicit `parameter_map` entry; a profile that does not map them refuses the run with
  `WORKFLOW_SIZE_NOT_MAPPED` instead of rendering at its own size. The canonical profiles now map
  `31.inputs.width`/`31.inputs.height`; an older project copy adopts the two keys through the audited
  standard-parameter migration, and `POST /workflow-profiles/{id}/standard-parameters` adds them only
  when the registered graph itself declares the inputs.
- **Clip length is applied to the shot, or refused by name.** A run-level `clip_seconds` revises the
  selected shots (dialogue must fit, internal cut timestamps must fit) and invalidates their earlier
  versions; the request never carries a duration different from the compiled shot spec.
- **The subject slot order follows the cast order of the shot.** The manifest is family-ordered
  (subjects, pictures, video, audio) and, inside the subject family, ordered by `spec.characters`
  (`order_bindings_for_cast`, applied at storyboard apply and again at render time for older shots).
  The compiler states the cast order and forbids face swapping; every listed character without a
  bound subject reference blocks compilation (`H3_CHARACTER_REFERENCE_MISSING`) instead of rendering
  an invented face. Headless acceptance on the real project confirmed `<Subject n>` now follows the
  shot's character order.
- **A voice master is optional.** H3 speaks a dialogue line natively; the shot reference policy
  reports `voice` as an optional role, the director prompt no longer requires an audio master, and
  the setup page presents fixed timbre as an enhancement (2–15 s reference limit unchanged).
- Evidence: `backend/tests/test_output_spec_and_identity.py`, `test_h3_compiler.py`,
  `test_bindings.py`, `test_storyboard.py`, `test_production_series.py`,
  `docs/ACCEPTANCE_20261104_DIRECTOR_MODE.md`.

## 2026-11-04 导演模式全旅程、一键开拍与语言修复（发版 0.2.0）

- **导演层是状态机驱动的单任务界面**：五步旅程（建作品→备素材→定风格→出分镜→制成片）
  由真实数据判定，页面级步骤条打勾；制作页按「①剧本分镜 ②排产制作 ③进度与结果」
  stage-dock 单任务聚焦，非当前阶段折叠为一行摘要；运行中的分镜链保持挂载不中断。
- **一键开拍（自动适配）是排产区唯一主按钮**：自动选方案 → 按方案 parameter_map 声明的
  参考槽位确定性补齐每镜绑定（无对白镜头不补人声）→ 自动勾选 → 提交；策略选择全部收进
  「高级设置」折叠。制作进行中隐藏「为什么不能开始」清单，状态由进度卡负责（阶段名、
  总进度条、镜头圆点、本镜/总计秒级计时——服务端 UTC 时间戳，解析必须补 Z 后缀）。
- **拍摄质量三档**（catalog 1.2.0）：快速 pdd8 / 高质量原生 20 步 / 超清 pdd8+放大，
  全部来自上游既有标准图；缺档标「未登记」，`POST /setup/profiles` 幂等补齐。
- **无对白镜头的外语呢喃根因是两层的**：①冻结风格「声音表演」维度与禁声合同自相矛盾
  （H3PromptCompiler 现按镜头有无对白裁剪该维度）；②真人音色参考被 H3 当语音线索——
  `validate_declared_transfer_families(dialogue_free=…)` 对无对白镜头豁免 audio family，
  未用音频装载节点由既有 prune_unused_reference_nodes 剪除。历史成片不重写，重新交付对照。
- **孤儿 H3 租约不再死锁 GPU 台账**：worker 崩在「取得租约后、写启动记录前」时，
  `_release_orphan_h3_lease` 以保守三证（job 未真实渲染、无活进程记录、受管端口空闲、
  无 handoff-reservation）释放并写审计；有 attempts/provider_job_id 的租约永远保守。
- **交付单按时间倒序，最新置顶带「最新」徽标，历史折叠**；版本号均为 v1 不代表新旧。
- 安装器 `install_once.ps1` 在首次安装与已安装两条成功路径上都会在桌面创建
  「Shine 视频工作台」快捷方式（指向 START_APP.cmd，幂等覆盖，失败不阻断安装）。
- 证据：`docs/ACCEPTANCE_20261104_DIRECTOR_JOURNEY_ONE_CLICK.md`（三轮真实出片、
  语言根因、质量档、孤儿租约）；新增/更新回归见 test_managed_process_release.py、
  test_h3_compiler.py、test_production_series.py、frontend seriesOrder/managed fixtures。

## 2026-11-04 启动器与发版规范（每次建设/发布必须执行）

本节是**强制流程**，不是建议。任何会改变启动方式、依赖、端口、数据目录或前端产物的
改动，都必须同时完成「开发机根目录一键启动」和「项目安装包」两条验收链。只改代码、
不验证入口的提交视为未完成。

### A. 开发机根目录一键启动（每轮必验）

入口只有两个，二者必须指向同一套脚本：

| 入口 | 作用 | 何时更新 |
| --- | --- | --- |
| `一键启动 Shine 视频工作台.lnk` | 日常双击 | 仓库路径、PowerShell 7 路径、脚本名/参数、图标变化时 |
| `启动 Shine 工作台.cmd` | 快捷方式失效时的便携入口 | 脚本名/参数变化时 |
| `scripts/start_dev.ps1` | 选择端口、收尾旧栈、调用 start_shine | 启动逻辑变化时 |
| `scripts/start_shine.ps1` | 迁移数据库、启动 API/Worker/Web、写进程记录 | 服务启动逻辑变化时 |

**更新快捷方式**：运行 `scripts\update_dev_shortcut.ps1`（重建 lnk 的
Target/Arguments/WorkingDirectory，保留原图标）。不要手写一个指向绝对路径的 lnk 后
就认为完成——脚本参数变了，lnk 也必须重建。

**每轮必须通过的四条验收入口**（至少跑 1、2、4；改端口/记录逻辑时四条全跑）：

1. **干净重启**：先停止旧栈，再双击。API `/health` 返回 200，Web
   `http://127.0.0.1:<web>/` 返回 200，浏览器落在正确页面：已有作品 → `/`
   （导演模式首页）；空库 → `/#d/setup`（导演模式基础配置）。
2. **默认端口被别的进程占用**：保留一个非本栈的监听进程在 8000，双击启动。
   启动器必须识别「记录里的 API 身份与 8000 上的进程不一致」，不能报「工作台已在运行」；
   必须自动换到 8001/5174 一类空闲端口，并让浏览器打开实际端口。
3. **半启动残栈**：只保留 API、杀掉 Web，双击。必须收尾并重启，不能把浏览器送到
   一个已死的 5173。
4. **停止**：`stop_shine.py --data-dir <数据目录>` 退出码 0，`processes.json` 消失，
   端口不再监听；无法核对身份的进程只归档、不盲杀，归档最多保留 5 份。

**启动器硬规则**：

- PowerShell 变量名大小写不敏感：不要写 `$apiPort` 和 `$ApiPort` 两个“不同”变量；
  参数默认值必须先存到 `$defaultApiPort` 再解析。
- 判断“端口空闲”必须先查 `Get-NetTCPConnection -State Listen`，再尝试
  `TcpListener`；只靠绑定会在地址复用时误判。
- “已在运行”必须同时满足：API `/health` 通、Web 页面 200、且 API 监听 PID 与
  `processes.json` 记录的 `listener_pid` 一致。只查 API 会把半死栈当成活着。
- 浏览器只能在 Web 真正返回 200 后打开；只等监听 socket 不够。
- 永不盲杀身份不明的进程；无法核对就归档记录并继续（唯一入口不能被一条坏记录卡死）。

### B. 项目安装包（每次发布必更）

安装包从**已提交的树**构建；已跟踪文件有未提交修改时脚本会拒绝（`BUILD_INFO` 必须
指向真实 commit）。未跟踪的个人文件不会进入包，也不会阻塞构建。

```powershell
scripts\build_test_package.ps1 -OutDir releases -Verify
```

**每次发布前必须同步的文件/字段**：

- `packaging/windows/START_HERE.md`、`TESTER_CHECKLIST.md`：入口、端口、浏览器落点、
  已知限制有变化时更新。
- `packaging/windows/START_APP.cmd`：必须调用 `start_shine.ps1 -OpenBrowser -AutoPorts`，
  保证 8000 被占用时安装包也能启动。
- `scripts/build_test_package.ps1` 里的静态字段：`fixed_materials`（组件数/下载体积/
  安装体积）、`known_blockers`、`closed_in_this_build` 必须与当前事实一致；
  不复制上一版文案。
- `scripts/install_once.ps1`：端口探测必须用 `Get-NetTCPConnection` 先行；
  安装包首次启动与二次双击都必须可用。

**验收**：

- 产出 `releases/ShineVideoStudio-<日期>-<短commit>-win64.zip` 与同名 `.sha256`；
- `-Verify` 必须通过：解压后入口文件齐全、文件数 = 清单 + 1、逐文件 SHA-256 一致；
- 保留上一版 zip，不覆盖历史发布；发布页同时上传 zip 与 sha256；
- 安装包绝不包含：模型权重、生成的视频、SQLite 数据库、`.env`/密钥、
  `node_modules`/虚拟环境、`docs/evidence`、日志。

### C. 提交与发布顺序

1. 后端 `pytest` 相关批次、`ruff check shine tests`；前端 `tsc --noEmit`、
   `vitest run`、`npm run build`。
2. 启动器四条验收（见 A）。
3. 更新 `AGENTS.md` 与 `docs/` 验收记录。
4. `git add` 明确路径（不要 `git add -A`），提交，推送 `main`。
5. 干净树上构建安装包并 `-Verify`，把 zip 与 sha256 放入 `releases/`；
   需要公开时再创建 GitHub Release 并上传两个资产。
6. 任何一条没跑通，就在提交说明/交付说明里写明「未验证」，不得口头声称已发布。


## 2026-11-05 受管出图渲染器与"谁拥有渲染器"（本轮新增）

- **出图渲染器属于工作台，不得由脚本手工拉起**。出图运行时只能绑定**受管实例**
  （`ComfyInstance.payload.managed` 为真，`POST /setup/image-runtime` 会拒绝非受管实例）。
  渲染器由工作台自己启动：`backend/shine/comfyops/image_renderer.py`，
  启动意图先落库、所有权由 PID/birth/命名 Job Object 三证、队列先关闭、
  适配器确认身份后才放行、显存与内存按实测准入，记录写在 `renderer-startup:` 前缀下，
  因此既有的「关闭本机后台并释放显存」路径能关掉它并回收显存。
  任何脚本或手工拉起的 ComfyUI 都会成为工作台无法证明归属、因而无法释放的孤儿渲染器。
- **谁拥有渲染器，决定证据的等级**。每条出图 attempt 与候选都记录 `renderer_owned`：
  只有**工作台自己拥有且本次真的启动**的渲染器产出的样本，才能把该类别提升到第四层
  （`RENDER_VERIFIED`）；外部或替身渲染器的字节只能证明链路可用，
  晋升出来的素材一律带 `offline_only`，永远不能算作已验证的生产素材。
  台账推导就绪度，不看任何状态标志。
- **等待显卡不是失败**。图片项先取共享 GPU 租约，再在租约内启动渲染器：
  租约被视频系列占用时按具名原因等待，渲染器不可用时按具名原因拒绝，
  都不把"忙"写成失败。渲染器已经不在时释放动作按"没有模型驻留"确认，
  而不是留下一条假的待对账。
- 浏览器 E2E 闸门（`scripts/verify_v13_web.py`）的实例生命周期段现在**执行**假实例计划：
  只批准不执行就不会产生实例行，下一步生命周期计划就会因"外部实例受保护"而失败。
  弹窗由一个可切换策略的处理函数统一接管（第二个处理函数无法再处理已被处理的弹窗；
  不要再注册第二个弹窗处理函数与它竞争同一个弹窗）。

## 2026-11-05 真实三类小样出图轮（本轮新增）

- **检测先于收编，探测行不挡收编**。本地模式的只读检测会为同一确定性身份
  （`local:http://127.0.0.1:<port>`）先写一条**未收编、非受管**的证据实例行；
  首次收编（`adopt_existing_installation`）现在**接管**这一行（响应与审计记
  `replaced_probe_row=true`），而不是 409 `INSTANCE_IDENTITY_CHANGED`。
  已收编但快照/根不符、或 prior 行本身是受管行的，仍然拒绝。
- **真实出图验收只认这一条链**：取配置哈希（签发授权前）→ scope → inspect → verify →
  adopt → configuration → image-runtime 绑定 → `/setup/image-runtime/start` 受管启动 →
  `image-readiness` 到第三层 `GRAPH_VERIFIED` → `/setup/image-runtime/validate`
  （需已批准 asset 权利记录）→ Worker 渲染 → 台账第四层 `RENDER_VERIFIED` →
  `/setup/image-runtime/stop` 回收显存。写操作带 `x-shine-local-action: 1`。
  真机证据见 `docs/ACCEPTANCE_20261105_REAL_IMAGE_VALIDATION.md`：
  run `8289c72e…` 三类全 CANDIDATE_READY、`renderer_owned=true`、technical/decode pass、
  GPU 回基线、无孤儿渲染器。
- **语义 QC 如实挂起**。验证小样的 QC 里 `semantic=PENDING_HUMAN_REVIEW` 是正确状态：
  技术质检可自动判定，构图/美术/像不像只能由用户评审；不得把 `READY_FOR_REVIEW`
  的候选写成已被接受。
- **浏览器闸门按当前界面导航**：`#preproduction` 的高级区在 reload 后会重新折叠；
  卡片只显示 8 位 id 前缀（两个 native fixture 同前缀，按显示的 `payload.name` 选卡）；
  crew 面板在 `#v/pro`、文本模型设置在 `#v/setup`、素材断言须在可见的 production 页跑；
  个人桌面路径下风格冻结 actor 是 `local-user`（前端 `api()` 恒带本地动作标记），
  思考深度按中文标签渲染（low → 低（快））。

## 2026-11-05 受托素材轮 → 正式出片（导演模式，本轮新增）

- **登记生成方案必须早于 crew 链**。导演在排参考集之前需要知道本项目
  已登记方案映射了几个图像槽位（`workflow_reference_budget` → 参考预算提示）。
  项目没有登记任何方案时，这个事实是空的，导演就会按剧本需要承诺超过任何方案能带的参考数
  （实测：钟表铺一镜 6 张），提交时才以 `PENDING_WORKFLOW_MAPPING` 拦下。
  顺序是：**登记方案 → 在真实渲染器上校验 → 再跑 crew 链 → 再排产**。
- **绑定更多主素材的项目要自己的宽方案**。真实节点 `MiniMaxH3ReferenceToVideo` 的
  `ref_images` 是 `COMFY_AUTOGROW_V3`，**max=9**（`ref_audios` max=3）。
  宽方案从规范方案派生：在节点 31 上补 `ref_images.ref_image_N` 与对应 `LoadImage` 节点，
  `parameter_map` 增加 `reference_picture_N`；然后**必须在真实渲染器上校验**
  （`POST /api/v1/workflow-profiles/{id}/validate` → `missing_nodes=[]`、`missing_inputs=[]`），
  未校验的方案不算数。宽方案是**项目自己的**方案，不改部署目录、不改标准方案的槽位声明。
  实测：宽方案下 6 张参考（人物 + 5 张道具/场景）真实出现在同一画面里。
- **声明了却没有的媒体族会被拦**。带 `-1aud` 的标准方案声明了 audio 传输族；
  有对白而没有音色主素材的镜头会被 `validate_declared_transfer_families` 判为
  「声明了却没绑定」。H3 原生朗读台词，音色主素材是可选的，因此**没有音色的项目要用
  只声明 image 传输族的方案**，而不是给方案补一个假的音频槽。
- **真实出产前先收遗留后台**。上一轮遗留的受管渲染器仍占着 8188 时，
  本轮 Worker 自己的受管渲染器起不来，任务在**提交前**就以 `WORKER_EXCEPTION` 失败。
  收尾用受管动作：`POST /api/v1/setup/managed-processes/release`（归属由命名 Job Object 证明）。
  提交前失败**不是不确定提交**（无 `provider_job_id`、`prompt_graph_sha256` 为空），
  可以 `POST /api/v1/operator/jobs/{id}/abandon` 结案（`resubmitted=false`）后重开序列；
  GPU 租约要等渲染器被证明消失后才由 `reconcile_dead_renderer_leases` 释放。
- **候选评审直接读本地图**。外部视觉工具对部分图 30s 超时且并发必超时；
  候选图下载到本地后逐张读图目检，并把本地文件的 sha256 与服务端登记的
  `content_sha256` 比对（12/12 一致），评审结论与注记写进晋升的 `evidence`。
- **后端测试必须在仓库根运行**。`backend/tests` 里的测试按相对路径解析仓库根脚本
  （如 `scripts/managed_comfy_bootstrap.py`），在 `backend/` 目录里跑 pytest 会让
  `test_image_managed_renderer.py` 误报失败。正确命令：
  `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests -q`（在仓库根）。
- **语义验收门如实挂起**。本机没有可用的 VLM/ASR 审片模型时，三镜都是
  `PASSED` 且 `acceptance_status=pending_review`、`acceptance_final_gate=needs_review`：
  技术质检与解码是真实的，构图/美术/对白只能由人看，最终验收门不得假装通过。

## 2026-11-06 导演模式可用性轮（页面状态、预览网格、素材命名）

- **页面切换不再丢状态**：导演模式的「素材与风格」「AI导演制作」「成片中心」三个页面**保持挂载**，
  只切换 `hidden`，因此已展开的阶段、已选的候选、已打开的预览在来回切换后都还在。
  代价是这些页面的面板必须能承受「数据还没到/字段缺失」：风格草稿可能没有七项，
  环境检测可能还没有 GPU 段——**任何一处 `undefined.trim()` 都会让整棵树崩掉**，
  面板读到缺失字段一律要给出可读的值。
- **`hidden` 必须真的隐藏**。CSS 里曾经只有 `#produce[hidden]{display:none!important}` 这一条作用域规则，
  于是别的页面的 `hidden` 属性形同虚设：DOM 里是隐藏的，屏幕上却照样渲染。
  现在是通用规则 `[hidden]{display:none!important}`，页面的 `display:flex` 之类的声明不得再盖过它。
- **素材阶段要认全服务端的四个阶段**。服务端会返回 `materials_ready`，
  前端只认三个值时，**已经做完的作品每次都显示成「① 读剧本」**。映射补全后，
  完成的作品停在「③ 选择素材」。
- **候选网格是一个密排网格，不是按批次分行**。批次只是台账里的容器，
  人在这里看的是图：一个网格放所有候选（人物/道具/场景），
  图在下、文字作说明，工程细节（批次 id、attempt）折进「批次与技术信息」。
  **还没有出图的条目也要占一格**，否则「出品人要澄清」这件事会从网格里消失。
- **产出的镜头必须能在页面上播放**。分段网格的预览之前指向一个**并不存在**的路由
  （`/api/v1/jobs/{id}/media`），点开永远是空的。现在有两个真实路由：
  `GET /api/v1/jobs/{job_id}/media`（该任务自己的产物，带摘要校验）和
  `GET /api/v1/jobs/{job_id}/thumbnail`（海报帧，供网格当封面）。
  分段卡片的封面是海报，点开才是视频；没有解码器时封面显示「预览不可用」，视频照样能放。
- **素材文件名带上人和顺序**。生成的图按 `{编号}-{人物/物品/场景名}.png` 命名，
  编号按这个实体**在剧本里第一次出现的场次**排（不是台账的字母序），
  这样按文件名排序就等于按剧本顺序，人、道具、场景在剧本与分镜之间对得上。
  规则写在 `shine/materialprep/naming.py`，出图与晋升两处都用它；
  没有对应条目的手工素材保留人自己的文件名。

## 2026-11-06 导演包与整部作品持续生产（本轮新增）

- **导演包是身份快照，不是物料副本**。`director_package_versions`（根）+ `director_package_episodes`（分集）
  只保存已有对象的 id / revision / hash（剧本、分集、镜头、素材、风格、工作流、制片准备、政策）。
  包不能批准素材、冻结风格、应用专业提案；确认只能由用户动作完成。
  根 manifest 只放范围、政策、绑定身份与每集清单 hash，**不得把整部长剧塞进一个 JSON 字段**。
- **`ready` 与 `startable` 是两件事**。`ready` = 内容可满足；`startable` = 已确认且未失效。
  风格重冻、工作流图变更、制片准备变更、镜头 revision 变更必须在就绪检查里变成明确 blocker，
  不能静默沿用旧结论，也不能把“一次成功生产”当成永久资格。
- **运行层不是第二个调度器**。`ProductionRun` 只在 Worker 同一个 tick 里做一件短事：
  决定哪个批次可领取。每次推进最多创建一个 `ProductionSeries`（有 live series 时不再派发），
  GPU 租约、renderer、job/attempt 全部仍在 series 机制里，Run 不持有整部作品期限的锁。
  `_advance_production_runs` 在 mock / offline / 无受管运行配置时返回 0：跑不了真实 H3 的机器不得凭空派发。
  运行层的任何异常都不得杀死 Worker 主循环。
- **一批不是一集**。属于某个 Run 批次的 series **不再自动发布自己的纪录片**（`enqueue_auto_delivery` 返回 `None`）；
  分集交付由 run episode 订单负责，跨多个 series 生成一条有序 EDL。重复发布就是重复交付。
- **交付范围靠列而不是靠 NULL 判重**。`delivery_orders` 有 `scope_kind/scope_id/input_digest`，
  分集用部分唯一索引 `(scope_kind, scope_id, version) WHERE scope_id IS NOT NULL`；
  旧的 `(series_id, version)` 唯一性必须保留，旧行 scope 回填为 `series`。
- **分层装配，不放大单次上限**。`EditDecisionList` 的 100 项合同不变；
  `DeliveryAssembler.assemble(page_size=100)` 先折页再拼总片，每层 `-c copy`，源片段只编码一次。
- **一个页面可能承载两个阶段**（剧本与素材）。步骤条只高亮“当前该做的那一个”，
  且六阶段完成态只能来自 `GET /projects/{id}/journey`；没有服务端状态时**不得**显示“全部完成”。
- **推荐只读、只算、不承诺**。`comfyops/recommendation.py` 读 `config/deployment` 的已审计组合，
  多卡显存不相加，未实测的组合只能是 `estimated`；目录里的整数 GiB 内存门槛要用
  `effective_floor_mb`（减 1 GiB）读取，否则推荐会比安装器更严——这就是“三套结论”。
- **迁移 head 现在是 `f180a0010001`**。`delivery_orders` 的列变更走 `batch_alter_table`（SQLite 表重建），
  旧库升级需验证历史订单行、两条唯一约束与 `alembic check` 无 drift。

## 2026-11-06 收尾修复轮（状态身份正确性与主需求闭环）

- **分集产物只认本 Run 的 series item**：`run_owned_passed_jobs`（episode→batch→series→item→job）是分集
  EDL/摘要/进度的唯一来源；全局"最新通过 job"永不入选。交付前置条件是**计划内全部批次**已创建且 PASSED
  ——批次没派发就是未完成，不是历史。已误标 COMPLETED 的 Run 由 `reconcile_run` 纠正为 NEEDS_REVIEW
  （保留事件与审计），不静默改写。
- **Run 控制必须到达子 series**：暂停/预算/存储走 `_pause_child_series`（QUEUED→PAUSED、RUNNING→
  PAUSE_REQUESTED），series.control_reason 记录暂停主体，resume 只恢复本 Run 暂停的；`claim_series`
  拒绝领取属于非活跃 Run 的 series。取消是两段式：有在途外部工作→CANCEL_REQUESTED，外部工作落定后由
  beat/对账收尾成 CANCELLED。预算/存储分支是条件 UPDATE（fencing），陈旧对象不能复活终态。
- **冻结包三哈希 + 派发复核**：package_readiness 校验根 manifest/scope/policy 哈希；每次批次派发重跑
  readiness，不可启动→具名阻塞，包确认不是永久启动资格。政策是 strict 类型化合同（extra=forbid），
  并经 series 落到每个 job（`max_semantic_retries`/`auto_repair` 覆盖项目默认）。
- **双入口一条链路**：`director/creative.py` 的 CreativeBrief 是版本化 AgentArtifact（人工创作留痕）；
  生成剧本是持久任务（远程文字需一次性同意并钉 provider 指纹；UNKNOWN→NEEDS_REVIEW 不重放），产物是
  普通 ProjectDocument，走既有大纲链路。分集先按剧本场次（sections 模式），`materialize` 只认各场
  **已应用**分镜的镜头；按镜头分组是兼容入口。
- **分集后期合同修订不重拍**：`EpisodePostContract`（入出点+字幕版本；音频混音按名拒绝）进 input_digest；
  修订=新交付版本、旧版保留、Job 数不变；字幕钉 revision+sha 且时码不得超出剪辑长度；装配中的订单不打断。
  长装配有 `AssemblyHeartbeat`：活跃装配不被跨租期抢占，失联可收回，迟到回写被 claim token 拒绝。
- **部署组合推荐是真机只读检测**：前端推荐卡片调 `/setup/deployment-recommendations`，展示要求/下载/
  复用/证据等级/具名阻塞；未实测组合只标 estimated，不写成已验证。
- **`/health` 暴露 schema 身份**（schema_revision/head/up_to_date，读不到=unknown）；前端 SchemaNotice 在
  库落后于程序时提示重启升级，旧栈 404 不再被当成"功能不存在"。
- 回归入口：`backend/tests/test_director_long_form_regressions.py`（18 条独立反例迁入 + 暂停/取消/纠正/
  T01/T02）、`test_director_entries_and_post.py`（双入口/先分集/后期合同）、`test_schema_identity.py`；
  迁移 head 为 `f180b0010001`。证据：`docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_20261106.md`。

## 2026-11-06 收尾修复 V3 轮（调度公平性与创意任务租约）

- **检查过 ≠ 有进展**。`advance_run` 必须区分四件事：**检查过**（`observations`）、**真实状态变化**
  （`actions`）、**新工作入队**（`batch_dispatched` / `episode_queued`）、**复用已有待执行单**
  （`episode_delivery_pending`）。只有真实变化才算 `progressed`，且只有真实变化才移动
  `last_progress_at`。没有真实变化时 `action="waiting"`，**这一拍必须让给下游执行器**。
  复用一份已存在的 PENDING/ASSEMBLING/DELIVERED 交付单**永远不是**新工作。
- **运行层不得独占 tick**。`_advance_production_runs` 返回 `RunAdvanceSummary(checked, progressed,
  dispatched, queued)`；`_run_configured_once` 只有在 `progressed` 时才 `return "run:N"`。
  否则它必须继续走交付执行器、series 执行器、普通队列。
- **多个 Run 之间要公平**。`advance_runs` 按 `updated_at ASC, created_at ASC` 轮转（每次推进都会
  刷新 `updated_at`），不能用 `created_at` 先到先服务——那会让最老的 Run 永远排在最前。
- **创意剧本任务是带租约的持久工作单元**。领取写 `lease_expires_at`（30 分钟）并由
  `_renew_creative_claim()` 续租；领取范围含 QUEUED 与 **RUNNING 且租约已过期**（`lease_expires_at
  IS NULL` 的历史孤儿同样算过期）。**过期表示"需要核对"，不表示"请求没发出"。**
- **创意任务的阶段机是权威进度**（`trace.stage`）：`DRAFT`（可证明未发出→可发）、`SENDING`
  （发送意图已落库→**不重放**，进 NEEDS_REVIEW）、`RESPONSE`（回答已落库为 `CreativeScriptDraft`
  产物→**复用回答写文档，不再买第二次模型调用**）、`DOCUMENT`（文档已入库→只收敛状态，绝不写第二份）。
  发送意图与稳定请求身份必须在**外部调用之前**落库。
- **创意任务的写回全项 fencing**：成功与失败都走条件 UPDATE（`state_version` + `lease_owner` +
  `status`）。租约失效的旧 owner 迟到写回被拒绝：不得覆盖终态、不得复活已终结任务、不得创建
  重复 ProjectDocument。文档与任务终态在**同一事务**提交。
- **前端必须显示中断、核对、失败与下一步**（`stage` / `reconciliation` / `next_step`），不能一直显示
  "生成中"。Worker 丢失后任务必须离开 RUNNING，否则用户会永远等待。
- **整链回归必须走真实 Worker 调度入口**：`backend/tests/test_director_worker_integration.py` 用
  `worker.run_configured_once()` 驱动 3 集 / 6 批次 / 6 镜直到 Run COMPLETED，不手工调用
  `advance_run`、不手工把 job/series 标为 PASSED、不手工补交付。**被替换的只有渲染边界**
  （物理 ComfyUI 进程与 H3 推理），GPU 独占准入仍走 `ResourceScheduler`，FFmpeg 装配是真实的。
  证据等级只到 OFFLINE_VERIFIED。
- **改了 `worker.py` 就必须重做 VLM 校准**：`client_review_identity` 钉住 `worker.py`，改动按设计
  使旧 VLM revision 与被冻结计划失效。真实生产验收前必须重新真实正负校准、发布新 revision、
  弃用旧 revision、重新分析并人工重新冻结计划；旧证据不得复用。**跑测试期间也不要改被钉住的
  文件**——`REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD` 会让整片原生 VLM 用例变红。
- 回归入口：`backend/tests/test_director_worker_scheduling.py`（7 条）、`test_creative_script_task_lifecycle.py`
  （7 条）、`test_director_worker_integration.py`（1 条）；原 18 条反例与上轮 3 条新反例的原始探针
  文件保持不变、独立可复跑。
- 本轮文档：进度 `docs/PROGRESS_20261106_DIRECTOR_LONG_FORM_V3.md`、
  分级验收 `docs/ACCEPTANCE_DIRECTOR_LONG_FORM_CLOSEOUT_V3_20261106.md`、
  测试报告 `docs/TEST_REPORT_20261106_DIRECTOR_LONG_FORM_V3.md`；
  证据目录 `.local-acceptance/closeout-v3-20261106/`（Git 忽略，不进安装包）。

## 2026-11-06 跨批次续接轮（跨 series 真实媒体续接 + 部署目录缺陷修复）

- **续接必须是真实媒体，不是提示词里的一句话**。`last_frame` / `hard_frame_anchor` /
  `video_reference` / `audio_reference` 各自派生一份带 lineage 的派生物
  （`shine/media/continuity.py`）：来源 job 必须 PASSED、未失效、shot revision 未变，
  attempt 与 artifact 的 sha/字节数一致。派生物**不是**用户批准的主素材：它不进人工绑定集合、
  不进冻结清单的 links，而是按 lineage 校验；上传前还要重新核验**源文件字节**，源被改动即拒绝。
- **Worker 拥有派生物**：`worker._load_comfy_context` 按 `role=continuity_derivative` 装载
  `MediaArtifact`；`preproduction.enforce_references` 把派生物与主素材分开校验。
  派生动作一律在**写事务之外**（FFmpeg），提交前重查来源。
- **跨批次调度必须冻结**。导演包策略新增 `split_continuity`（默认开）：批次边界可以落在连续性链上，
  下一个 series 绑定上一个 series 已验证的来源媒体。策略随包冻结，重启不得改变计划。
  以续接开场的批次**不立即建 job**，由 Worker 在拿到租约后解析真实来源并派生引用。
- **来源解析顺序固定**：同 series 前序镜 → 本 Run 自有已通过的 job（可跨集）→ 声明的 previous_job_id →
  项目台账里该镜最近一次**真实通过**的 job。更新的其他生产 job 不得被采用。
- **部署目录必须能声明审片模型的能力与显存预算**。`CatalogComponent` 增加
  `capabilities` / `resource_mb` / `resource_measurement`，`_catalog_package_spec()` 透传；
  目录里 `qwen3-vl-8b-fp8 → ['VLM_REVIEW'], 8000 MiB, ESTIMATED`，
  `qwen3-asr-0.6b → ['ASR'], 4000 MiB, ESTIMATED`（本机基线，不是实测峰值）。
  否则安装出来的模型包 `capabilities=[]`，**原生 VLM/ASR 能力永远无法发布**。
  `register_catalog_packages()` 允许"文件逐字节相同、只有声明变化"的升级并写审计；文件变了仍然拒绝。
- **目录变化会让运行时身份变化**：服务验证证据随即失效，必须按
  `configuration → services/validate → runtime/activate` 重跑，不得跳过。
- **ASR 的 Python ABI 问题单独记录**：本机 ComfyUI 内嵌 Python 3.13.11，已固定的 `nagisa` wheel 是
  cp312，`qwen_asr` 导入失败。ASR 是 advisory，不阻断 H3、不改变门禁强度，也**不得伪报 READY**；
  但它使"同时钉住 VLM 与 ASR 的生产计划"在本机被 `CAPABILITY_MISSING` 硬阻断。
- 回归：`test_continuity_media.py`、`test_cross_series_source.py`、`test_cross_series_continuity.py`（新增）、
  `test_production_series.py`、`test_director_worker_integration.py`。
  证据：`docs/ACCEPTANCE_20261106_CROSS_SERIES_CONTINUITY.md`。
  **改动 `worker.py` / `preproduction.py` 后仍必须重新做真实正负校准**；本轮因本机 8B 推理未在
  验收窗口内结束而未完成新 revision，旧 revision 不得复用，也不得据此声称已重新校准。

## 2026-11-06 接手轮（导演模式解阻 + 本机真实 H3 出片）

- **本地调用不得继承宿主代理**。本机 `HTTP_PROXY` 指向一个会替 `/health` 返回 404 的代理，
  而 `LocalReviewRelease.release_models()` 与审片客户端都继承了它：前者使"模型已释放"的
  证明永远不成立（租约因此永久阻塞），后者会把一次长推理交给代理。凡是**已证明为环回**的
  端点一律 `trust_env=False`；远端审片仍走宿主代理。证据与复现见
  `docs/ACCEPTANCE_20261106_DIRECTOR_UNBLOCK_REAL_H3.md` §2.1。
- **中断的子调用必须能被证据判死**。`reconcile` 不再是空操作：产物已落库 → 采用；子调用停在
  `PREPARING / WAITING_RESOURCE / PREPARING_PROVIDER`（网关在真正调用前才写
  `WAITING_PROVIDER`）→ 判 `NOT_SENT_PROVEN`，注销为 `SUPERSEDED` 并**释放唯一的
  `request_identity`**（否则新请求 `IntegrityError`）；可能已发出 → `UNCONFIRMABLE`，保持阻塞。
  `FAILED`（有回答但被合同拒绝）不得新建重请求。只有持久化的 pre-provider 阶段证据或
  provider transport 明确 `NOT_SENT`，并写入 `NOT_SENT_PROVEN` reconciliation 后，才可释放旧
  request identity 并重新请求。
  注销的行是历史，不是答案：`prepare_scenes` 的复用查询必须排除 `SUPERSEDED`。
- **有疑问的大纲需要用户决定，而不是无限重生成**。`confirm` 接受
  `acknowledge_questions_sha256`（由 `GET story-preparation` 提供，绑定当前问题列表）；
  没有它或与当前问题不符仍然 409。确认审计记录 `questions_acknowledged`，artifact 的
  payload/sha256 不改写。悬疑剧本里"问题"是创作意图，不是缺陷。
- **方案的参考槽位是有限资源**：`local-h3-ref2va-pdd8-2img-1aud` 只有 1 个 `reference_subject`
  与 1 个 `reference_picture` 槽。两个人物时第二个必须绑成 `picture`（编译器同样为它生成
  `<Picture n>` 标签），否则 `PENDING_WORKFLOW_MAPPING`。多人物镜头要用更宽的方案。
- **中文 Style Bible 不能直接进 H3 提示词**：手工镜头必须自带英文 `prompt_style`
  （七维转写），否则 `H3_ENGLISH` 在提交前拒绝。分镜链产出的镜头本来就有英文 `prompt_style`。
- **启动阶段失败的 H3 租约会死锁**：`release_dead_renderer_lease` 对有 attempt 的任务保守拒绝，
  而 `operator/jobs/{id}/abandon` 要求 `H3_LEASE_STILL_ACTIVE` 先解除。当前唯一出口是重启
  工作台触发启动对账（本轮实测有效）。修这个死锁时不得放宽"有外部提交就必须对账"的前提。
- **素材计划无法跨场合并同一人物**：同一人物在不同场有不同外观描述时，
  `MATERIAL_IDENTITY_AMBIGUOUS` 直接拒绝整份计划，而产品没有"合并同一人物"的用户动作。
  可用的收敛方式：只对**单场**建计划（导演模式"准备所选段落的素材清单"即此语义），
  或让场次准备产出统一名字。
- 本轮真实出片（唯一一镜）：job `aa8baa09…`，prompt `72738f4e…`，artifact
  `92b862e6…`（1024×576 / 24fps / 158 帧 / 6.583 s / h264+AAC），`ffmpeg -f null -` 解码 0 错，
  technical `passed`、final `needs_review`、`acceptance_status=pending_review`。
  **不是** 20 段成片，也**不是**验收通过。
- **审计写入必须有真实实体**：审片释放失败的 `ADVISORY_REVIEW_RELEASE_FALLBACK` 曾把
  `entity_id` 传 `None`，`NOT NULL` 直接终止 Worker 主循环（出片完成后一秒后台"卡死"、
  显卡空闲）。任何 worker 路径里的审计/记账写入都必须挂在一个存在的 id 上，并且
  自身失败不得结束这一拍。
- 证据：`docs/ACCEPTANCE_20261106_DIRECTOR_UNBLOCK_REAL_H3.md`；
  回归：`backend/tests/test_story_preparation.py`（含 4 条新反例）、
  `backend/tests/test_advisory_queue.py`（审片释放失败不终止 Worker）。

## 2026-11-07 第 7 轮（场景底图全量入库 + 两处导演提示词缺陷修复）

- **无剪辑表场景的导演提示词必须禁止 same-beat cuts**：`validate_output` 要求每个 cut 窗口
  覆盖所涉 beat 的全部台词，而旧提示词从未约束无 `editorial_timing_plan` 时的 cuts，模型把
  一个 beat 剪成 2.5/4.2/6.5/8.5s 的碎片，`EDITORIAL_TIMING_CONFLICT` 永远不可自愈（S3 连续
  4 次，抓包实证）。修复：无剪辑表时"一 beat 一 shot、禁用 cuts、cut 只折叠更晚的 beat"。
  无声场景能通过不构成反证——它们只是没有台词。
- **台词说话方=必须绑定的人物**：服务器把 dialogue speaker 计入 required_characters，
  旧提示词只要求"出场人物"。修复：说话方即使从不露面，其已批准主素材也必须出现在该片段
  `asset_ids`（S8 三次 `CHARACTER_MASTER_BINDING_REQUIRED` 实证）。
  **2026-11-08 变更**：这条门禁不再拦在 crew 链里，而是**需求链**的一环 —— 分镜提出需求
  （缺哪个角色主素材），需求随答案交给素材层，拦住未满足需求的是「拍摄素材准备」这一步与
  导演包冻结（`SHOT_CHARACTER_MASTER_MISSING`）。说到的仍是"说话方必须可绑定"。
  以上两处回归都在 `backend/tests/test_crew_prompt_contract.py`；真实链以 S3/S8
  "修前连败、修后一次过"验证。证据：`docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md`。
- **仅声音的说话实体**（如念名单的"广播"）产品未建模：`_character_coverage` 按人物名解析
  必然 409。本轮把该实体按 character 家族建单场素材计划（视觉=舱壁铜质号角喇叭特写），
  promote 为 entity_id=`广播` 的主素材，如实地满足"说话方必须可绑定"的门禁。
- **多人物/多参考项目要宽方案并真实校验**：本项目注册了已提交的
  `config/workflows/ref2va-pdd8-6img.json`（1 subject + 5 picture，image-only 传输族），
  真实渲染器 `live_object_info` 校验 `missing_nodes=[]`。第二人物按既有语义绑成 picture
  参考是合法答案（真实镜头 96c6d395 已验证）。
- R7 接手时台账快照（数据目录 `.local-acceptance/live-browser`）：主素材 39（26 场景底图 + 6 人物 +
  原有 7）；当时订单 9 卡在 apply-camera 422（camera artifact `529c502d`），12–21 未跑。
  R7 快照见 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §1–§6；第 8 轮续跑后的权威结果见 §7。
- **候选评审口径**：候选图逐一与台账 `content_sha256` 比对（32/32 一致）+ VLM 目检；
  拼版自绘编号不是水印、码头系缆桩不是人影——复核原图再下结论，不要把拼版标注当缺陷。
- 本轮未改 `worker.py`/`preproduction.py`，不触发 `client_review_identity` 重校准义务；
  VLM 正负校准仍未做，不得声称已校准。

## 2026-11-07 第 8 轮真实续跑（订单 9–21）

- 第 8 轮使用真实 MiniMax 五角色链与产品 `apply-camera`。订单 9 根因由本地同构 ShotSpec 重建确认：dialogue `speaker_id=广播` 未列入 `characters`，因为旧 continuity fact 说它不是出场人物。受托 `local-user` 改一条事实生成新 task 后成功应用 3 镜；旧 artifacts 保留。
- orders 12–17、21 已按单段真实运行并应用（新增 22 镜；加订单 9 的 3 镜，共 25 镜）。orders 12 和 14 的 `LOCKED_FACTS_CHANGED` 按产品路径 resume 后成功。详见 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §7.2。
- orders 18、19、20 的子任务分别以 `LLM_TIMEOUT` / `external_outcome=UNKNOWN` 结束；没有 provider request id、HTTP 状态或 response hash。产品 `abandon-remote-response` 把丢失响应审计为 `ABANDONED`，仍是 `UNKNOWN` 且 `automatic_replay=false`。本节早先写的“没有重发”后来被同一台账中的三条 `STORY_UNKNOWN_SUBTASK_REPLACED` 事件证伪：旧 UI 允许创建新 task identity；事件与修复见本文件 §10 及进度 §10。不得把保留旧行或 `automatic_replay=false` 当作未重请求的证明。
- `POST /episode-plans/1/materialize` 因 `EPISODE_SECTION_NOT_STORYBOARDED` 返回 409，明确列出 orders 18–20 的三个 pending section；只有确认所有段均已应用后才能 materialize、confirm v2、建 DirectorPackage 和 ProductionRun。当前 `/journey` 为 54 project shots / 0 planned shots；本轮没有新 H3 attempt。台账保留上轮唯一真实 job `aa8baa09-0c1f-4627-a874-193a1a3647e1`（`PASSED`、`quality_verification=real`，artifact SHA-256 `92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190`、`last_qc_result=needs_review`），不代表整片完成或 accepted。
- 本轮没有改源代码或 `worker.py` / `preproduction.py`，R7 pytest/Ruff 报告仍是旧证据；VLM 正负校准继续未完成。H3 renderer 未启动。工作台按 `scripts/stop_shine.py --data-dir .local-acceptance/live-browser` 停止，端口 8000/5173/8188 均已验证无监听。
- 本轮完整续跑及真实边界见 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §7 与 `docs/TEST_REPORT_20261107_DIRECTOR_JOURNEY_R7.md` §5。

## 2026-11-07 串行模型资源与全片续跑收尾

- **所有本机模型阶段共享一张全局资源租约表，严格串行**。`ResourceScheduler.admit`
  即使账面显存预算可并排，也只允许一个 `ACTIVE`；过期 `ACTIVE` 必须转
  `RECONCILE_REQUIRED`，不能按超时当成资源已释放。
- **无人续租的 WAITING 预约要有限期**。`WAITING_LEASE_TTL_SECONDS=60`；真实排队方在
  `enqueue` 时续租，已过期且不是当前活跃请求的 WAITING 行可以置为 `RELEASED`（它从未持有
  renderer/provider side effect）。当前请求若在等待期间过期，可在 `admit` 中续自己的位置；
  任何别人的过期 WAITING 都不能被冒认成活跃。这样旧 H3 队列项不会永久挡住后续 MiniMax
  资源请求。
- **Comfy `/free` 是异步卸载请求，HTTP 200 不是释放证明**。`ComfyUIClient.free_models()`
  最多等 120 秒、每 0.5 秒轮询；只有 Comfy 队列 running/pending 均为空，且 `/system_stats`
  证明 live CUDA tensor ≤512 MiB（ComfyUI <0.39 按旧字段要求 torch pool 为 0），才能归还
  lease。读取不可用、格式未知或超时一律 fail closed；再由 reconciliation 处理，禁止再发第二次
  `/free`，也禁止用固定睡眠代替释放证据。空闲 allocator cache 不等于 live model memory。
- **真实 H3 的串行单位是 production series**：同一 series 内的 shots 通过 idle keeper 续用同一
  renderer/租约，不在每镜之间卸载；series 完结时只停止经 PID/birth + Job Object 证明归本安装
  所有的 renderer，且确认端口释放后，租约和 series 状态才一起提交。下一 series 此后才可开机，
  不可把 per-shot 续用改成并发或未经证明的释放。
- **恢复队列的 UI 状态跟实际领取状态一致**。safe pre-submit recovery 只将 series/batch 设为
  `QUEUED`，由唯一 resident Worker 领取、取得资源租约后再变 `RUNNING`；reconcile 也将无 worker
  claim 的 `QUEUED` series 同步回 batch，避免若干待领取批次残留成“同时拍摄中”。`UNKNOWN`、有
  provider id/attempt/artifact 或活动租约的请求仍不得重开。
- 根因与真实证据见 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §8、测试与限制见
  `docs/TEST_REPORT_20261107_DIRECTOR_JOURNEY_R7.md` §6。新增回归覆盖：旧 WAITING 不再饿死队列、
  ACTIVE 超时仍阻塞、异步 `/free` 超过旧 3 秒仍能被确认、预检失败不重复 unload、safe resume
  留在 QUEUED、reconcile 清除 stale RUNNING batch。改动未涉及 `worker.py` / `preproduction.py`；
  VLM calibration 仍未完成，不能声称已校准。

## 2026-11-07 第 9 轮（64 镜 / 4 集真实 Run 收尾）

- 项目 `3707a9ed-eced-435b-b69a-0b1ecdd5f146` 的 episode plan v2
  `0af55b3b-a628-48eb-a3fd-34f8bb326dd4`（SHA-256
  `2e856030014f8f126239409bbed7b7f076fc23f349acb9509a399d0950428a6b`）来自
  `script_sections_materialized`，由所有者授权委托的 `local-user` 确认。整部 Run
  `e2da69c8-5b28-45b4-83e8-102b7c09a997` 终态 `COMPLETED`：64/64 非 mock H3 镜头
  `PASSED`、12/12 批次 `PASSED`、4/4 集 `DELIVERED`、0 blocker、0 active series、
  0 live resource lease。逐镜 SHA/字节、H.264 1024×576@24fps `ffprobe`、全文件
  `ffmpeg -f null -` exit 0 均通过。逐镜证据在本机忽略目录
  `.local-acceptance/r7/final_run_verification_20261107.json`。
- 每集 EDL clip order、delivery order 与 run episode manifest 对齐；每个 EDL payload
  的台账原文 SHA-256、输入 digest、成片 artifact SHA-256 / 时长见
  `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §9。
- 实际内置浏览器显示 4/4 集交付、64/64 镜完成；打开 ep-04 预览后加载出真实画面。
  UI 仍将每集显示为「待你确认」，因此这是生产与确定性技术交付完成，不是人工艺术验收；
  `pending_review` 没有改成 `accepted`。VLM 正负 calibration 未执行。
- Advisory 队列已排空：ASR 29 条 `UNAVAILABLE`（未配置）、VLM 68 条 `UNAVAILABLE`
  （reviewer 路由无法提供），不会阻塞确定性生产。
- MiniMax M3.1 Flash agent 输入及 JSON schema 使用紧凑 JSON；无显式 output budget 时
  默认 `max_completion_tokens=16384`，并对该模型施加同值 ceiling；不裁剪上下文/事实。
  MiniMax 官方调试台将 M3.1 Flash 标为 1M context，示例请求使用 16,384 completion
  tokens。该值是保守输出预算，不是 context 上限。历史订单 18–20 没有 provider
  response/body/id 持久化，`UNKNOWN` 不能证明 context overflow。审计显示三个旧 UNKNOWN 子调用
  后续曾通过显式替换创建新 task 并成功；这是旧产品入口允许的行为，但违反 no-resend 门禁，
  现已在 API 与 UI 禁止，并在后续 §10 如实记录。
- 根因、实跑摘要及验收边界见进度 §9 / 测试报告 §7。该轮不等于用户的艺术验收。

## 2026-11-07 UNKNOWN 请求身份安全补充（覆盖此前显式替换规则）

- `external_outcome=UNKNOWN` 只允许核对原 task、采用身份链完整的已落库 artifact，或保持
  `UNCONFIRMABLE`；不得因用户授权、一次性 grant、旧任务仍保留或 `automatic_replay=false`
  而创建替代 task。只有 durable 证据证明请求未送达，并写入 `NOT_SENT_PROVEN` 后才可再请求。
- 只有明确 transport `NOT_SENT` 或可证明停在 gateway 的 `WAITING_PROVIDER` 之前，才能生成
  `NOT_SENT_PROVEN`。无 phase、超时、模型已返回但合同拒绝、HTTP 错误状态不自动等价于未发送。
  没有充分证据保持原状态并阻塞。
- 导演分段、整剧分镜、直接 FilmAgentDispatcher 与单场素材分析都执行此规则。旧 API
  `replace-unknown` / `replace_interrupted_task_id` / `replace_unknown_task_id` 返回拒绝；单场素材
  UI 提供“核对已有结果”，它不发起 provider 请求。对已证明未发送的单场任务，核对仅解除
  identity 并提示用户单独执行下一次分析；不会自动重试。
- 2026-11-07 台账中有三次历史 `STORY_UNKNOWN_SUBTASK_REPLACED`，actor=`local-user`，旧 task
  仍 `UNKNOWN/ABANDONED`，其新 task 已成功；三次都违反了本规则。该事实不可回滚或改写；64 镜
  技术交付证据仍成立，但本轮不得声称整段旅程完全遵守 UNKNOWN 门禁或已获艺术验收。详见
  `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §10。
- 若修改 `worker.py` / `preproduction.py`，仍按上文 `client_review_identity` 规则重做真实
  positive/negative calibration；本补丁未修改这两个文件。

## 2026-11-07 导演阶段严格使用确认分集范围

- `/journey` 的 storyboard `planned_shot_ids` 是当前 confirmed episode plan 的权威镜头顺序；
  无 confirmed plan 时才按原兼容语义使用项目全部 shot。导演模式排产候选只允许使用此范围，
  不得用 project shot 总数代替本次计划数。全项目 `ShotPanel` 和任务审计仍保留历史记录。
- 导演 stage-dock 的通过数使用 journey 的 `passed_shot_count/scope_shot_count`；若任务页展示
  全项目 job 历史，必须清楚说明其包含计划外旧镜头，不能用其通过/失败总数冒充当前 ProductionRun。
- 修订没有改变已确认计划、Run、生成物或技术/人工质量门禁。64 镜、4 集的全片输出证据和本次
  UI 根因修复见 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §9–§11 与
  `docs/TEST_REPORT_20261107_DIRECTOR_JOURNEY_R7.md` §7.5–§7.7；人工艺术确认仍保留给用户。

## 2026-11-07 H3 spoken-audio contract

- Locked story facts may mention dialogue that belongs to another shot. The H3 compiler labels them as background continuity context, never as material to speak or narrate. Dialogue-free shots always carry a silence contract even when project language is unset; dialogue shots allow only their explicitly tagged lines in the project language.
- ASR advisory review covers every shot in `basic` or `full` self-review scope, including dialogue-free shots. Recognized speech in a dialogue-free shot, or ASR text beyond the explicitly scripted lines, is uncertain advisory evidence only; it never becomes a deterministic production failure. An absent, mock, pending, or unavailable ASR result must be visible at delivery and cannot be presented as language verification.
- FFprobe and decode establish technical audio presence/integrity only. They do not establish spoken language or silence. Existing artifacts are never rewritten or silently re-accepted after an audio prompt/reviewer change.
- Episode delivery downloads use the verified project title plus the episode's ordinal within its
  run in the HTTP `Content-Disposition` filename (for example `项目名-第1集.mp4`). Windows-invalid
  filename characters are replaced and the project-title portion is bounded. Later revisions may
  add `-vN`; generic non-episode deliveries use `项目名-成片.mp4`.
- Keep the underlying delivery file content-addressed and immutable. A display-name change must not
  rename storage paths, modify bytes, or bypass SHA/size/decode or pending-review gates.
- The Director 「生成分段」 view follows the newest `ProductionRun`'s confirmed episode and shot order across all of its `ProductionRunBatch` series; it must not present only the newest batch as the whole run. Each shot keeps its source batch `series_id`, so retake selection still writes through that batch's existing assembly gate. Show at most 6 shot cards per page. A passed but unsatisfactory shot can be explicitly retaken, with the old artifact preserved and a newly passed replacement explicitly selected. A historical `NEEDS_REVIEW` mapping failure stops blocking only when all attempt/audit/renderer-activation evidence proves no external submission; the explicit retake action records `ExternalJobReconciliation.result=NOT_SENT_PROVEN` before scheduling a new job. Other failed, `NEEDS_REVIEW`, or `UNKNOWN` work stays blocked until its original outcome is reconciled.

## 2026-11-07 新用户全旅程修复：LLM P0、模型调用外部结果分类与 12GB 低端机候选

- 本轮依据 `docs/NEW_USER_JOURNEY_REPAIR_PLAN_20261107.md` 实施阶段 A（LLM P0）、阶段 C
  （硬件资格与变体清单）、阶段 D（图形化安装外壳与官方权重核实）；阶段 B（安装持久化与续传）
  另行实施。实施证据见 `docs/NEW_USER_JOURNEY_REPAIR_IMPLEMENTATION_20261107.md`。
- **模型调用外部结果必须分类，不得一律压成 UNKNOWN**：`agent_call.classify_external_outcome()`
  区分三种事实——`NOT_SENT`（根本没发出请求：本地配置错误、拒绝同意、发送前传输失败）、
  `RESPONSE_RECEIVED`（服务商已答复：4xx 拒绝没有答案，2xx 答复不可用）、`UNKNOWN`（可能已
  答复但答复丢失：5xx、超时、发送后连接中断）。分类依据是 Provider 层已经记录的 HTTP 状态，
  不做猜测；`response_status` 随 trace 落盘作为可核对证据。
- **`UNKNOWN` 门禁保持不变**：可能已发出的请求仍然不重发、不替代、不释放身份，核对结论仍是
  `UNCONFIRMABLE`，恢复仍是 409。`RESPONSE_RECEIVED` 是**另一个**结论：请求已结束、不存在
  可被重复购买的答案，因此允许在 `MAX_SUBTASK_RETRIES` 预算内释放一次并重新提问；记录的是
  `RESPONSE_RECEIVED_NO_ANSWER`，**不是** `NOT_SENT_PROVEN`，不得据此声称“未发出”。
- **核对结论必须幂等**：已判定的块在重复核对时保持原结论，不得被覆盖回 `UNCONFIRMABLE`；
  后端 `resumable` 判定与恢复门禁必须是同一套规则，界面不得给出服务端必然拒绝的按钮。
- **官方权重事实（A 级，2026-11-07 核实 revision `54472221d27c695fca9621b0c3ed52f06c4af707`）**：
  `Comfy-Org/MiniMax-H3` 全部 39 个文件中**不存在** W4/AWQ/GPTQ/INT4 的 DiT，唯一 4bit 文件是
  文本编码器 `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`。官方最小 DiT 是
  `minimax_h3_ref2va_pruned_w6a8.safetensors`（15,983,746,636 B /
  `ece96bbbce76670ec782de84acc888fd9d9b210bd2ab97c1957b39896734f9f1`），官方 INT8 ConvRot
  视频 VAE 为 2,811,065,184 B。官方**未公布**任何显存/内存门槛，12GB 显卡能否运行属于待核；
  候选变体 `acceptance_status` 只能是 `CANDIDATE`，不得进入默认生产推荐。
- 面向用户的 W4 ConvRot 需求登记为**显式未钉住的候选槽位**（`source: THIRD_PARTY_UNVERIFIED`、
  全部 pin 为 `unknown`、`acceptance_status: BLOCKED`），并列出钉住它所需的可信来源、revision、
  size、SHA-256、许可证原文与显存实测来源。**不得编造** URL、体积、哈希或许可证结论。

## 2026-11-08 导演链路阻滞：死亡 worker 的子任务与「按状态复用任务」

- **场景子任务必须能被识别为「worker 已死」**。`materialprep/scenes.py` 的单场子任务此前
  **完全没有租约**，worker 带着请求死在半路时，行永远停在 `RUNNING`，与「正在跑」无法区分：
  界面显示假进行中，而该场再也不能被重新请求，整条大纲就停在那里。现在子任务以
  `created_at` 超过 `SCENE_DEAD_AFTER_SECONDS`（900s，超过服务商超时上限）判定为死亡，
  并按最后一个 phase 决定：
  - phase 在 `PRE_REQUEST_PHASES`（`PREPARING`/`WAITING_RESOURCE`/`PREPARING_PROVIDER`）
    → 可证明请求从未发出，写入 `NOT_SENT_PROVEN`、退休该行并释放身份，**以新请求重问这一场**；
  - phase 已到 `WAITING_PROVIDER` 或更晚 → 结果仍未知，**不退休、不重发**，保留身份等人核对，
    并在结果里带 `dead_worker`/`outcome_unknown`，界面不再显示「运行中」。
  - 同一场最多自动退休 `MAX_SCENE_DEAD_RETRIES`（2）次，超过即停并等人，避免无限重试。
- **任务复用必须看状态，不能只看身份**。`story.queue_outline` 之前按 `request_identity` 无条件
  复用旧任务，于是**已取消或已失败的大纲任务被永远原样返回**：用户点多少次「后台提取并归并
  总体大纲」都拿不回可推进的任务。现在只有 `REUSABLE_TASK_STATUSES`
  （`QUEUED/RUNNING/PAUSED/NEEDS_REVIEW/SUCCEEDED`）的旧任务会被复用；其余尝试保留身份作为
  历史，新尝试使用带 `attempt` 的新身份（base identity 记录在 trace 里，所有尝试都能被找到）。
  归并子调用（`_merge`）同样处理死亡 worker。
- **已取消的任务仍可「核对已有结果」**：核对只读证据、不发 Provider 请求，因此允许对
  `CANCELLED` 任务执行，让用户能释放可证明未发出的身份；`CANCELLED` 任务不提供「恢复未完成工作」。
- 以上都不放松 UNKNOWN 门禁：可能已发出的请求仍然不重发、不替代、不释放身份，核对结论仍是
  `UNCONFIRMABLE`，恢复仍是 409。
- 验收：`backend/tests/test_scene_dead_worker.py`（3 项）与 `backend/tests/test_story_outline_unblock.py`
  （4 项）、`frontend/src/test/storyOutlineUnblock.test.tsx`（4 项）。

## 2026-11-08 用户授权的未知子任务一次性重发 + 连续模型调用间隔控制

- **（覆盖 2026-11-07 的「不得因用户授权创建替代 task」）** `external_outcome=UNKNOWN` 的子任务，
  在**用户显式确认**后允许**一次**新的模型请求：
  - 入口：`POST /projects/{id}/story-preparation/tasks/{task_id}/resend-unknown`；`expected_state_version`
    绑定用户看到的状态，`evidence` 记录用户的确认说明。
  - 只在用户点「明确重发这一场」并确认后发生；系统**绝不自动**发起，核对（reconcile）仍然只读证据。
  - 旧任务退休时记录的原因必须写 `USER_AUTHORIZED_NEW_REQUEST`，**不得**写 `NOT_SENT_PROVEN`：
    旧请求的结果仍然未知，它只是被用户明确覆盖的一条未知事实。
  - 每一场最多一次（`MAX_UNKNOWN_RESENDS = 1`），第二次同一场被拒（`STORY_UNKNOWN_RESEND_ALREADY_USED`）；
    审计事件 `STORY_UNKNOWN_SUBTASK_RESEND_AUTHORIZED` 记录旧身份、证据与「允许一次新请求」。
  - 其余门禁不变：UNKNOWN 仍不会被自动重放或自动替代，身份在未被用户覆盖前不会释放。
- **调用间隔控制收归唯一入口**：`agent_call.call_material_agent` 现在在**每次** Provider 调用前调用
  `pace_provider_call()`（`SHINE_AGENT_STEP_DELAY_MS`，默认 1200ms）。此前只有 brief 路径调用它，
  而**逐场大纲每场是一个任务步，跨步之间没有任何间隔**——21 场连续调用会共用同一条连接与同一个
  限流，失败的表现正是「卡住」。`scenes.prepare_scenes` 里那次重复 sleep 已移除，间隔只有一个口径。
  逐场调用保持**串行**：一次一个任务步，不并发、不并行扇出。
- 验收：`backend/tests/test_story_unknown_resend.py`（3 项）、`backend/tests/test_scene_dead_worker.py`
  （3 项）、`frontend/src/test/storyOutlineUnblock.test.tsx`（5 项）。

## 2026-11-08 公开仓库的主分支推送限制（仅仓库所有者可推送）

- **远端仓是公开仓库**（`visibility: public`），但 **`main` 只允许仓库所有者 `anjingdtl` 更新**。
  约束由仓库 Ruleset `main-owner-only-push`（id `26763523`）执行：`update` 规则作用于
  `refs/heads/main`，bypass 白名单只有一条 —— `RepositoryRole` admin（`actor_id=5`，
  `bypass_mode=always`）。本仓库目前只有 `anjingdtl` 一个 admin（协作者 API 实测），
  因此「admin 角色」即「所有者本人」。
- **不要用 classic 分支保护的用户级限制来做这件事**：GitHub 对个人账户仓库直接拒绝
  （`Only organization repositories can have users and team restrictions`），Ruleset 是
  个人仓库可用的等价机制。若将来要按**人**授权，必须先把仓库迁到 Organization。
- **所有者仍可直接推 `main`、仍可合并 PR**（bypass 为 `always`）；**其他分支不受限制**，
  feature branch + PR 的工作流照旧可用。外部贡献者只能走 fork + PR，且只有所有者能合并
  （合并同样会写 `main`）。
- **被拒的推送是正确结果，不是故障**：不要为了让推送通过而删除 ruleset、删掉 bypass
  白名单或改用 `--force`。Ruleset 同时挡住了 force push 与 `main` 删除。
- 验收：临时清空 bypass 白名单后做了一次**真实**推送，服务端返回
  `GH013: Cannot update this protected ref`，远端 `main` sha 未变（零污染）；恢复白名单后
  所有者推送非 `main` 分支成功。证据见 `docs/BRANCH_PROTECTION.md`。
## 2026-11-08 四阶段二轮复验（R01–R08 关闭）

- **一次授权只买一次新尝试**：`start_pipeline_new_attempt` 在建新任务的**同一事务**里用
  `state_version` + `status` fence 消费授权，旧行写 `new_attempt_authorization`；重复调用
  返回同一回执。旧行保持 `UNKNOWN` 历史且不再提供动作。审计分开：`PIPELINE_TASK_NEW_ATTEMPT`
  与 `PIPELINE_TASK_RECONCILED`。
- **四阶段任务有角色匹配的对账入口**：`POST /projects/{id}/pipeline/tasks/{id}/reconcile`。
  只有 `NOT_SENT_PROVEN` 才允许普通继续；`UNKNOWN_REQUIRES_NEW_ATTEMPT` 只允许「核对后
  重新发送一次」。普通 resume 仍拒绝 UNKNOWN。
- **分镜交付按「预期单元」核验，不按场次数**：`expected_storyboard_units` 由当前场次
  revision、来源范围、chunk ordinal/hash 与连续性基线算出稳定单元清单；逐单元结果、失败、
  剧组步骤与 apply 句柄存在 `four-stage-storyboard-units:{pid}:{vid}` 台账里。丢片、重复、
  越界、失效、未应用、失败全部具名拒绝正式交付；局部 `section_ids` 重做只替换自己。
- **交付快照才是复用键**：`delivery_snapshot` 绑定 unit/apply、镜头 revision+spec、绑定、
  冻结风格、规则包与工作流图/参数映射；只有完全相同的快照才复用包。镜头升版产生新包，
  旧包以 `SHOT_REVISION_CHANGED` 阻止新 Run 与未提交批次，历史仍可读。缺 lineage 的旧
  **四阶段**包进入新生产前需重新交付（`CREATIVE_LINEAGE_MISSING`），普通旧导演包不受影响。
- **正式分集交付保留 EpisodeScript 身份**：`PlannedEpisode.episode_script_id` 是类型化
  字段，分集清单同时保留它。分集合同以**当前**大纲与场次剧本为准，被替代即具名拒绝；
  `volume_scope` 只在没有任何已确认分集合同时作为兼容合同保留。
- **蓝图依据是可验证的**：蓝图产物带 `basis`（有序 outline refs + plan revision）；确认、
  读取、卷发布与包准入读同一个 `bible_basis_blockers`。草稿蓝图写出的场次在发布时与确认
  蓝图比对主线/人物/世界/时间线/锁定事实/结局/跨卷开放项。
- **面板渲染服务端状态**：四阶段面板不再把分镜草案放在本地 state，刷新/崩溃/重启后仍能看到
  待应用镜头、失败与对账句柄、发布回执与**确切 package ID**；「核对并只重做这一场」只重做
  该场；地址栏 `#d/package?package=<id>` 可直达指定导演包。
- 验收：`backend/tests/test_four_stage_reacceptance.py`（R01–R06）、
  `frontend/src/test/fourStageRecovery.test.tsx`（R07/R08）、
  `scripts/verify_four_stage_browser.py`（隔离浏览器旅程）。
  证据见 `docs/FOUR_STAGE_REACCEPTANCE_REPORT_20261108.md`。


## 2026-11-08 追加：素材后置（用户裁定）

- **素材一律后置**：剧本定稿、分镜设计完成后，才到素材库**按分镜**准备素材，然后拍摄。
  每镜需要什么素材是分镜的产物（`required_asset_ids` 来自摄影方案），不是剧本的产物。
- **阶段次序改为 7 步**：`script 创意与剧本 → plan 剧本统筹 → storyboard 镜头拆解 →
  assets 拍摄素材准备 → package 导演包 → production 视频生产 → post 后期成片`。
  ② 只判分集方案与冻结风格，③ 只判镜头自身，④ 判逐镜素材。
- **素材是可选类需求（2026-11-08 第二轮收尾修订）**：④ 的素材缺口只**报**（notice），不再是 blocker；
  导演包冻结也不再因缺主素材拒绝。缺的那个图片由 H3 自行生成。
  详见下一节。
- **四阶段面板按同一顺序**：单元回执带 `missing_characters`，面板如实显示；「应用这一场」不再
  因为缺素材而禁用（缺的那个图片由 H3 自行生成，可以先落镜）。
- 验收：`backend/tests/test_director_long_form.py`（两条新断言）、
  `frontend/src/test/setupEntry.test.tsx`（阶段条次序）。
  报告见 `docs/FOUR_STAGE_REACCEPTANCE_REPORT_20261108.md` 第 8 节。

## 2026-11-08 第二轮收尾：素材是可选类需求（R09）

- **素材缺失只提示，不再硬阻滞**：镜头点名的素材若不存在、不是本项目的、或没有批准锁定，
  `apply-camera` **跳过绑定**并回 `material_notices`（`MISSING_IMAGE_MATERIAL`，
  「无图片素材将由H3自行生成」），这一镜照样落。素材阶段（journey ④）与导演包冻结
  （`package_readiness`）都不再因此拒绝。
- **跳过绑定必须同时改「要求」**：被跳过的素材要从这一镜的 `required_asset_ids` 里去掉，
  否则 `create_shot_record` 与渲染预检仍会拒掉它——只跳过绑定不改要求，等于把一种 422
  换成另一种 422。
- **绝不绑定未批准锁定的素材**：已绑定但没确认锁定的素材仍然具名拒绝（`ASSET_NOT_MASTER`）；
  渲染侧 `_validate_binding_inputs` 也会拒绝。跳过的字节不进渲染。
- **渲染侧的 image 族豁免有前提**：方案声明 image 传输族时，这一镜**一张图片都没绑**才豁免
  （`WORKFLOW_MEDIA_FAMILY_NOT_BOUND` 不再触发，未用的图片装载节点会被剪掉，不会有占位文件名
  送到渲染器）；绑定了就一张都不丢。audio 族的规则不变：有台词的镜头仍必须有音频参考。
- **人物主体缺位仍然拦住**：H3 编译期 `H3_CHARACTER_REFERENCE_MISSING` 保护身份漂移的规则不变；
  「人物主体」不是可选类素材。缺主素材且这一镜**没有任何参考**时走无参考的 T2VA 编译，
  H3 临时生成人物（画面一致，但不保证是你指定的那个角色）。
- **「一键开拍（自动适配）」尚未改**：排产提交入口仍会在缺参考素材时挡住提交（画面参考与人物主体
  都算缺）。人物主体缺位应当继续拦住；画面参考缺位要降级为提示，得先解决 Ref2VA 方案
  「至少一张参考」与无参考时编译模式退化为 T2VA 的匹配问题。
- 验收：`backend/tests/test_four_stage_reacceptance.py`（R09 两条）、
  `backend/tests/test_reference_transfer.py`（两条反向断言）、
  `backend/tests/test_production_series.py`（API 层反向断言）。
  报告见 `docs/FOUR_STAGE_REACCEPTANCE_REPORT_20261108.md` 第 9 节。

## 2026-11-09 W6A8 低显存候选轮（导演模式新档）

- **一个变体，一个目录，一套方案**。`shine/comfyops/variant_binding.py` 是唯一解析
  变体→部署目录→工作流文件的地方。`POST /setup/profiles` 带 `variant_id` 时登记的是
  **该变体自己的**方案（`local-h3-ref2va-lowend-pdd-free-2img`），绝不借用默认目录的方案；
  **不传变体参数的调用完全不变**（默认目录 + 原三个方案）。
- **变体与目录不一致就拒绝**。`VARIANT_CATALOG_MISMATCH`（409）：变体清单登记的
  `catalog_id` 与调用方指定的不一致时，不合并、不回退到任何一个。同名同项目内容不同仍然
  `PROFILE_CONTENT_CHANGED`；重复登记仍然幂等。
- **身份链要能核对**。变体清单登记 `checkpoint_id` 与 `graph_sha256`（方案名→图哈希），
  `profile_metadata` 才能从图反查变体；候选方案因此带 `variant_id=ref2va-lowend-candidate`、
  `variant_acceptance_status=CANDIDATE`、`variant_verified=false`。**候选不继承基线的验收**。
- **五层就绪 + 唯一下一步**。`GET /setup/variant-readiness` 给出「待准备权重 / 文件已核验 /
  工作流已登记 / 本机运行环境已验证 / 真实出片已验证」，并只给**一个**下一步。
  「差不多能用」不是动作。权重要求**跟着方案图走**：图里没加载的可选 turbo LoRA 不算缺件。
- **候选的下载仍然是关闭的**。`auto_download_allowed` 仍是 `false`，安装计划仍以
  `VARIANT_NOT_DOWNLOADABLE` 拒绝；**禁止**通过改 `SUPPORTED`、改 `auto_download_allowed`
  或跳过资格检查来解锁。已批准的准备路径是既有的「检测已有安装 / 导入模型」，并且这条路径
  现在按**变体自己的目录**比对 revision、字节数与 SHA-256。
- **候选必须走完整门禁才能进生产**。`shine/comfyops/profile_gate.py` 只对**未验收**变体加码，
  已验收变体（含 INT8 基线）行为完全不变。门禁项：项目作用域、图身份、已登记到本作品、
  节点已核对、权重已就位并逐字节核验、受管运行时已配置、宽高已接入。任一不满足 → 具名 409，
  **不替换方案**（不改 INT8、不改 FL2VA、不换参考资产、不换 checkpoint），且拒绝发生在
  **改镜头规格、自动补绑定、入队之前**。
- **服务端是唯一的门，浏览器只是显示**。`create_series`、`create_render_job`、
  `build_package` 调用同一个门禁，绕过界面直接调接口同样被拒；浏览器读到不了结论时
  **不发明拒绝**，由服务端裁决并给出它自己的原因。
- **候选不与基线混档**。拍摄质量新增独立档「低显存候选 · W6A8」；W6A8 方案绝不会被当成
  「高质量 · 原生20步」，一键开拍不会把它换回 INT8。**显式选中的候选方案优先于质量档**：
  要么用它，要么明确说为什么不能用。
- **显式选定就保持同一身份**。导演包钉住 `workflow_profile_id / graph_sha256 / checkpoint_id /
  parameter_map / media_transfers / validated_against`，生产系列与 Job 带同一个
  `workflow_profile_id`，实际 `/prompt` 用同一份 `parameter_map`。
- **最终用哪套方案由用户决定（2026-11-09 用户裁定）**。基础配置的方案列表是**参考清单**，
  不是产品的决定；真正用哪一套由用户在制作页自己选。**用户显式选过的方案，一键开拍与
  导演包都必须照用，任何情况下都不替换**——包括质量档与所选方案不一致时。拍摄质量档只在
  **用户还没选过方案**时充当推荐依据。界面必须显示「用你选定的方案 X」。
- **五层就绪状态是累积的**。后一层只有在前一层达成时才算达成：权重都没到位就不能说
  「文件已核验」，方案没登记就不能说「本机运行环境已验证」。层与层之间的依赖顺序写在
  `variant_readiness.py`，页面不允许出现依赖未达成却显示「已完成」的层。
- **就绪状态区不能长得像方案列表**。五层用独立的 `readiness-layers` 样式并写明标题
  「五层就绪状态」；候选在参考清单里是**同一份列表里的一行**（标注「候选 · 未登记」），
  不能另起一张看起来像方案清单的卡片，否则用户会以为原来的方案不见了。
- **本轮不改 `client_review_identity` 钉住的文件**（`worker.py` / `preproduction.py` /
  handoff / quality），因此不触发 VLM 重校准；但真实正负校准本来就没做过，**不得声称已校准**。
- **本轮真机出片是 BLOCKED**：本机是 RTX 5080 16GB（无 4070 Super 12GB），且官方 W6A8 权重
  未就位（候选不进自动下载路径）。因此**没有**任何 attempt / artifact / 显存峰值 / 解码 /
  人工观感证据，**不得声称任何真实出片结论**。
- 证据：`docs/ACCEPTANCE_20261109_W6A8_LOWEND_VARIANT.md`；回归
  `backend/tests/test_variant_binding_and_gate.py`（21 项）、
  `frontend/src/test/variantCandidate.test.tsx`（4 项）。

## 2026-11-09 复验收尾轮（R01–R07：入口与状态交接）

- **当前制作依据只有一个解析口**。`shine/director/basis.py` 是「这次制作属于哪一版剧本」
  的唯一答案：显式选中的文档、该文档下已确认的大纲，以及镜头**真正来自哪一版大纲**
  （分节运行 → 分镜应用回执 → 镜头）。普通按镜头分组、按场次物化和四阶段按卷共用它。
- **按镜头分组必须记录真实来源**。兼容入口不再留空来源：它记录镜头实际来自的大纲，
  所以「A 的镜头重新分组」不会被无声认作 B。旧行有可信回执证据就自动衔接；
  证据缺失只在旅程里指出现有的「确认大纲 / 重新拆分镜」，不新增审批层。
- **生产准入读「已核验」，不读「已发现」**。`profile_gate` 现在区分
  `PROFILE_WEIGHTS_NOT_AVAILABLE`（没就位）、`PROFILE_WEIGHTS_NOT_VERIFIED`（没逐字节核验）
  和 `PROFILE_WEIGHTS_NOT_CONSUMABLE`（已核验但当前受管实例不消费那个目录）。
  `prepared` 只用于显示。已核验缓存、实际绑定目录和当前实例是三件事：
  切换受管实例后重新判断消费关系，正常提交不反复全量哈希。
- **导演包与采用按执行合同判定**。导演包的 `output` 走既有 `OutputSpec`：
  24 fps、成对的 32 倍数画幅、4–15 秒片段；不合规时草案可以显示缺项，
  确认与开拍按 `OUTPUT_SPEC_INVALID` 拒绝。片段长度还要装得下每一镜的对白与内部剪辑点
  （`CLIP_TOO_SHORT_FOR_DIALOGUE` / `CLIP_TOO_SHORT_FOR_CUT`）。
  素材权利撤权后，采用预览必须显示 `ASSET_RIGHTS_NOT_APPROVED`、采用不得写新 revision，
  已冻结的包不得继续显示可开拍。
- **一键开拍判定整批，不只第一镜**。提交带 `expected_shot_revisions`（前端看到的镜头修订）
  与显式授权的绑定；服务端先核对快照，再在同一事务写绑定，并对**每一镜**跑单镜入口
  用的静态判定。任一镜不可执行 → `PRODUCTION_SERIES_SHOT_NOT_EXECUTABLE`，
  列出该镜原因与仍可开拍的镜头；不留半写绑定，也不产生意外 Job。
- **拍摄归属只有一个答案**。`shine/comfyops/ownership.py` 回答「这一镜现在归谁」：
  生产序列的预留（哪怕还没建 Job）与活动 Job 都算占用。单镜、Series 与 Run 派发共用它；
  独立请求撞上活动拍摄 → `SHOT_ALREADY_IN_PRODUCTION` 并指出原任务；
  内部派发按自己持有的预留建 Job；同一请求身份（`request_key`）重接已有回执。
- **首次向导读真实回执**。计划状态不是真相，写入物理目标的尝试才是：
  运行中的尝试在向导原地续上并显示进度，已完成的沿原步骤继续准备实例，
  真正需要对账的才交给专业设置；向导与基础配置共用同一套回执读取。
- **旅程范围跟随当前 Run**。选集与镜头来自 Run 冻结的 scope/episode 行；
  整包概况是独立字段（`package_scope`），进度分母、可操作 Run、后期与下载用同一选定范围。
  切换包/作品/Run 时丢弃旧异步结果。
- 回归：`backend/tests/test_director_basis_grouping.py`、
  `test_production_gate_weight_evidence.py`、`test_director_execution_contract.py`、
  `test_one_click_batch_admission.py`、`test_shoot_ownership.py`、
  `test_director_run_scope.py`；前端 `frontend/src/test/realWizard.test.tsx`（+2）。
  报告见 `docs/REACCEPT_R01_R07_REPORT_20261109.md`。

## 2026-11-10 成片库、真实文件名与采用目录

- **成片库是既有台账的只读视图，加上一条受保护的删除通道**。`GET/POST
  /api/v1/projects/{id}/video-library` 只聚合 `media_artifacts` 的 `video` 与 `delivery`：
  同一 artifact 的多个交付回执只出现一次，缩略图与连续性派生图不是成片条目。
  采用判定来自 `shine/media/video_layout.py`：显式装配选择优先，否则最新的正常通过生产；
  只有“选为当前装配版本”才会让重拍成为当前，重拍在选中前是历史。
- **删除是墓碑，不是改写历史**。迁移 `f180b0070001` 增加 `deleted_at/deleted_by/deleted_reason`；
  服务端只接受本项目 artifact ID，逐项阻止活动/待核对任务、当前采用、被有效成片或连续性关系引用、
  哈希不一致与路径越界；同名共享文件只移除记录保留字节；文件已缺失可清理记录。
  Job、attempt、交付回执与审计保留原 artifact 身份；媒体接口对已删除视频返回 404，
  交付列表带 `artifact_deleted`。
- **真实文件名即磁盘文件名**。`shine/media/video_naming.py` 生成
  `《项目名称_003镜_02集》.mp4` / `《项目名称_001-006镜_02集》.mp4`；无集号写 `未分集`，
  范围不明写 `镜号未登记`；非法字符、Windows 保留名、同名冲突（`_v2`）与 Unicode 都在此处理。
  SHA-256 仍是身份，旧文件不改名、不批量迁移；只有用户显式选择重拍时才移动相关文件。
- **采用与未采用分目录**：新单镜采用版本写 `artifacts/<项目>/current/`，重拍先写 `history/`，
  显式选中时两文件互换并同步 attempt 输出路径与连续性来源路径（提交失败回滚文件移动）；
  新的自动交付写 `delivery/current/`，手工装配写 `delivery/history/`。
  库页的「整理采用目录」是唯一会批量移动既有文件的动作（Operator，只移动、不删除、不改 SHA，
  失败逐条报告）；普通加载与生产不自动迁移。正式六镜、三镜与 v2 不得在验收中删除或迁移。
- **视频封面取 1 秒代表帧，封面失败仍可播放**。`thumbnail(..., seek_s=1.0)` 由视频接口使用，
  图片接口保持首帧；封面 URL 带 `?poster=1s` 以绕过旧的 immutable 缓存。
  `VideoPreview` 封面失败时内嵌 `<video>`，不显示死帧“预览不可用”。
- **单张普通参考图 + 已选 Ref2VA 不再被推断成 I2VA**（`make_render_request` 的 `mode_hint`）；
  显式工作流/检查点、无方案推断与帧角色校验不变。
- 回归：`backend/tests/test_video_library.py`（13 项），以及 `test_delivery_assembly`、
  `test_delivery_orders`、`test_generation_segments`、`test_job_media`、三个迁移测试；
  前端 `videoLibrary.test.tsx`（8 项）。运行依赖真实 HTTP 的测试时，若本会话 `NO_PROXY`
  含 `[::1]`，先 `env -u NO_PROXY`，否则 httpx 解析失败会被误判为发布失败。
- 本轮修改了 `worker.py`，按 `client_review_identity` 约定旧 VLM revision 失效；真实正负校准
  仍未完成，不得声称已校准。两镜试拍与新单镜均为技术通过、`final=needs_review`，不是人工验收。

## 2026-11-10 长文本 2/3 分块与手工镜头英文风格投影

- **长文本按原尺寸的 2/3 切分**：大纲 `segment_document(..., max_scene_chars=MAX_SCENE_CHARS * 2 // 3)`
  （4000→2666），长文本分镜 `max_scene_chars=1200`（原 1800）。切分仍由
  `shine/materialprep/source_spans.py` 的 `segment_document` 完成，不新建分块器。
- **预算控制器统一输入与输出**：`budget_report(..., input_share=2/3)` 用同一份
  `context_window_tokens`、输出预留与 margin 计算可用预算；`scenes.prepare_scenes` 用
  `input_share=2/3` 决定是否继续二分。不要另建一套 token 估算。
- **手工镜头的中文冻结风格要被投射成英文**：`services.make_render_request` 在
  `shot_spec.prompt_style` 为空且风格含 CJK 时，用
  `shine/director_styles/service.py::english_style_bible` 读取冻结模板的
  `instruction_en`，把结果附着到**请求的 shot_spec.prompt_style**（不是替换 style
  snapshot），让 `H3_ENGLISH` 与 `SHINE_PROVENANCE` 同时通过；冻结风格 id/hash 不变。
  不要只在编译时临时换 style，否则校验器重编译会报 provenance 不一致。
- **新登记方案必须先在本机验证**：`PROFILE_NOT_VALIDATED` 时先到「基础配置 → 模型与生成方案」
  对本项目方案点「验证本机节点」（真实渲染器 object_info），再排产；不要用 API 绕过。
- 回归：`backend/tests/test_material_budget_share.py`（2 项）、
  `test_director_styles.py::test_english_style_projection_uses_the_frozen_template_techniques`。

- **新提交不得继承旧输入的 UNKNOWN**：`prepare_scenes` 的旧格式兼容查询只允许
  `request_identity is None` 的旧行按文档/场次复用；带身份的 UNKNOWN 属于它自己的输入
  （不同的锁定事实、提示词或 provider 就是另一份请求），新提交必须真正发出新请求。
  回归见 `test_material_scenes.py::test_a_new_scene_input_does_not_inherit_an_unknown_from_a_previous_input`。

## 2026-11-10 影片类型与手动解锁/应用新冻结（本轮新增）

- **影片类型是服务端固定的 H3 合同，不是自由文本**：`StyleBible.film_type` 只接受
  `live_action` / `animation`；固定英文范例在 `shine/h3/film_type.py`，由
  `H3PromptCompiler` 写进每条提示词。`The entire shot is 2D cel-shaded animation...`
  这类句子不得交给模型自由改写；动画项目不选影片类型，模型可能自行拍成真人画面。
- **旧风格快照的哈希形状不能变**：`film_type` 未设置时序列化必须省略该键。Pydantic 的
  `model_config.exclude_none` 在本版本不会作用于 `model_dump`，因此 `StyleBible` 用
  `model_serializer` 过滤 `None`；嵌套 `ShotSpec.prompt_style` 也一样，旧风格/旧请求哈希
  保持可复验。语义审片仍只审七维（`STYLE_BIBLE_FIELDS`），影片类型是确定性提示词合同。
- **手工镜头与分镜镜头的英文风格都要继承冻结影片类型**：`_prompt_style` 为旧
  `prompt_style` 补上冻结的 `film_type`；`services.make_render_request` 的英文风格投射
  也显式带上它。不要只改 style snapshot，否则请求与编译哈希不一致。
- **每个冻结设置都有用户手动解锁入口，且旧历史不重写**：
  `POST /projects/{id}/freeze/style/refresh` 冻结当前草稿并把所有旧镜头修订到新风格；
  `POST /projects/{id}/freeze/shots/style` 只把当前已冻结风格应用到旧镜头；
  `POST /projects/{id}/freeze/assets/{asset_id}/unlock` 解除主素材的 approved/locked，
  修改后走既有「确认使用」重新冻结；`POST /projects/{id}/freeze/episode-plans/{version}/unlock`
  把已确认分集退回 DRAFT 并将其已确认导演包标记 stale；
  `POST /projects/{id}/freeze/director-packages/{package_id}/unlock` 只把包标记 stale，
  不重写已确认快照。
- **有活生产时拒绝解锁**：风格/镜头/素材/分集/导演包的解锁都先查活动 Job、活动 series 或
  活动 run；返回具名 409，不打断正在上传或生成的镜头。旧产物、旧风格版本、旧导演包
  全部保留，重拍/重冻产生新 revision/version。
- **素材候选列表缺省不是丢失**：直接上传并确认的素材没有生成批次，素材页的
  「③ 选择素材 · 0 项候选」是真实事实；页面现在明确提示「先在 ① 分析剧本、② 确认需求」，
  不伪造候选，也不把直接上传的主素材混进候选网格。
- 回归：`backend/tests/test_freeze_unlock.py`（4 项）、`test_h3_compiler.py` 的影片类型/
  旧哈希形状、`test_director_styles.py` 的固定类型目录；前端
  `frontend/src/test/directorStyle.test.tsx`、`styleFreeze.test.tsx`。改动未触及
  `worker.py` / `preproduction.py`。

## 2026-11-10 输出目录与可读文件名轮

- **全机只有一个输出根目录**。未设置时沿用现有默认目录 `settings.data_dir`；设置后所有项目的
  素材、视频、成片、音色都存放两级：`<根目录>/<项目名>/素材|视频|音色/...`。无项目的全局素材
  放在 `<根目录>/素材中心/素材|视频|音色/...`。解析入口唯一：`shine/media/output_layout.py`
  的 `output_root(db, settings)`；**不要**在别处再拼一套目录规则。
- **文件名统一是 `项目_类型_名称.ext`**：项目、类型（素材/视频/音色）、可读名称。镜头视频为
  `项目_视频_003镜_02集.mp4`，成片为 `项目_视频_001-006镜_02集.mp4`，素材为
  `项目_素材_01-易千寒.png`，音色为 `项目_音色_易千寒.wav`；素材中心为
  `素材中心_素材_...`。不再使用《》或内容哈希作为磁盘文件名；哈希仍是台账身份。
  同名不同内容用 `_v2`、`_v3` 区分，绝不覆盖。
- **调整目录是一次迁移，不是改一行设置**。`PUT /api/v1/settings/output-directory`：
  先拒绝活动镜头/素材任务（`OUTPUT_DIRECTORY_BUSY`），写迁移锁；逐文件复制（同卷优先硬链接）
  到新根并逐字节核验台账 SHA-256；全部通过后才在**一个事务**里切换 `storage_path`、
  `JobAttempt.output_path`、连续性来源路径和 `output-directory` 设置；提交后才删除旧文件。
  任一步失败都不切换目录（`OUTPUT_DIRECTORY_SOURCE_INVALID` / `TARGET_CONFLICT` / `COPY_FAILED`），
  已复制的新文件只是无害的孤立副本。迁移锁期间的写入返回 `STORAGE_MIGRATION_IN_PROGRESS`。
- **请求里的旧路径不是身份**：移动根目录后，已存 `job.request` 的 `storage_key` 会过时。
  读取方必须调用 `refresh_reference_paths(db, request)`（worker 装载上下文、审片队列都用它）；
  H3 引用校验只按 SHA-256 + content_type 认身份，**不再**把 `storage_key` 与
  `asset.storage_path` 的不等视为身份变化。旧 Job 的 request 不被改写。
- **默认目录也使用同一套命名**：新产物即使没有自定义目录，也写入
  `<data_dir>/<项目>/素材|视频|音色`。旧文件不会自动搬动；把目录保存为默认（空路径）或
  任意自定义目录都会把旧文件迁移进新布局。旧历史字节不重写。
- **页面入口**：导演模式「基础配置 → 安装与下载」和专业设置都渲染 `OutputDirectoryPanel`，
  显示当前根、两级结构、文件数、可用空间和活动阻塞；不存在只改设置不迁移的路径。
- **迁移不信任未核验的共享文件**：`LibraryMedia` 与 `LibraryVersion` 可能共享同一物理文件，
  迁移按台账逐行分配可读名；同名同哈希复用，同字节不同名各复制一份。候选删除前会再查
  `JobAttempt`、连续性 lineage 和全部 `storage_path` 列，仍在引用的旧文件不删。
- 本轮改了 `worker.py`（输出根解析、引用路径刷新），按 `client_review_identity` 约定旧 VLM
  revision 已失效；真实正负校准**尚未**重做，不得声称已校准。
- 回归按影响范围定向执行：`test_output_directory.py`（9 项）、`test_asset_center.py`、
  `test_workflow.py`、`test_security_windows.py`、`test_material_image_jobs.py`、
  `test_offline_e2e.py`、`test_video_library.py` 共 102 项通过；另跑媒体/系列、素材/剧组、
  参考/门禁、迁移/模式四批定向用例。按用户要求**不跑全量后端套件**。前端
  `OutputDirectoryPanel`、`npm.cmd run build` / `npm.cmd test`。证据见
  `docs/ACCEPTANCE_20261110_OUTPUT_DIRECTORY.md`。

## 2026-11-11 穿测修复轮（限流、宿主代理、素材缺口、崩溃遗留任务）

浏览器实测《放开那个女巫1-10章》④ 分镜编写阶段暴露了七个缺陷，全部已修：

- **服务商 429 不再直接判死一场分镜**：`openai_compatible._request` 过去对 POST 的 429
  立即抛出，一次限流就让整场失败。现在按 `Retry-After`/指数退避重试
  （`RATE_LIMIT_BACKOFF_SECONDS`），并把冷却记在**同一端点**上（`note_rate_limit`），
  后续调用共享等待。预算由 `ProviderConfig.rate_limit_retry_attempts` 决定：
  **agent 通道 2 次**（`AGENT_RATE_LIMIT_RETRY_ATTEMPTS`），连通性探测仍是 0 次。
  429 是「拒绝且没有答案」，因此重试不会买到第二份答案；5xx 与超时的未知结论仍不重放。
- **剧组链的重试要带具名原因**：`RETRYABLE_REJECTIONS` 增加 `LLM_RATE_LIMIT` 与
  `LLM_FORMAT`；合同拒绝（对白非原文、格式不符等）重试时由
  `CONTRACT_REPAIR_NOTES` 给出可执行的做法，而不是让模型原样再错一次。
  纠错说明只写规则与做法，**不携带服务商文本**。
- **宿主代理环境不得打倒本机客户端**：`[::1]` 这类带方括号的 `NO_PROXY` 条目会让 httpx
  拒绝建客户端（`InvalidURL: Invalid port: ':1]'`），此前只有文字通道规整过，渲染器、
  本地文本模型、实例健康检查仍会失败。规则统一到 `shine/http_env.py`：
  **环回端点一律 `trust_env=False`**，远端调用先规整 `NO_PROXY`。
- **素材缺口不是连续性冲突**：`shot_reference_policy` 现在区分「策略违规」与
  「素材请求」：`policy_compliant` 只由被禁用的引用决定，`material_request` 列出还缺的
  主素材。连续性报告里只报告素材缺口的条目由 `material_request_issues` 判为素材请求，
  **不进仲裁也不阻塞单元**（与 2026-11-08 的「素材是可选类需求」一致）。
- **崩溃遗留的剧组任务必须能核对**：服务商调用活不过它自己的超时，因此超过该超时仍处于
  `RUNNING` 的剧组任务属于已死进程：请求阶段证明没发出的按 `NOT_SENT_PROVEN` 释放，
  可能已发出的保持 `UNKNOWN_REQUIRES_NEW_ATTEMPT`，由
  `POST /projects/{id}/film-runs/{task_id}/resend-unknown` 记录人的明确授权
  （旧行退役为 `USER_AUTHORIZED_NEW_REQUEST`，**不得**写成「未发出」）。
- **核对必须幂等**：结论一旦写入就不可再被版本挡住（已核对的结论在版本检查之前返回）；
  请求开始前的「先前未决任务」拒绝必须带上 `state_version`，否则面板永远重做不了这一场。
- **面板要能解释**：剧组的拒绝原因、素材提示与缺项都在 ④ 面板如实显示；镜头描述含中文被
  拒时，重试的纠错说明要求用角色标签代替中文专名。
- 回归：`backend/tests/test_agent_retry_and_rate_limit.py`（新增）、
  `test_film_unknown_outcomes.py`（新增 4 条）、`test_storyboard.py`（新增 2 条）、
  `test_llm_setup_contract.py`（NO_PROXY 断言迁到共用模块）。

## 2026-11-12 素材分析进度透传与 UNKNOWN 分场授权重发（穿测缺陷修复）

- **逐场素材分析的进度必须从台账透传，而不是只活在浏览器内存里**。`POST
  /material-preparation/scenes/prepare` 是同步长请求；旧面板把返回的 `scenes`
  存在本地 state，刷新即丢，运行中也没有逐场进度。新增只读
  `GET /projects/{id}/material-preparation/scenes/status?document_id=`
  （`scenes.scene_status`，按分割顺序返回每场最新任务的状态/phase/外部结果/
  核对结论），面板挂载即读、分析期间 5s 轮询；分析进行中禁用「分析剧本」，
  避免并发重复提交。读路径不发模型调用、不建任务。
- **素材分场的 UNKNOWN 也要有用户授权的一次性重发入口**。发送后断连
  （`LLM_CONNECTION`/UNKNOWN）的分场此前既不能 dead-worker 退休（状态是
  NEEDS_REVIEW 而非 RUNNING），reconcile 又只回 UNCONFIRMABLE，story 的
  resend 入口不接收素材任务——永久阻塞。新增
  `POST /projects/{id}/material-preparation/scenes/{task_id}/resend-unknown`
  （`scenes.authorize_scene_unknown_resend`）：镜像 story 语义，一次、带证据、
  审计 `MATERIAL_SCENE_UNKNOWN_RESEND_AUTHORIZED`，旧行退役为
  `USER_AUTHORIZED_NEW_REQUEST` 且**保持 UNKNOWN，不得写成未发送**；预算按
  同 input_hash 计，第二次授权 409 `MATERIAL_SCENE_UNKNOWN_RESEND_ALREADY_USED`；
  下一次「分析剧本」才创建那一次新请求。
- 回归：`backend/tests/test_material_scene_progress.py`（3 项）+
  `test_material_scenes.py`/`test_scene_dead_worker.py`/`test_material_scene_safety.py`
  共 25 项；前端 `materialPreparation.test.tsx` 17 项、全量 261 项、tsc/build。
- **分镜输出上限 16K→32K（2026-11-12 用户裁定）**：长分镜答案在 16K 处被
  `finish_reason=length` 截断、重试表现为「卡」。`DEFAULT_AGENT_OUTPUT_TOKENS`
  提至 32_768（agent 通道默认 ceiling；显式配置更小仍优先；创作通道
  `literary.budget` 不受影响）。回归 `test_llm_gateway.py` 等 6 批 100 项。
- **分集勾选只做一次（2026-11-12 用户裁定）**：四阶段分集合同已确认的作品，
  素材页内嵌的 `StoryPreparationPanel` 不再显示旧链路的生产段落勾选网格与
  「准备所选段落的素材清单 / 为所选生产段落生成正式分镜」按钮，改为只读
  「总体大纲与分集范围 · 已确认（无需再勾选）」摘要（逐集列出已确认场次）；
  范围由 `GET /projects/{id}/pipeline` 的 confirmed episode_contracts 推导。
  无合同的作品保留旧勾选入口。回归 `materialPreparation.test.tsx`（+2）。
- **素材清单复用四阶段分析，零模型调用（2026-11-12 用户裁定）**：剧组链已逐场
  读过合同场次，二次整剧逐场分析是重复购买。新增
  `POST /projects/{id}/material-preparation/derive`（`materialprep/derive.py`）：
  只认**当前有效**的已确认分集合同，按 `source_refs` 偏移把场次映射到素材分割，
  人物取自场次剧本 characters+对白说话方（描述用全局圣经 identity），场景描述
  取场次原文开头；产出同一 ScriptPreparation 合同进 `create_draft_plan`，确认/
  出图门禁不变。无合同的作品保留「分析剧本」旧入口。面板有合同时主按钮为
  「复用分镜分析生成素材清单」，隐藏二次分析入口。回归
  `test_material_derive.py`（2 项，含 forbidden responder 证明零调用）、
  `materialPreparation.test.tsx`（+1）。
- **旧四步层归并进七阶段（2026-11-12 用户裁定）**：制作页删除旧「①剧本分镜
  ②素材准备③排产制作④进度与结果」聚焦条与③④两个 dock，只保留③镜头拆解、
  ④拍摄素材准备两个工作区；排产（一键开拍/单镜高级）与进度结果整体移入
  ⑥视频生产页（run），与整部作品运行面板同页；旧深链 `#d/produce?stage=produce|monitor`
  重定向到 run。七阶段旅程条是唯一阶段导航；rail 主/次按钮自动共存不丢入口。
  回归 `produceFocus.test.tsx` 重写、`directorEntries/modeEntry/shotDraft` 导航更新，
  全量 265 项。
- **素材清单全项目统揽（2026-11-12 用户裁定）**：逐场拼名会把同一人拆成多个
  主素材、前后不一致。新增 `materialprep/catalog.py`：一次全项目模型调用
  （producer，合同 `MaterialEntityCatalog`）读全局圣经+全部合同场次，产出
  人物唯一条目（aliases 合并、identity 稳定、variants 按场记衣服/外貌变化）、
  关键场景、关键道具（core_prop_reason+carrier）。目录按 basis（圣经+场次剧本
  哈希）落 artifact，basis 不变复用、不买第二次调用；UNKNOWN 不自动重放，
  核对/授权重发入口与分场同语义（守卫接受 producer/entity_catalog）。derive
  从目录映射到素材分割生成清单；人物描述只用稳定 identity（变体在目录与镜头
  视觉描述里），避免跨场描述差异造成假冲突。回归 `test_material_derive.py`
  重写 3 项（含 UNKNOWN 核对+一次重发）、前端 265 项。
- **素材小样必须携带冻结影片类型（2026-11-12 穿测目检发现）**：动画项目的
  参考图曾出成写实照片——brief 只拿到中文七维+film_type 代码，模型漏了媒介。
  `image_prompts` 升 v5：compose 接收 `film_style`（`h3.film_type.film_type_sentence`
  的固定英文句）作为提示词层，动画/实拍各加对应 negative 排除；brief 上下文加
  `film_style_instruction` 与媒介规则。模板版本绑定 readiness 指纹 → 升版后必须
  重新「验证三类真实小样」再出图。回归 `test_material_no_overlay_rule.py`（+1）。
- **人物定义全貌化（2026-11-12 用户裁定）**：程岩即罗兰·温布顿，素材中心必须
  拿到准确的全貌定义而非碎片短名。统揽提示词要求 name 用后续通用完整姓名、
  identity 写全貌（含穿越背景）、aliases 收本名/简称、variants 按场记外貌；
  目录复用键加入 prompt_sha256（提示词改进即重新统揽）。derive 把
  character_definition（canonical_name/aliases/identity/variants）附着到人物
  需求 payload 与计划；绑定键 entity_id 仍用镜头用名（零绑定风险）。新增
  `GET …/material-preparation/entity-catalog` 只读接口；素材中心展示规范全名+
  全貌+按场变体；晋升时定义随主素材 provenance 入库。回归 test_material_derive（+断言）。
