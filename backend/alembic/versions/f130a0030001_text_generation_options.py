"""Explicit per-provider text generation limits, preserving historical defaults."""
import sqlalchemy as sa

from alembic import op

revision = "f130a0030001"
down_revision = "f130a0020001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("provider_profiles", sa.Column("generation_options", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("provider_profiles", "generation_options")
