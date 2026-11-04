# ADR 0006 — Director AI material preparation on the existing ledger

2026-11-05. Extends ADR 0003 (controlled preproduction), ADR 0004 (adversarial
authority and recovery) and ADR 0005 (controlled lifecycle and model subcalls).
Baseline `bff25ad`, branch `codex/director-ai-material-preparation`.
Plan of record: `docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md`.

## Context

The director workbench required every character, location and prop image to be
prepared outside the product and uploaded, and a script's character names had to
be matched to uploads by hand. V1.3 already permits AI to create `CandidateAsset`
rows, but only an explicit local-user grant may promote one to an `Asset` master.
What was missing is the middle: a versioned script preparation, a user-confirmed
requirement list, a per-item appearance brief, and a durable image generation run
that the resident Worker executes on the locally managed ComfyUI.

The existing `candidate-assets/generate` endpoint could not carry that. It runs
synchronously inside the HTTP request, resolves a `FakeImageProvider` when no
image backend is configured, takes the shared GPU lease for the whole call, and
harvests the first `type == "output"` image from any output node in ComfyUI
history. Wrapping it in a 202 would have produced an endpoint that claims Worker
recovery it does not have.

## Decision

### Reuse, not a second system

| Concern | Reused as-is | Added |
| --- | --- | --- |
| Plan ledger | `PreProductionPlan`, `AssetRequirement` | `purpose` column |
| Candidate + promotion | `CandidateAsset`, `assets.create_candidate`, `assets.promote`, `ApprovalGrant` | — |
| Identity | `asset_identity.resolve_character_asset`, `entity_id` authority | — |
| Agent calls | LLM Gateway, `AgentTaskRecord`, `AgentArtifact`, `AgentRoute`, `preparation_task` replay guard | a second `producer` tool schema |
| Scheduling | `ResourceScheduler` / `ResourceLease`, one resident Worker | one dispatch step |
| Renderer ownership | `renderer_release`, PID/birth/Job Object proof | — |
| Video production | `H3PromptCompiler`, `preproduction.enforce`, series | — |
| Image execution | `ComfyUIClient` transport | `MaterialRun` / `MaterialItem` / `MaterialImageAttempt`, an output-node-scoped fetch |

One database, one Worker, one lease ledger, one candidate store, one audit
trail. A batch is a domain view over its items; it is not a second scheduler and
it holds no lease of its own.

### Plan purpose isolation

`PreProductionPlan.purpose` is `production` or `asset_preparation`, backfilled to
`production` by the column's server default. The isolation lives in the two
selectors every production path already reads through — `current_plan` and
`_plan_for_manifest` — plus an explicit `require_plan_for_purpose` for the new
by-id material routes and a purpose term in the `run_preparation` claim `UPDATE`.

Consequences that are intended and must not be "fixed" later:

* A project whose only plan is a material plan has **no** production contract.
  `readiness` answers `PREPRODUCTION_NOT_ANALYZED`, `enforce` takes the legacy
  no-plan path, and the style freeze is not treated as protected by it.
* A material plan can never satisfy `enforce`, freeze, revalidate or a Worker
  preproduction gate, however recent it is and even if its payload carries a
  `manifest`-shaped key.
* Existing frozen plans keep their id, payload, `sha256` and row order. Verified
  against a copy of the live acceptance database: all 13 rows byte-identical,
  rowid order preserved, and `current_plan` still resolves to the documented
  frozen plan `7832906a-d383-4eaa-a94d-0e0b510769d2`.

### Durable execution, not an HTTP request

The API only creates rows and returns 202. `FastAPI BackgroundTasks` and
in-memory threads are not a task store here. The Worker claims an **item**,
acquires the exclusive GPU lease for that item alone, and releases it before the
next item, so a long batch cannot starve the H3 queue and a running series is
never preempted (the ledger already refuses to admit while any lease is `ACTIVE`
or `RECONCILE_REQUIRED`).

The attempt row carries the submit-intent protocol from ADR 0004/0005: the final
graph hash, seed, size, output node and runtime fingerprint are committed
**before** `/prompt`; the external call runs outside the SQLite writer
transaction; the returned `prompt_id` is committed immediately after. An uncertain
submission is `UNKNOWN` forever and is only ever reconciled, never resubmitted.

### Anti-duplicate constraints

Two independent constraints, because an idempotency key alone cannot stop two
browsers that generated two different keys:

* `uq_material_runs_idempotency (project_id, idempotency_key)` — same key and same
  request hash returns the original batch; same key with a different payload is a
  409.
* `uq_material_items_revision (project_id, requirement_id, generation_revision)` —
  one item per confirmed requirement revision, project-wide.
* `uq_material_image_attempts_active (active_intent_key)` — at most one unfinished
  attempt per item. The key is cleared when the attempt reaches a terminal state,
  which is what allows a bounded resume after a definite failure while still
  blocking a second live submission.

`seed` and `byte_size` are `BigInteger`: the audited template ships the 48-bit
seed `447606998181262`, which a 32-bit column would silently corrupt.

### Real-only image entrypoint

`MaterialRun.mode` accepts the single literal `real`. There is deliberately no
`fake` or `auto` value, and the new route never reaches the
`api_preparation.generate` provider-resolution branch that falls back to
`FakeImageProvider`. A missing configuration, a Fake provider or an unvalidated
workflow is an explicit refusal. The legacy candidate endpoint keeps its existing
compatibility behaviour, including its `is_mock` labelling; it is audited, not
silently rerouted, and it cannot produce real verification.

### Prompt authority

The image prompt template is a separate deterministic module and is **not** part
of `H3PromptCompiler`. It owns the fixed composition rules per family
(character: single half-body, complete head, clear face, neutral grey-white
background; scene: no people; prop: single complete object, no holder), appends
the confirmed visual direction, and has its own pinned version and hash. The
producer model supplies appearance text only — never a node id, a seed, a sampler
parameter, a workflow choice or an H3 label.

H3's own gates are untouched: Style/Rule hash, per-shot masters, request-local
labels, real reference slots and `media_transfers`. A generated candidate that is
promoted becomes an ordinary master and is referenced exactly like an uploaded
one. Voice stays optional and a dialogue-free shot still gets no audio reference.

### Preparation is not the storyboard

`ScriptPreparation` is shallow, per-scene and allowed before the Style Bible is
frozen. It carries no shots, durations, asset UUIDs or H3 prompt text and it can
never be presented as a `ScriptBreakdown`. The formal storyboard chain still reads
the confirmed preparation result and the identity mapping, then produces its own
`ScriptBreakdown` under the existing timing, dialogue and rule-pack gates.

## Consequences

* Technical image success, advisory model review and human artistic acceptance
  stay three separate records. A missing or failing advisory reviewer is recorded
  and never blocks the managed render.
* Image capability readiness is per family (character / scene / prop) and is
  bound to a fingerprint of runtime, model files, nodes, API graph, execution
  adapter and prompt template. Any of those changing invalidates the evidence.
* An unready image capability blocks AI generation only. Upload, selection,
  promotion and H3 production continue to work.
* Promoting a candidate still consumes a one-use grant bound to the candidate's
  content hash, identity, action and confirmation snapshot. Generation authority
  never includes promotion authority.
