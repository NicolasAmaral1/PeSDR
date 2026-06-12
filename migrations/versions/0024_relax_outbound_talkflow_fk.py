"""relax FK on outbound_messages.talkflow_id (FlowEngine FE-01b)

FlowEngine v2 reuses the talkflow_id column to hold Talk UUIDs (from
the new talks table). The legacy FK to talkflows blocks this. We drop
the FK constraint; the column stays NOT NULL and the existing v1 rows
remain valid because they still point to real talkflows.

FE-02 cleans this up properly by adding a dedicated talk_id column +
migrating rows + dropping the legacy talkflow_id. For FE-01b we accept
the column reuse.

Revision ID: 0024_relax_outbound_talkflow_fk
Revises: 0023_add_tenant_architecture_version
Create Date: 2026-06-02 00:00:00
"""

from alembic import op

revision = "0024_relax_outbound_talkflow_fk"
down_revision = "0023_add_tenant_architecture_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "outbound_messages_talkflow_id_fkey",
        "outbound_messages",
        type_="foreignkey",
    )


def downgrade() -> None:
    op.create_foreign_key(
        "outbound_messages_talkflow_id_fkey",
        "outbound_messages",
        "talkflows",
        ["talkflow_id"],
        ["id"],
        ondelete="CASCADE",
    )
