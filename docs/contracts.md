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
| `GET /jobs/{id}/artifacts` | Stored non-mock artifacts only when a real provider produced one. |
| `POST /artifacts/{id}/technical-qc` | Re-run deterministic technical QC on a stored artifact. |

Mock jobs remain `simulated=true`, `video_created=false`, and QC `is_mock=true`; mock success is not a video-quality claim. A ComfyUI artifact is `is_mock=false` and only exists after download/storage.

## H3 prompt compiler

`shine.h3.H3PromptCompiler` is the only prompt source. It pins:

- `SKILL.md` SHA-256 `a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0`
- `base-en.txt` `2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc`
- `ref-en.txt` `1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7`
- Rule version `h3-prompt-writing@d21241f0a4b3`.

Base modes emit `integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music` in that order, with the official first/last-frame alignment instruction where applicable. Ref2VA emits `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`. Compilation rejects unresolved labels, label/type mismatch, unknown video/audio duration, >9 images, >3 videos, >3 audio, >12 files, per-type duration >15s, dialogue overlap/overrun, missing speaker references in Ref2VA, and style-less requests.

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
| `POST /comfy/test` | `{base_url, timeout_s}`; returns system stats, node count, H3-like node names. |
| `GET /comfy/capabilities` | Node inventory and profile validation against `/object_info`. |
| `GET/POST /workflow-profiles` | Register a validated API workflow graph, `parameter_map` (`node.inputs.field` paths), required node classes, checkpoint family, mode. A `positive_prompt` mapping is mandatory. |
| `GET /workflow-profiles/{id}` | Includes the graph for local administration. |
| `DELETE /workflow-profiles/{id}` | Remove a profile. |

The adapter supports connection checks, object info, uploads, `/prompt`, `/queue`, `/history`, `/view`, interrupt, timeouts, external prompt-ID persistence, and conservative reconciliation. No real H3 workflow JSON was available in this build; the generic mapping must be replaced with the user's exported API workflow during local acceptance.

## QC and repair contracts

`TechnicalQCResult` contains typed checks with `pass`, `fail`, `unavailable`, or `skipped`, plus duration/resolution/fps/audio/SHA. Missing FFprobe/FFmpeg yields `unavailable`, never a false pass.

`SemanticFinding` carries code, category, severity (`hard|soft|uncertain`), confidence, message, optional evidence time/subject. `SemanticReviewResult` records reviewer/model/mock flag/input hash. Fusion:

- technical failure → `failed`;
- hard finding confidence ≥0.7 → `failed`;
- hard finding confidence <0.7, uncertain, or high-confidence soft → `needs_review`;
- otherwise pass, with coverage showing which reviewers actually ran;
- any mock reviewer marks the fused result `is_mock=true`.

`RepairPlan` actions: `rebuild_reference_bindings`, `revise_shot_intent`, `adjust_prompt_strategy`, `adjust_workflow_parameters`, `rerender_after_transient`, `wait_for_human`. Retries are capped at two; passed jobs are never automatically rewritten; low confidence or unknown root cause escalates.

## Audit and workers

| Operation | Contract |
| --- | --- |
| `GET /projects/{id}/events` | Paginated project audit (`limit`, `offset`, `entity_type`, `entity_id`, `event_type`, `actor`, `since`, `until`). Returns `{items,total,limit,offset}`. |
| `GET /system/status` | API/Worker/Mock/H3 distinction, pending/running/failed/blocked counts, worker instances, last error, render mode. |
| `GET /workers` | Worker heartbeat, status, current job/phase, processed count, last error. |

Audit payloads redact credential-like fields/values and absolute local paths. Jobs/scopes are backfilled by the M2/M3 migration.
