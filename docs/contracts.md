# Contracts — M1 hardened + M2/M3 offline

The API is served under `/api/v1`. OpenAPI is available at `/docs` during local development. All endpoints are loopback-only and unauthenticated; do not expose them to a LAN.

## Project, script, asset, and style APIs

| Operation | Contract |
| --- | --- |
| `GET /projects` | List projects with language, style draft, frozen version, review/repair switches. |
| `POST /projects` | `{title, description?, language?}`. Empty titles are rejected. |
| `PATCH /projects/{id}/settings` | Partial `{language, allow_remote_review, auto_repair_enabled}`. |
| `POST /projects/{id}/documents` | Multipart `file` (TXT, Markdown, JSON, DOCX, PDF; max 20 MiB). Parses and stores text plus metadata; never executes the document. |
| `GET /projects/{id}/documents` | Imported document metadata and a short preview. |
| `POST /projects/{id}/assets` | Multipart `asset_kind=image|video|audio`, optional `role`, `entity_id`, `tags` (comma-separated). Configurable byte limit, Windows/POSIX filename sanitization, signature check, structural media probe, SHA-256 dedup, atomic storage. |
| `PATCH /assets/{id}/metadata` | Update `role`, `entity_id`, `tags`. |
| `POST /assets/{id}/approve` | Explicit human approval; records approver/timestamp. |
| `POST /assets/{id}/lock` | Requires approval. Locks the master asset for shot/reference use. |
| `PUT /projects/{id}/style/draft` | Validates all seven Style Bible dimensions. |
| `POST /projects/{id}/style/freeze` | Optional `{approval_note}`. Creates an immutable version with canonical hash, approver, timestamp, and `impact` (`affected_shot_ids`, `invalidated_job_ids`). Re-freezing an identical draft is rejected. |
| `GET /projects/{id}/styles` | Ordered frozen versions with hashes/timestamps. |
| `GET /projects/{id}/style/impact` | Current style vs superseded shot/job IDs. |
| `GET /projects/{id}/quality-policy` | The effective per-project quality policy and whether it was explicitly configured. |
| `PUT /projects/{id}/quality-policy` | `{require_semantic_review, require_audio_review, require_style_review, min_confidence, accept_mock_review, allow_unverified_technical, priority_thresholds, hard_rules}`. Hard rules are never relaxed by a priority threshold. |
| `POST /projects/{id}/shots` | `{name, spec, bindings[]}`. Requires a frozen style; new shots must use the current frozen style; required assets must be approved/locked; bindings are validated per type/project/limits. |
| `GET /projects/{id}/shots` | Shots with independent binding rows. |
| `POST /shots/{id}/revise` | New `ShotSpec`. Increments revision, marks prior jobs stale, invalidates only downstream dependants, and audits the change. |
| `GET /shots/{id}/bindings` | Current binding rows with asset metadata. |
| `PUT /shots/{id}/bindings` | Replace bindings (`asset_id, use_as, role?, frame_role?, ordinal`). Enforces 9 images, 3 videos, 3 audio, 12 total. |
| `POST /shots/{id}/compile` | Compile preview. Returns the mode, official-format prompt, sections, prompt hash, compiler/rule versions, labels, warnings, manifest; creates no job. |

## ShotSpec and bindings

`ShotSpec` schema version `1.0` contains a 4–15 second target, priority A/B/C, optional/current frozen style ID, camera/sound maps, characters, required asset IDs, ordered dialogue lines, a typed continuity object (`cut`, `last_frame`, `video_reference`, `audio_reference`), and locked story facts. Dialogue must fit the shot and every speaker must appear in `characters`.

Per-shot bindings are the authoritative asset references. A job may omit `references` and use saved bindings, or provide references only if they match the saved binding set exactly. This prevents global/UI state from leaking one shot's assets into another. The request-local manifest assigns `<Subject N>`, `<Picture N>`, `<Video N>`, or `<Audio N>` deterministically per job; a global asset ID is never used as an H3 label number.

## Job states

`PLANNED / WAITING_ASSET → QUEUED → SUBMITTING → RUNNING → QC_PENDING → PASSED`

Branches: `READY`, `REPAIR_PENDING`, `NEEDS_REVIEW`, `FAILED`.

Every transition increments `state_version`. The worker records a durable submit intent before external submission and persists the provider `prompt_id` after acknowledgement. An ambiguous submission becomes `NEEDS_REVIEW`, never an automatic resubmit.

| Operation | Contract |
| --- | --- |
| `POST /projects/{id}/jobs` | `{shot_id, depends_on?, references?, provider: mock|comfyui, generation_mode?, workflow_profile_id?, mock_scenario?}`. ComfyUI jobs require a registered workflow profile. Continuity modes require a passed, non-stale source job; ComfyUI continuity additionally requires a real source artifact. |
| `GET /projects/{id}/jobs` | Jobs with state version, mode, attempt, repair, QC summary. |
| `GET /jobs/{id}` | Full job including compiled prompt/sections. |
| `POST /jobs/{id}/pause` / `resume` | Only before submission; stale jobs cannot resume. |
| `POST /jobs/{id}/repair` | Typed `RepairPlan`; bounded by the job's semantic budget; human escalation moves to `NEEDS_REVIEW`. |
| `POST /jobs/{id}/repair/suggest` | Root-cause classification and bounded plan from the latest QC report. |
| `GET /jobs/{id}/qc` | QC reports with `is_mock`, reviewer, technical checks, semantic findings, coverage. |
| `GET /jobs/{id}/quality-gates` | The per-attempt gate ledger: `technical`, `semantic`, `audio`, `style`, `final` with status, `required`, `is_mock`, model, confidence, error code, coverage and evidence. |
| `GET /jobs/{id}/artifacts` | Stored non-mock artifacts only when a real provider produced one. |
| `POST /artifacts/{id}/technical-qc` | Re-run deterministic technical QC on a stored artifact. |

A job response also carries `request_revision` (how many times the request was
revised) and `quality_verification` (`real`, `mock_verified` or `partial`).
Every attempt carries `attempt_kind` (`initial`/`repair`), `parent_attempt_id`,
`request_fingerprint`, `prompt_sha256`, `parameter_sha256`, `manifest_sha256`,
`prompt_graph_sha256` and `repair_reason`.

Mock jobs remain `simulated=true`, `video_created=false`, and QC `is_mock=true`; mock success is not a video-quality claim. A ComfyUI artifact is `is_mock=false` and only exists after download/storage.

## H3 prompt compiler

`shine.h3.H3PromptCompiler` is the only prompt source. It pins:

- `SKILL.md` SHA-256 `a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0`
- `base-en.txt` `2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc`
- `ref-en.txt` `1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7`
- Rule version `h3-prompt-writing@d21241f0a4b3`.

Base modes emit `integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music` in that order, with the official first/last-frame alignment instruction where applicable. Ref2VA emits `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`. Compilation rejects unresolved labels, label/type mismatch, unknown video/audio duration, >9 images, >3 videos, >3 audio, >12 files, per-type duration >15s, dialogue overlap/overrun, missing speaker references in Ref2VA, style-less requests, and a frame role that contradicts the chosen mode (I2VA with a last frame, L2VA with a first frame, FL2VA without one of each).

## LLM gateway and Agent

| Operation | Contract |
| --- | --- |
| `GET/POST /provider-profiles` | Profiles expose `has_api_key` but never the key. Keys go to DPAPI/`0600` storage. |
| `PUT/DELETE /provider-profiles/{id}` | Update fields or delete when no role route references it. |
| `PUT/DELETE /provider-profiles/{id}/key` | Set/clear the local secret. |
| `POST /provider-profiles/{id}/test` | Capability probe (`/models` for OpenAI/Anthropic, deterministic Mock). Returns classified errors. |
| `GET/PUT/DELETE /agent-routes` | Role → profile mapping for `script_planner`, `shot_planner`, `repair_planner`, `qc_reviewer`. |
| `GET /config/llm` | Secret-store mode and route summary. |
| `POST /projects/{id}/agent/plan` | `{text|document_id, max_shots, apply}`. Uses a configured LLM role when present, otherwise the deterministic local planner. Invalid JSON is rejected; wrong style/unknown assets are rejected. `apply=true` creates shots as actor `agent` only after revalidation. |
| `POST /projects/{id}/agent/shots` | Single structured ShotSpec proposal; requires a `shot_planner` route. |

The Agent has no database, shell, arbitrary path, or approval tool. It cannot freeze styles or approve/lock assets.

## ComfyUI adapter and workflow profiles

| Operation | Contract |
| --- | --- |
| `GET /comfy/config` | Configured base URL, render mode, profile count, explicit `pending_local_h3` marker. |
| `POST /comfy/test` | `{base_url, timeout_s}`; returns system stats, node count, H3-like node names. The URL must be loopback unless `SHINE_ALLOW_REMOTE_COMFYUI=1` is set. |
| `GET /comfy/capabilities` | Node inventory and profile validation against `/object_info`. |
| `GET/POST /workflow-profiles` | Register a validated API workflow graph, `parameter_map` (`node.inputs.field` paths), optional `media_transfers` (per family: `upload_path`, `field_name`, `subfolder`, `extension`, `required_node_classes`, `style`), required node classes, checkpoint family, mode. A `positive_prompt` **and** a `duration` mapping are mandatory. |
| `POST /workflow-profiles/{id}/validate` | Re-check the registered profile against the live node inventory and store the result. A profile with missing node classes (`COMFY_NODE_UNAVAILABLE`) or a changed node definition (`COMFY_WORKFLOW_VERSION_MISMATCH`) is refused at submit time. |
| `GET /workflow-profiles/{id}` | Includes the graph for local administration. |
| `DELETE /workflow-profiles/{id}` | Remove a profile. |

The adapter supports connection checks, object info, uploads, `/prompt`, `/queue`, `/history`, `/view`, interrupt, timeouts, external prompt-ID persistence, and conservative reconciliation. No real H3 workflow JSON was available in this build; the generic mapping must be replaced with the user's exported API workflow during local acceptance.

### Reference media transfer

Every manifest entry owns a deterministic logical slot
(`reference_subject_1`, `reference_picture_1`, `reference_video_1`,
`reference_audio_1`, …). A slot without a `parameter_map` entry blocks the
render with `PENDING_WORKFLOW_MAPPING`; a reference is never dropped silently.
`positive_prompt` and `duration` must also be mapped, and `seed` must be mapped
whenever a repair sets one.

The upload extension always comes from the asset's content type. Image uses
ComfyUI's documented `/upload/image` multipart default. **Video and audio have
no default transport** and require a `media_transfers` declaration, because
custom nodes do not share one upload interface. The declared loader node
classes must exist in the workflow graph and on the server.

Before every upload the master asset is re-verified: approved, locked, in the
same project, manifest hash matches, on-disk digest matches, and the media type
matches the bound role. A violation is `COMFY_REFERENCE_INVALID` and the
render stops before anything is submitted.

### Submission outcomes

| Situation | Error code | Behaviour |
| --- | --- | --- |
| TCP connection never established | `COMFY_UNAVAILABLE` | safe failure, nothing was queued |
| Request sent, response lost | `COMFY_SUBMISSION_OUTCOME_UNKNOWN` | `NEEDS_REVIEW` + reconciliation record, never auto-resubmitted |
| 5xx from `/prompt` | `COMFY_SUBMISSION_OUTCOME_UNKNOWN` | same as above |
| 4xx from `/prompt` | `COMFY_PROMPT_REJECTED` | definitely not queued |
| Node validation error | `COMFY_WORKFLOW_INVALID` | definitely not queued |
| Prompt id returned | — | persisted immediately |
| Job errored during execution | `COMFY_EXECUTION_ERROR` | `NEEDS_REVIEW` |
| Job finished without an output file | `COMFY_OUTPUT_MISSING` | `NEEDS_REVIEW` |
| No record in history/queue | `EXTERNAL_JOB_NOT_FOUND` | `NEEDS_REVIEW`, never resubmitted |
| ComfyUI unreachable during recovery | `RECONCILIATION_UNAVAILABLE` | `NEEDS_REVIEW` |

## QC and repair contracts

`TechnicalQCResult` contains typed checks with `pass`, `fail`, `unavailable`, or `skipped`, plus duration/resolution/fps/audio/SHA. Missing FFprobe/FFmpeg yields `unavailable`, never a false pass. Container inspection (`container`, `stream_metadata`) and a real end-to-end decode (`decode`) are separate checks: reading metadata is never reported as a decode.

`SemanticFinding` carries code, category, severity (`hard|soft|uncertain`), confidence, message, optional evidence time/subject. `SemanticReviewResult` records reviewer/model/mock flag/input hash.

### Quality gates

Five gates are evaluated per attempt and persisted in `quality_gates`:

| Gate | Required when | Notes |
| --- | --- | --- |
| `technical` | always | the deterministic technical QC |
| `semantic` | the policy requires it and the shot has characters, references or dialogue | a missing reviewer is `not_run` / `SEMANTIC_REVIEW_NOT_CONFIGURED` |
| `audio` | the policy requires it and the shot has dialogue | a missing ASR is `not_run` / `ASR_NOT_CONFIGURED` |
| `style` | the policy requires it | style conformance cannot be asserted without a semantic review |
| `final` | always | `passed` only when every required gate passed |

Each gate records `status`, `required`, `is_mock`, `model`, `confidence`,
`error_code`, `coverage` (`covered`, `not_covered`, `failed`, `mock_covered`)
and its evidence. The final gate's evidence lists every gate status and the
verification label (`real`, `mock_verified` or `partial`).

Reviewer failures are separated: `SEMANTIC_REVIEW_FAILED`,
`SEMANTIC_REVIEW_TIMEOUT`, `SEMANTIC_REVIEW_INVALID_JSON`, `ASR_FAILED`,
`SEMANTIC_REVIEW_NOT_CONFIGURED`, `ASR_NOT_CONFIGURED`,
`MOCK_REVIEW_NOT_ACCEPTED` and `STYLE_REVIEW_NOT_COVERED`.

Fusion:

- technical failure → `failed`;
- hard finding confidence ≥ the priority threshold → `failed`;
- a hard rule (identity, master asset, key dialogue) → `failed` regardless of
  confidence or shot priority;
- hard finding below the threshold, uncertain findings and high-confidence soft
  findings → `needs_review`;
- otherwise pass, with coverage showing which reviewers actually ran;
- any mock reviewer marks the fused result `is_mock=true`.

### Repair

`RepairPlan` actions: `rebuild_reference_bindings`, `revise_shot_intent`, `adjust_prompt_strategy`, `adjust_workflow_parameters`, `rerender_after_transient`, `wait_for_human`. Retries are capped at two; passed jobs are never automatically rewritten; low confidence or unknown root cause escalates.

An automatic repair must produce a **different** request:

* `adjust_prompt_strategy` recompiles the prompt through the pinned
  `H3PromptCompiler` after an authorised camera/sound directive; the locked
  story facts, the master assets and the frozen Style Bible are untouched;
* `rebuild_reference_bindings` rebuilds the request-local manifest from the
  shot's saved bindings;
* `adjust_workflow_parameters` / `rerender_after_transient` change the
  generation parameters (seed) after the profile is re-validated.

A revision that would not change anything is refused, and the job escalates to
a human. The new attempt records its own request snapshot, prompt hash,
parameter hash, manifest hash, parent attempt and repair reason; the previous
attempt is never overwritten. A repeated root cause escalates instead of
rerolling. Repairing an upstream job invalidates its dependants so their
validity is recomputed.

## Audit and workers

| Operation | Contract |
| --- | --- |
| `GET /projects/{id}/events` | Paginated project audit (`limit`, `offset`, `entity_type`, `entity_id`, `event_type`, `actor`, `since`, `until`). Returns `{items,total,limit,offset}`. |
| `GET /system/status` | API/Worker/Mock/H3 distinction, pending/running/failed/blocked counts, worker instances, last error, render mode. |
| `GET /workers` | Worker heartbeat, status, current job/phase, processed count, last error. |

Audit payloads redact credential-like fields/values and absolute local paths. Jobs/scopes are backfilled by the M2/M3 migration.


## V1.2.1 operator/continuity amendment

See [V1.2.1 closeout](H3_REF2VA_V1.2.1_FINAL_CLOSEOUT.md) and
[operator review](operator-review.md). `last_frame` means SOFT_REFERENCE_CONTINUITY;
`hard_frame_anchor` means HARD_FRAME_ANCHOR_CONTINUITY and is blocked until tested;
`video_reference` is VIDEO_REFERENCE_CONTINUITY, with real transfer still unverified.
The API also accepts those full requirement names and normalizes the legacy field values.
No historical request is rewritten. Workflow identity separates family/mode/variant/precision/
weight files from the immutable graph hash. Alembic head is `f121a0010001`.
Human approval requires a server-authorized local operator and the current complete snapshot;
it appends independent per-item evidence and cannot borrow passed gates from an older Attempt.
Process health and renderer probing are separate. Low-resolution 480×864 H3 results prove
integration/technical delivery, not accepted film quality. The user's explicit voice/dialogue/
lip-sync confirmation is recorded separately from formal per-gate approval events.
