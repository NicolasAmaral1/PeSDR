"""sandbox flags on talks and leads

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-23 00:00:00

Adds sandbox marking columns per PR #24 (Sandbox Console Extension):

1. `talks.is_sandbox` BOOLEAN — distinguishes sandbox test conversations from real prod.
2. `talks.sandbox_llm_mode` TEXT ('real' | 'fake') — picks LLM strategy in
   process_sandbox_turn worker. NULL when is_sandbox=false.
3. `leads.is_sandbox` BOOLEAN — Lead carries the flag explicitly (Nicolas's
   recommendation, option (a) from PR #24 review). Lets crons + inbox queries
   filter sandbox out without joining to talks.

Both columns have partial indexes — zero cost on prod queries that don't touch
sandbox rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # talks.is_sandbox
    op.add_column(
        "talks",
        sa.Column(
            "is_sandbox",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Distinguishes sandbox Talks (test) from production. "
            "Default false. Set true only by /console/{slug}/sandbox/talks/new.",
        ),
    )

    # talks.sandbox_llm_mode
    op.add_column(
        "talks",
        sa.Column(
            "sandbox_llm_mode",
            sa.Text(),
            nullable=True,
            comment="LLM mode for sandbox Talks: 'real' (Anthropic) or 'fake' "
            "(FakeListChatModel scripted). NULL when is_sandbox=false.",
        ),
    )
    op.create_check_constraint(
        "ck_sandbox_llm_mode",
        "talks",
        "sandbox_llm_mode IS NULL OR sandbox_llm_mode IN ('real', 'fake')",
    )
    op.create_check_constraint(
        "ck_sandbox_llm_mode_required",
        "talks",
        "(is_sandbox = false AND sandbox_llm_mode IS NULL) "
        "OR (is_sandbox = true AND sandbox_llm_mode IS NOT NULL)",
    )

    # Partial index on talks: only sandbox rows
    op.create_index(
        "ix_talks_sandbox",
        "talks",
        ["tenant_id", "is_sandbox"],
        postgresql_where=sa.text("is_sandbox = true"),
    )

    # leads.is_sandbox (Nicolas option (a) — explicit flag on Lead too)
    op.add_column(
        "leads",
        sa.Column(
            "is_sandbox",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Lead carries sandbox flag explicitly. Crons + inbox queries "
            "filter via WHERE is_sandbox = false (no JOIN to talks needed).",
        ),
    )
    op.create_index(
        "ix_leads_sandbox",
        "leads",
        ["tenant_id", "is_sandbox"],
        postgresql_where=sa.text("is_sandbox = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_leads_sandbox", table_name="leads")
    op.drop_column("leads", "is_sandbox")
    op.drop_index("ix_talks_sandbox", table_name="talks")
    op.drop_constraint("ck_sandbox_llm_mode_required", "talks", type_="check")
    op.drop_constraint("ck_sandbox_llm_mode", "talks", type_="check")
    op.drop_column("talks", "sandbox_llm_mode")
    op.drop_column("talks", "is_sandbox")
