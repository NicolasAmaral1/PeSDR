"""leads table (with RLS + per-tenant unique indexes)

Revision ID: 0006_leads_table
Revises: 0005_kb_tables
Create Date: 2026-05-25 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0006_leads_table"
down_revision = "0005_kb_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("whatsapp_e164", sa.Text(), nullable=True),
        sa.Column("external_label", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending_assignment'"),
        ),
        sa.Column("unreachable_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending_assignment','active','unreachable')",
            name="ck_leads_status",
        ),
    )
    op.create_index(
        "uq_leads_tenant_wa",
        "leads",
        ["tenant_id", "whatsapp_e164"],
        unique=True,
        postgresql_where=sa.text("whatsapp_e164 IS NOT NULL"),
    )
    op.create_index(
        "uq_leads_tenant_label",
        "leads",
        ["tenant_id", "external_label"],
        unique=True,
        postgresql_where=sa.text("external_label IS NOT NULL"),
    )
    op.create_index("ix_leads_tenant_status", "leads", ["tenant_id", "status"])

    # RLS — same pattern as kb_documents
    op.execute("ALTER TABLE leads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leads FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY leads_tenant_isolation ON leads
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS leads_tenant_isolation ON leads")
    op.drop_index("ix_leads_tenant_status", table_name="leads")
    op.drop_index("uq_leads_tenant_label", table_name="leads")
    op.drop_index("uq_leads_tenant_wa", table_name="leads")
    op.drop_table("leads")
