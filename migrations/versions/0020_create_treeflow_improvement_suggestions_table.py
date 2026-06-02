"""treeflow_improvement_suggestions table (FlowEngine FE-01a, reserved slot)

Per spec §24.4. V2 feedback loop: weekly batch job analyzes rejected
response_reviews and proposes TreeFlow changes. Operator reviews via API.

Revision ID: 0020_create_treeflow_improvement_suggestions_table
Revises: 0019_create_adapter_calls_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020_create_treeflow_improvement_suggestions_table"
down_revision = "0019_create_adapter_calls_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "treeflow_improvement_suggestions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("treeflow_id", sa.Text(), nullable=False),
        sa.Column("target_node_id", sa.Text(), nullable=True),
        sa.Column("pattern_summary", sa.Text(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "sample_review_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("suggested_change", JSONB(), nullable=False),
        sa.Column("suggested_change_natural_language", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending_review"
        ),
        sa.Column("operator_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending_review', 'accepted', 'rejected', 'expired')",
            name="ck_tfis_status",
        ),
    )

    op.create_index(
        "ix_tfis_tenant_status",
        "treeflow_improvement_suggestions",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )

    op.execute(
        "ALTER TABLE treeflow_improvement_suggestions ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE treeflow_improvement_suggestions FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tfis_tenant_isolation ON treeflow_improvement_suggestions
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tfis_tenant_isolation ON treeflow_improvement_suggestions"
    )
    op.drop_index("ix_tfis_tenant_status", table_name="treeflow_improvement_suggestions")
    op.drop_table("treeflow_improvement_suggestions")
