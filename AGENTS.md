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
  `FAILED`（有回答但被合同拒绝）走有上限的 `FAILED_RETRY_ALLOWED`（每场 2 次）。
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
- orders 18、19、20 的子任务分别以 `LLM_TIMEOUT` / `external_outcome=UNKNOWN` 结束；没有 provider request id、HTTP 状态或 response hash。产品 `abandon-remote-response` 只可把这些丢失响应审计为 `ABANDONED`，仍是 `UNKNOWN` 且 `automatic_replay=false`。本轮由所有者委托的 `local-user` 通过一次性 `model_reconcile` grant 完成三条放弃动作；没有重发，也不得改输入另建近似任务绕过 UNKNOWN。
- `POST /episode-plans/1/materialize` 因 `EPISODE_SECTION_NOT_STORYBOARDED` 返回 409，明确列出 orders 18–20 的三个 pending section；只有确认所有段均已应用后才能 materialize、confirm v2、建 DirectorPackage 和 ProductionRun。当前 `/journey` 为 54 project shots / 0 planned shots；本轮没有新 H3 attempt。台账保留上轮唯一真实 job `aa8baa09-0c1f-4627-a874-193a1a3647e1`（`PASSED`、`quality_verification=real`，artifact SHA-256 `92b862e64eb7b4e31b2961c455503ff1f0b8fc4bf7163f184ea2bef8a718a190`、`last_qc_result=needs_review`），不代表整片完成或 accepted。
- 本轮没有改源代码或 `worker.py` / `preproduction.py`，R7 pytest/Ruff 报告仍是旧证据；VLM 正负校准继续未完成。H3 renderer 未启动。工作台按 `scripts/stop_shine.py --data-dir .local-acceptance/live-browser` 停止，端口 8000/5173/8188 均已验证无监听。
- 本轮完整续跑及真实边界见 `docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §7 与 `docs/TEST_REPORT_20261107_DIRECTOR_JOURNEY_R7.md` §5。
