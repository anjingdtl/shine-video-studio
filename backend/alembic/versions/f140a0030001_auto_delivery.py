"""The automatic delivery: durable orders, edit intent binding on the series.

A series that finished with every shot passed must reach a playable reel
without a script or an API call.  The delivery_orders table is the durable
unit of that work: it is created in the same transaction that completes the
series, survives a crash, retries a bounded number of times and reports
itself when it cannot continue.  The series also records which frozen edit
intent its automatic delivery inherits, and the delivered result itself.

Both changes are additive; no historical row is touched.
"""

import sqlalchemy as sa

from alembic import op

revision = "f140a0030001"
down_revision = "f140a0020001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "delivery_orders",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("series_id", sa.String(length=36), nullable=False),
        sa.Column("origin", sa.String(length=24), nullable=False, server_default="series_auto"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
                sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("shots_digest", sa.String(length=64), nullable=True),
        sa.Column("edit_digest", sa.String(length=64), nullable=True),
        sa.Column("edl_payload", sa.JSON(), nullable=True),
        sa.Column("edl_source", sa.String(length=40), nullable=True),
        sa.Column("edit_rules", sa.JSON(), nullable=True),
        sa.Column("subtitle_revision", sa.Integer(), nullable=True),
        sa.Column("subtitle_sha256", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_note", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["series_id"], ["production_series.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("series_id", "version", name="uq_delivery_order_series_version"),
    )
    op.create_index("ix_delivery_orders_runnable", "delivery_orders", ["status", "updated_at"])
    op.add_column("production_series", sa.Column("edit_intent_artifact_id", sa.String(length=36), nullable=True))
    op.add_column("production_series", sa.Column("delivery_result", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("production_series", "delivery_result")
    op.drop_column("production_series", "edit_intent_artifact_id")
    op.drop_index("ix_delivery_orders_runnable", table_name="delivery_orders")
    op.drop_table("delivery_orders")
