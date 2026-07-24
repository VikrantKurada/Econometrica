"""Shared test fixtures.

Database tests run against the real ``econometrica_test`` Postgres database,
never SQLite: TimescaleDB hypertables and pgvector columns have no SQLite
equivalent, so a substitute engine would prove nothing.
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from econometrica.config import get_settings

# Importing the models package registers every table on ``Base.metadata``.
from econometrica.db import models  # noqa: F401
from econometrica.db.base import Base

_FALLBACK_TEST_DATABASE_URL = (
    "postgresql+asyncpg://econometrica:change-me-locally@localhost:5433/econometrica_test"
)


def _test_database_url() -> str:
    """Resolve the test database URL from the environment / .env, with a fallback."""
    return get_settings().test_database_url or _FALLBACK_TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """One engine per test session, with a freshly built schema."""
    async_engine = create_async_engine(_test_database_url(), future=True, pool_pre_ping=True)
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield async_engine
    await async_engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """A session wrapped in a transaction that is always rolled back.

    Tests therefore never leak state into one another, and no test needs to
    clean up after itself.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async_session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield async_session
        finally:
            await async_session.close()
            if transaction.is_active:
                await transaction.rollback()
