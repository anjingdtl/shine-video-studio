"""Record the base model and image prompt metadata on the shared LoRA ledger."""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010010"
down_revision = "f180f0010009"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"]: column["type"] for column in inspector.get_columns("style_lora_assets")}
    indexes = {index["name"] for index in inspector.get_indexes("style_lora_assets")}
    with op.batch_alter_table("style_lora_assets") as batch:
        for name, width, default in (
            ("base_model", 32, "minimax_h3"),
            ("negative_trigger_word", 500, ""),
            ("trigger_position", 16, "suffix"),
        ):
            if name not in columns:
                batch.add_column(sa.Column(name, sa.String(width), nullable=False, server_default=default))
        if columns["trigger_word"].length != 500:
            batch.alter_column("trigger_word", existing_type=columns["trigger_word"], type_=sa.String(500))
        if "ix_style_lora_assets_base_model" not in indexes:
            batch.create_index("ix_style_lora_assets_base_model", ["base_model"])


def downgrade():
    with op.batch_alter_table("style_lora_assets") as batch:
        batch.drop_index("ix_style_lora_assets_base_model")
        batch.drop_column("trigger_position")
        batch.drop_column("negative_trigger_word")
        batch.drop_column("base_model")
        batch.alter_column("trigger_word", existing_type=sa.String(500), type_=sa.String(160))
