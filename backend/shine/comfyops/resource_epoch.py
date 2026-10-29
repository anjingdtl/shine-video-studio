"""Monotonic ledger for resource admissions and model starts.

Callers hold SQLite's writer lock; a timestamp or an expired lease is not an
ordering proof. This ledger tracks application-managed activity only.
"""

from fastapi import HTTPException

from shine.models import AppSetting

KEY = "physical-resource-epoch"


def read_epoch(db):
    row = db.get(AppSetting, KEY)
    if row is None:
        return {"epoch": 0, "kind": None, "lease_id": None}
    value = row.value
    if (not isinstance(value, dict) or type(value.get("epoch")) is not int or value["epoch"] < 0
            or value.get("kind") not in {"lease", "model"} or not isinstance(value.get("lease_id"), str)):
        raise HTTPException(409, "RESOURCE_EPOCH_INVALID")
    return dict(value)


def advance_epoch(db, lease_id, kind):
    if not isinstance(lease_id, str) or not lease_id or kind not in {"lease", "model"}:
        raise HTTPException(409, "RESOURCE_EPOCH_INVALID")
    current = read_epoch(db)
    value = {"epoch": current["epoch"] + 1, "kind": kind, "lease_id": lease_id}
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=value))
    else:
        row.value = value
    db.flush()
    return value
