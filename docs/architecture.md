# Architecture — M1 hardened + M2/M3 offline

Shine is a local modular monolith with one FastAPI application, one SQLite database in WAL mode, content-addressed local files, a React client, and a separately restartable Python worker. The API and Vite server default to `127.0.0.1`.

## State ownership

- **Project core** owns projects, imported script documents, shots, revisions, and per-shot asset bindings.
- **Asset registry** stores user uploads under SHA-256-derived paths. It records media type, MIME signature, byte size, digest, role/entity/tags, probed duration/resolution, approval, lock, and timestamps. Upload never approves. A shot binding and a render request both require approved + locked assets in the same project.
- **Style registry** stores the editable draft and immutable frozen versions. A new shot defaults to the current frozen version. Revising an existing shot may keep its original version. Creating a render job requires the current frozen version. Freezing a new version records the hash/time/approver, returns impact analysis, and moves unstarted/stale work using the previous version to `NEEDS_REVIEW` with `STYLE_VERSION_SUPERSEDED`. Passed historical jobs are preserved.
- **Agent boundary** (`shine/agents`) accepts a structured-model port and has no database session, filesystem, shell, or approval tools. Planning services build a sanitized `ProjectContext` (asset IDs, filenames, roles, tags; no storage paths) and re-validate every proposal against current project state.
- **LLM gateway** (`shine/llm`) provides OpenAI-compatible, Anthropic Messages, Ollama-compatible, and Mock providers behind one port. Provider profiles and role routes live in SQLite; API keys live outside SQLite in DPAPI (Windows) or a `0600` file (other hosts). Errors are classified as auth, model-not-found, rate-limit, timeout, connection, server, or format. JSON is parsed and validated against the caller's schema.
- **H3 prompt compiler** (`shine/h3`) is the only path from structured ShotSpec to prompt text. It pins the official `h3-prompt-writing` skill hashes, compiles T2VA/I2VA/FL2VA/L2VA or the six-section Ref2VA format, validates labels/counts/durations/dialogue, and records compiler/rule versions and a prompt hash in the immutable render request.
- **Render boundary** has two explicit providers:
  - `mock`: scheduling/QC simulator; never creates a video or artifact.
  - `comfyui`: HTTP adapter for a user-registered API workflow profile. It uploads request-local references, maps parameters, submits `/prompt`, persists the external `prompt_id` before polling, downloads `/view` output, and records it as a non-mock artifact only after storage.
- **Orchestrator/worker** is the sole writer of job transitions. It records dependencies, idempotency keys, `SUBMIT_INTENT`, lease owner/expiry, state version, provider acknowledgement, QC, repairs, reconciliation, and audit events.
- **Quality engine** (`shine/quality`) separates deterministic technical checks from probabilistic VLM/ASR findings. Technical QC never claims decode without FFprobe. Fusion treats low-confidence and uncertain evidence as review, never a pass. The repair engine classifies root cause, enforces the retry budget, protects passed versions, and escalates hard/creative problems.

## Request and job flow

```text
ShotSpec + per-shot bindings
  → approved/locked asset validation
  → request-local reference manifest (Subject/Picture/Video/Audio numbering)
  → H3PromptCompiler (official format + frozen Style Bible)
  → RenderRequest (hashes, labels, compiler/rule versions)
  → Job (QUEUED or dependency-gated PLANNED)
  → Worker conditional claim + SUBMIT_INTENT + lease
  → Mock provider OR ComfyUI API workflow
  → external prompt_id persisted
  → artifact download + atomic storage (ComfyUI only)
  → technical QC + optional semantic review
  → PASSED / REPAIR_PENDING / NEEDS_REVIEW
```

A shot revision marks its prior jobs stale and invalidates only downstream jobs that consume them. Independent shots keep running. The worker re-checks job status, state version, lease owner, and shot revision before writing any result; a stale render is discarded rather than written into the current library.

## Persistence

SQLAlchemy models have Alembic migrations under `backend/alembic/versions`. Runtime startup does not create tables implicitly. SQLite connections enable WAL, foreign keys, `busy_timeout=10000`, and `synchronous=NORMAL`.

Tables include:

`projects`, `project_documents`, `assets`, `style_versions`, `shots`, `shot_asset_bindings`, `jobs`, `job_dependencies`, `job_attempts`, `qc_reports`, `media_artifacts`, `repair_records`, `provider_profiles`, `agent_routes`, `workflow_profiles`, `external_job_reconciliations`, `app_settings`, `worker_instances`, and `audit_events`.

The M2/M3 migration uses direct `ALTER TABLE ADD COLUMN` rather than SQLite batch recreation because batch `DROP TABLE` on a parent with `ON DELETE CASCADE` can silently delete child rows. The upgrade path from `70afa9c775ab` preserves existing projects/assets/styles/shots/jobs and backfills `audit_events.project_id`.

## Scheduling and recovery

- Claims use a conditional `UPDATE ... WHERE status='QUEUED' AND NOT stale`; `state_version` increments on every transition.
- The worker persists `SUBMIT_INTENT` before calling a provider, and persists the external `prompt_id` separately.
- An expired `SUBMITTING`/ComfyUI submission without an acknowledged external ID becomes `NEEDS_REVIEW` with `SUBMISSION_OUTCOME_UNKNOWN`. It is never blindly re-submitted.
- ComfyUI recovery queries `/history` and `/queue`: running work gets a renewed lease; completed work requires a dedicated QC path; missing history becomes `EXTERNAL_JOB_NOT_FOUND`; unavailable ComfyUI becomes `RECONCILIATION_UNAVAILABLE`.
- Mock recovery can complete a mock QC record only when an acknowledgement was persisted, and only for the current shot revision.
- Two workers cannot claim the same job; the second conditional update returns zero rows.
- Failed submissions, semantic failures, uncertain submissions, and infrastructure exceptions are separate outcomes.

## Quality and repair

- **Technical QC:** file existence, size, container structure, FFprobe decode (when installed), duration tolerance, resolution, frame rate, audio stream presence, silence detection (FFmpeg when installed), SHA-256. Missing tools produce `unavailable`, not `pass`.
- **Semantic QC:** typed findings with code/category/severity/confidence/evidence time. Mock reviewers never inspect pixels. A remote VLM/ASR requires project consent and receives only derived evidence frames/audio, never master assets by default.
- **Fusion:** hard findings above 0.7 confidence fail; hard findings below 0.7 require review; uncertain findings require review; high-confidence soft findings require review; technical failure always wins. Coverage records whether semantic/ASR actually ran.
- **Repair:** classifies reference/dialogue/prompt/workflow/transient errors, limits semantic retries to two, auto-applies only bounded transient/prompt/workflow actions when the project enables it, and escalates creative or low-confidence failures.

## Security and limits

- API/Vite bind to loopback. There is no authentication; do not expose to LAN.
- Uploads sanitize POSIX and Windows separators, reject Windows-invalid characters, check extensions/signatures, enforce a configurable byte limit, probe media facts, and write through an atomic temporary file. Disk-full returns 507.
- Provider keys are not stored in SQLite, logs, audit payloads, or API responses. The key store mode is reported (`dpapi` or `file-0600`).
- Audit payloads redact credential-like keys/values and absolute local paths.
- Agent routes can call only the LLM gateway; they cannot approve assets, freeze styles, write the database, run shell commands, or read arbitrary paths.
- Mock/Fake HTTP success is always labelled `is_mock`/`simulated`. Real artifact QC is labelled `is_mock=false` and only exists after a stored ComfyUI output.
