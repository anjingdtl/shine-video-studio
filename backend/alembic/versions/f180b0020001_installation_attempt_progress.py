"""An installation attempt becomes a persistent task with a re-attachable state.

The repair plan (docs/NEW_USER_JOURNEY_REPAIR_PLAN_20261107.md, phase B) stops
the installation from living inside one long HTTP request.  The API queues an
attempt, the resident Worker claims it, and the browser re-attaches by attempt
id after a refresh or a restart.  These columns hold that state:

* ``stage`` — the honest stage vocabulary (QUEUED, DOWNLOADING, RETRY_WAIT,
  VERIFYING, INSTALLING, FILES_VERIFIED_PENDING_RUNTIME, ...).  Files on disk
  are not a runtime, so the stage never claims one.
* ``confirmed_bytes`` / ``total_bytes`` / ``speed_bps`` — progress written
  from the ``.part`` file that is really on disk, never from a client estimate.
* ``current_component`` / ``current_file`` — what the attempt is doing now.
* ``retry_count``, ``error_code``, ``error_message`` — the honest failure
  state; a retryable failure is retryable, an uncertain one is not.
* ``heartbeat_at``, ``lease_owner``, ``lease_expires_at`` — the Worker claim,
  so a dead owner leaves a claimable attempt instead of a locked one.
* ``cancel_requested`` / ``pause_requested`` — an explicit stop request.  Only
  an explicit cancel asks the server to stop; closing the browser does not.

Every column is additive and defaulted, so historical rows keep their meaning
(a legacy attempt has no stage and stays outside the new state machine).
"""

import sqlalchemy as sa

from alembic import op

revision = "f180b0020001"
down_revision = "f180b0010001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("installation_attempts", sa.Column("stage", sa.String(40), nullable=True))
    op.add_column(
        "installation_attempts",
        sa.Column("confirmed_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "installation_attempts",
        sa.Column("total_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "installation_attempts",
        sa.Column("speed_bps", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("installation_attempts", sa.Column("current_component", sa.String(120), nullable=True))
    op.add_column("installation_attempts", sa.Column("current_file", sa.String(255), nullable=True))
    op.add_column(
        "installation_attempts",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("installation_attempts", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("installation_attempts", sa.Column("error_message", sa.String(500), nullable=True))
    op.add_column("installation_attempts", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.add_column("installation_attempts", sa.Column("lease_owner", sa.String(64), nullable=True))
    op.add_column("installation_attempts", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column(
        "installation_attempts",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "installation_attempts",
        sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_installation_attempts_stage", "installation_attempts", ["stage"])


def downgrade():
    op.drop_index("ix_installation_attempts_stage", table_name="installation_attempts")
    op.drop_column("installation_attempts", "pause_requested")
    op.drop_column("installation_attempts", "cancel_requested")
    op.drop_column("installation_attempts", "lease_expires_at")
    op.drop_column("installation_attempts", "lease_owner")
    op.drop_column("installation_attempts", "heartbeat_at")
    op.drop_column("installation_attempts", "error_message")
    op.drop_column("installation_attempts", "error_code")
    op.drop_column("installation_attempts", "retry_count")
    op.drop_column("installation_attempts", "current_file")
    op.drop_column("installation_attempts", "current_component")
    op.drop_column("installation_attempts", "speed_bps")
    op.drop_column("installation_attempts", "total_bytes")
    op.drop_column("installation_attempts", "confirmed_bytes")
    op.drop_column("installation_attempts", "stage")
