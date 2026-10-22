# ADR 0003: controlled pre-production in the existing ledger

Accepted for V1.3 software validation, 2026-10-22.

V1.0's prohibition on AI master creation is refined: a generator may create an immutable
candidate. It cannot create an approved master. Authenticated promotion, after content,
rights and technical checks, creates a new existing `Asset` without modifying historical
masters. Fake candidates remain `offline_only` after promotion and cannot enter real H3 jobs.

Preparation tables share the existing SQLAlchemy metadata and SQLite file. Media operations
persist intent in `AgentTaskRecord`; the original Job/Attempt/QC ledger, H3 compiler and
seven crew roles remain authoritative. ComfyOps is an eighth typed proposal role on the
same Gateway, with no shell, HTTP, filesystem, database or approval tools for the model.

Human privileges use the existing server-provisioned operator identity, plus one-use,
five-minute grants bound to action, target and full state digest. Install approvals also
expire after thirty minutes. Fake and local install execution are separate choices; the
Web API never executes local installation. The Windows CLI accepts a current approved
local plan and prompts the human for the same server-configured credential.

A project opts into the pre-production contract by analyzing its script. Such projects
must freeze a ready manifest before Job creation, and Worker rechecks it before submitting.
Legacy projects retain their previous contract. Upstream asset/rights/style/voice/workflow
changes invalidate the computed manifest; already passed artifacts are never rewritten.

One SQLite resource ledger covers the H3 Worker and media providers. Expired or ambiguous
leases require reconciliation. The scheduler does not infer that expiry unloads a GPU model.
Failure to confirm model release keeps the reservation blocked. Hardware budgets are
estimates pending local measurements.
