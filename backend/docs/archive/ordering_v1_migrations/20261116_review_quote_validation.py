"""Preserve each quote's revalidation evidence when the order later recovers."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261116_review"
down_revision = "20261116_b04"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "b_quotes",
        sa.Column(
            "revalidation_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    # Recover older failed quotes even if the plan has already moved to a new quote.
    # Existing B-04 response evidence contains the exact quote and INVALID status.
    op.execute(
        sa.text("""
        UPDATE b_quotes SET revalidation_snapshot = (
            SELECT response_snapshot -> 'view' FROM b_idempotency_records
            WHERE operation = 'B04' AND resource_scope = b_quotes.order_plan_id
              AND response_snapshot ->> 'next_action' = 'RESEARCH_REQUIRED'
              AND response_snapshot -> 'view' -> 'quote' ->> 'quote_id' = b_quotes.quote_id
            ORDER BY created_at DESC LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM b_idempotency_records
            WHERE operation = 'B04' AND resource_scope = b_quotes.order_plan_id
              AND response_snapshot ->> 'next_action' = 'RESEARCH_REQUIRED'
              AND response_snapshot -> 'view' -> 'quote' ->> 'quote_id' = b_quotes.quote_id
        )
    """)
    )
    # ->> and correlated subqueries work on PostgreSQL 18 and bundled SQLite.
    # Copy only the quote explicitly identified by the existing plan evidence.
    op.execute(
        sa.text("""
        UPDATE b_quotes
        SET revalidation_snapshot = (
            SELECT b_order_plans.revalidation_snapshot FROM b_order_plans
            WHERE b_order_plans.order_plan_id = b_quotes.order_plan_id
        )
        WHERE EXISTS (
            SELECT 1 FROM b_order_plans
            WHERE b_order_plans.order_plan_id = b_quotes.order_plan_id
              AND b_order_plans.revalidation_snapshot ->> 'quote_id' = b_quotes.quote_id
        )
    """)
    )


def downgrade():
    op.drop_column("b_quotes", "revalidation_snapshot")
