"""Restore the production-series job reference on older Windows ledgers."""

import sqlalchemy as sa

from alembic import op

revision = "f180c0020001"
down_revision = "f180c0010001"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().as_sql:
        return  # the original CREATE TABLE in the SQL already contains the constraint

    table = "production_series_items"
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys(table)
    if any(fk["constrained_columns"] == ["job_id"] and fk["referred_table"] == "jobs" for fk in foreign_keys):
        return

    # Match ON DELETE SET NULL for jobs already absent from the legacy ledger.
    # No table references the item rows, so the SQLite batch copy cannot cascade.
    op.execute(sa.text(
        "UPDATE production_series_items SET job_id = NULL "
        "WHERE job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.id = production_series_items.job_id)"
    ))
    with op.batch_alter_table(table) as batch:
        batch.create_foreign_key("fk_production_series_items_job_id", "jobs", ["job_id"], ["id"], ondelete="SET NULL")


def downgrade():
    # Earlier revisions already declare this constraint. Keep the repaired schema.
    pass
