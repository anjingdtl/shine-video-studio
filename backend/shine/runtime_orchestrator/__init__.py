"""Unified runtime control plane: one lifecycle executor per installation.

The Web workbench, the asset center, the director mode and the resident
Worker used to each decide on their own when a ComfyUI process should exist.
This package is the single control surface mandated by the Runtime
Orchestrator plan:

* the API only records **control intents** (short-lived START / STOP / PROBE /
  RECONCILE / RELEASE operations) and reads derived state;
* the resident Worker is the only executor that turns an intent into a real
  process action, through the existing ComfyOps functions;
* the GPU admission ledger stays the existing ``ResourceScheduler``; no
  second queue is introduced;
* the same physical installation identity guards every lifecycle action, so
  H3 and the image runtime cannot each start "their own" renderer on one
  ComfyUI install.

Nothing here persists business truth that already lives in jobs, attempts,
artifacts, leases or the OS: the operation row records only the control
decision and its outcome.
"""

from shine.runtime_orchestrator.contracts import (
    ACTION_ENSURE,
    ACTION_PROBE,
    ACTION_RECONCILE,
    ACTION_RELEASE,
    ACTION_STOP,
    BUSY_ACTIONS,
    OPERATION_STATUSES,
    OperationError,
    orchestrator_enabled,
)
from shine.runtime_orchestrator.guard import renderer_lifecycle_guard
from shine.runtime_orchestrator.identity import installation_activity
from shine.runtime_orchestrator.operations import (
    claim_pending_operations,
    create_or_get_operation,
    fail_operation,
    get_operation,
    reap_orphaned_operations,
    succeed_operation,
)

__all__ = [
    "ACTION_ENSURE",
    "ACTION_PROBE",
    "ACTION_RECONCILE",
    "ACTION_RELEASE",
    "ACTION_STOP",
    "BUSY_ACTIONS",
    "OPERATION_STATUSES",
    "OperationError",
    "orchestrator_enabled",
    "renderer_lifecycle_guard",
    "installation_activity",
    "claim_pending_operations",
    "create_or_get_operation",
    "fail_operation",
    "get_operation",
    "reap_orphaned_operations",
    "succeed_operation",
]
