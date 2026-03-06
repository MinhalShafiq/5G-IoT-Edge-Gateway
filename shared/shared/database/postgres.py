"""PostgreSQL async engine and session management via SQLAlchemy."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""

    pass


def get_async_engine(dsn: str, pool_size: int = 10) -> AsyncEngine:
    """Create or return the singleton async engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=20,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create or return the singleton session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_async_session(dsn: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session. Use as an async context manager or FastAPI dependency."""
    engine = get_async_engine(dsn)
    factory = get_session_factory(engine)
    async with factory() as session:
        yield session


async def init_db(dsn: str) -> None:
    """Create all tables. Call once at startup."""
    engine = get_async_engine(dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
