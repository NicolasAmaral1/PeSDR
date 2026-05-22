import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
async def test_create_and_read_tenant(session: AsyncSession) -> None:
    # Cleanup any leftover from prior test runs
    await session.execute(Tenant.__table__.delete().where(Tenant.slug == "test-create-read"))
    await session.commit()

    t = Tenant(slug="test-create-read", display_name="Test Create Read")
    session.add(t)
    await session.commit()

    fetched = (
        await session.execute(select(Tenant).where(Tenant.slug == "test-create-read"))
    ).scalar_one()
    assert fetched.display_name == "Test Create Read"
    assert fetched.id is not None
