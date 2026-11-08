# ADR 0009: Shine Assets global library and project identity bridge

- Status: Implemented; review and local capability verification remain open
- Date: 2026-11-08
- Parent architecture: [ADR 0008](0008-asset-center-shared-kernel.md)
- Migration: `f180b0030001` (down revision `f180b0020001`)

## Decision

Shine Assets owns global media and version identity without a project foreign key. Assigning a global version to a film project creates a new project `Asset` row and an immutable `LibraryProjectLink`. The global media bytes may be content-addressed and shared, while each project retains its own asset identity, entity name, role, authorization scope and frozen version reference.

The migration preserves the required project foreign keys for `Asset`, `VoiceProfile`, `RightsRecord` and existing candidate constraints. It changes only the project-scoped `RightsRecord.project_id` delete action to `CASCADE`, so the project-local rights snapshot is removed with its project; no project ID is made nullable. No placeholder project is created to host global assets.

## Tables and ownership

| Table | Identity and responsibility | Mutability |
|---|---|---|
| `library_assets` | Projectless image/audio container, category, title, tags, favorite, archive state and selected version pointer | Metadata uses `state_version`; archive is explicit |
| `library_media` | Hash-verified uploaded source, reference, mask or imported audio with detected media facts and rights record | File bytes are immutable and addressed by hash |
| `library_versions` | Immutable candidate/formal version, ordinal, parent, prompt, source mode, rights, QC, manifest and provenance | Candidate QC state advances; file/hash and lineage do not change |
| `library_source_references` | Ordered immutable media/version reference, role, hash, optional mask hash and authorization snapshot | Append-only with its version |
| `library_rights` | Provenance, license declaration, allowed projects/uses, commercial scope, expiration, revocation and approver | Snapshot changes require expected-hash compare and audit |
| `library_render_tasks` | Explicit image/voice request, idempotency, queue state, claim, lease, external result and candidate link | State changes use conditional version checks |
| `library_task_attempts` | Submit intent or deterministic local operation, runtime/graph identity, outcome and output hash | Append-only evidence |
| `library_voice_profiles` | Global voice version, conditioning media, voice hash and human freeze state | Freeze is an explicit audibility and rights decision |
| `library_project_links` | Global version → new project `Asset` and optional project `VoiceProfile`, role/entity/scope and source snapshot | Immutable project-use record |
| `library_approval_grants` | Short-lived one-time grant bound to action target and immutable snapshot | Consumed once in the promotion/freeze transaction |

`library_project_links` also enforces a unique semantic assignment key over project, version, role, entity and use scope, in addition to its request idempotency key. This prevents parallel requests with different keys from creating duplicate semantic assignments.

## Candidate, promotion and project flow

1. An upload or Worker result creates a media row and candidate version. Content is decoded, hashed and stored by the existing content-addressed store.
2. Technical QC verifies a fresh hash and full media decode. Voice dialogue keeps the existing transcript, word alignment and hard-error checks.
3. A person reviews the candidate. A short-lived grant is bound to the version snapshot and current rights state.
4. Promotion consumes the grant, updates the version and global current-version pointer atomically, and writes the audit event. A global metadata update never rewrites prior versions.
5. Assignment rechecks the promoted version, bytes, rights and optional current material-requirement hash. It creates a new project `Asset` (and separate project `VoiceProfile` where needed), a link and audit event in one transaction.
6. Returning a locked project asset to the global library is an explicit save operation with rights confirmation and source hash retention. It never borrows another project's IDs.

A project frozen on V1 stays bound to V1 when the global pointer moves to V2. Removing a project link does not delete global bytes or another project's records.

Deleting a project removes its project-local `Asset`, link and project-scoped rights rows. The global asset container, immutable versions, global rights, content-addressed bytes and every other project's local identity stay intact. `library_assets`, `library_versions`, and referenced source media continue to use restrictive deletion rules so a project cleanup cannot remove shared global history.

## Image task evidence

T2I remains on the existing pinned API graph and shared `resolve_runtime`. Edit tasks compile the pinned official Qwen-Image-2.1 edit template into the registered API subgraph. The first input is the target, later inputs are numbered in UI order, and prompt text receives an explicit `<imageN>` role map. The actual input bytes are re-read by hash and current rights snapshot before upload and checked again before the `/prompt` submit intent. The ComfyUI graph is checked against current `object_info` before submission.

The official edit example exposes ten UI image slots and has no native mask input. Inpainting uses the actual uploaded mask after harvest: white selects generated pixels and black copies the original target pixel exactly. The mask hash, dimensions, semantics and result composite hash are preserved as version evidence. Inpainting stays `PENDING_LOCAL_VALIDATION` until a real render proves the complete path.

RGBA has two distinct outcomes. The deterministic manual cutout operation turns a user-drawn white-subject/black-background mask into real alpha pixels and is software-ready; the output inherits source-image rights. Prompt-based native Qwen transparency remains pending until a real model output has decoded transparent pixels. A T2I run cannot be labelled transparent from its filename or checkbox.

## Worker and failure semantics

Image/voice tasks are claimed serially by the existing Worker. The projectless image/voice queue selects the oldest eligible request and dispatches one request per Worker tick. Model work takes the existing exclusive `ResourceLease`; no background daemon or second scheduler is added. H3 production keeps priority. A local manual alpha cutout uses the same durable Worker and candidate ledger but does not acquire GPU memory because it performs only bounded PNG pixel processing.

The lease stays active until cleanup is confirmed. ComfyUI cleanup requires an empty queue, a successful `/free`, and runtime memory stats with no live CUDA tensor allocation. Voice SDK processes must exit; the local HTTP provider must acknowledge `released: true`. TTS releases the synthesis model before ASR and releases ASR before alignment. A missing or unknown release proof blocks the next stage and retains reconciliation state instead of timing out into reuse.

Comfy `/prompt` is preceded by a durable submit intent. A response without a prompt ID or an uncertain transport failure remains `UNKNOWN`; only read-only history reconciliation can settle it. A separate new-attempt endpoint accepts only a failed task whose outcome is proven `NOT_SENT`, requires explicit confirmation and a new idempotency identity, and revalidates all user and capability state. It rejects `UNKNOWN` tasks.

## Rights and license boundary

User-declared source rights are not model rights. Qwen-Image-2.1 is recorded under the upstream 2026-09-20 research license, which does not imply commercial permission. Derived voice rights inherit speaker consent and its expiry/revocation boundary. Imported sound effects and music are managed as files; no generation provider is fabricated.

## Rejected alternatives

- Making project IDs nullable on the existing project asset table: this weakens the director data contract and project boundary.
- Creating a synthetic “global library” project: this misrepresents user work and contaminates project listing, permissions and production state.
- Sharing project `Asset.id`, `VoiceProfile.id` or `RightsRecord.id` across films: this leaks project identity and allows role/consent state to bleed between projects.
- Treating local UI masks as Comfy inputs without server-side byte validation: this loses pixel alignment and cannot prove which pixels were edited.
- Updating every project's pinned version when the global pointer changes: this violates frozen production snapshots.
