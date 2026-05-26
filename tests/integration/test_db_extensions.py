import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@pytest.fixture
async def engine() -> AsyncEngine:
    from ai_sdr.settings import get_settings

    eng = create_async_engine(get_settings().database_url)
    yield eng
    await eng.dispose()


@pytest.mark.integration
async def test_extensions_installed(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT extname FROM pg_extension;"))
        names = {row[0] for row in result.all()}
        assert "uuid-ossp" in names
        assert "vector" in names
