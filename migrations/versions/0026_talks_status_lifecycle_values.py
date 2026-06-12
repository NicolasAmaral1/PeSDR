"""talks.status enum: add lifecycle close values (FlowEngine FE-03b)

Per spec §16.3. Adds closed_completed_success/failure, closed_no_interest,
closed_duration. Preserves backward-compat with closed_completed.

Revision ID: 0026_talks_status_lifecycle_values
Revises: 0025_talks_requires_review_reason
Create Date: 2026-06-10 00:00:00
"""

from alembic import op

from ai_sdr.models.talk_status import ALL_STATUSES

revision = "0026_talks_status_lifecycle_values"
down_revision = "0025_talks_requires_review_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_talks_status", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_status",
        "talks",
        "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_status", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_status",
        "talks",
        "status IN ('active', 'requires_review', 'closed_completed', "
        "'closed_inactivity', 'closed_optout', 'closed_banned')",
    )
