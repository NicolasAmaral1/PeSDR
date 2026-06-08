"""talkflow_states table — runtime state per Talk (FlowEngine FE-01a)

1:1 with Talk (talk_id is PK). Carries the FlowEngine runtime state per
spec §3.3: current node, collected fields, extracted facts, the rolling
window of recent messages, active objection treatment, objection history,
and a slot for the (V2) sub-talk stack.

RLS uses denormalized tenant_id (matches the talks pattern).

Revision ID: 0014_create_talkflow_states_table
Revises: 0013_create_talks_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014_create_talkflow_states_table"
down_revision = "0013_create_talks_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "talkflow_states",
        sa.Column("talk_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("current_node", sa.Text(), nullable=False),
        sa.Column(
            "collected",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "extracted_facts",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "messages",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("history_summary", sa.Text(), nullable=True),
        sa.Column(
            "history_summary_covers_until_turn", sa.Integer(), nullable=True
        ),
        sa.Column("active_treatment", JSONB(), nullable=True),
        sa.Column(
            "objections_handled",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "talkflow_stack",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("talk_id"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "ix_talkflow_states_tenant_updated",
        "talkflow_states",
        ["tenant_id", sa.text("updated_at DESC")],
    )

    op.execute("ALTER TABLE talkflow_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE talkflow_states FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY talkflow_states_tenant_isolation ON talkflow_states
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS talkflow_states_tenant_isolation ON talkflow_states"
    )
    op.drop_index("ix_talkflow_states_tenant_updated", table_name="talkflow_states")
    op.drop_table("talkflow_states")
