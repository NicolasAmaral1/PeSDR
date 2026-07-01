"""form ingestion + CRM refs (spec 2026-06-16)

Revision ID: 0038_form_ingestion_and_crm_refs
Revises: 0037_sandbox_flags
Create Date: 2026-06-25 00:00:00

Implements the schema half of the Form ingestion + CRM write-only design
(`docs/superpowers/specs/2026-06-16-form-ingestion-and-crm-write-only-design.md`).

1. `inbound_form_submissions` table — analogue of `inbound_messages` for form
   submissions (Respondi first impl). Dedup via UNIQUE (tenant_id, provider,
   external_id). RLS by tenant_id, mirroring `talks`.

2. `leads.crm_refs` JSONB column — stores per-vendor external IDs as
   `{"rdstation": {"contact_id": "...", "deal_id": "...", "last_synced_at": "..."}}`.
   ADR CRM Fase 1 ("write-only + refs", 2026-06-12). GIN index for queries that
   look up a Lead by external CRM id.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_form_ingestion_and_crm_refs"
down_revision = "0037_sandbox_flags"
branch_labels = None
depends_on = None


ALL_FORM_STATUSES = ("queued", "processed", "skipped_dedupe", "error")


def upgrade() -> None:
    op.create_table(
        "inbound_form_submissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("field_values", postgresql.JSONB(), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in ALL_FORM_STATUSES) + ")",
            name="ck_inbound_form_submissions_status",
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider", "external_id",
            name="uq_inbound_form_submissions_extid",
        ),
    )
    op.create_index(
        "ix_inbound_form_lead_status",
        "inbound_form_submissions",
        ["lead_id", "status"],
        postgresql_where=sa.text("status IN ('queued', 'error')"),
    )
    op.create_index(
        "ix_inbound_form_tenant_provider",
        "inbound_form_submissions",
        ["tenant_id", "provider"],
    )

    # RLS — same shape as talks/action_executions.
    op.execute("ALTER TABLE inbound_form_submissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbound_form_submissions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY inbound_form_submissions_tenant_isolation "
        "ON inbound_form_submissions "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )

    # leads.crm_refs — JSONB map of vendor → external IDs.
    op.add_column(
        "leads",
        sa.Column(
            "crm_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Per-vendor external CRM ids. Shape: "
            "{\"<provider>\": {\"contact_id\": \"...\", \"deal_id\": \"...\", "
            "\"last_synced_at\": \"...\"}}. ADR CRM Fase 1.",
        ),
    )
    op.create_index(
        "ix_leads_crm_refs_gin",
        "leads",
        ["crm_refs"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_leads_crm_refs_gin", table_name="leads")
    op.drop_column("leads", "crm_refs")

    op.execute(
        "DROP POLICY IF EXISTS inbound_form_submissions_tenant_isolation "
        "ON inbound_form_submissions"
    )
    op.drop_index("ix_inbound_form_tenant_provider", table_name="inbound_form_submissions")
    op.drop_index("ix_inbound_form_lead_status", table_name="inbound_form_submissions")
    op.drop_table("inbound_form_submissions")
