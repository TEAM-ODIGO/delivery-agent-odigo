"""Keep B-issued option questions with their immutable quote revision."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261116_b03"
down_revision = "20261116_b07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "b_quotes",
        sa.Column(
            "supplement_questions_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade():
    op.drop_column("b_quotes", "supplement_questions_snapshot")
