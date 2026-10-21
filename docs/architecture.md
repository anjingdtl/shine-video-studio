# Architecture — M1 hardened + M2/M3 offline + final offline hardening

Shine is a local modular monolith with one FastAPI application, one SQLite database in WAL mode, content-addressed local files, a React client, and a separately restartable Python worker. The API and Vite server default to `127.0.0.1`.

## State ownership

- **Project core** owns projects, imported script documents, shots, revisions, per-shot asset bindings, and the per-project quality policy.
- **Asset registry** stores user uploads under SHA-256-derived paths. It records media type, MIME signature, byte size, digest, role/entity/tags, probed duration/resolution, approval, lock, and timestamps. Upload never approves. A shot binding and a render request both require approved + locked assets in the same project.
- **Style registry** stores the editable draft and immutable frozen versions. A new shot defaults to the current frozen version. Revising an existing shot may keep its original version. Creating a render job requires the current frozen version. Freezing a new version records the hash/time/approver, returns impact analysis, and moves unstarted/stale work using the previous version to `NEEDS_REVIEW` with `STYLE_VERSION_SUPERSEDED`. Passed historical jobs are preserved.
- **Agent boundary** (`shine/agents`) accepts a structured-model port and has no database session, filesystem, shell, or approval tools. Planning services build a sanitized `ProjectContext` (asset IDs, filenames, roles, tags; no storage paths) and re-validate every proposal against current project state.
- **LLM gateway** (`shine/llm`) provides OpenAI-compatible, Anthropic Messages, Ollama-compatible, and Mock providers behind one port. Provider profiles and role routes live in SQLite; API keys live outside SQLite in DPAPI (Windows) or a `0600` file (other hosts). Errors are classified as auth, model-not-found, rate-limit, timeout, connection, server, or format. JSON is parsed and validated against the caller's schema.
- **H3 prompt compiler** (`shine/h3`) is the only path from structured ShotSpec to prompt text. It pins the official `h3-prompt-writing` skill hashes, compiles T2VA/I2VA/FL2VA/L2VA or the six-section Ref2VA format, validates labels/counts/durations/dialogue **and the frame-role contract of each mode**, and records compiler/rule versions and a prompt hash in the immutable render request.
- **Render boundary** has two explicit providers:
  - `mock`: scheduling/QC simulator; never creates a video or artifact.
  - `comfyui`: HTTP adapter for a user-registered API workflow profile. It prepares the prompt (uploading the request-local references through a per-family media transfer adapter), persists the submit intent, submits `/prompt`, persists the external `prompt_id`, downloads `/view` output, and records it as a non-mock artifact only after storage.
- **Orchestrator/worker** is the sole writer of job transitions. It records dependencies, idempotency keys, `SUBMIT_INTENT`, lease owner/expiry, state version, provider acknowledgement, QC gates, repairs, reconciliation, and audit events.
- **Quality engine** (`shine/quality`) separates deterministic technical checks from probabilistic VLM/ASR findings. Technical QC never claims a decode without a real FFmpeg decode. Five named gates (`technical`, `semantic`, `audio`, `style`, `final`) are evaluated and persisted per attempt; a shot is final-passed only when every required gate passed. The repair engine classifies root cause, builds an authorised **request revision**, enforces the retry budget, protects passed versions, and escalates hard/creative problems.

## Request and job flow

```text
ShotSpec + per-shot bindings
  → approved/locked asset validation
  → request-local reference manifest (Subject/Picture/Video/Audio numbering)
  → H3PromptCompiler (official format + frozen Style Bible)
  → RenderRequest (hashes, labels, compiler/rule versions)
  → Job (QUEUED or dependency-gated PLANNED)
  → Worker conditional claim + SUBMIT_INTENT + lease
  → reference transfer (per-family media adapter, hash/approval re-check)
  → durable submit intent (prompt graph hash) → /prompt
  → external prompt_id persisted
  → artifact download + atomic storage (ComfyUI only)
  → technical QC + named quality gates
  → PASSED / request revision + new attempt / NEEDS_REVIEW
```

A shot revision marks its prior jobs stale and invalidates only downstream jobs that consume them. Independent shots keep running. The worker re-checks job status, state version, lease owner, and shot revision before writing any result; a stale render is discarded rather than written into the current library.

## Reference media transfer

Each manifest entry owns a deterministic logical slot (`reference_subject_1`,
`reference_picture_1`, `reference_video_1`, `reference_audio_1`, …). A slot
without a `parameter_map` entry blocks the render with
`PENDING_WORKFLOW_MAPPING`; references are never dropped silently.

`shine/comfy/media.py` models one `MediaTransfer` per media family
(`upload_path`, `field_name`, `subfolder`, `extension`, transport style and the
loader node classes it needs). Image uses ComfyUI's documented
`/upload/image` default. **Video and audio have no default**: the workflow
profile must declare how they reach the H3 nodes, because custom nodes do not
share one upload interface. The file extension always comes from the asset's
content type, never from the content-addressed storage key.

Before every upload the master asset is re-verified (approval, lock, project
scope, manifest hash, on-disk digest, media-type/role compatibility).

## Persistence

SQLAlchemy models have Alembic migrations under `backend/alembic/versions`. Runtime startup does not create tables implicitly. SQLite connections enable WAL, foreign keys, `busy_timeout=10000`, and `synchronous=NORMAL`.

Tables include:

`projects`, `project_documents`, `assets`, `style_versions`, `shots`, `shot_asset_bindings`, `jobs`, `job_dependencies`, `job_attempts`, `qc_reports`, `quality_gates`, `media_artifacts`, `repair_records`, `provider_profiles`, `agent_routes`, `workflow_profiles`, `external_job_reconciliations`, `app_settings`, `worker_instances`, and `audit_events`.

The M2/M3 migration uses direct `ALTER TABLE ADD COLUMN` rather than SQLite batch recreation because batch `DROP TABLE` on a parent with `ON DELETE CASCADE` can silently delete child rows. The final hardening migration `c3d81f6a2b45` follows the same rule: it only adds columns and one new table (`quality_gates`). The upgrade path from `70afa9c775ab` and from `9f2c4d7a10b3` preserves existing projects/assets/styles/shots/jobs.

## Scheduling and recovery

- Claims use a conditional `UPDATE ... WHERE status='QUEUED' AND NOT stale`; `state_version` increments on every transition.
- The worker persists `SUBMIT_INTENT` **before** the external call, including the hash of the exact prompt graph it is about to send.
- A connection failure is a safe failure (`COMFY_UNAVAILABLE`). A request that may have been received is an *uncertain* outcome (`COMFY_SUBMISSION_OUTCOME_UNKNOWN`) and becomes `NEEDS_REVIEW`; it is never blindly re-submitted.
- A lost acknowledgement is recovered by matching the persisted prompt-graph hash against `/history`; an unmatched job stays `NEEDS_REVIEW`.
- ComfyUI recovery queries `/history` and `/queue`: running work gets a renewed lease; **completed work is downloaded, stored atomically, hash-verified and re-checked automatically**; missing history becomes `EXTERNAL_JOB_NOT_FOUND`; an unreachable ComfyUI becomes `RECONCILIATION_UNAVAILABLE`.
- Recovery takes the job with a compare-and-set, so a second worker can never take over the same task.
- Mock recovery can complete a mock QC record only when an acknowledgement was persisted, and only for the current shot revision.
- Two workers cannot claim the same job; the second conditional update returns zero rows.
- Failed submissions, uncertain submissions, execution errors, output errors, missing node classes, workflow version mismatches, and infrastructure exceptions are separate outcomes with separate error codes.

## Quality and repair

- **Technical QC:** file existence, size, container structure, stream metadata, full FFmpeg decode, duration tolerance, resolution, frame rate, audio stream presence, silence detection, SHA-256. Probing metadata is explicitly *not* a decode. Missing tools produce `unavailable`, not `pass`.
- **Semantic QC:** typed findings with code/category/severity/confidence/evidence time. Mock reviewers never inspect pixels. A remote VLM/ASR requires project consent and receives only derived evidence frames/audio, never master assets by default.
- **Gates:** `technical`, `semantic`, `audio`, `style` and `final` are persisted per attempt with their status, requirement flag, model, confidence, error code, coverage and evidence. The per-project quality policy may relax non-critical thresholds per shot priority; hard rules are never relaxed.
- **Repair:** classifies reference/dialogue/prompt/workflow/transient/execution/review errors, limits semantic retries to two, and produces an authorised **request revision** — a recompiled prompt, a rebuilt reference manifest, or changed generation parameters. A revision that would not change anything is refused and escalated. A repeated root cause escalates. Repairing an upstream job invalidates its dependants so their validity is recomputed.

## Security and limits

- API/Vite bind to loopback. There is no authentication; do not expose to LAN.
- The ComfyUI renderer URL must be loopback unless `SHINE_ALLOW_REMOTE_COMFYUI=1` is set explicitly, because reference bytes are uploaded to it.
- Uploads sanitize POSIX and Windows separators, reject Windows-invalid characters, check extensions/signatures, enforce a configurable byte limit, probe media facts, and write through an atomic temporary file. Disk-full returns 507.
- Provider keys are not stored in SQLite, logs, audit payloads, or API responses. The key store mode is reported (`dpapi` or `file-0600`).
- Audit payloads redact credential-like keys/values and absolute local paths.
- Agent routes can call only the LLM gateway; they cannot approve assets, freeze styles, write the database, run shell commands, or read arbitrary paths.
- Mock/Fake HTTP success is always labelled `is_mock`/`simulated`/`mock_verified`. Real artifact QC is labelled `is_mock=false` and only exists after a stored ComfyUI output.
