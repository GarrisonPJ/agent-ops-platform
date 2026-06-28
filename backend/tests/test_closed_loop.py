"""Integration tests for the closed-loop policy pipeline.

Tests analyze -> compile -> store flow against a real PostgreSQL database
(identical to the test_e2e_smoke.py pattern).  All tests that exercise the
compile pipeline use trajectories with 3+ failure dimensions so that
``needs_human_review=True`` internally and the auto-replay / orchestrator
branch is skipped (avoiding the need for a live LLM).

Notes on what is *not* asserted on the returned ``Policy``:

- ``PolicyPatch.needs_human_review`` and ``source_trajectories`` are
  **not** persisted to the database, so ``PolicyStore._to_policy()``
  reconstructs them with defaults (``False`` / ``[]``).  The behavioural
  outcome we verify is that the pipeline completes without crashing
  (proving the replay branch was skipped) and the stored status reflects
  correct handling.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Step, Trajectory

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://agentops:agentops@localhost:5433/agentops_test",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_session() -> AsyncSession:
    """Real PostgreSQL session; tables created per test, dropped on teardown."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


async def _create_traj(
    session: AsyncSession,
    *,
    task: str = "test task",
    status: str = "success",
    steps: list[dict] | None = None,
    score: float | None = None,
) -> str:
    """Persist a trajectory with the given steps and return its id."""
    traj_id = str(uuid4())
    traj = Trajectory(
        id=traj_id,
        task=task,
        status=status,
        score=score,
    )
    session.add(traj)
    for sd in steps or ():
        now = datetime.now(timezone.utc)
        step = Step(
            trajectory_id=traj_id,
            index=sd["index"],
            thought=sd.get("thought", ""),
            action=sd.get("action"),
            observation=sd.get("observation", ""),
            latency_ms=sd.get("latency_ms", 100),
            context_window=sd.get("context_window"),
            started_at=now,
        )
        session.add(step)
    await session.commit()
    return traj_id


def _multi_dim_trajectory() -> list[dict]:
    """6-step trajectory that triggers execution + planning + context dimensions.

    - Steps 0-1: observation contains execution error keywords ("Error", "Timeout").
    - Steps 2-4: identical tool/args triple -> planning loop detector.
    - Step 5:   observation contains "truncated" -> context detector.
    """
    return [
        # execution errors
        {
            "index": 0,
            "action": {"name": "search", "arguments": {"q": "x"}},
            "observation": "Error: connection refused",
            "latency_ms": 500,
        },
        {
            "index": 1,
            "action": {"name": "search", "arguments": {"q": "x"}},
            "observation": "Timeout: no response",
            "latency_ms": 500,
        },
        # planning loop — 3x identical action/args (3rd triggers evidence)
        {
            "index": 2,
            "action": {"name": "search", "arguments": {"q": "same"}},
            "observation": "result 0",
            "latency_ms": 100,
        },
        {
            "index": 3,
            "action": {"name": "search", "arguments": {"q": "same"}},
            "observation": "result 1",
            "latency_ms": 100,
        },
        {
            "index": 4,
            "action": {"name": "search", "arguments": {"q": "same"}},
            "observation": "result 2",
            "latency_ms": 100,
        },
        # context pressure via observation keyword
        {
            "index": 5,
            "action": {"name": "read", "arguments": {"url": "x"}},
            "observation": "truncated output",
            "latency_ms": 100,
        },
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClosedLoopPipeline:
    """Integration tests for the closed-loop pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_compile_and_store(
        self,
        real_session: AsyncSession,
    ) -> None:
        """analyze -> compile -> store produces a stored policy."""
        from app.policy_pipeline import _run_compile_pipeline
        from app.policy_store import PolicyStore

        store = PolicyStore(real_session)

        for i in range(2):
            await _create_traj(
                real_session,
                task=f"pipeline-{i}",
                status="failed",
                steps=_multi_dim_trajectory(),
            )

        policy = await _run_compile_pipeline(store, real_session)
        assert policy is not None
        assert policy.version_display == "v1"
        # Pipeline completed without crashing (replay was skipped because
        # multi-dim trajectories produce needs_human_review=True internally).
        assert policy.status == "pending_review"
        assert "system_prompt_suffix" in policy.patch.patch

        # Verify the policy is actually persisted
        fetched = await store.get_policy(policy.version_id)
        assert fetched is not None
        assert fetched.version_display == "v1"

        # Confirm at least one trajectory-policy map row was created
        from app.models import TrajectoryPolicyMap
        from sqlalchemy import select, func

        count = await real_session.scalar(
            select(func.count(TrajectoryPolicyMap.policy_version_id))
            .where(TrajectoryPolicyMap.policy_version_id == policy.version_id)
        )
        assert count == 2, "Expected 2 trajectory-policy map rows"

    @pytest.mark.asyncio
    async def test_cold_start_insufficient_data(
        self,
        real_session: AsyncSession,
    ) -> None:
        """Fewer than 10 trajectories -> run_closed_loop returns None."""
        from app.policy_pipeline import run_closed_loop
        from app.trajectory_repo import TrajectoryRepository

        repo = TrajectoryRepository(real_session)
        for i in range(5):
            await repo.create_trajectory(f"cold-{i}")
        await real_session.commit()

        result = await run_closed_loop(real_session, "dummy")
        assert result is None

    @pytest.mark.asyncio
    async def test_cold_start_triggers_on_threshold(
        self,
        real_session: AsyncSession,
    ) -> None:
        """10+ trajectories with failures -> policy is compiled via run_closed_loop."""
        from app.policy_pipeline import run_closed_loop

        for i in range(10):
            await _create_traj(
                real_session,
                task=f"threshold-{i}",
                status="failed",
                steps=_multi_dim_trajectory(),
            )

        result = await run_closed_loop(real_session, "dummy")
        assert result is not None
        assert result.version_display == "v1"
        assert result.status == "pending_review"

    @pytest.mark.asyncio
    async def test_needs_human_review_skips_replay(
        self,
        real_session: AsyncSession,
    ) -> None:
        """3+ failure dimensions bypasses orchestrator/replay.

        The ``_run_compile_pipeline`` function completes successfully
        without needing a live LLM or ``AgentOrchestrator``, which
        proves the ``needs_human_review=True`` internal path was taken
        (otherwise it would fail trying to initialise an orchestrator).
        """
        from app.policy_pipeline import _run_compile_pipeline
        from app.policy_store import PolicyStore

        store = PolicyStore(real_session)

        for i in range(2):
            await _create_traj(
                real_session,
                task=f"review-{i}",
                status="failed",
                steps=_multi_dim_trajectory(),
            )

        # This would crash if the replay branch were entered, because
        # AgentOrchestrator requires LLM credentials.  The fact that it
        # succeeds proves the needs_human_review=True path was taken.
        policy = await _run_compile_pipeline(store, real_session)
        assert policy is not None
        assert policy.status == "pending_review"

        # Confirm the policy is persisted
        stored = await store.get_policy(policy.version_id)
        assert stored is not None
        assert stored.status == "pending_review"

    @pytest.mark.asyncio
    async def test_render_scoring_view_eager_load(
        self,
        real_session: AsyncSession,
    ) -> None:
        """Regression: eager-loaded steps must not trigger MissingGreenlet."""
        from app.serializer import render_scoring_view
        from app.config import settings
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.models import Trajectory
        from app.trajectory_repo import TrajectoryRepository

        repo = TrajectoryRepository(real_session)
        traj = await repo.create_trajectory("eager-load-regression")
        await real_session.commit()

        stmt = (
            select(Trajectory)
            .options(selectinload(Trajectory.steps))
            .where(Trajectory.id == traj.id)
        )
        result = await real_session.execute(stmt)
        loaded = result.scalar_one()

        # Must not raise MissingGreenlet
        d = render_scoring_view(loaded, settings.llm_max_steps)
        assert d["status"] == "running"
        assert isinstance(d["steps"], list)

    @pytest.mark.asyncio
    async def test_policy_store_crud(
        self,
        real_session: AsyncSession,
    ) -> None:
        """PolicyStore CRUD round-trip: create -> get -> update -> list."""
        from app.policy_store import PolicyStore

        store = PolicyStore(real_session)

        # ---- create ----
        patch = {"system_prompt_suffix": "Be careful."}
        policy = await store.create_policy(
            version_display="v1",
            parent_version=None,
            patch=patch,
            rationale="Test rationale",
            expected_impact={
                "affected_dimensions": ["execution"],
                "estimated_improvement": 0.2,
            },
            confidence="high",
            source_trajectories=[],  # empty list avoids FK constraint
        )
        assert policy.version_display == "v1"
        assert policy.status == "pending_review"
        assert policy.patch.patch == patch
        await real_session.commit()

        # ---- get by id ----
        fetched = await store.get_policy(policy.version_id)
        assert fetched is not None
        assert fetched.version_id == policy.version_id
        assert fetched.patch.patch == patch

        # ---- update status ----
        updated = await store.update_policy_status(
            policy.version_id,
            "active",
            score_delta=0.15,
        )
        assert updated is not None
        assert updated.status == "active"
        assert updated.score_delta == 0.15

        # ---- list ----
        all_policies = await store.list_policies()
        assert len(all_policies) >= 1
        ids = [p.version_id for p in all_policies]
        assert policy.version_id in ids

    @pytest.mark.asyncio
    async def test_pipeline_no_failures_returns_none(
        self,
        real_session: AsyncSession,
    ) -> None:
        """Trajectories without any failures -> pipeline returns None."""
        from app.policy_pipeline import _run_compile_pipeline
        from app.policy_store import PolicyStore

        store = PolicyStore(real_session)

        # Create trajectories with no error keywords, no planning loops,
        # and no context triggers
        for i in range(3):
            await _create_traj(
                real_session,
                task=f"clean-{i}",
                status="success",
                steps=[
                    {
                        "index": 0,
                        "action": {"name": "search", "arguments": {"q": "hello"}},
                        "observation": "Clean result",
                        "latency_ms": 100,
                    },
                ],
            )

        result = await _run_compile_pipeline(store, real_session)
        assert result is None
