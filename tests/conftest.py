"""Shared test fixtures for integration and E2E tests."""

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

# Override settings for test environment
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault(
    "POSTGRES_DSN",
    "postgresql+asyncpg://iot_gateway:changeme@localhost:5432/iot_gateway",
)
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("LOG_LEVEL", "WARNING")


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: requires the docker compose stack running")


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def redis_client():
    """Provide a test Redis client that flushes the test DB after each test."""
    from shared.database.redis_client import RedisClient

    client = RedisClient(os.environ["REDIS_URL"])
    yield client
    await client.redis.flushdb()
    await client.close()


async def _ensure_test_database(dsn: str) -> str:
    """Return a DSN for ``<db>_test`` on the same server, creating it if needed.

    Tests create and drop tables, so they must never touch the database the
    running services write to.
    """
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(dsn)
    test_db = f"{url.database}_test"
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": test_db}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{test_db}"'))
    await admin.dispose()
    return url.set(database=test_db).render_as_string(hide_password=False)


@pytest_asyncio.fixture
async def db_session():
    """Provide a session on a dedicated test database, dropping tables afterwards."""
    from shared.database.postgres import get_async_engine, Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine = get_async_engine(await _ensure_test_database(os.environ["POSTGRES_DSN"]))

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        yield session
        await session.rollback()

    # Drop all tables after tests (test database only)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
