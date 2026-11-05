"""Domain states for the material-preparation ledger.

These are durable server-side states mapped onto the shared SQLite ledger, not
frontend display states.  A state that only exists in the browser cannot
survive a Worker restart, and the whole point of the batch/item/attempt tables
is that the run resumes from the row.
"""

from __future__ import annotations

# --- plan revision ---------------------------------------------------------
PLAN_ANALYZING = "ANALYZING"
PLAN_DRAFT = "DRAFT"
PLAN_CONFIRMED = "CONFIRMED"
PLAN_PREPARING = "PREPARING"
PLAN_MATERIALS_READY = "MATERIALS_READY"
PLAN_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
PLAN_STATES = frozenset(
    {
        PLAN_ANALYZING,
        PLAN_DRAFT,
        PLAN_CONFIRMED,
        PLAN_PREPARING,
        PLAN_MATERIALS_READY,
        PLAN_NEEDS_CLARIFICATION,
    }
)

# --- single item -----------------------------------------------------------
ITEM_QUEUED = "QUEUED"
ITEM_BRIEF_RUNNING = "BRIEF_RUNNING"
ITEM_BRIEF_READY = "BRIEF_READY"
ITEM_WAITING_GPU = "WAITING_GPU"
ITEM_SUBMIT_INTENT = "SUBMIT_INTENT"
ITEM_RUNNING = "RUNNING"
ITEM_OUTPUT_STORED = "OUTPUT_STORED"
ITEM_QC_PENDING = "QC_PENDING"
ITEM_CANDIDATE_READY = "CANDIDATE_READY"
ITEM_FAILED = "FAILED"
ITEM_NEEDS_REVIEW = "NEEDS_REVIEW"
ITEM_CANCELLED = "CANCELLED"

ITEM_TERMINAL = frozenset({ITEM_CANDIDATE_READY, ITEM_FAILED, ITEM_NEEDS_REVIEW, ITEM_CANCELLED})
"""States whose execution intent is finished, so the uniqueness key is cleared.

``NEEDS_REVIEW`` is terminal for scheduling but not for reconciliation: an
unknown external outcome stays recorded forever and is never resubmitted.
"""

ITEM_CLAIMABLE = frozenset({ITEM_QUEUED, ITEM_BRIEF_READY, ITEM_WAITING_GPU})
"""States a Worker may claim.  ``SUBMIT_INTENT``/``RUNNING`` are excluded: an
item that already persisted a submit intent is reconciled, never re-claimed
into a second external submission.

``FAILED`` is excluded as well.  A failed item is terminal for scheduling: it
waits for the operator to resume or regenerate it.  When a failed item stayed
claimable, the Worker re-claimed it in a tight loop, which burned the CPU,
starved the heartbeat and kept every other queued item from ever running."""

ITEM_IN_FLIGHT = frozenset(
    {
        ITEM_BRIEF_RUNNING,
        ITEM_BRIEF_READY,
        ITEM_WAITING_GPU,
        ITEM_SUBMIT_INTENT,
        ITEM_RUNNING,
        ITEM_OUTPUT_STORED,
        ITEM_QC_PENDING,
    }
)
"""Started but not concluded. A batch holding one of these is still running."""

# --- batch -----------------------------------------------------------------
RUN_QUEUED = "QUEUED"
RUN_RUNNING = "RUNNING"
RUN_COMPLETED = "COMPLETED"
RUN_PARTIAL_FAILED = "PARTIAL_FAILED"
RUN_NEEDS_REVIEW = "NEEDS_REVIEW"
RUN_CANCEL_REQUESTED = "CANCEL_REQUESTED"
RUN_CANCELLED = "CANCELLED"
RUN_TERMINAL = frozenset({RUN_COMPLETED, RUN_PARTIAL_FAILED, RUN_NEEDS_REVIEW, RUN_CANCELLED})

# --- image attempt ---------------------------------------------------------
ATTEMPT_PREPARED = "PREPARED"
ATTEMPT_SUBMIT_INTENT = "SUBMIT_INTENT"
ATTEMPT_SUBMITTED = "SUBMITTED"
ATTEMPT_POLLING = "POLLING"
ATTEMPT_OUTPUT_STORED = "OUTPUT_STORED"
ATTEMPT_ADOPTED = "ADOPTED"
ATTEMPT_QC_PENDING = "QC_PENDING"
ATTEMPT_SUCCEEDED = "SUCCEEDED"
ATTEMPT_FAILED = "FAILED"
ATTEMPT_UNKNOWN = "UNKNOWN"
ATTEMPT_CANCELLED = "CANCELLED"
ATTEMPT_TERMINAL = frozenset({ATTEMPT_SUCCEEDED, ATTEMPT_FAILED, ATTEMPT_UNKNOWN, ATTEMPT_CANCELLED})

# --- external outcome ------------------------------------------------------
OUTCOME_NOT_SENT = "NOT_SENT"
OUTCOME_ACKNOWLEDGED = "ACKNOWLEDGED"
OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNKNOWN = "UNKNOWN"
"""An unknown external outcome is only ever reconciled, never auto-resent."""

EXECUTION_MODE_REAL = "real"
"""The only mode the formal image entrypoint accepts.

There is deliberately no ``fake``/``auto`` value: a real entrypoint that could
be asked for a mock would eventually be handed one by an unconfigured machine,
and a Fake candidate must never be able to become real verification.
"""

RECONCILE_ADOPTED_PERSISTED_IMAGE = "adopted_persisted_image"
RECONCILE_COMPLETE = "RECONCILE_COMPLETE"
RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
