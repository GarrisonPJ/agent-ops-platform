"""Tests for the long-term memory module (Embedder + MemoryRetriever).

Tests are split into two groups:

* **Embedder unit tests** — mock ``httpx``, no database required.
* **Retriever integration tests** — require PostgreSQL with pgvector
  (marked ``needs_postgresql``).
"""

from __future__ import annotations

import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.memory.embedder import Embedder
from app.memory.retriever import MemoryRetriever, store_trajectory_memory
from app.models import Base, Trajectory, TrajectoryEmbedding

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://agentops:agentops@localhost:5433/agentops_test",
)


def _make_vec(value_at_0: float) -> list[float]:
    """Return a 1536-dimensional (axis-0-dominant) vector.

    All elements are 0 except the first one, which is *value_at_0*.
    """
    v = [0.0] * 1536
    v[0] = value_at_0
    return v


# ============================================================================
# Embedder — unit tests (no DB, httpx mocked)
# ============================================================================


class TestEmbedder:
    """Unit tests for ``Embedder.embed()`` with a mocked HTTP client."""

    @pytest.mark.asyncio
    async def test_embed_success(self) -> None:
        """Happy path: embedder returns the vector from the API."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            }
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        embedder = Embedder(
            "https://api.example.com/v1", "sk-test", client=mock_client
        )
        result = await embedder.embed("hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embed_no_base_url(self) -> None:
        """When base_url is empty, embed() returns None (no-op)."""
        embedder = Embedder("", "", client=AsyncMock(spec=httpx.AsyncClient))
        result = await embedder.embed("hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_empty_text(self) -> None:
        """Empty text returns None without making an API call."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        embedder = Embedder(
            "https://api.example.com/v1", "sk-test", client=mock_client
        )
        result = await embedder.embed("")
        assert result is None
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_api_error(self) -> None:
        """API error is caught and logged; embed() returns None."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "403 Forbidden",
                request=MagicMock(),
                response=MagicMock(status_code=403),
            )
        )

        embedder = Embedder(
            "https://api.example.com/v1", "bad-key", client=mock_client
        )
        result = await embedder.embed("hello")
        assert result is None


# ============================================================================
# MemoryRetriever — integration tests (require PostgreSQL + pgvector)
# ============================================================================


@pytest_asyncio.fixture(scope="function")
async def pg_engine():
    """Create a test engine with pgvector + all tables, drop after test.

    Skipped when TEST_DATABASE_URL env var is not set (no PostgreSQL available).
    """
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set — requires PostgreSQL + pgvector")
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a fresh SQLAlchemy session backed by the pgvector-enabled engine."""
    factory = async_sessionmaker(
        pg_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as sess:
        yield sess


def _seed_trajectory(pg_session: AsyncSession, tid: str, task: str) -> None:
    """Insert a Trajectory row (FK target for TrajectoryEmbedding)."""
    pg_session.add(
        Trajectory(
            id=tid,
            task=task,
            status="success",
        )
    )


async def _seed_embedding(
    pg_session: AsyncSession, tid: str, value_at_0: float, summary: str
) -> None:
    """Insert a TrajectoryEmbedding row with a known axis-0 vector."""
    await store_trajectory_memory(
        pg_session, tid, _make_vec(value_at_0), summary
    )


@pytest.mark.needs_postgresql
class TestMemoryRetriever:
    """Integration tests against a real PostgreSQL instance with pgvector."""

    @pytest.mark.asyncio
    async def test_retrieve_similar_trajectories(self, pg_session: AsyncSession) -> None:
        """Seed two trajectories; query with a vector similar to one of them.

        Expect only the matching one to be returned.
        """
        _seed_trajectory(pg_session, "traj-a", "Cooking task")
        _seed_trajectory(pg_session, "traj-b", "Programming task")
        await _seed_embedding(pg_session, "traj-a", 1.0, "Cooked pasta")
        await _seed_embedding(pg_session, "traj-b", 0.0, "Wrote Python code")
        await pg_session.commit()

        retriever = MemoryRetriever(pg_session)
        # Query vector with high axis-0 value (similar to traj-a)
        results = await retriever.query(
            _make_vec(0.95), k=3, min_similarity=0.7
        )

        assert results == ["Cooked pasta"]

    @pytest.mark.asyncio
    async def test_retrieve_unrelated_returns_empty(
        self, pg_session: AsyncSession
    ) -> None:
        """Seed two trajectories; query with an unrelated vector.

        Expect no results (cold-start / empty).
        """
        _seed_trajectory(pg_session, "traj-a", "Cooking task")
        _seed_trajectory(pg_session, "traj-b", "Programming task")
        await _seed_embedding(pg_session, "traj-a", 1.0, "Cooked pasta")
        await _seed_embedding(pg_session, "traj-b", 0.0, "Wrote Python code")
        await pg_session.commit()

        retriever = MemoryRetriever(pg_session)
        # Vector with axis-1 dominant — unrelated to both axis-0 seeds
        unrelated = [0.0] * 1536
        unrelated[1] = 1.0
        results = await retriever.query(unrelated, k=3, min_similarity=0.7)

        assert results == []

    @pytest.mark.asyncio
    async def test_cold_start_empty(self, pg_session: AsyncSession) -> None:
        """Query with no embeddings stored returns empty list."""
        retriever = MemoryRetriever(pg_session)
        results = await retriever.query(_make_vec(0.5))
        assert results == []

    @pytest.mark.asyncio
    async def test_store_and_retrieve_round_trip(
        self, pg_session: AsyncSession
    ) -> None:
        """Store an embedding, then retrieve it with a matching query."""
        _seed_trajectory(pg_session, "round-trip-tid", "Test task")
        await _seed_embedding(
            pg_session, "round-trip-tid", 1.0, "Round-trip summary"
        )
        await pg_session.commit()

        retriever = MemoryRetriever(pg_session)
        results = await retriever.query(_make_vec(1.0), k=3, min_similarity=0.7)

        assert results == ["Round-trip summary"]

    @pytest.mark.asyncio
    async def test_query_returns_multiple_when_multiple_match(
        self, pg_session: AsyncSession
    ) -> None:
        """When all seeded embeddings are similar, all are returned."""
        for i in range(3):
            tid = f"multi-{i}"
            _seed_trajectory(pg_session, tid, f"Task {i}")
            await _seed_embedding(
                pg_session, tid, 1.0, f"Summary {i}"
            )
        await pg_session.commit()

        retriever = MemoryRetriever(pg_session)
        results = await retriever.query(_make_vec(1.0), k=5, min_similarity=0.7)

        assert len(results) == 3
        assert set(results) == {"Summary 0", "Summary 1", "Summary 2"}
