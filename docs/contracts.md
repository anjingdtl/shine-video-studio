# M0/M1 contracts

The API is served under `/api/v1`. OpenAPI is available from FastAPI at `/docs` during local development.

## Project, asset, and style APIs

| Operation | Contract |
| --- | --- |
| `POST /projects` | Creates a local project from a title and optional description. |
| `POST /projects/{id}/assets` | Multipart upload with `asset_kind=image|video|audio` and `file`; max 25 MiB; validates extension and signature; creates an unapproved user asset. |
| `POST /assets/{id}/approve` | Explicit human approval event. |
| `POST /assets/{id}/lock` | Requires approval; locks the master asset against use before review. |
| `PUT /projects/{id}/style/draft` | Validates the seven required Style Bible fields and stores the editable draft. |
| `POST /projects/{id}/style/freeze` | Creates a new immutable snapshot version and SHA-256; requires a complete draft. |
| `POST /projects/{id}/shots` | Requires a frozen project style; validates shot duration, dialogue timecodes, same-project style, and required approved/locked assets. |
| `POST /projects/{id}/jobs` | Requires the frozen shot style and approved/locked references; stores a request-specific manifest and local numbering; creates `QUEUED` or dependency-gated `PLANNED` mock work. |

H3 reference numbering is local to the request: each image/audio/video label family starts at 1 for each job. It does not use a global asset ID as a `<Picture N>`, `<Video N>`, or `<Audio N>` number. The service enforces the published image/video/audio and total-file caps. A reference's content hash and storage key are captured in the immutable request.

## ShotSpec and render request

`ShotSpec` is a Pydantic v2 model with schema version `1.0`, a 4–15 second target, priority A/B/C, frozen style ID, camera and sound objects, required asset IDs, dialogue lines with ordered in-range timecodes, continuity, and locked story facts. Unknown fields fail validation.

`RenderRequest` contains an idempotency key, shot ID and revision, frozen style ID and hash, `MiniMax-H3-Base` model-family marker, `provider="mock"`, local reference manifest, typed shot spec, style snapshot, and expected duration. No online video service or alternate video model adapter is available.

## Job states

`PLANNED → QUEUED → SUBMITTING → RUNNING → QC_PENDING → PASSED`

Failure and review branches use `WAITING_ASSET`, `READY`, `REPAIR_PENDING`, `NEEDS_REVIEW`, and `FAILED`. The application checks transitions. `SUBMITTING` means a durable intent exists; if its result is ambiguous, it becomes `NEEDS_REVIEW`, never an automatic resubmit. Jobs can be paused/resumed only before submission. A repaired job adds a new `job_attempts` row and cannot exceed two semantic retries.

The mock worker returns a deterministic provider ID and mock QC record. `is_mock=true`, `simulated=true`, `video_created=false`, and `artifact_kind="none"` remain visible in the API. A mock `PASSED` result is not a generated-video quality claim.

## Agent boundary

`ProducerAgentRuntime` calls an injected `StructuredModel.complete_json` interface with a JSON schema and brief. It validates the returned JSON as `ShotSpec` and rejects a style ID that differs from the frozen style supplied by the caller. It cannot access SQLAlchemy, files, HTTP, shell, tools, or approval endpoints. OpenAI-compatible profiles, credentials, user consent flows, and agent-driven project editing remain outside this release.
