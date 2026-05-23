"""checkpointer schema is created by langgraph-checkpoint-postgres at startup (no-op stamp)

Revision ID: 0004_checkpointer_setup
Revises: 0003_treeflow_tables
Create Date: 2026-05-22 00:00:00
"""


revision = "0004_checkpointer_setup"
down_revision = "0003_treeflow_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: the `checkpoints`, `checkpoint_writes`, `checkpoint_migrations` tables
    are created by `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.setup()`,
    invoked from `ai_sdr.treeflow.checkpointer.ensure_checkpointer_schema()` at app
    startup. This stamp records that, by revision 0004, those tables are expected
    to exist."""


def downgrade() -> None:
    pass
