"""action_executions table + RLS + constraints (FlowEngine FE-03c)

Per spec §5. Creates the table for tracking on_collected action lifecycle:
pending → executing → success | failed. UNIQUE (talk_id, field, value_hash)
enforces idempotency. RLS by tenant_id mirrors talks.

Revision ID: 0028_action_executions
Revises: 0027_talks_closed_by_lifecycle_values
Create Date: 2026-06-12 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ai_sdr.models.action_status import ALL_STATUSES

revision = "0028_action_executions"
down_revision = "0027_talks_closed_by_lifecycle_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value_hash", sa.Text(), nullable=False),
        sa.Column("adapter_name", sa.Text(), nullable=False),
        sa.Column("handler", sa.Text(), nullable=False),
        sa.Column("params_resolved", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
            name="ck_action_executions_status",
        ),
        sa.UniqueConstraint("talk_id", "field", "value_hash", name="uq_action_executions_dedup"),
    )
    op.create_index(
        "ix_action_executions_pending",
        "action_executions",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'executing')"),
    )
    op.create_index(
        "ix_action_executions_tenant_talk",
        "action_executions",
        ["tenant_id", "talk_id"],
    )

    # RLS — mirror the talks pattern.
    op.execute("ALTER TABLE action_executions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE action_executions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY action_executions_tenant_isolation ON action_executions "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS action_executions_tenant_isolation ON action_executions")
    op.drop_index("ix_action_executions_tenant_talk", table_name="action_executions")
    op.drop_index("ix_action_executions_pending", table_name="action_executions")
    op.drop_table("action_executions")
