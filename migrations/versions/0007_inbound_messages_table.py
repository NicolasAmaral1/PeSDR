"""inbound_messages table (with RLS + dedupe unique + status check)

Revision ID: 0007_inbound_messages_table
Revises: 0006_leads_table
Create Date: 2026-05-25 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007_inbound_messages_table"
down_revision = "0006_leads_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("raw", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "external_id",
            name="uq_inbound_provider_extid",
        ),
        sa.CheckConstraint(
            "status IN ('queued','processed','skipped_dedupe','error')",
            name="ck_inbound_messages_status",
        ),
    )
    op.create_index(
        "ix_inbound_lead_status",
        "inbound_messages",
        ["lead_id", "status"],
        postgresql_where=sa.text("status IN ('queued','error')"),
    )

    op.execute("ALTER TABLE inbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbound_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY inbound_messages_tenant_isolation ON inbound_messages
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS inbound_messages_tenant_isolation ON inbound_messages")
    op.drop_index("ix_inbound_lead_status", table_name="inbound_messages")
    op.drop_table("inbound_messages")
