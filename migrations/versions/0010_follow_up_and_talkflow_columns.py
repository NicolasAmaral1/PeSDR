"""follow_up_jobs table (with RLS + partial indexes) + TalkFlow timing columns

Revision ID: 0010_follow_up_and_talkflow_columns
Revises: 0008_talkflows_lead_id_fk
Create Date: 2026-05-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_follow_up_and_talkflow_columns"
down_revision = "0008_talkflows_lead_id_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- follow_up_jobs table ---
    op.create_table(
        "follow_up_jobs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talkflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_external_id", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talkflow_id"], ["talkflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled', 'error')",
            name="ck_follow_up_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_follow_up_jobs_attempt_positive",
        ),
    )
    op.create_index(
        "ix_follow_up_jobs_due",
        "follow_up_jobs",
        ["scheduled_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_follow_up_jobs_lead_pending",
        "follow_up_jobs",
        ["lead_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.execute("ALTER TABLE follow_up_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE follow_up_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY follow_up_jobs_tenant_isolation ON follow_up_jobs
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )

    # --- talkflows columns ---
    op.add_column(
        "talkflows",
        sa.Column("last_agent_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "talkflows",
        sa.Column("last_lead_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "talkflows",
        sa.Column(
            "follow_up_attempt_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("talkflows", "follow_up_attempt_number")
    op.drop_column("talkflows", "last_lead_message_at")
    op.drop_column("talkflows", "last_agent_message_at")
    op.execute("DROP POLICY IF EXISTS follow_up_jobs_tenant_isolation ON follow_up_jobs")
    op.drop_index("ix_follow_up_jobs_lead_pending", table_name="follow_up_jobs")
    op.drop_index("ix_follow_up_jobs_due", table_name="follow_up_jobs")
    op.drop_table("follow_up_jobs")
