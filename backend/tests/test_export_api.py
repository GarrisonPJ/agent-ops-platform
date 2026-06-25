"""API contract tests for ``GET /api/eval/export``.

Uses the same test infrastructure as ``test_eval_api.py`` (FastAPI
``dependency_overrides`` + httpx AsyncClient + function-scoped DB fixtures).
"""

from __future__ import annotations

import json
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
async def seeded_trajectories(session: AsyncSession) -> dict[str, Any]:
    """Insert multiple trajectories for the same task and return their IDs.

    Creates three trajectories for 'bench_01' with different scores, plus a
    single trajectory for 'bench_02'.
    """
    from app.runtime import Step as RuntimeStep
    from app.runtime import ToolCall

    step = RuntimeStep(
        index=0,
        thought="Final thought",
        action=None,
        observation="Answer is 42",
        latency_ms=50,
        started_at=1000.0,
    )

    repo = TrajectoryRepository(session)
    ids: dict[str, Any] = {}

    # ── bench_01 trajectories (3, with descending scores) ────────────
    tasks_scores = [("bench_01", 0.9), ("bench_01", 0.5), ("bench_01", 0.1)]
    for i, (task, score) in enumerate(tasks_scores):
        traj = await repo.create_trajectory(task)
        await repo.add_step(traj.id, step)
        await repo.update_trajectory_status(traj.id, "success")
        traj.score = score
        ids[f"bench_01_{i}"] = traj.id

    # ── bench_02 (single trajectory, SFT / jsonl single) ─────────────
    traj = await repo.create_trajectory("bench_02")
    await repo.add_step(traj.id, step)
    await repo.update_trajectory_status(traj.id, "success")
    traj.score = 0.7
    ids["bench_02_0"] = traj.id

    await session.commit()
    return ids


class TestExportAPI:
    """Contract tests for GET /api/eval/export."""

    # ------------------------------------------------------------------
    # openai_sft
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_openai_sft_by_trajectory_id(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """openai_sft with trajectory_id returns valid messages format."""
        tid = seeded_trajectories["bench_01_0"]
        resp = await client.get(
            "/api/eval/export",
            params={"trajectory_id": tid, "format": "openai_sft"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/x-ndjson"
        data = json.loads(resp.text.strip())
        assert "messages" in data
        assert len(data["messages"]) == 3
        assert data["messages"][0]["role"] == "system"
        assert data["messages"][1]["role"] == "user"
        assert data["messages"][2]["role"] == "assistant"
        assert data["messages"][2]["content"] == "Answer is 42"

    @pytest.mark.asyncio
    async def test_openai_sft_by_task_name(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """openai_sft with task_name picks the best trajectory."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "bench_01", "format": "openai_sft"},
        )
        assert resp.status_code == 200, resp.text
        data = json.loads(resp.text.strip())
        assert "messages" in data
        assert data["messages"][1]["content"] == "bench_01"

    @pytest.mark.asyncio
    async def test_openai_sft_content_type(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """openai_sft response has NDJSON content type and disposition."""
        tid = seeded_trajectories["bench_01_0"]
        resp = await client.get(
            "/api/eval/export",
            params={"trajectory_id": tid, "format": "openai_sft"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-ndjson"
        assert 'filename="export.jsonl"' in resp.headers["content-disposition"]

    # ------------------------------------------------------------------
    # rlhf_pair
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_rlhf_pair_by_task_name(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """rlhf_pair with task_name returns chosen/rejected structure."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "bench_01", "format": "rlhf_pair"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "chosen" in data
        assert "rejected" in data
        # chosen should be the highest-scoring trajectory
        assert data["chosen"]["score"] == 0.9
        assert data["rejected"]["score"] == 0.1

    @pytest.mark.asyncio
    async def test_rlhf_pair_content_type(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """rlhf_pair response has JSON content type and disposition."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "bench_01", "format": "rlhf_pair"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert 'filename="export.json"' in resp.headers["content-disposition"]

    # ------------------------------------------------------------------
    # jsonl
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_jsonl_by_task_name(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """jsonl with task_name dumps all trajectories as NDJSON."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "bench_01", "format": "jsonl"},
        )
        assert resp.status_code == 200, resp.text
        lines = resp.text.strip().split("\n")
        assert len(lines) == 3  # three bench_01 trajectories
        for line in lines:
            obj = json.loads(line)
            assert "id" in obj
            assert "task" in obj
            assert "steps" in obj
            assert obj["task"] == "bench_01"

    @pytest.mark.asyncio
    async def test_jsonl_by_trajectory_id(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """jsonl with trajectory_id dumps a single trajectory."""
        tid = seeded_trajectories["bench_01_0"]
        resp = await client.get(
            "/api/eval/export",
            params={"trajectory_id": tid, "format": "jsonl"},
        )
        assert resp.status_code == 200, resp.text
        lines = resp.text.strip().split("\n")
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["id"] == tid
        assert obj["task"] == "bench_01"

    @pytest.mark.asyncio
    async def test_jsonl_content_type(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """jsonl response has NDJSON content type and disposition."""
        tid = seeded_trajectories["bench_01_0"]
        resp = await client.get(
            "/api/eval/export",
            params={"trajectory_id": tid, "format": "jsonl"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-ndjson"
        assert 'filename="export.jsonl"' in resp.headers["content-disposition"]

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unknown_format(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """Unknown format returns 422."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "bench_01", "format": "csv"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_non_existent_trajectory_id(
        self, client: AsyncClient
    ) -> None:
        """Non-existent trajectory_id returns 404."""
        resp = await client.get(
            "/api/eval/export",
            params={"trajectory_id": "does-not-exist", "format": "jsonl"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_non_existent_task_name(
        self, client: AsyncClient
    ) -> None:
        """Non-existent task_name returns 404."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "no-such-task", "format": "jsonl"},
        )
        assert resp.status_code == 404
        assert "no trajectories found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rlhf_pair_insufficient_trajectories(
        self, client: AsyncClient, seeded_trajectories: dict[str, Any]
    ) -> None:
        """rlhf_pair with a task that has only 1 trajectory returns 400."""
        resp = await client.get(
            "/api/eval/export",
            params={"task_name": "bench_02", "format": "rlhf_pair"},
        )
        assert resp.status_code == 400
        assert "need at least 2" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_both_params(
        self, client: AsyncClient
    ) -> None:
        """Missing both task_name and trajectory_id returns 422."""
        resp = await client.get(
            "/api/eval/export",
            params={"format": "jsonl"},
        )
        assert resp.status_code == 422
