# migrations/versions/0032_instances.py
"""instances table + RLS + backfill one 'main' instance per tenant.

Revision ID: 0032_instances
Revises: 0031_add_voice_synthesis_failed_reason
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_instances"
down_revision = "0031_add_voice_synthesis_failed_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_label", sa.Text(), nullable=False, server_default="main"),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "channel_label", name="uq_instances_tenant_channel"),
    )
    op.execute("ALTER TABLE instances ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE instances FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY instances_tenant_isolation ON instances "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    # Backfill: one 'main' instance per existing tenant.
    op.execute(
        "INSERT INTO instances (tenant_id, channel_label, display_name) "
        "SELECT id, 'main', display_name FROM tenants "
        "ON CONFLICT (tenant_id, channel_label) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS instances_tenant_isolation ON instances")
    op.drop_table("instances")
