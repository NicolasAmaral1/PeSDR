"""form ingestion + crm refs

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-16 00:00:00

Migration completa pra Fase A da spec 2026-06-16-form-ingestion-and-crm-write-only-design.md:

1. `leads.crm_refs` JSONB (NOT NULL DEFAULT '{}'::jsonb)
   Armazena refs externas dos sistemas de CRM:
     {"rdstation": {"contact_id": "...", "deal_id_<product>": "...", "last_synced_at": "..."}}

2. `inbound_form_submissions` table
   Audit + dedup de submissões de formulário. RLS via tenant_id.
   UNIQUE (tenant_id, provider, external_id) garante idempotência.

3. RLS + policy em inbound_form_submissions (FORCE).

Esta migration é IDEMPOTENTE — pode rodar em DB já em produção sem perder
dados de Lead existentes (default '{}'::jsonb preenche rows existentes).

Downgrade reverte limpo.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── 1. leads.crm_refs JSONB ──────────────────────────────────────────
    op.add_column(
        "leads",
        sa.Column(
            "crm_refs",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="External CRM IDs and sync metadata, keyed by provider. "
            "Example: {'rdstation': {'contact_id': 'abc', 'deal_id_mentoria': 'def'}}",
        ),
    )
    op.create_index(
        "ix_leads_crm_refs_gin",
        "leads",
        ["crm_refs"],
        postgresql_using="gin",
    )

    # ─── 2. inbound_form_submissions table ────────────────────────────────
    op.create_table(
        "inbound_form_submissions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False, comment="e.g., 'respondi'"),
        sa.Column(
            "external_id",
            sa.Text(),
            nullable=False,
            comment="Provider-native id (Respondi: respondent_id)",
        ),
        sa.Column(
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
            comment="Resolved during ingestion. Null se identity resolution falhou.",
        ),
        sa.Column(
            "raw",
            JSONB(),
            nullable=False,
            comment="Payload original do provider — pra audit/replay",
        ),
        sa.Column(
            "field_values",
            JSONB(),
            nullable=False,
            comment="Mapped field_values pós field_mapping do tenant.yaml",
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="queued",
            comment="queued | processed | skipped_dedupe | error",
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.Text()),
        sa.CheckConstraint(
            "status IN ('queued', 'processed', 'skipped_dedupe', 'error')",
            name="ck_inbound_form_status",
        ),
    )

    # Dedup index — UNIQUE (tenant_id, provider, external_id)
    op.create_index(
        "uq_inbound_form_extid",
        "inbound_form_submissions",
        ["tenant_id", "provider", "external_id"],
        unique=True,
    )

    # Partial index pra worker scan de jobs pendentes
    op.create_index(
        "ix_inbound_form_lead_status",
        "inbound_form_submissions",
        ["lead_id", "status"],
        postgresql_where=sa.text("status IN ('queued', 'error')"),
    )

    # ─── 3. RLS ───────────────────────────────────────────────────────────
    op.execute(
        "ALTER TABLE inbound_form_submissions ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE inbound_form_submissions FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON inbound_form_submissions
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    # Reverte na ordem oposta
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON inbound_form_submissions")
    op.drop_index("ix_inbound_form_lead_status", table_name="inbound_form_submissions")
    op.drop_index("uq_inbound_form_extid", table_name="inbound_form_submissions")
    op.drop_table("inbound_form_submissions")
    op.drop_index("ix_leads_crm_refs_gin", table_name="leads")
    op.drop_column("leads", "crm_refs")
