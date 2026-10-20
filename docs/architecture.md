# M0/M1 architecture

Shine is a local modular monolith with one FastAPI application, one SQLite database in WAL mode, content-addressed local asset files, a React client, and a separately restartable Python worker. The API and Vite development server default to `127.0.0.1`.

## State ownership

- **Project core** owns projects, shots, and shot revisions.
- **Asset registry** stores uploaded files under SHA-256-derived paths. The database records media type, byte size, digest, approval, and lock state. Upload never approves an asset. A user must approve and then lock it before a shot or render request can use it.
- **Style registry** stores the editable project draft and immutable frozen versions. Freezing validates all seven required dimensions, stores a canonical JSON snapshot and SHA-256, and updates the project's current frozen version in one SQLite transaction. There is no API that edits an existing frozen row.
- **Orchestrator** is the sole writer of job state transitions. It records dependency edges, idempotency keys, a durable submit-intent attempt, lease owner/expiry, provider acknowledgement, QC, and audit events.
- **Agent boundary** accepts a structured-model port and validates proposed output against `ShotSpec`. It has no database session or tools. A connected LLM gateway/provider profile is not implemented in M1.
- **Render boundary** currently instantiates only `MockH3Provider`. Mock success means the software workflow and mock QC passed. It writes no video, media artifact, or synthetic playable file. Real video production remains reserved for local MiniMax-H3 through ComfyUI.

## Persistence

SQLAlchemy models have an Alembic migration under `backend/alembic/versions`. Runtime startup does not create tables implicitly; run `alembic upgrade head`. SQLite connections enable WAL, foreign keys, and a bounded busy timeout. User assets live below `SHINE_DATA_DIR/assets/<sha-prefix>/<sha256>` and are excluded from Git.

Current tables are `projects`, `assets`, `style_versions`, `shots`, `jobs`, `job_dependencies`, `job_attempts`, `qc_reports`, and `audit_events`. Job attempts are separate records under a logical job. Every render request snapshots the shot revision, frozen style hash, H3-Base identifier, and job-local labels and hashes for its assets.

## Scheduling and recovery

The worker claims queued work with a conditional SQLite update, writes `SUBMIT_INTENT` before calling the provider, and commits the provider acknowledgement separately. A dependency waits in `PLANNED` until all parents reach `PASSED`; a blocked or stale parent moves the child to `NEEDS_REVIEW`. Revising a source shot marks only jobs that consume its prior output stale; independent shots remain intact.

If a lease expires while a job is still `SUBMITTING`, recovery moves it to `NEEDS_REVIEW` with `SUBMISSION_OUTCOME_UNKNOWN`. The worker never blindly submits it again. If an external acknowledgement was persisted before a crash, recovery can complete the mock QC record without resubmitting. The real ComfyUI history/queue reconciliation path is M2 and must preserve the same conservative behavior.

The initial semantic repair budget is two retries after the first failed attempt. A repair plan records root cause and changes; user escalation leaves the task in `NEEDS_REVIEW`. Infrastructure failures, semantic repairs, and uncertain submissions remain distinct outcomes.

## Security and limits

Upload size is capped at 25 MiB. The API sanitizes POSIX and Windows path separators, checks file signatures, hashes content, and writes through an atomic rename. No endpoint configures or logs API credentials. The current mock path sends no assets to any remote model. The API has no user authentication yet; keep it loopback-only and do not expose it to a LAN. Adding real Agent tools or model/ComfyUI endpoints requires explicit authorization, credential handling, and network policy work.
