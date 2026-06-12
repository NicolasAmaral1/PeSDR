"""extend leads with long-lived identity fields (FlowEngine FE-01a)

Adds 9 columns to the existing leads table to carry the long-lived Lead
identity used by the FlowEngine: channel routing, display, profile (long-
term memory slot), risk-level state machine for Sentinel, acquisition
metadata for BI attribution.

The earlier draft of the spec named this concept 'User' on a new table.
Renamed to extend 'leads' to avoid collision with the existing P11 'users'
table that holds operators.

Revision ID: 0012_extend_leads_with_identity_fields
Revises: 0011_outbound_messages
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_extend_leads_with_identity_fields"
down_revision = "0011_outbound_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "channel_identifiers",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("leads", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "profile",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column("profile_last_updated", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "long_term_memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "risk_level",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column("risk_level_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("leads", sa.Column("risk_level_reason", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "acquisition_metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_check_constraint(
        "ck_leads_risk_level",
        "leads",
        "risk_level IN ('normal', 'elevated', 'banned')",
    )

    op.create_index(
        "ix_leads_tenant_risk_level",
        "leads",
        ["tenant_id", "risk_level"],
        postgresql_where=sa.text("risk_level <> 'normal'"),
    )


def downgrade() -> None:
    op.drop_index("ix_leads_tenant_risk_level", table_name="leads")
    op.drop_constraint("ck_leads_risk_level", "leads", type_="check")
    op.drop_column("leads", "acquisition_metadata")
    op.drop_column("leads", "risk_level_reason")
    op.drop_column("leads", "risk_level_since")
    op.drop_column("leads", "risk_level")
    op.drop_column("leads", "long_term_memory_enabled")
    op.drop_column("leads", "profile_last_updated")
    op.drop_column("leads", "profile")
    op.drop_column("leads", "display_name")
    op.drop_column("leads", "channel_identifiers")
