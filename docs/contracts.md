# Contracts — M1 hardened + M2/M3 offline

The API is served under `/api/v1`. OpenAPI is available at `/docs` during local development.
Bind the API to loopback: ordinary CRUD has no account login, while protected review,
approval, installation and reconciliation actions require a configured local human
operator, same-origin validation and the action-specific grants described below.

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

New VLM responses must explicitly include both `findings` and a `verdict`
(`passed|failed|needs_review`). Missing/null verdicts are unusable received responses,
not a default pass. Historical stored records retain an optional verdict for reading
without rewriting their evidence. A non-pass verdict cannot pass fusion or required
semantic/style/final gates even if findings are empty and confidence is high.
Low overall confidence also requires review; this measures completeness of the
review, separately from confidence in an individual finding. Hard failures remain
failures. Free-text summaries are never parsed into fabricated findings or passes.

New VLM wire responses also require `style_input_checks`: exactly one typed
comparison for each of the seven frozen Style Bible fields. Each reports
`consistent|conflict|uncertain` and a short explanation of the **text input**
comparison against ShotSpec. This does not claim audio or pixel conformance.
An input conflict/uncertainty requires a corresponding `STYLE_INPUT_<FIELD>`
finding and a non-passing verdict. Missing, duplicate, incomplete or internally
contradictory coverage is a received contract failure: retain the response digest,
release resources and never automatically retry. Historical records remain
readable without this field; new native capability adoption requires the current
contract, and old VLM evidence cannot qualify a newly frozen production manifest.

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

## V1.3 amendment

The detailed additive endpoints and request schema names are in
[V1.3 API reference](V1.3_API_REFERENCE.md). Legacy upload remains available, while every new master approve/lock requires a server-verified
operator and an asset_approve/asset_lock grant bound to the complete current asset snapshot. Candidate-byte re-import
and mutation of promoted masters are rejected. New high-risk operations require the existing
loopback operator credential and same-origin check, plus an action/target/snapshot-bound
grant. Grants expire after five minutes and can be consumed once. Install execution approvals
expire after thirty minutes; changed hashes or replayed execution are rejected.

`POST /preparation/approval-grants` returns `{grant_id,snapshot,expires_at}`. Human decisions
include `{grant_id,snapshot,offline,evidence}`; the server supplies actor identity. Fake
promotion requires `offline=true`. Local install plans use `executor=local`; the HTTP execute
route always rejects them. Explicit human execution uses the Windows CLI in the local checklist.

Workflow states never promote Fake execution to real testing. A real-evidence request binds
an authenticated local preview candidate or current manually verified H3 attempt/artifact.
Publishing requires `REAL_TESTED` and a separate operator grant. Unsupported hard continuity
and unverified video-reference transfer keep the existing fail-closed H3 rules.

Pre-production requirement input is typed and project-scoped. A project with an analyzed plan
cannot create a Job until a ready manifest is frozen. API, Worker and operator review compare the pinned manifest and exact workflow profile with current
state; stale manifests raise `PREPRODUCTION_OUTDATED`. `SHINE_OFFLINE_SIMULATION=1` is only
for isolated Fake-ComfyUI testing, never an authorization for real production.

Voice dialogue requests bind exact frozen voice hashes and character IDs. ASR text/speaker,
language/confidence and aligned word times are checked independently. SRT/ASS exports are
available per render; authenticated subtitle corrections append a revision and preserve the
original recognition evidence and Mock flag. Corrected text cannot rewrite locked dialogue.

For an analyzed V1.3 project, Style freeze requires the authenticated operator plus a
`style_freeze` grant targeting the Project's current draft/frozen-version snapshot.
`StyleFreezeRequest` accepts `grant_id`, `snapshot` and `approval_note`. The browser sends
only the specified decision fields, never the grant response's expiry field. Known Fake
content hashes retain Mock provenance after candidate import and cannot use a legacy
cross-project upload to escape the promotion boundary.


## Adversarial closeout amendment

Master approve/lock now take Decision with grant_id, snapshot, evidence and the operator
Authorization header. Grants are one-use and action/target/snapshot scoped; old no-body calls
cannot approve. Clone requests on every route require execution_decision and a voice_execute
grant targeting the consent record. Supply context as the complete VoiceDesign request with
schema defaults, excluding execution_decision. Cloned-voice dialogue uses context containing
voice_id and the complete Dialogue request excluding execution_decision. Consent may carry
expires_at and never substitutes for execution authority. Actual render references are checked
against the frozen set at Job creation, final Worker submission and human review.

Detailed acceptance evidence and actual hardware boundaries are recorded in V1.3_ADVERSARIAL_REVIEW.md.

WorkflowDraft binds model_ids, node_package_ids and server-captured package_hashes;
mutated dependency snapshots block validation/publication/readiness. generation_modes,
duration_range, aspect_ratios, instance_ids, references/media_transfers, resource_mb and
license constraints participate in matching. Revisions freeze after execution/publication;
deprecation needs a separate human grant and preserves historical payloads.

Generate/VoiceDesign/Dialogue.execution_mode selects fake/local and must match the actual
provider. It cannot relabel results. Candidate semantic findings carry model, result,
confidence and evidence; low confidence stays human-reviewed, hard failures block promotion.
Dialogue unknown ASR speaker/confidence requires listened=true and explicit human evidence
at promotion while preserving the original findings. It cannot waive text/timing errors.

InstanceOperation plans enumerate exact action/root/packages/Python/argv/current tree,
L2/L3 risk and expiring human authority. POST /comfyops/instance-plans/{id}/execute uses
only that approved hash. POST /comfyops/backup-plans copies a scoped existing instance read-only.
POST /comfyops/instance-attempts/{id}/reconcile verifies dead owner and records outcome;
rollback is a separate approved operation. /comfyops/execution-context and /comfyops/scope
expose/save a grant-bound subset of administrator limits.

GET /projects/{id}/preparation/model-calls returns the existing task ledger. Explicit
POST /preparation/model-calls/{id}/abandon requires model_reconcile authority, owner/queue/
release proof and records ABANDONED, not success. Reconciliation itself is durably owned.
Resource release cannot treat reconciled=true or expired time as proof of unload.

GET /comfyops/install-plans accepts an optional project_id filter. GET
/comfyops/install-plans/{id}/attempts returns only that plan's ordered attempt history.
POST /comfyops/install-attempts/{id}/reconcile requires an install_reconcile grant on
the terminal FAILED_RECONCILE_REQUIRED attempt. Under package OS locks it verifies
the approved plan and registered/embedded manifests, no active install or unfinished
swap, and absent or exactly matching destination files. It records
RECONCILED_NO_RETRY without package-content changes, runtime promotion or execution.
Continuing requires a fresh plan and separate approval; neither old grants nor failed
attempts are replayed. The Web history/reconciliation controls use these same APIs.

Fixed inbound model GETs may continue from a partial cache. Production HTTP downloads
use at most 64 MiB per range for large files, revalidating each approved redirect and
the exact `Content-Range` and received length. Interrupted requests finish the
same fixed file block; the end byte never advances along with a partial offset. At most three interrupted streams are
allowed before a complete range; the interruption counter resets only after a fully
validated range advances the file. This bounds requests by `3 * (ceil(file_size / segment_bytes) + 1)`. Invalid ranges, excess bytes and digest failures are not retried.
A server ignoring Range must supply the entire file from byte zero. Final promotion
always requires the pinned whole-file size and SHA-256. This is not permission to
replay an installation attempt or any inference/submission.


Mock provenance is monotonic across provider boundaries. An HTTP reviewer reporting
is_mock=true remains Mock even when reached through a non-Mock HTTP adapter. Model task
trace distinguishes provider_is_mock and result_is_mock; effective is_mock is their OR.
The UI cannot relabel an existing result by switching mode. Live H3 leases cannot be
manually released; Worker recovery must settle its owner/state first, and final /prompt
rechecks an ACTIVE, unexpired lease and persists any rejection before returning.


Local SDK alignment may restore tokenizer-omitted script punctuation/spacing only when all other characters
match exactly. It never changes start/end times or inserts a missing spoken character/symbol. Voice renders
retain the raw alignment tokens in optional alignment_evidence. Zero/reversed/out-of-range spans remain hard
errors. Standard user identity variables needed by torch/getpass are allowed in private child environments;
operator/provider secret variables remain excluded. Human listening/identity acceptance remains independent.


### Local review executor file identity

The optional `executor_identity` on local VLM/ASR responses contains schema version `1`,
model ID, canonical verified-file SHA-256, file count/bytes, and runtime SHA-256. The latter
pins the adapter source, Python version and Torch/Transformers distribution versions.
`/health` exposes the verified model descriptors and runtime digest, without absolute
model paths or credentials. The old `manifest_sha256` remains an operator-supplied label;
it is not the canonical verified-file digest.

Startup and every cold model load verify manifest file sizes (where declared) and SHA-256;
path aliases to the same file are rejected. A matching typed identity is copied from each
actual response into the completed model-call trace. A malformed identity, different
response model, or Mock envelope claiming a real identity is a definitive response failure;
there is no automatic inference retry. An ordinary compatible endpoint without this
extension remains usable but has no native capability identity evidence. Prior calls are
not backfilled. Outer VLM Mock provenance is monotonic even when inner JSON says false.

This records execution identity only. Native workflow registration and frozen provider
bindings use the separate evidence/grant contract below; identity fields alone do not
upgrade a capability, asset or film.


### Native VLM/ASR workflow capabilities

`WorkflowDraft.native_provider` is an optional typed binding for `VLM_REVIEW` and `ASR`
only. It pins the configured role's profile ID/kind/model/URL hash, executor identity,
one registered model package and its exact runtime file set. Such a revision has empty
`graph`/`parameter_map`; it cannot masquerade as H3 or acquire Fake ComfyUI execution evidence.
Absent native fields are omitted when serializing old drafts, preserving their payload hashes.
The package and workflow must declare the same admission budget, at least the local
baseline (VLM 8000 MiB / ASR 4000 MiB) and at most 16384 MiB. Larger checked packages
can declare a larger budget. Configured local reviewers reserve the largest valid,
static-validated native declaration for that exact route; this enables calibration without
conferring production approval. Tampered or deprecated declarations are excluded.
These declarations remain estimates unless measured. A calibration call's released lease
must cover the declared workflow budget, and a production subcall's active lease is
rechecked against its frozen workflow budget both before and after the actual response.

Native file bindings accept monolithic weights or a checked safetensors index plus shards.
The executor hashes the index and every shard and rejects unlisted/missing/extra shards
or a mixed monolithic/indexed layout before loading; identity fields cannot omit a shard.

`POST /workflow-capabilities/{id}/validate-native` is authenticated and performs no inference.
It checks the current role binding and the loopback executor's `GET /identity`, which
re-hashes actual files under the executor guard. The cheap `/health` startup identity is
insufficient for this preparation check. Changed/missing files or an unavailable/mismatched
executor block validation and real readiness.

`POST /workflow-capabilities/{id}/native-execution-evidence` consumes a `workflow_validate`
grant and accepts `project_id` plus `samples`. Each sample references a completed, non-Mock
model call with a released lease, matching provider/executor identity and exact returned-result
digest. VLM requires distinct positive and negative input/call IDs, explicit matching verdicts,
confidence >= 0.7, no positive findings and a confident hard negative finding. ASR requires
one actual transcript matching a nonempty `expected_text` after the existing normalization.
This proves bounded calibration only, not general accuracy, identity, voice quality or film
acceptance. The revision hash includes the bound sample IDs/digests; publication needs a fresh
`workflow_publish` grant and rechecks live identity and dependencies. Old records are not backfilled.

Real preproduction accepts VLM/ASR revisions bound to the providers the normal Worker uses;
an unrelated approved ComfyUI review graph cannot satisfy that requirement. Freezing pins
these revisions. The Worker also checks its job's frozen provider/executor before and after
each actual QC subcall, including after a long H3 render. A changed route, plan, response
identity, revoked package, mutated workflow/package payload or Mock result fails adoption; a received result digest remains in the ledger even
when a post-call pin fails. Cleanup failure still retains its separate reconciliation state.
Unanalyzed legacy projects and explicitly matching frozen simulations keep their existing
contracts; simulations cannot be relabelled as real by changing only request mode.

Web workflow cards select native validation/calibration for these revisions, retain the
stronger H3 `job_id` evidence route, and preserve the ordinary ComfyUI history route for
other capabilities. Native cards do not offer Fake node/execution controls. The current
project supplies native calibration scope; a conflicting project ID is rejected before
issuing a grant. JSON evidence remains readable in a full-width editor.

The local full-model executor also rejects unlisted root-level loader inputs (including alternate weights, tokenizer/template/generation JSON and case-insensitive vocabulary names). It does not support PEFT base-model redirects; declaring an adapter_config.json does not authorize an external base. Known local audit notes are not loader inputs. Cold loads and identity probes recheck this boundary before model construction.


### Independent text-input consistency

The film role `qc_reviewer` accepts a typed `FilmRunRequest.shot_spec` tied to the current frozen Style. Its `StyleInputAssessment` compares exactly seven fields with verbatim Style quotes and authentic ShotSpec excerpts. A conflict, uncertainty or confidence below 0.7 cannot recommend `proceed_to_preflight`. The model receives only the two text snapshots and their Style identity; asset metadata, prior artifacts and media are omitted. Remote calls require explicit `allow_remote_text` before execution. No master asset, frozen Style or film approval can be created by this role.

New analyzed plans declare `independent-text-v1` in the frozen manifest. Real Job creation chooses the latest exact matching assessment (never an older positive behind a newer non-pass) and pins its ID/hash into `RenderRequest`. `preproduction.enforce`, shared by Job creation and Worker claim/submit, verifies exact input/style identity, immutable result hash, source task, active role/provider snapshot, consent, staleness and genuine versus Mock provenance. Missing/outdated/conflicting evidence blocks; a Mock assessment is usable only in an explicit offline path. Old frozen plans retain their original contract, but any request carrying an assessment is revalidated. A passing input assessment permits preflight consideration only; it supplies no pixel/audio/final quality coverage.

Text provider envelopes retain their Mock marker in `LLMResponse.raw_meta`; film task provenance is the logical OR of configured Mock mode and transport Mock metadata. A compatible URL alone never converts a reported Mock result into real evidence.


### Film-agent unknown outcomes and remote abandonment

Non-Mock timeouts, connection failures and server errors are unknown external outcomes (`NEEDS_REVIEW`), not definitive rejected completions. The same request is blocked before a new model call. Request comparison includes input and schema identity and treats equivalent provider aliases as the same target; changing a schema cannot evade an unreconciled outcome. SQLite's writer lock makes the check and durable intent atomic. After an explicit abandonment, the original request remains blocked; a genuinely changed schema is a new reviewed request. Known contract rejections retain a digest and whitelisted reason code, never provider exception text or raw returned model labels.

`POST /projects/{project_id}/film-runs/{task_id}/abandon-remote-response` requires an authenticated operator and a one-use `model_reconcile` grant bound to the task snapshot. It applies only to ended, uncertain remote text-role calls, including legacy timeout rows incorrectly marked `REJECTED`. It rejects loopback/model-media calls, changed provider identities, and any unreleased resource lease, including expired leases. It sends no inference or cleanup request. The record becomes `ABANDONED` with external outcome `UNKNOWN`, no automatic replay and no local-model-unload claim; original error/classification evidence remains in its trace/audit. Local model calls retain their separate owner/release reconciliation requirements. Input preflight considers the newest matching task even if it produced no artifact, so an unresolved or rejected newer call cannot silently fall back to older positive evidence.

QC text wire `independent-text-v2` returns seven judgments with JSON pointers to existing nonempty ShotSpec strings. Frozen Style and source quotations are server-bound; attempts to return source quote fields are rejected. Stored `StyleInputAssessment` remains version 1.1 and may retain its optional source pointer. This is input consistency only, never media QC or human approval.

Pixel review may carry `ReviewContext.independent_input` as an artifact ID/hash pair. The tracked model gate rebuilds evidence from authoritative database sources before resource admission, immediately before submission and after a received result. Caller-supplied judgments, cross-project/current-Style mismatches, stale/latest-failed evidence and changed provider/input pins are rejected. The reviewer receives the verified assessment as untrusted user evidence while retaining its own seven-field judgments and unchanged cross-field consistency validation. Missing transcript cannot determine whether two written requirements conflict; independent audio coverage remains mandatory.

Native bindings may pin `input_assessment_required=true` only with the explicit structured review protocol and `input_evidence_protocol=text-assessment-compact-v2`. The compact representation carries all seven field judgments, source pointers, confidence and detail; source quotations remain in the separately validated Style/ShotSpec and the complete assessment remains hash-pinned. Any evidence-assisted call records that policy and protocol and cannot qualify an unguarded or differently versioned production binding. Evidence adoption rechecks its source pins. A frozen required policy also blocks Job/Worker preflight without the assessment, and production review compares the policy and protocol before and after inference. Historical contexts with no assessment retain their original model-call fingerprint and replay guard. The decoder wire schema and executor runtime are unchanged by this client-side evidence path.

Every structured native VLM binding also pins `client_review_sha256`, a fingerprint of the loaded review prompt, context/assessment validation, frame extraction, quality gates, Worker and admission code. The fingerprint is captured when those modules load and checked against saved sources before admission, immediately before submission, on response and on capability validation. Changing code on disk without restarting fails closed. Every film-review context records a separate logical input hash over artifact, ShotSpec and Style: an unresolved call blocks aliases, transcript changes, revisions and client-version changes for those same logical inputs. Different media can continue independently. A legacy unresolved review predecessor lacking this logical hash must be reconciled first because its target cannot be safely compared.

The media-review adapter rejects a small allowlist of dialogue-absence/mismatch codes when there is no non-Mock ASR transcript; even with real ASR, those claims cannot be labeled deterministic hard errors because recognition is probabilistic. These are contract failures, not successful reviews. Worker diagnostics separately classify JSON parse errors, received contract failures and unknown timeouts. Configured Mock providers and transport envelopes cannot erase their Mock provenance.

For structured local VLM reviews, each finding message is limited to 240 characters and an overlong message rejects the response instead of being truncated. An identity mismatch needs a derived local reference bound to the exact character subject in the frozen request; a scene, prop, costume or another character's reference is insufficient. Ambiguous multi-character subjects are rejected. Missing reference evidence never becomes a passing identity judgment, and non-identity appearance or action observations remain separate.

Local model release confirmation has a separate timeout capped at 30 seconds (or the provider's shorter timeout). A timeout is not release proof: the resource lease remains `RECONCILE_REQUIRED` until an authenticated reconciliation verifies physical release. Inference timeout and unknown-outcome protections remain separate.

Provider `generation_options` explicitly supports OpenAI-compatible `max_completion_tokens` (1..524288) and `reasoning_effort` (low/medium/high/xhigh/max). Empty options preserve historical requests and identity. Nonempty options are hashed into text review identity and checked before proof use/reconciliation; they do not relax replay guards. A length/max_tokens/content_filter termination is `LLM_FORMAT`, never successful quality evidence. Other provider kinds reject these options explicitly.

Native structured review is explicit opt-in `local-review-json-schema-v1`. Provider capability discovery supplies the protocol/decoder pin; native bindings pin both. Requests use strict json_schema, and responses attest protocol + exact schema + decoder. Source SDK files and schema are verified before import/model load; generated output still must pass application schema and seven-field semantic consistency. New protocol evidence cannot qualify from a legacy/missing proof; production rechecks it before and after the call. Adapter fingerprint changes require fresh runtime qualification for both VLM and ASR.
