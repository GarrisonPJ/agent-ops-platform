"""E2E smoke test — validates asyncpg-specific paths like lazy-load behaviour.

Runs against a real PostgreSQL + asyncpg so it catches MissingGreenlet
and other runtime errors that SQLite in-memory can't reproduce.

Requires ``TEST_DATABASE_URL`` pointing to a running PostgreSQL instance
or the Docker Compose dev environment.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://agentops:agentops@localhost:5433/agentops",
)


@pytest_asyncio.fixture
async def real_session() -> AsyncSession:  # type: ignore[misc]
    """Create a session against a real PostgreSQL database."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.smoke
class TestE2ESmoke:
    """Smoke tests that exercise real asyncpg paths."""

    @pytest.mark.asyncio
    async def test_trajectory_create_and_read(
        self, real_session: AsyncSession
    ) -> None:
        """Trajectory CRUD works against real PostgreSQL."""
        from app.trajectory_repo import TrajectoryRepository

        repo = TrajectoryRepository(real_session)
        traj = await repo.create_trajectory("smoke: create and read")
        await real_session.commit()

        loaded = await repo.get_trajectory(traj.id)
        assert loaded is not None
        assert loaded.status == "running"

    @pytest.mark.asyncio
    async def test_render_scoring_view_with_eager_load(
        self, real_session: AsyncSession
    ) -> None:
        """Regression: eager-loaded steps must not trigger MissingGreenlet."""
        from app.serializer import render_scoring_view
        from app.config import settings
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.models import Trajectory
        from app.trajectory_repo import TrajectoryRepository

        repo = TrajectoryRepository(real_session)
        traj = await repo.create_trajectory("smoke: eager-load")
        await real_session.commit()

        stmt = (
            select(Trajectory)
            .options(selectinload(Trajectory.steps))
            .where(Trajectory.id == traj.id)
        )
        result = await real_session.execute(stmt)
        loaded = result.scalar_one()

        # Must not raise — this was the MissingGreenlet bug
        d = render_scoring_view(loaded, settings.llm_max_steps)
        assert d["status"] == traj.status
        assert isinstance(d["steps"], list)

    @pytest.mark.asyncio
    async def test_render_scoring_view_without_eager_load_raises(
        self, real_session: AsyncSession
    ) -> None:
        """Verify MissingGreenlet IS raised without eager-load.

        Proves the regression test above is meaningful. If SQLAlchemy ever
        changes lazy-load behaviour, this alerts us.
        """
        from app.serializer import render_scoring_view
        from app.config import settings
        from sqlalchemy import select
        from app.models import Trajectory
        from app.trajectory_repo import TrajectoryRepository

        repo = TrajectoryRepository(real_session)
        traj = await repo.create_trajectory("smoke: no eager-load")
        await real_session.commit()

        stmt = select(Trajectory).where(Trajectory.id == traj.id)
        result = await real_session.execute(stmt)
        loaded = result.scalar_one()

        with pytest.raises(Exception) as exc_info:  # noqa: PT011
            render_scoring_view(loaded, settings.llm_max_steps)

        name = type(exc_info.value).__name__
        msg = str(exc_info.value).lower()
        assert "MissingGreenlet" in name or "greenlet" in msg or "lazy" in msg, \
            f"Expected lazy-load error, got {name}: {msg}"

    @pytest.mark.asyncio
    async def test_pipeline_trajectory_fetch_uses_eager_load(
        self, real_session: AsyncSession
    ) -> None:
        """Policy pipeline trajectory query uses selectinload — no MissingGreenlet."""
        from app.trajectory_repo import TrajectoryRepository
        from app.policy_pipeline import _run_compile_pipeline
        from app.policy_store import PolicyStore
        from app.models import Step, Trajectory
        from datetime import datetime, timezone

        repo = TrajectoryRepository(real_session)
        store = PolicyStore(real_session)

        traj = await repo.create_trajectory("smoke: pipeline")
        # Insert a step via ORM directly (add_step needs RuntimeStep, complex to mock)
        step = Step(
            trajectory_id=traj.id,
            index=0,
            thought="test",
            action=None,
            observation="Final answer",
            latency_ms=100,
            started_at=datetime.now(timezone.utc),
        )
        real_session.add(step)
        traj.status = "success"
        await real_session.commit()

        # The pipeline must not crash with MissingGreenlet
        result = await _run_compile_pipeline(store, real_session)
        assert result is None or result is not None  # just verify no crash
