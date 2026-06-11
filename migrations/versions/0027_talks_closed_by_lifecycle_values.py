"""talks.closed_by check constraint: add lifecycle close paths (FE-03b hotfix)

The original ck_talks_closed_by from migration 0013 listed
{rule, optout, llm, operator, sentinel}. FE-03b introduced two new close
paths that the constraint didn't cover:

- 'pipeline_hook' — completion_rule close from close_lifecycle.py (T12)
- 'scan_job' — inactivity/duration close from worker scan_talks.py (T15)

Migration 0026 extended ck_talks_status but missed this sibling constraint.

Revision ID: 0027_talks_closed_by_lifecycle_values
Revises: 0026_talks_status_lifecycle_values
Create Date: 2026-06-11 00:00:00
"""

from alembic import op

from ai_sdr.models.talk_closed_by import ALL_CLOSED_BY

revision = "0027_talks_closed_by_lifecycle_values"
down_revision = "0026_talks_status_lifecycle_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_talks_closed_by", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_closed_by",
        "talks",
        "closed_by IS NULL OR closed_by IN (" + ", ".join(f"'{v}'" for v in ALL_CLOSED_BY) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_closed_by", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_closed_by",
        "talks",
        "closed_by IS NULL OR closed_by IN ('rule', 'optout', 'llm', 'operator', 'sentinel')",
    )
