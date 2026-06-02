"""events table — event-sourced audit + BI feed (FlowEngine FE-01a)

Per spec §22: the canonical event log. Emitters are added in FE-06; this
migration only lays down the schema so emitter wiring has somewhere to
write.

Revision ID: 0015_create_events_table
Revises: 0014_create_talkflow_states_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0015_create_events_table"
down_revision = "0014_create_talkflow_states_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_variant", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "ix_events_tenant_occurred",
        "events",
        ["tenant_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_events_talk",
        "events",
        ["talk_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("talk_id IS NOT NULL"),
    )
    op.create_index(
        "ix_events_type_occurred",
        "events",
        ["event_type", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_events_experiment",
        "events",
        ["experiment_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("experiment_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY events_tenant_isolation ON events
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS events_tenant_isolation ON events")
    op.drop_index("ix_events_experiment", table_name="events")
    op.drop_index("ix_events_type_occurred", table_name="events")
    op.drop_index("ix_events_talk", table_name="events")
    op.drop_index("ix_events_tenant_occurred", table_name="events")
    op.drop_table("events")
