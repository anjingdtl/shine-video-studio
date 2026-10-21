"""Add graph-derived workflow identity without touching historical requests or media."""

import sqlalchemy as sa

from alembic import op

revision = "f121a0010001"
down_revision = "e8f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_profiles", sa.Column("model_identity", sa.JSON(), nullable=False, server_default="{}"))
    from shine.comfy.capabilities import profile_metadata

    table = sa.table(
        "workflow_profiles",
        sa.column("id"),
        sa.column("graph", sa.JSON()),
        sa.column("mode"),
        sa.column("parameter_map", sa.JSON()),
        sa.column("media_transfers", sa.JSON()),
        sa.column("checkpoint_id"),
        sa.column("model_identity", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings():
        identity = profile_metadata(row["graph"], row["mode"], row["parameter_map"], row["media_transfers"])
        values = {"model_identity": identity}
        if identity["variant"] == "Ref2VA INT8 ConvRot":
            values["checkpoint_id"] = identity["weight_files"][0]
        connection.execute(table.update().where(table.c.id == row["id"]).values(**values))


def downgrade():
    op.drop_column("workflow_profiles", "model_identity")
