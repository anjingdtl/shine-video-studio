"""Run control reaches the series; frozen repair policy reaches the job.

Three additive columns close the gaps the second independent acceptance found:

* ``production_series.control_reason`` — a run-level pause/cancel records *who*
  asked, so a resume only undoes the pauses that run caused and never steals a
  pause that belongs to another control path.
* ``production_series.max_semantic_retries`` / ``auto_repair`` — the frozen
  policy of the owning whole-film run travels with the series and reaches every
  job the series creates, including the dependent shots the Worker queues later.
* ``jobs.auto_repair`` — a job-level override of the project default, set from
  the frozen package policy when the job belongs to a run.
* ``production_run_episodes.post_contract`` — the confirmed post-production
  contract of one episode (cut points, chosen subtitle revision).  A revision of
  it creates a new delivery version and never re-renders H3.

All are nullable: ``None`` keeps the previous behaviour (project default,
no recorded control cause, full-take cut without subtitles), so existing rows
and historical contracts are unchanged.  Plain column DDL, no table rebuild — a
rebuild of ``jobs`` would cascade-delete its attempts and QC reports during a
downgrade.
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0010001"
down_revision = "f180a0010001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("production_series", sa.Column("control_reason", sa.String(200), nullable=True))
    op.add_column("production_series", sa.Column("max_semantic_retries", sa.Integer(), nullable=True))
    op.add_column("production_series", sa.Column("auto_repair", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("auto_repair", sa.Boolean(), nullable=True))
    op.add_column("production_run_episodes", sa.Column("post_contract", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("production_run_episodes", "post_contract")
    op.drop_column("jobs", "auto_repair")
    op.drop_column("production_series", "auto_repair")
    op.drop_column("production_series", "max_semantic_retries")
    op.drop_column("production_series", "control_reason")
