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
