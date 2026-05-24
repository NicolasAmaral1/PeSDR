"""kb_documents + kb_chunks tables (with RLS + IVFFlat index)

Revision ID: 0005_kb_tables
Revises: 0004_checkpointer_setup
Create Date: 2026-05-24 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_kb_tables"
down_revision = "0004_checkpointer_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_documents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", sa.String(length=128), nullable=False),
        sa.Column("doc_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
        sa.UniqueConstraint("tenant_id", "kb_id", "doc_path", name="uq_kb_documents_path"),
    )
    op.create_index("ix_kb_documents_tenant_id", "kb_documents", ["tenant_id"])

    op.create_table(
        "kb_chunks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "chunk_idx", name="uq_kb_chunks_doc_idx"),
    )
    op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])
    op.create_index("ix_kb_chunks_filter", "kb_chunks", ["tenant_id", "kb_id"])
    # IVFFlat index for cosine similarity search. lists=100 is fine for <10k chunks.
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding ON kb_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);"
    )

    # RLS on both tables
    for tbl in ("kb_documents", "kb_chunks"):
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
    for tbl in ("kb_chunks", "kb_documents"):
        op.execute(f"DROP POLICY IF EXISTS tenant_iso ON {tbl};")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding;")
    op.drop_index("ix_kb_chunks_filter", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_document_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_kb_documents_tenant_id", table_name="kb_documents")
    op.drop_table("kb_documents")
