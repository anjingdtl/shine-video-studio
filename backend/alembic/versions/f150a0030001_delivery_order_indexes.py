"""Delivery-order foreign-key indexes: close the model/migration drift.

The ``delivery_orders`` model (backend/shine/media/delivery_models.py) always
declared ``index=True`` on ``project_id`` and ``series_id``, but the original
auto-delivery migration f140a0030001 never created those two indexes, so
``alembic check`` reported drift on every upgraded database:

    ix_delivery_orders_project_id
    ix_delivery_orders_series_id

This forward-only migration creates both indexes idempotently (``IF NOT
EXISTS``) so databases created via ``Base.metadata.create_all`` (which already
have them) upgrade cleanly too. Published history is not modified.
"""

from alembic import op

revision = "f150a0030001"
down_revision = "f150a0020001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_delivery_orders_project_id", "delivery_orders", ["project_id"],
        unique=False, if_not_exists=True,
    )
    op.create_index(
        "ix_delivery_orders_series_id", "delivery_orders", ["series_id"],
        unique=False, if_not_exists=True,
    )


def downgrade():
    op.drop_index("ix_delivery_orders_series_id", table_name="delivery_orders", if_exists=True)
    op.drop_index("ix_delivery_orders_project_id", table_name="delivery_orders", if_exists=True)
