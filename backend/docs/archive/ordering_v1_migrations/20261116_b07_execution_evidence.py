"""Store execution-check request and observed D response without changing D state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261116_b07"
down_revision = "20261116_b06"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "b_execution_checks",
        sa.Column(
            "request_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "b_execution_checks",
        sa.Column(
            "approval_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("b_execution_checks", "approval_snapshot")
    op.drop_column("b_execution_checks", "request_snapshot")
