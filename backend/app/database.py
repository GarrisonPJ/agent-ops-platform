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
    # closed-loop — policy pipeline
    "ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS max_steps INTEGER",
    "CREATE TABLE IF NOT EXISTS policy_versions (version_id TEXT PRIMARY KEY, version_display TEXT UNIQUE, parent_version TEXT, patch JSONB, rationale TEXT, expected_impact JSONB, confidence TEXT, status TEXT DEFAULT 'pending_review', score_delta FLOAT, reject_reason TEXT, created_at TIMESTAMPTZ)",
    "CREATE TABLE IF NOT EXISTS trajectory_policy_map (trajectory_id TEXT REFERENCES trajectories(id), policy_version_id TEXT REFERENCES policy_versions(version_id), PRIMARY KEY (trajectory_id, policy_version_id))",
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
        # Enable required PostgreSQL extensions before table creation
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass  # SQLite doesn't support PostgreSQL extensions — no-op

        await conn.run_sync(Base.metadata.create_all)

        # Run pending migrations (safe thanks to IF NOT EXISTS)
        for stmt in _MIGRATIONS:
            await conn.execute(text(stmt))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with async_session() as session:
        yield session
