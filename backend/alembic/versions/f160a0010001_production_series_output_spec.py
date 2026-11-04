"""Production series records the output size and clip length of its run.

The director workbench lets a person choose the delivered video's frame
(portrait / landscape / square, H3 multiples of 32) and one clip length before
queueing a series.  Those choices belong to the run itself, not to a browser
tab: the resident Worker creates every following shot of the series, so it has
to read the same values from the durable row.

The column is nullable: an existing series keeps rendering at whatever its
workflow profile defines, exactly as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "f160a0010001"
down_revision = "f150a0030001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("production_series") as batch:
        batch.add_column(sa.Column("output_spec", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("production_series") as batch:
        batch.drop_column("output_spec")
