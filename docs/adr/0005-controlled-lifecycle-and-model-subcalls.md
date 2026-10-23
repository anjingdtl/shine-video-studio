# ADR 0005 — Controlled lifecycle and model subcalls

2026-10-22. Extends ADR 0004; no new database or scheduler.

Every preparation tool subcall (image, design/clone, conditioning, TTS, ASR, alignment,
VLM) persists an AgentTaskRecord before entering the existing ResourceLease gate.
The task records its parent, process birth identity, input digest, resource and result
provenance. Comfy image acknowledgement is committed before polling. Uncertain results
block identical calls; reconciliation consumes a human grant before contacting a provider,
checks live owners and idle external queues, confirms release and records ABANDONED,
never a successful result. Reconciliation itself has a durable owner. H3 releases its
exclusive lease only after unloading before admitting QC models. /free acknowledgement
alone is insufficient: the client also observes an idle queue and zero allocated PyTorch
memory using system_stats. Unknown counters retain the reservation.

Instance plans and attempts reuse InstallationPlan/InstallationAttempt. L2 creates only
an app-owned isolated instance. L3 plans bind root, current tree, Python executable digest,
fixed package manifests, argv, actor and expiring approval. Dependencies install only from
hashed local wheels with pip --no-index --require-hashes. Third-party code is never run in
cloud tests. Process stops verify PID plus birth time and target only owned instances.
comfy-cli's global stop is deliberately not used for per-instance process control.

Verified snapshots precede mutation. Durable intent, command ownership, swap paths and
backup manifests permit explicit reconciliation after process death. Recovery never reruns
an uncertain command. A separate approved rollback restores the verified snapshot. Existing
external instances are protected; their backup operation copies only an explicitly selected
allowlisted root into app-owned storage, checks idle before/after and preserves source identity.
It does not adopt, restart or overwrite production instances.

Frozen production sets bind workflow/model/node package snapshots. Per-shot production
checks validate actual RenderRequest bindings against that set, while unrelated revoked
assets do not stop independent shots. A new freeze never authorizes an old queued Job.
Structured scripts retain exact dialogue and critical entity dependencies. Missing or
unverified media/continuity contracts fail closed. Fake evidence stays Fake across UI modes.

Qwen voice integration runs the pinned official SDK in-process using verified local model
packages; Comfy image integration uses existing ComfyUI HTTP APIs. SDK imports are lazy,
weights are local-only, runtime versions and bytes are verified. Unsupported expressive
parameters or reference formats are explicit errors. ASR without speaker/confidence proof
requires human listening; hard transcript/timing failures cannot be waived.

HTTP transport does not establish real evidence. Preserve an explicit Mock flag in VLM/ASR
responses and record provider/result flags separately in the task ledger. Resource release
refuses live H3 ownership, and the final submission writer persists an expired-lease rejection.
