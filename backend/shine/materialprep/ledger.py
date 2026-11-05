"""Shared conditional writes for the material-preparation ledger.

Every write a Worker makes to an item is conditional on the state version it
read.  The rule is the same for the image path and for the brief path, so the
implementation lives here once:

* a conditional update that touches no row means another Worker owns the item
  now, and the caller must stop instead of writing over the new owner;
* the returned value is the new state version, or ``None`` when the claim was
  lost.
"""

from __future__ import annotations

from sqlalchemy import update

from shine.materialprep.models import MaterialItem


def touch_item(factory, item_id: str, expected_version: int, **values) -> int | None:
    """A conditional item write; returns the new version, or None if the claim is gone.

    ``None`` means another Worker owns this item now. The caller must stop and
    must not write again, which is how an expired claim is kept harmless.
    """
    with factory.begin() as db:
        result = db.execute(
            update(MaterialItem)
            .where(MaterialItem.id == item_id, MaterialItem.state_version == expected_version)
            .values(state_version=expected_version + 1, **values)
        )
        return expected_version + 1 if result.rowcount == 1 else None

