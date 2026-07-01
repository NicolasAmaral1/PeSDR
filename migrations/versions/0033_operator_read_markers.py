# migrations/versions/0033_operator_read_markers.py
"""operator_read_markers — per-(operator, contact) read state + RLS.

Revision ID: 0033_operator_read_markers
Revises: 0032_instances
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_operator_read_markers"
down_revision = "0032_instances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_read_markers",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_read_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_read_message_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "lead_id", name="pk_operator_read_markers"),
    )
    op.create_index("ix_read_markers_lead", "operator_read_markers", ["lead_id"])
    op.execute("ALTER TABLE operator_read_markers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operator_read_markers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY read_markers_tenant_isolation ON operator_read_markers "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS read_markers_tenant_isolation ON operator_read_markers")
    op.drop_table("operator_read_markers")
