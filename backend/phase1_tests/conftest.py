from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.phase1_main import create_app
from app.phase1_models import Base


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]], None]:
    os.environ["RUNNER_TOKEN"] = "test-runner-token"
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app(
        session_factory=factory,
        database_engine=engine,
        initialize_database=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory
    await engine.dispose()
