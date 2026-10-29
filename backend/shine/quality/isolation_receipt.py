"""Validate a local isolated-review receipt without treating it as a QC verdict."""

import hashlib
import json
import re
from uuid import UUID


def validate_receipt_fields(receipt):
    if not isinstance(receipt, dict):
        raise ValueError("ISOLATION_RECEIPT_INVALID")
    try:
        stage_id = receipt.get("stage_id")
        if not isinstance(stage_id, str) or str(UUID(stage_id)) != stage_id:
            raise ValueError
        if (receipt.get("method") != "POST"
                or receipt.get("path") not in {"/v1/chat/completions", "/v1/audio/transcriptions"}
                or type(receipt.get("pid")) is not int or receipt["pid"] <= 0
                or not isinstance(receipt.get("birth"), str) or not receipt["birth"].isdigit()
                or receipt.get("joined") is not True or type(receipt.get("exitcode")) is not int
                or receipt["exitcode"] != 0 or type(receipt.get("group_active_processes")) is not int
                or receipt["group_active_processes"] != 0 or type(receipt.get("group_members_waited")) is not int
                or receipt["group_members_waited"] < 0):
            raise ValueError
        for key in ("request_sha256", "response_sha256", "runtime_sha256"):
            if not isinstance(receipt.get(key), str) or not re.fullmatch("[0-9a-f]{64}", receipt[key]):
                raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("ISOLATION_RECEIPT_INVALID") from exc
    return dict(receipt)


def validate_isolation_receipt(body):
    if not isinstance(body, dict):
        raise ValueError("ISOLATION_RECEIPT_INVALID")
    receipt = body.get("isolation_receipt")
    if receipt is None:
        return None  # Legacy endpoints can be used outside the isolated handoff.
    receipt = validate_receipt_fields(receipt)
    try:
        unsigned = {key: value for key, value in body.items() if key != "isolation_receipt"}
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf8")
        if hashlib.sha256(canonical).hexdigest() != receipt["response_sha256"]:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("ISOLATION_RECEIPT_INVALID") from exc
    return dict(receipt)
