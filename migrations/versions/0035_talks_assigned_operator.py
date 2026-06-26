"""talks.assigned_operator_id — which operator owns a human-held talk.

Revision ID: 0035_talks_assigned_operator
Revises: 0034_inbox_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_talks_assigned_operator"
down_revision = "0034_inbox_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "talks",
        sa.Column("assigned_operator_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_talks_assigned_operator", "talks", "users",
        ["assigned_operator_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_talks_assigned_operator", "talks", type_="foreignkey")
    op.drop_column("talks", "assigned_operator_id")
