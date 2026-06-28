"""API contract tests for ``POST /api/eval/score``.

These tests use ``httpx.AsyncClient`` against the real FastAPI app and require a
running PostgreSQL database.  The database is cleaned up after each test session.
"""
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

# Allow pointing to a separate test database
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


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Yield an httpx client with the ``get_db`` dependency overridden."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded_trajectory_id(session: AsyncSession) -> str:
    """Insert a trajectory with steps and return its ID."""
    repo = TrajectoryRepository(session)
    traj = await repo.create_trajectory("Test task for scoring")
    # Add a couple of steps
    from app.runtime import Step as RuntimeStep
    from app.runtime import ToolCall

    step1 = RuntimeStep(
        index=0,
        thought="Let me search",
        action=ToolCall(id="call_1", name="search", arguments={"q": "hello"}),
        observation="Found some results",
        latency_ms=150,
        started_at=1000.0,
    )
    step2 = RuntimeStep(
        index=1,
        thought="Let me read",
        action=ToolCall(id="call_2", name="read", arguments={"url": "https://x.com"}),
        observation="Page loaded successfully",
        latency_ms=250,
        started_at=2000.0,
    )
    step3 = RuntimeStep(
        index=2,
        thought="Here is the answer",
        action=None,
        observation="Final answer: hello world",
        latency_ms=50,
        started_at=3000.0,
    )
    await repo.add_step(traj.id, step1)
    await repo.add_step(traj.id, step2)
    await repo.add_step(traj.id, step3)
    await repo.update_trajectory_status(traj.id, "success")
    await session.commit()
    return traj.id


class TestEvalScoreAPI:
    """Contract tests for POST /api/eval/score."""

    @pytest.mark.asyncio
    async def test_happy_path(
        self, client: AsyncClient, seeded_trajectory_id: str
    ) -> None:
        """Score an existing trajectory with default weights."""
        resp = await client.post(
            "/api/eval/score",
            json={"trajectory_id": seeded_trajectory_id},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["trajectory_id"] == seeded_trajectory_id
        assert isinstance(data["score"], float)
        assert "breakdown" in data
        assert "success_reward" in data["breakdown"]
        assert "cost_penalty" in data["breakdown"]
        assert "latency_penalty" in data["breakdown"]
        assert "tool_failure_penalty" in data["breakdown"]

    @pytest.mark.asyncio
    async def test_404_unknown_trajectory(
        self, client: AsyncClient
    ) -> None:
        """Non-existent trajectory_id returns 404."""
        resp = await client.post(
            "/api/eval/score",
            json={"trajectory_id": "non-existent-id"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_custom_weights(
        self, client: AsyncClient, seeded_trajectory_id: str
    ) -> None:
        """Custom weights produce a different score and correct breakdown."""
        resp = await client.post(
            "/api/eval/score",
            json={
                "trajectory_id": seeded_trajectory_id,
                "weights": {"cost": 0, "latency": 0, "tool_failure": 0},
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # With zero weights, score should be exactly 1.0 (only success_reward)
        assert data["score"] == 1.0
        assert data["breakdown"]["cost_penalty"] == 0.0
        assert data["breakdown"]["latency_penalty"] == 0.0
        assert data["breakdown"]["tool_failure_penalty"] == 0.0

    @pytest.mark.asyncio
    async def test_invalid_weights_negative(
        self, client: AsyncClient, seeded_trajectory_id: str
    ) -> None:
        """Negative weight values return 422."""
        resp = await client.post(
            "/api/eval/score",
            json={
                "trajectory_id": seeded_trajectory_id,
                "weights": {"cost": -1},
            },
        )
        assert resp.status_code == 422
        assert "non-negative" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_weights_unknown_key(
        self, client: AsyncClient, seeded_trajectory_id: str
    ) -> None:
        """Unknown weight keys return 422."""
        resp = await client.post(
            "/api/eval/score",
            json={
                "trajectory_id": seeded_trajectory_id,
                "weights": {"invalid_key": 0.5},
            },
        )
        assert resp.status_code == 422
        assert "unknown" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_trajectory_id(
        self, client: AsyncClient
    ) -> None:
        """Missing trajectory_id in body returns 422."""
        resp = await client.post(
            "/api/eval/score",
            json={},
        )
        assert resp.status_code == 422
