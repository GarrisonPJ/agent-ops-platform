"""API contract tests for benchmark endpoints.

Tests cover ``GET /api/eval/benchmarks`` and ``POST /api/eval/benchmark``.

Uses the same pattern as ``test_eval_api.py``: FastAPI ``dependency_overrides``,
``httpx.AsyncClient``, and function-scoped DB fixtures.  The
``run_benchmark_task`` callable is mocked via ``unittest.mock.patch`` to return
pre-seeded trajectory IDs (no real agent runs).
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator
from unittest.mock import patch

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
async def seeded_benchmark_trajs(session: AsyncSession) -> list[str]:
    """Create 5 trajectories with descending scores for ranking tests.

    Scores: 0.95, 0.80, 0.80 (tied), 0.65, 0.45 — allowing verification of
    dense ranking logic.
    """
    repo = TrajectoryRepository(session)
    ids: list[str] = []
    for score in [0.95, 0.80, 0.80, 0.65, 0.45]:
        traj = await repo.create_trajectory("Benchmark test task")
        await session.flush()
        traj.status = "success"
        await repo.set_score(traj.id, score, {"dummy": True})
        ids.append(traj.id)
    await session.commit()
    return ids


@pytest_asyncio.fixture
async def client(
    session: AsyncSession,
    seeded_benchmark_trajs: list[str],
) -> AsyncGenerator[AsyncClient, None]:
    """Yield an httpx client with ``get_db`` overridden and ``run_benchmark_task`` mocked.

    The ``get_db`` dependency returns the test session.
    The ``run_benchmark_task`` callable is replaced with a mock that returns
    pre-seeded trajectory IDs, avoiding any real LLM or executor calls.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield session

    ids_iter = iter(seeded_benchmark_trajs)

    async def mock_runner(task: str) -> str:
        return next(ids_iter)

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    with patch("app.main.run_benchmark_task", side_effect=mock_runner):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()


class TestBenchmarkAPI:
    """Contract tests for GET /api/eval/benchmarks and POST /api/eval/benchmark."""

    # ------------------------------------------------------------------
    # GET /api/eval/benchmarks
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_benchmarks(self, client: AsyncClient) -> None:
        """GET returns 5 predefined tasks with correct fields."""
        resp = await client.get("/api/eval/benchmarks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5
        for item in data:
            assert "name" in item
            assert "task" in item
            assert "description" in item

    # ------------------------------------------------------------------
    # POST /api/eval/benchmark — happy path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_valid_task_name(self, client: AsyncClient) -> None:
        """POST with valid task_name returns 200 with correct response schema."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task_name": "bench_01_http_request", "n_runs": 3},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # -- task field ---------------------------------------------------------
        expected_task = (
            "使用 http_request 发送 GET 请求到 https://httpbin.org/json 获取数据"
        )
        assert data["task"] == expected_task

        # -- counts -------------------------------------------------------------
        assert data["n_runs"] == 3
        assert data["completed"] == 3
        assert len(data["rankings"]) == 3

        # -- schema checks on each ranking entry --------------------------------
        for entry in data["rankings"]:
            assert "trajectory_id" in entry
            assert isinstance(entry["rank"], int)
            assert entry["rank"] >= 1
            assert isinstance(entry["score"], float)
            assert isinstance(entry["status"], str)

        # -- scores sorted descending -------------------------------------------
        scores = [r["score"] for r in data["rankings"]]
        assert scores == sorted(scores, reverse=True), "Rankings not sorted"

        # -- best / worst present -----------------------------------------------
        assert data["best"] is not None
        assert "trajectory_id" in data["best"]
        assert "score" in data["best"]
        assert data["worst"] is not None
        assert "trajectory_id" in data["worst"]
        assert "score" in data["worst"]

    @pytest.mark.asyncio
    async def test_custom_task(self, client: AsyncClient) -> None:
        """POST with custom inline task (no predefined name) returns 200."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task": "我的自定义任务", "n_runs": 2},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["task"] == "我的自定义任务"
        assert data["n_runs"] == 2
        assert data["completed"] == 2
        assert len(data["rankings"]) == 2

    @pytest.mark.asyncio
    async def test_dense_ranking(self, client: AsyncClient) -> None:
        """Dense ranking: same score = same rank, no gaps in rank values.

        With seeded scores [0.95, 0.80, 0.80, 0.65, 0.45], dense ranks
        should be [1, 2, 2, 3, 4].
        """
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task_name": "bench_01_http_request", "n_runs": 5},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        rankings = data["rankings"]

        assert len(rankings) == 5

        # Dense ranking expectations:
        #   score 0.95  -> rank 1
        #   score 0.80  -> rank 2
        #   score 0.80  -> rank 2 (tied)
        #   score 0.65  -> rank 3
        #   score 0.45  -> rank 4
        assert rankings[0]["score"] == 0.95
        assert rankings[0]["rank"] == 1

        assert rankings[1]["score"] == 0.80
        assert rankings[1]["rank"] == 2

        assert rankings[2]["score"] == 0.80
        assert rankings[2]["rank"] == 2  # tied with previous

        assert rankings[3]["score"] == 0.65
        assert rankings[3]["rank"] == 3

        assert rankings[4]["score"] == 0.45
        assert rankings[4]["rank"] == 4

        # best / worst pointers
        assert data["best"]["score"] == 0.95
        assert data["best"]["trajectory_id"] == rankings[0]["trajectory_id"]
        assert data["worst"]["score"] == 0.45
        assert data["worst"]["trajectory_id"] == rankings[4]["trajectory_id"]

    # ------------------------------------------------------------------
    # POST /api/eval/benchmark — validation errors
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_n_runs_0(self, client: AsyncClient) -> None:
        """n_runs=0 returns 422."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task_name": "bench_01_http_request", "n_runs": 0},
        )
        assert resp.status_code == 422
        assert "n_runs" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_n_runs_11(self, client: AsyncClient) -> None:
        """n_runs=11 returns 422 (hard cap is 10)."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task_name": "bench_01_http_request", "n_runs": 11},
        )
        assert resp.status_code == 422
        assert "n_runs" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_unknown_task_name(self, client: AsyncClient) -> None:
        """Unknown task_name returns 404."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task_name": "nonexistent_benchmark", "n_runs": 5},
        )
        assert resp.status_code == 404
        assert "unknown" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_both_task_name_and_task(self, client: AsyncClient) -> None:
        """Both task_name and task in body returns 422."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"task_name": "bench_01", "task": "custom", "n_runs": 5},
        )
        assert resp.status_code == 422
        assert "not both" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_neither_task_nor_task_name(self, client: AsyncClient) -> None:
        """Neither task_name nor task in body returns 422."""
        resp = await client.post(
            "/api/eval/benchmark",
            json={"n_runs": 5},
        )
        assert resp.status_code == 422
        assert "required" in resp.json()["detail"].lower()
