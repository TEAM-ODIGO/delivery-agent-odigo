"""Record authoritative failed revalidation without replacing historical prices."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261116_b04"
down_revision = "20261116_b03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "b_order_plans",
        sa.Column(
            "revalidation_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade():
    op.drop_column("b_order_plans", "revalidation_snapshot")
