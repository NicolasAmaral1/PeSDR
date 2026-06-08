"""talks table — conversation session per Lead (FlowEngine FE-01a)

A Talk is a discrete period of agent-lead interaction. A Lead can have
many Talks over time (V1 restricts to one active per tenant). Each Talk
is bound to an immutable TreeFlow version snapshot.

RLS uses denormalized tenant_id (matches outbound_messages pattern).

Revision ID: 0013_create_talks_table
Revises: 0012_extend_leads_with_identity_fields
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013_create_talks_table"
down_revision = "0012_extend_leads_with_identity_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "talks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("treeflow_id", sa.Text(), nullable=False),
        sa.Column("treeflow_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("handling_mode", sa.Text(), nullable=False, server_default="ai"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("closed_by", sa.Text(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_category", sa.Text(), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("experiment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_variant", sa.Text(), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "tokens_consumed",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["treeflow_version_id"], ["treeflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'requires_review', "
            "'closed_completed', 'closed_inactivity', 'closed_optout', "
            "'closed_banned')",
            name="ck_talks_status",
        ),
        sa.CheckConstraint(
            "handling_mode IN ('ai', 'human', 'auto_with_approval')",
            name="ck_talks_handling_mode",
        ),
        sa.CheckConstraint(
            "closed_by IS NULL OR closed_by IN "
            "('rule', 'optout', 'llm', 'operator', 'sentinel')",
            name="ck_talks_closed_by",
        ),
    )

    op.create_index(
        "ix_talks_tenant_status_last_msg",
        "talks",
        ["tenant_id", "status", sa.text("last_message_at DESC")],
    )
    op.create_index(
        "ix_talks_lead_created",
        "talks",
        ["lead_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_talks_experiment",
        "talks",
        ["experiment_id"],
        postgresql_where=sa.text("experiment_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE talks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE talks FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY talks_tenant_isolation ON talks
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS talks_tenant_isolation ON talks")
    op.drop_index("ix_talks_experiment", table_name="talks")
    op.drop_index("ix_talks_lead_created", table_name="talks")
    op.drop_index("ix_talks_tenant_status_last_msg", table_name="talks")
    op.drop_table("talks")
