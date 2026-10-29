"""Pin the loaded review client, admission and Worker code for native calibration."""

import hashlib
import json
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_FILES = (
    "shine/quality/client_identity.py",
    "shine/quality/basic_character_reviewer.py",
    "shine/quality/basic_review.py",
    "shine/quality/semantic.py",
    "shine/quality/input_assessment.py",
    "shine/quality/gates.py",
    "shine/quality/runtime.py",
    "shine/quality/identity.py",
    "shine/quality/isolation_receipt.py",
    "shine/quality/review_admission.py",
    "shine/quality/review_release.py",
    "shine/media/frames.py",
    "shine/schemas.py",
    "shine/worker.py",
    "shine/comfy/client.py",
    "shine/comfyops/model_calls.py",
    "shine/comfyops/resource_epoch.py",
    "shine/comfyops/handoff_journal.py",
    "shine/comfyops/handoff_preparation.py",
    "shine/comfyops/handoff_activation.py",
    "shine/comfyops/handoff_transfer.py",
    "shine/comfyops/handoff_idle.py",
    "shine/comfyops/native.py",
    "shine/comfyops/preproduction.py",
    "shine/api_preparation.py",
)


def _source_identity():
    try:
        files = {name: hashlib.sha256((_BACKEND / name).read_bytes()).hexdigest() for name in _FILES}
    except OSError as exc:
        raise ValueError("REVIEW_CLIENT_SOURCE_UNAVAILABLE") from exc
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_LOADED_IDENTITY = _source_identity()


def review_client_identity():
    current = _source_identity()
    if current != _LOADED_IDENTITY:
        raise ValueError("REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD")
    return current
