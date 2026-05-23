import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_create_and_read_versions_and_talkflows(session: AsyncSession) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
        await set_tenant_context(session, t.id)

        v = TreeflowVersion(
            tenant_id=t.id,
            treeflow_id="demo",
            version="0.1.0",
            content_hash="deadbeef",
            content_yaml="id: demo\nversion: 0.1.0\n",
        )
        session.add(v)
        await session.flush()

        tf = TalkFlow(
            tenant_id=t.id,
            lead_id="lead-1",
            treeflow_version_id=v.id,
            thread_id=f"{t.id}:demo:lead-1",
        )
        session.add(tf)

    async with session.begin():
        await set_tenant_context(session, t.id)
        got = (
            await session.execute(select(TalkFlow).where(TalkFlow.lead_id == "lead-1"))
        ).scalar_one()
        assert got.status == "active"


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_reads_on_talkflows(session: AsyncSession) -> None:
    async with session.begin():
        t1 = Tenant(slug=f"a-{uuid.uuid4().hex[:8]}", display_name="A")
        t2 = Tenant(slug=f"b-{uuid.uuid4().hex[:8]}", display_name="B")
        session.add_all([t1, t2])
        await session.flush()

        await set_tenant_context(session, t1.id)
        v1 = TreeflowVersion(
            tenant_id=t1.id,
            treeflow_id="d",
            version="0.1.0",
            content_hash="x",
            content_yaml="x",
        )
        session.add(v1)
        await session.flush()
        session.add(
            TalkFlow(
                tenant_id=t1.id,
                lead_id="L1",
                treeflow_version_id=v1.id,
                thread_id=f"{t1.id}:d:L1",
            )
        )

        await set_tenant_context(session, t2.id)
        v2 = TreeflowVersion(
            tenant_id=t2.id,
            treeflow_id="d",
            version="0.1.0",
            content_hash="x",
            content_yaml="x",
        )
        session.add(v2)
        await session.flush()
        session.add(
            TalkFlow(
                tenant_id=t2.id,
                lead_id="L2",
                treeflow_version_id=v2.id,
                thread_id=f"{t2.id}:d:L2",
            )
        )

    # Read as t1 — should see only L1
    async with session.begin():
        await set_tenant_context(session, t1.id)
        rows = (await session.execute(select(TalkFlow))).scalars().all()
        leads = sorted(r.lead_id for r in rows)
        assert leads == ["L1"]

    # Read as t2 — should see only L2
    async with session.begin():
        await set_tenant_context(session, t2.id)
        rows = (await session.execute(select(TalkFlow))).scalars().all()
        leads = sorted(r.lead_id for r in rows)
        assert leads == ["L2"]
