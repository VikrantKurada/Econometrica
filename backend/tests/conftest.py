"""Shared test fixtures.

Database tests run against the real ``econometrica_test`` Postgres database,
never SQLite: TimescaleDB hypertables and pgvector columns have no SQLite
equivalent, so a substitute engine would prove nothing.
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from econometrica.config import get_settings

# Importing the models package registers every table on ``Base.metadata``.
from econometrica.db import models  # noqa: F401
from econometrica.db.base import Base
from econometrica.db.session import get_session
from econometrica.main import app

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


@pytest_asyncio.fixture(loop_scope="session")
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client whose requests run against the rolled-back test session.

    Overriding ``get_session`` is what keeps the API off the real
    ``econometrica`` database: without it every request would open its own
    session against ``DATABASE_URL``, commit for real, and leave rows behind.
    The session joins the fixture's outer transaction, so a ``commit()`` inside
    a route flushes and stays visible to later requests in the same test, yet is
    still discarded when the fixture rolls back.
    """

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.clear()
