"""Style LoRA assets registry (single authoritative ledger for discovered & imported LoRAs).

Only immutable file/metadata identity is persisted here; live ComfyUI visibility
and runtime status are projected dynamically on read and never cached in SQLite.

Revision ID: f180f0010009
Revises: f180f0010008
Create Date: 2026-11-25
"""

import sqlalchemy as sa

from alembic import op

revision = "f180f0010009"
down_revision = "f180f0010008"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade():
    if "style_lora_assets" not in _tables():
        op.create_table(
            "style_lora_assets",
            sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("display_name", sa.String(length=160), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.String(length=1024), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("default_strength", sa.Float(), nullable=False, server_default=sa.text("0.65")),
            sa.Column("trigger_word", sa.String(length=160), nullable=False, server_default=sa.text("''")),
            sa.Column("origin", sa.String(length=24), nullable=False, server_default=sa.text("'imported'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_style_lora_assets_sha256", "style_lora_assets", ["sha256"], unique=True)


def downgrade():
    if "style_lora_assets" in _tables():
        op.drop_index("ix_style_lora_assets_sha256", table_name="style_lora_assets")
        op.drop_table("style_lora_assets")
