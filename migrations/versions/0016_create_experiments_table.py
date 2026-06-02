"""experiments table — A/B test definitions (FlowEngine FE-01a, reserved slot)

Per spec §25. Schema is laid down now so FE-07 can implement assignment
and analytics. Empty at v1 launch; populated when first experiment is
created.

Revision ID: 0016_create_experiments_table
Revises: 0015_create_events_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0016_create_experiments_table"
down_revision = "0015_create_events_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("variants", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "eligibility_rules",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_sample_size", sa.Integer(), nullable=False),
        sa.Column("primary_success_metric", sa.Text(), nullable=False),
        sa.Column(
            "secondary_metrics",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("exclusivity", sa.Text(), nullable=False, server_default="exclusive"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "on_conclusion_behavior",
            sa.Text(),
            nullable=False,
            server_default="preserve_running_talks",
        ),
        sa.Column("winner", sa.Text(), nullable=True),
        sa.Column("statistical_confidence", sa.Float(), nullable=True),
        sa.Column("analysis_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_experiments_tenant_key"),
        sa.CheckConstraint(
            "status IN ('draft', 'running', 'paused', 'concluded')",
            name="ck_experiments_status",
        ),
        sa.CheckConstraint(
            "exclusivity IN ('exclusive', 'orthogonal')",
            name="ck_experiments_exclusivity",
        ),
        sa.CheckConstraint(
            "on_conclusion_behavior IN ('preserve_running_talks', 'migrate_to_winner')",
            name="ck_experiments_on_conclusion",
        ),
    )

    op.create_index(
        "ix_experiments_tenant_status",
        "experiments",
        ["tenant_id", "status"],
    )

    op.execute("ALTER TABLE experiments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE experiments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY experiments_tenant_isolation ON experiments
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS experiments_tenant_isolation ON experiments")
    op.drop_index("ix_experiments_tenant_status", table_name="experiments")
    op.drop_table("experiments")
