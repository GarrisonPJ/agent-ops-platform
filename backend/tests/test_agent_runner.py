"""Tests for the agent runner module and trajectory_repo layering fix."""
from __future__ import annotations

import pytest
pytestmark = pytest.mark.needs_postgresql

import os
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models import Base
from app.trajectory_repo import TrajectoryRepository

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://agentops:agentops@localhost:5433/agentops_test",
)


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a test engine and all tables before tests, drop after."""
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a fresh async session for each test."""
    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as sess:
        yield sess


class TestTrajectoryStatusNoScoring:
    """After the refactor, ``update_trajectory_status`` must NOT trigger scoring."""

    @pytest.mark.asyncio
    async def test_update_status_does_not_set_score(
        self, session: AsyncSession
    ) -> None:
        """Calling update_trajectory_status should leave score as None."""
        from app.runtime import Step as RuntimeStep
        from app.runtime import ToolCall

        repo = TrajectoryRepository(session)

        # Create trajectory and store id separately
        traj = await repo.create_trajectory("Test task")
        traj_id = traj.id

        # Add a step
        step = RuntimeStep(
            index=0,
            thought="Test thought",
            action=ToolCall(id="call_1", name="search", arguments={"q": "hello"}),
            observation="Found",
            latency_ms=100,
            started_at=1000.0,
        )
        await repo.add_step(traj_id, step)

        # Update status — after refactor this should NOT set score
        await repo.update_trajectory_status(traj_id, "success")
        await session.commit()

        # Fresh query to verify score is still None
        reloaded = await repo.get_trajectory(traj_id)
        assert reloaded is not None
        assert reloaded.score is None, (
            "update_trajectory_status should not trigger scoring"
        )

    @pytest.mark.asyncio
    async def test_update_status_still_updates_aggregates(
        self, session: AsyncSession
    ) -> None:
        """Token aggregates should still be computed on status update."""
        from app.runtime import Step as RuntimeStep
        from app.runtime import ToolCall

        repo = TrajectoryRepository(session)

        traj = await repo.create_trajectory("Token test")
        traj_id = traj.id

        step1 = RuntimeStep(
            index=0,
            thought="Step 1",
            action=ToolCall(id="c1", name="search", arguments={"q": "a"}),
            observation="Done",
            latency_ms=100,
            started_at=1000.0,
            token_prompt=50,
            token_completion=100,
        )
        step2 = RuntimeStep(
            index=1,
            thought="Step 2",
            action=None,
            observation="Final",
            latency_ms=50,
            started_at=2000.0,
            token_prompt=30,
            token_completion=70,
        )
        await repo.add_step(traj_id, step1)
        await repo.add_step(traj_id, step2)

        await repo.update_trajectory_status(traj_id, "success")
        await session.commit()

        # Fresh query
        reloaded = await repo.get_trajectory(traj_id)
        assert reloaded is not None
        # total_tokens = 50 + 100 + 30 + 70 = 250
        assert reloaded.total_tokens == 250
        assert reloaded.status == "success"
