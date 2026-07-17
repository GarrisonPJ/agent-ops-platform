"""Async database setup with short, explicit connection timeouts."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.phase1_models import Base


DEFAULT_DATABASE_URL = "postgresql+asyncpg://agentops:agentops@db:5432/agentops"


def make_engine(database_url: str | None = None) -> AsyncEngine:
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    options: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql+asyncpg"):
        options["connect_args"] = {"timeout": 3, "command_timeout": 5}
    return create_async_engine(url, **options)


engine = make_engine()
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(target_engine: AsyncEngine = engine) -> None:
    """Create a fresh development/demo schema.

    Production deployments use Alembic. Keeping this small bootstrap makes the
    deterministic local demo one-command while avoiding hand-written ALTERs.
    """
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
