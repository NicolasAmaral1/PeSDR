"""outbound_messages table (with RLS + XOR constraint + partial indexes)

Revision ID: 0011_outbound_messages
Revises: 0010_follow_up_and_talkflow_columns
Create Date: 2026-05-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_outbound_messages"
down_revision = "0010_follow_up_and_talkflow_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talkflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("template_ref", sa.Text(), nullable=True),
        sa.Column("template_language", sa.Text(), nullable=True),
        sa.Column("template_params", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("inbound_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("follow_up_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["inbound_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["follow_up_job_id"], ["follow_up_jobs.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "message_type IN ('text', 'template')",
            name="ck_outbound_message_type",
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed')",
            name="ck_outbound_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery')",
            name="ck_outbound_triggered_by",
        ),
        sa.CheckConstraint(
            "(message_type = 'text' AND body_text IS NOT NULL AND template_ref IS NULL) "
            "OR (message_type = 'template' AND template_ref IS NOT NULL AND body_text IS NULL)",
            name="ck_outbound_body_consistency",
        ),
    )

    op.create_index(
        "ix_outbound_messages_lead_sent",
        "outbound_messages",
        ["lead_id", sa.text("sent_at DESC")],
    )
    op.create_index(
        "ix_outbound_messages_tenant_sent",
        "outbound_messages",
        ["tenant_id", sa.text("sent_at DESC")],
    )

    op.execute("ALTER TABLE outbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbound_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY outbound_messages_tenant_isolation ON outbound_messages
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS outbound_messages_tenant_isolation ON outbound_messages")
    op.drop_index("ix_outbound_messages_tenant_sent", table_name="outbound_messages")
    op.drop_index("ix_outbound_messages_lead_sent", table_name="outbound_messages")
    op.drop_table("outbound_messages")
