# ADR 0004: approvals, frozen references and interrupted installation

Status: implemented scope; complete V1.3 acceptance remains partial.

All new master approval/lock actions require the existing server-provisioned local operator
and one-use action/target/state grants, including legacy uploads and projects. Candidate
promotion consumes authority in its business service. Historical approvals remain unchanged.
Clone execution authority is distinct from consent: it binds the complete request and current
consent snapshot, independently of the route name. It is never stored as a reusable credential.

The actual RenderRequest must be a subset of the frozen asset/voice collection. The Worker
rechecks after preparation and holds the SQLite writer across the final bounded /prompt call,
serializing revocation with submission. The graph submit intent is committed beforehand, so
an ambiguous result still requires reconciliation. This temporarily blocks other SQLite writers;
no second authorization database or scheduler is introduced. Result guards and human review also
recheck actual references. Project-wide readiness scoping remains an explicit unfinished item.

Installation and recovery share a stable OS advisory guard inode. A separate owner marker
contains PID and OS process birth identity. Missing owner evidence is an explicit blocker,
never permission to delete a lock. Journals are durably written before directory changes.
VERIFIED commits the new version; prior failures restore the backup, subsequent recovery finishes
cleanup. Recovery never runs arbitrary commands or terminates an external user process.
