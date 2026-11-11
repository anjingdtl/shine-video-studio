"""Typed contracts shared by the control plane's API, executor and snapshot."""

from __future__ import annotations

import os

from fastapi import HTTPException

# Short-lived lifecycle actions only.  Shots, assets and other business work
# keep flowing through the existing job/library tables, never through here.
ACTION_ENSURE = "ensure"
ACTION_STOP = "stop"
ACTION_PROBE = "probe"
ACTION_RECONCILE = "reconcile"
ACTION_RELEASE = "release"
KNOWN_ACTIONS = (ACTION_ENSURE, ACTION_STOP, ACTION_PROBE, ACTION_RECONCILE, ACTION_RELEASE)

# Actions that may not run while another control operation on the same
# installation is executing (probe is side-effect free and never excluded).
BUSY_ACTIONS = frozenset({ACTION_ENSURE, ACTION_STOP, ACTION_RELEASE})

OPERATION_STATUSES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "REFUSED")

# Terminal operation statuses (no executor may rewrite them).
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "REFUSED"})

# Staged rollout switch: 1 (default) routes the legacy HTTP process controls
# through the unified executor; 0 restores the pre-orchestrator direct calls.
# The two paths never share execution rights in one run.
ENV_FLAG = "SHINE_RUNTIME_ORCHESTRATOR_ENABLED"

# The image renderer's durable startup record prefix.  Defined here (not in
# comfyops.image_renderer) so the read-only identity scanner and the executor
# can share it without an import cycle.
IMAGE_STARTUP_PREFIX = "renderer-startup:image:"


def orchestrator_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "1") != "0"


class OperationError(HTTPException):
    """A control-intent refusal with a machine-readable code."""

    def __init__(self, status_code: int, code: str, detail: str = ""):
        super().__init__(status_code, {"code": code, "detail": detail or code})
