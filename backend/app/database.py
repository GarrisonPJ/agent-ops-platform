"""SQLAlchemy async engine, session factory, and DB initialization."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


_MIGRATIONS: list[str] = [
    # v0.3 — scoring engine
    "ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS score FLOAT",
    "ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS score_breakdown JSONB",
]


async def init_db() -> None:
    """Create all tables defined by models and run pending migrations.

    Imports models here so they register with ``Base.metadata`` before the
    DDL is issued.  ``Base.metadata.create_all`` handles table *creation* but
    will not add columns to existing tables — we run ``ALTER TABLE`` statements
    for those.
    """
    from app.models import Base  # noqa: F811

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Run pending migrations (safe thanks to IF NOT EXISTS)
        for stmt in _MIGRATIONS:
            await conn.execute(text(stmt))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with async_session() as session:
        yield session
