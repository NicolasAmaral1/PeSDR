"""response_reviews table — HITL approval queue (FlowEngine FE-01a, reserved slot)

Per spec §24.1. Reserved terreno: tables exist, runtime activation in FE-07.

Revision ID: 0017_create_response_reviews_table
Revises: 0016_create_experiments_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017_create_response_reviews_table"
down_revision = "0016_create_experiments_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_reviews",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("correction_iteration", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_review_id", UUID(as_uuid=True), nullable=True),
        sa.Column("original_response", sa.Text(), nullable=False),
        sa.Column("original_turn_decision", JSONB(), nullable=False),
        sa.Column("original_system_prompt_snapshot", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("operator_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_response", sa.Text(), nullable=True),
        sa.Column("edit_reason", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("improvement_category", sa.Text(), nullable=True),
        sa.Column("final_response_sent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_review_id"], ["response_reviews.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'edited', 'rejected', 'expired', 'auto_approved')",
            name="ck_response_reviews_status",
        ),
        sa.CheckConstraint(
            "improvement_category IS NULL OR improvement_category IN "
            "('tone', 'factual', 'scope', 'premature_transition', "
            "'missed_signal', 'incomplete', 'other')",
            name="ck_response_reviews_improvement_category",
        ),
    )

    op.create_index(
        "ix_response_reviews_tenant_status_created",
        "response_reviews",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_response_reviews_talk",
        "response_reviews",
        ["talk_id", "turn_index"],
    )

    op.execute("ALTER TABLE response_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE response_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY response_reviews_tenant_isolation ON response_reviews
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS response_reviews_tenant_isolation ON response_reviews")
    op.drop_index("ix_response_reviews_talk", table_name="response_reviews")
    op.drop_index("ix_response_reviews_tenant_status_created", table_name="response_reviews")
    op.drop_table("response_reviews")
