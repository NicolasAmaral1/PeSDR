"""users + user_tenant_access tables (no RLS — auth-serving tables)

Revision ID: 0009_users_and_access
Revises: 0008_talkflows_lead_id_fk
Create Date: 2026-05-26 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0009_users_and_access"
down_revision = "0008_talkflows_lead_id_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Case-insensitive unique on username
    op.execute("CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))")

    op.create_table(
        "user_tenant_access",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "tenant_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "role IN ('operator', 'tenant_admin')",
            name="ck_user_tenant_access_role",
        ),
    )
    op.create_index(
        "ix_user_tenant_access_tenant",
        "user_tenant_access",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_tenant_access_tenant", table_name="user_tenant_access")
    op.drop_table("user_tenant_access")
    op.execute("DROP INDEX IF EXISTS uq_users_username_lower")
    op.drop_table("users")
