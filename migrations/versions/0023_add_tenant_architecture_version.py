"""add tenant.architecture_version (FlowEngine FE-01a)

Per spec §21.2. Feature flag routing process_lead_inbox between v1
(LangGraph) and v2 (FlowEngine pipeline). Default 1 keeps existing
behavior; FE-01b sets specific tenants to 2 to activate the new pipeline.

Revision ID: 0023_add_tenant_architecture_version
Revises: 0022_extend_inbound_messages_with_media
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_add_tenant_architecture_version"
down_revision = "0022_extend_inbound_messages_with_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "architecture_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_tenants_architecture_version",
        "tenants",
        "architecture_version IN (1, 2)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenants_architecture_version", "tenants", type_="check")
    op.drop_column("tenants", "architecture_version")
