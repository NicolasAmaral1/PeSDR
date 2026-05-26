"""treeflow_versions + talkflows tables (with RLS)

Revision ID: 0003_treeflow_tables
Revises: 0002_tenants_table
Create Date: 2026-05-22 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_treeflow_tables"
down_revision = "0002_tenants_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "treeflow_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("treeflow_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_yaml", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "treeflow_id", "version", name="uq_tfv_tenant_id_ver"),
    )
    op.create_index("ix_treeflow_versions_tenant_id", "treeflow_versions", ["tenant_id"])

    op.create_table(
        "talkflows",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.String(length=128), nullable=False),
        sa.Column("treeflow_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.String(length=256), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "completed", "cold", name="talkflow_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["treeflow_version_id"], ["treeflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("tenant_id", "lead_id", name="uq_talkflows_tenant_lead"),
        sa.UniqueConstraint("thread_id", name="uq_talkflows_thread_id"),
    )
    op.create_index("ix_talkflows_tenant_id", "talkflows", ["tenant_id"])
    op.create_index("ix_talkflows_thread_id", "talkflows", ["thread_id"], unique=True)

    # RLS — both tables
    for tbl in ("treeflow_versions", "talkflows"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_iso ON {tbl}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            """
        )


def downgrade() -> None:
    for tbl in ("talkflows", "treeflow_versions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_iso ON {tbl};")
    op.drop_index("ix_talkflows_thread_id", table_name="talkflows")
    op.drop_index("ix_talkflows_tenant_id", table_name="talkflows")
    op.drop_table("talkflows")
    op.execute("DROP TYPE IF EXISTS talkflow_status;")
    op.drop_index("ix_treeflow_versions_tenant_id", table_name="treeflow_versions")
    op.drop_table("treeflow_versions")
