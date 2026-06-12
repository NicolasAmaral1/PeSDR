"""sentinel_reviews table — Sentinel audit (FlowEngine FE-01a, reserved slot)

Per spec §8.5. Records each Sentinel invocation (heuristic-triggered or
elevated-mode). FE-04 implements the runtime that writes here.

Revision ID: 0018_create_sentinel_reviews_table
Revises: 0017_create_response_reviews_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018_create_sentinel_reviews_table"
down_revision = "0017_create_response_reviews_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentinel_reviews",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inbound_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level_before", sa.Text(), nullable=False),
        sa.Column("risk_level_after", sa.Text(), nullable=False),
        sa.Column(
            "heuristic_matches",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "classification IN ('safe', 'suspicious', 'attack')",
            name="ck_sentinel_reviews_classification",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('heuristic', 'elevated_mode', 'llm_self_flag')",
            name="ck_sentinel_reviews_triggered_by",
        ),
    )

    op.create_index(
        "ix_sentinel_reviews_lead_created",
        "sentinel_reviews",
        ["lead_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_sentinel_reviews_tenant_classification",
        "sentinel_reviews",
        ["tenant_id", "classification", sa.text("created_at DESC")],
    )

    op.execute("ALTER TABLE sentinel_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sentinel_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY sentinel_reviews_tenant_isolation ON sentinel_reviews
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sentinel_reviews_tenant_isolation ON sentinel_reviews")
    op.drop_index("ix_sentinel_reviews_tenant_classification", table_name="sentinel_reviews")
    op.drop_index("ix_sentinel_reviews_lead_created", table_name="sentinel_reviews")
    op.drop_table("sentinel_reviews")
