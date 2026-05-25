"""talkflows.lead_id String → UUID FK to leads(id)

Revision ID: 0008_talkflows_lead_id_fk
Revises: 0007_inbound_messages_table
Create Date: 2026-05-25 00:00:00

Backfill: for each distinct (tenant_id, lead_id) in existing talkflows,
create a Lead row with external_label=<old_string>, status='active',
whatsapp_e164=NULL. Then point talkflows at the new UUIDs.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_talkflows_lead_id_fk"
down_revision = "0007_inbound_messages_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0. Temporarily disable FORCE RLS on talkflows + leads so the migration's
    #    DML (which runs as ai_sdr_app without a tenant_id set) can see + write
    #    all rows. Restored at the end. Without this:
    #      - The INSERT INTO leads fails the WITH CHECK clause (no tenant set).
    #      - The UPDATE talkflows matches 0 rows (USING filters everything).
    #      - The subsequent ALTER ... SET NOT NULL hits NULLs from the fresh
    #        column on the still-existing rows.
    op.execute("ALTER TABLE leads NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE talkflows NO FORCE ROW LEVEL SECURITY")

    # 1. Backfill: insert one Lead row per distinct (tenant_id, lead_id) in talkflows.
    #    ON CONFLICT DO NOTHING in case multiple talkflows share a label
    #    (the unique constraint uq_leads_tenant_label catches it).
    op.execute(
        """
        INSERT INTO leads (tenant_id, external_label, status)
        SELECT DISTINCT tenant_id, lead_id, 'active'
        FROM talkflows
        ON CONFLICT (tenant_id, external_label)
            WHERE external_label IS NOT NULL
            DO NOTHING
        """
    )

    # 2. Drop existing unique constraint on (tenant_id, lead_id) — we'll recreate
    #    it on the new UUID column.
    op.drop_constraint("uq_talkflows_tenant_lead", "talkflows", type_="unique")

    # 3. Add the new UUID column (nullable for the duration of the data move).
    op.add_column(
        "talkflows",
        sa.Column("lead_uuid", UUID(as_uuid=True), nullable=True),
    )

    # 4. Populate lead_uuid from leads.id via the external_label join.
    op.execute(
        """
        UPDATE talkflows tf
        SET lead_uuid = l.id
        FROM leads l
        WHERE l.tenant_id = tf.tenant_id
          AND l.external_label = tf.lead_id
        """
    )

    # 5. Drop the old string column.
    op.drop_column("talkflows", "lead_id")

    # 6. Rename lead_uuid → lead_id.
    op.alter_column("talkflows", "lead_uuid", new_column_name="lead_id")

    # 7. Add NOT NULL + FK + unique constraint.
    op.alter_column(
        "talkflows",
        "lead_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_talkflows_lead_id",
        "talkflows",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_talkflows_tenant_lead",
        "talkflows",
        ["tenant_id", "lead_id"],
    )

    # 8. Restore FORCE RLS on both tables.
    op.execute("ALTER TABLE talkflows FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leads FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Best-effort: replace UUID with the lead's external_label. Lossy if a
    # lead has no external_label (e.g., one created by the webhook handler).
    op.drop_constraint("uq_talkflows_tenant_lead", "talkflows", type_="unique")
    op.drop_constraint("fk_talkflows_lead_id", "talkflows", type_="foreignkey")
    op.add_column("talkflows", sa.Column("lead_id_str", sa.String(length=128), nullable=True))
    op.execute(
        """
        UPDATE talkflows tf
        SET lead_id_str = COALESCE(l.external_label, l.id::text)
        FROM leads l
        WHERE l.id = tf.lead_id
        """
    )
    op.drop_column("talkflows", "lead_id")
    op.alter_column("talkflows", "lead_id_str", new_column_name="lead_id")
    op.alter_column(
        "talkflows",
        "lead_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.create_unique_constraint("uq_talkflows_tenant_lead", "talkflows", ["tenant_id", "lead_id"])
