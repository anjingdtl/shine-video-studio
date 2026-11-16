"""Action-scoped target fingerprint for support authorizations.

The snapshot revision covers the setup verdict and the managed-process state.
A preview must also be invalidated when the *resources that action targets*
change: the installation identity, the instance binding, the verification
receipts, the workflow contract or the release targets.  ``target_fingerprint``
stores that action-scoped fact set; ``NULL`` means it could not be read and the
preview may never be executed.

Revision ID: f180f0010004
Revises: f180f0010003
Create Date: 2026-11-16
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010004"
down_revision = "f180f0010003"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {row["name"] for row in inspector.get_columns(table)}


def upgrade():
    columns = _columns(op.get_bind(), "support_actions")
    if columns and "target_fingerprint" not in columns:
        op.add_column(
            "support_actions",
            sa.Column("target_fingerprint", sa.String(length=64), nullable=True),
        )


def downgrade():
    columns = _columns(op.get_bind(), "support_actions")
    if "target_fingerprint" in columns:
        op.drop_column("support_actions", "target_fingerprint")
