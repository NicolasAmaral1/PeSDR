"""kb_documents + kb_chunks: insert/select round-trip + RLS isolation + IVFFlat index."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_round_trip_document_and_chunks(session: AsyncSession) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
        await set_tenant_context(session, t.id)

        doc = KbDocument(
            tenant_id=t.id,
            kb_id="kb_x",
            doc_path="kb/t/kb_x/precos.md",
            content_hash="deadbeef",
            content_md="## Preços\n\nMentoria custa R$ 6000.",
        )
        session.add(doc)
        await session.flush()

        chunk = KbChunk(
            document_id=doc.id,
            tenant_id=t.id,
            kb_id="kb_x",
            chunk_idx=0,
            heading_path="Preços",
            content="Mentoria custa R$ 6000.",
            token_count=10,
            embedding=[0.1] * 1536,
        )
        session.add(chunk)

    async with session.begin():
        await set_tenant_context(session, t.id)
        got_doc = (
            await session.execute(select(KbDocument).where(KbDocument.tenant_id == t.id))
        ).scalar_one()
        assert got_doc.kb_id == "kb_x"
        got_chunk = (
            await session.execute(select(KbChunk).where(KbChunk.document_id == got_doc.id))
        ).scalar_one()
        assert got_chunk.heading_path == "Preços"
        assert len(got_chunk.embedding) == 1536


@pytest.mark.integration
async def test_rls_isolates_kb_chunks_across_tenants(session: AsyncSession) -> None:
    async with session.begin():
        t1 = Tenant(slug=f"a-{uuid.uuid4().hex[:8]}", display_name="A")
        t2 = Tenant(slug=f"b-{uuid.uuid4().hex[:8]}", display_name="B")
        session.add_all([t1, t2])
        await session.flush()

        await set_tenant_context(session, t1.id)
        d1 = KbDocument(
            tenant_id=t1.id,
            kb_id="kb",
            doc_path="d1.md",
            content_hash="h1",
            content_md="x",
        )
        session.add(d1)
        await session.flush()
        session.add(
            KbChunk(
                document_id=d1.id,
                tenant_id=t1.id,
                kb_id="kb",
                chunk_idx=0,
                content="t1",
                token_count=1,
                embedding=[0.1] * 1536,
            )
        )
        # Flush t1's chunk under t1 context BEFORE switching to t2 — otherwise the
        # WITH CHECK policy rejects the row when it's flushed with t2 context active.
        await session.flush()

        await set_tenant_context(session, t2.id)
        d2 = KbDocument(
            tenant_id=t2.id,
            kb_id="kb",
            doc_path="d2.md",
            content_hash="h2",
            content_md="y",
        )
        session.add(d2)
        await session.flush()
        session.add(
            KbChunk(
                document_id=d2.id,
                tenant_id=t2.id,
                kb_id="kb",
                chunk_idx=0,
                content="t2",
                token_count=1,
                embedding=[0.2] * 1536,
            )
        )
        await session.flush()

    async with session.begin():
        await set_tenant_context(session, t1.id)
        rows = (await session.execute(select(KbChunk))).scalars().all()
        contents = sorted(r.content for r in rows)
        assert contents == ["t1"]

    async with session.begin():
        await set_tenant_context(session, t2.id)
        rows = (await session.execute(select(KbChunk))).scalars().all()
        contents = sorted(r.content for r in rows)
        assert contents == ["t2"]


@pytest.mark.integration
async def test_ivfflat_index_exists(session: AsyncSession) -> None:
    async with session.begin():
        result = await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'kb_chunks' AND indexname = 'ix_kb_chunks_embedding'"
            )
        )
        row = result.scalar_one_or_none()
    assert row is not None
    assert "ivfflat" in row.lower()
    assert "vector_cosine_ops" in row.lower()
