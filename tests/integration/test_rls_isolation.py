import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_reads(session: AsyncSession) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # Create ephemeral table with RLS (asyncpg requires one statement per execute)
    async with session.begin():
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS _rls_test ("
                "id SERIAL PRIMARY KEY, tenant_id UUID NOT NULL, value TEXT)"
            )
        )
        await session.execute(text("ALTER TABLE _rls_test ENABLE ROW LEVEL SECURITY"))
        await session.execute(text("ALTER TABLE _rls_test FORCE ROW LEVEL SECURITY"))
        await session.execute(text("DROP POLICY IF EXISTS tenant_iso ON _rls_test"))
        await session.execute(
            text(
                "CREATE POLICY tenant_iso ON _rls_test "
                "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
            )
        )

    try:
        # Insert as tenant_a
        async with session.begin():
            await set_tenant_context(session, tenant_a)
            await session.execute(
                text("INSERT INTO _rls_test (tenant_id, value) VALUES (:t, :v)"),
                {"t": str(tenant_a), "v": "row_a"},
            )

        # Insert as tenant_b
        async with session.begin():
            await set_tenant_context(session, tenant_b)
            await session.execute(
                text("INSERT INTO _rls_test (tenant_id, value) VALUES (:t, :v)"),
                {"t": str(tenant_b), "v": "row_b"},
            )

        # Read as tenant_a — should see only row_a
        async with session.begin():
            await set_tenant_context(session, tenant_a)
            rows = (await session.execute(text("SELECT value FROM _rls_test ORDER BY value"))).all()
            assert [r[0] for r in rows] == ["row_a"], f"expected only row_a, got {rows}"

        # Read as tenant_b — should see only row_b
        async with session.begin():
            await set_tenant_context(session, tenant_b)
            rows = (await session.execute(text("SELECT value FROM _rls_test ORDER BY value"))).all()
            assert [r[0] for r in rows] == ["row_b"], f"expected only row_b, got {rows}"
    finally:
        async with session.begin():
            await session.execute(text("DROP TABLE IF EXISTS _rls_test;"))
