"""adapter_calls table — adapter audit log (FlowEngine FE-01a, reserved slot)

Per spec §13. Each call to a generalized adapter (CRM, calendar,
notification, analytics, storage, voice) writes a row here for audit,
retry tracking, and BI cost reporting. FE-05 wires the dispatch.

Idempotency: (tenant_id, idempotency_key) is unique to prevent duplicate
side effects when worker retries.

Revision ID: 0019_create_adapter_calls_table
Revises: 0018_create_sentinel_reviews_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019_create_adapter_calls_table"
down_revision = "0018_create_sentinel_reviews_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adapter_calls",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("adapter_category", sa.Text(), nullable=False),
        sa.Column("adapter_provider", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column(
            "args",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_adapter_calls_tenant_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'failed', 'pending', 'cancelled')",
            name="ck_adapter_calls_status",
        ),
    )

    op.create_index(
        "ix_adapter_calls_tenant_started",
        "adapter_calls",
        ["tenant_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_adapter_calls_talk",
        "adapter_calls",
        ["talk_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("talk_id IS NOT NULL"),
    )
    op.create_index(
        "ix_adapter_calls_failed",
        "adapter_calls",
        ["tenant_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("status = 'failed'"),
    )

    op.execute("ALTER TABLE adapter_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE adapter_calls FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY adapter_calls_tenant_isolation ON adapter_calls
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS adapter_calls_tenant_isolation ON adapter_calls")
    op.drop_index("ix_adapter_calls_failed", table_name="adapter_calls")
    op.drop_index("ix_adapter_calls_talk", table_name="adapter_calls")
    op.drop_index("ix_adapter_calls_tenant_started", table_name="adapter_calls")
    op.drop_table("adapter_calls")
