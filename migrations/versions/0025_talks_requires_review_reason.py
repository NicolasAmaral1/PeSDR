"""add talks.requires_review_reason (FlowEngine FE-03a)

Per spec §11. Records WHY a Talk was flagged for human review, so the
operator HITL console (FE-07) can prioritise/route differently per
reason. Multiple FE-03a code paths converge on Talk.status=requires_review:
this column makes the converging streams distinguishable downstream.

Revision ID: 0025_talks_requires_review_reason
Revises: 0024_relax_outbound_talkflow_fk
Create Date: 2026-06-10 00:00:00
"""

import sqlalchemy as sa
from alembic import op

from ai_sdr.models.review_reason import ALL_REASONS as REASONS

revision = "0025_talks_requires_review_reason"
down_revision = "0024_relax_outbound_talkflow_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "talks",
        sa.Column("requires_review_reason", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_talks_requires_review_reason",
        "talks",
        "requires_review_reason IS NULL OR requires_review_reason IN ("
        + ", ".join(f"'{r}'" for r in REASONS)
        + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_requires_review_reason", "talks", type_="check")
    op.drop_column("talks", "requires_review_reason")
