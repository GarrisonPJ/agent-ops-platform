"""Trajectory persistence layer.

Provides CRUD operations for ``trajectories`` and ``steps`` tables via
SQLAlchemy async sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Step as StepModel
from app.models import Trajectory
from app.runtime import Step as RuntimeStep


class TrajectoryRepository:
    """Repository for trajectory and step persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_trajectory(self, task: str) -> Trajectory:
        """Create a new trajectory with ``status="running"`` and a fresh UUID."""
        import uuid

        trajectory = Trajectory(
            id=str(uuid.uuid4()),
            task=task,
            status="running",
        )
        self.session.add(trajectory)
        return trajectory

    async def add_step(self, trajectory_id: str, step: RuntimeStep) -> None:
        """Insert a step row linked to the given trajectory."""
        db_step = StepModel(
            trajectory_id=trajectory_id,
            index=step.index,
            thought=step.thought,
            action={"id": step.action.id, "name": step.action.name, "arguments": step.action.arguments}
            if step.action else None,
            observation=step.observation,
            latency_ms=step.latency_ms,
            started_at=datetime.fromtimestamp(step.started_at, tz=timezone.utc),
            context_window={
                "used": step.context_window.used,
                "limit": step.context_window.limit,
            },
            container_id=getattr(step, "container_id", None),
            token_prompt=getattr(step, "token_prompt", None),
            token_completion=getattr(step, "token_completion", None),
        )
        self.session.add(db_step)

    async def update_trajectory_status(
        self, trajectory_id: str, status: str
    ) -> None:
        """Set the final status and compute token aggregates.

        After all steps have been added, this flushes the session, computes
        ``total_tokens`` (sum of prompt + completion across all steps) and
        ``context_window_peak`` (highest ``context_window.used``), persists
        them on the trajectory row, and computes an automatic quality score.

        No-op if the trajectory does not exist.
        """
        trajectory = await self.session.get(Trajectory, trajectory_id)
        if trajectory is not None:
            trajectory.status = status

            # Flush so that newly added steps are visible to aggregation queries
            await self.session.flush()

            agg_q = select(
                func.coalesce(func.sum(StepModel.token_prompt), 0),
                func.coalesce(func.sum(StepModel.token_completion), 0),
                func.max(
                    cast(StepModel.context_window["used"].astext, Integer)
                ),
            ).where(StepModel.trajectory_id == trajectory_id)
            agg_result = await self.session.execute(agg_q)
            sum_prompt, sum_completion, peak = agg_result.one()

            total = sum_prompt + sum_completion
            trajectory.total_tokens = total if total > 0 else None
            trajectory.context_window_peak = peak

            # (Scoring is now handled by the agent_runner after status update)

    async def set_score(
        self,
        trajectory_id: str,
        score: float,
        breakdown: dict | None = None,
    ) -> None:
        """Persist a score (and optional breakdown) on a trajectory row."""
        trajectory = await self.session.get(Trajectory, trajectory_id)
        if trajectory is not None:
            trajectory.score = score
            trajectory.score_breakdown = breakdown

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_trajectories(
        self,
        status: str | None = None,
        tool: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Trajectory], int, dict[str, int]]:
        """Return (trajectories, total_count, step_counts).

        *trajectories* — list of ``Trajectory`` objects (without eagerly loaded
        steps), sorted by ``created_at DESC`` and paginated.

        *total_count* — number of matching rows *before* pagination.

        *step_counts* — mapping of ``{trajectory_id: step_count}`` for every
        trajectory in the returned page.
        """
        # -- build filtered query -------------------------------------------
        query = select(Trajectory).options(selectinload(Trajectory.steps))

        if status is not None:
            query = query.where(Trajectory.status == status)

        if tool is not None:
            query = (
                query.join(StepModel)
                .where(StepModel.action.op("->>")("name") == tool)
            )

        # -- total (before pagination) --------------------------------------
        # Use a distinct subquery when a JOIN is present to avoid counting
        # duplicate rows caused by multiple matching steps.
        count_sub = query.with_only_columns(Trajectory.id)
        if tool is not None:
            count_sub = count_sub.distinct()
        total = await self.session.scalar(
            select(func.count()).select_from(count_sub.subquery())
        )
        total = total or 0

        # -- paginated results ----------------------------------------------
        if tool is not None:
            query = query.distinct()
        query = query.order_by(Trajectory.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        trajectories = list(result.scalars().all())

        # -- step counts for the returned page ------------------------------
        step_counts: dict[str, int] = {}
        if trajectories:
            ids = [t.id for t in trajectories]
            sc_q = (
                select(StepModel.trajectory_id, func.count(StepModel.id))
                .where(StepModel.trajectory_id.in_(ids))
                .group_by(StepModel.trajectory_id)
            )
            sc_result = await self.session.execute(sc_q)
            step_counts = dict(sc_result.all())

        return trajectories, total, step_counts

    async def get_trajectory(
        self, trajectory_id: str
    ) -> Trajectory | None:
        """Return the trajectory with all its steps eagerly loaded.

        Steps are ordered by ``index ASC`` (sorted in Python after loading).
        Returns ``None`` when not found.
        """
        query = (
            select(Trajectory)
            .where(Trajectory.id == trajectory_id)
            .options(selectinload(Trajectory.steps))
        )
        result = await self.session.execute(query)
        trajectory = result.scalar_one_or_none()
        if trajectory is not None:
            trajectory.steps.sort(key=lambda s: s.index)
        return trajectory

    async def get_trajectories_by_task(
        self, task_name: str
    ) -> list[Trajectory]:
        """Return all trajectories matching the exact task name.

        Steps are eagerly loaded and sorted by index.  Results are ordered by
        score descending (nulls last), then by created_at descending.
        """
        query = (
            select(Trajectory)
            .where(Trajectory.task == task_name)
            .options(selectinload(Trajectory.steps))
            .order_by(Trajectory.score.desc().nullslast(), Trajectory.created_at.desc())
        )
        result = await self.session.execute(query)
        trajectories = list(result.scalars().all())
        for t in trajectories:
            t.steps.sort(key=lambda s: s.index)
        return trajectories
