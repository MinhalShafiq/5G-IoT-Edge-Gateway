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


@pytest_asyncio.fixture
async def db_session():
    """Provide a test database session with automatic rollback."""
    from shared.database.postgres import get_async_engine, Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine = get_async_engine(os.environ["POSTGRES_DSN"])

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        yield session
        await session.rollback()

    # Drop all tables after tests
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
