"""Policy pipeline — closed-loop policy compilation and auto-replay.

Extracted from ``agent_runner`` to break the ``orchestrator ↔ agent_runner``
circular dependency.  Exposes a single entry point: ``run_closed_loop``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from logging import getLogger
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.config import settings
from app.failure_analyzer import FailureReport, analyze_trajectory
from app.policy_compiler import compile_policy
from app.policy_store import PolicyStore
from app.serializer import render_scoring_view, render_step
from app.trajectory_repo import TrajectoryRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.policy_compiler import Policy

logger = getLogger(__name__)


async def run_closed_loop(
    session: "AsyncSession",
    source_trajectory_id: str,
) -> Policy | None:
    """Check conditions and trigger the closed-loop pipeline.

    Conditions for automatic compilation:
    1. No active policy exists (cold-start) AND >=10 trajectories exist, OR
    2. >=10 new trajectories since last compilation, OR
    3. 30 minutes elapsed since last compilation.

    Returns the compiled policy if one was created, or *None*.
    """
    repo = TrajectoryRepository(session)
    store = PolicyStore(session)

    active = await store.get_active_policy()
    warmup = await store.get_warmup_status()
    policies = await store.list_policies()

    # ── Cold-start check ─────────────────────────────────────────
    if active is None:
        if not warmup["ready"]:
            logger.info(
                "Cold-start warmup in progress: %d/%d trajectories",
                warmup["total_trajectories"],
                warmup["threshold"],
            )
            return None

        logger.info("Cold-start threshold met — triggering auto-compile")
        return await _run_compile_pipeline(store, session)

    # ── Periodic checks: new trajectories or time elapsed since last compile ──
    if policies:
        from app.models import Trajectory

        latest_policy = policies[0]
        latest_ts = latest_policy.created_at
        if latest_ts:
            latest_dt = datetime.fromisoformat(latest_ts)

            # Check 1: >=10 new trajectories since last compile
            stmt = select(func.count(Trajectory.id)).where(
                Trajectory.created_at > latest_dt
            )
            result = await session.execute(stmt)
            new_count = result.scalar() or 0
            if new_count >= 10:
                logger.info(
                    "%d new trajectories since last policy — triggering auto-compile",
                    new_count,
                )
                return await _run_compile_pipeline(store, session)

            # Check 2: 30 minutes elapsed since last compile
            elapsed = datetime.now(timezone.utc) - latest_dt
            if elapsed.total_seconds() >= 1800:
                logger.info(
                    "30 minutes since last policy — triggering auto-compile"
                )
                return await _run_compile_pipeline(store, session)

    logger.debug("Closed-loop conditions not met — skipping")
    return None


async def _run_compile_pipeline(
    store: PolicyStore,
    session,
) -> Policy | None:
    """Run the full compile pipeline: analyze -> compile -> store -> replay.

    Returns the compiled policy dict if one was created, or *None*.
    """
    # Get all trajectories
    from app.models import Trajectory

    stmt = select(Trajectory).order_by(Trajectory.created_at.desc()).limit(50)
    result = await session.execute(stmt)
    trajectories = result.scalars().all()

    if not trajectories:
        return None

    # Build trajectory dicts and analyze each
    all_evidence: list = []
    dim_totals: dict[str, float] = {}
    traj_ids: list[str] = []

    for traj in trajectories:
        traj_dict = render_scoring_view(traj, settings.llm_max_steps)
        report = analyze_trajectory(traj_dict)

        for dim, rate in report.dimensions.items():
            dim_totals[dim] = dim_totals.get(dim, 0.0) + rate

        all_evidence.extend(report.evidence)
        traj_ids.append(traj.id)

    if not dim_totals:
        logger.info("No failures found in recent trajectories — skipping compile")
        return None

    # Create aggregate failure report
    n = len(trajectories)
    agg_dims = {d: v / n for d, v in dim_totals.items()}
    agg_report = FailureReport(
        dimensions=agg_dims,
        evidence=all_evidence,
    )

    # Compile policy
    patch = compile_policy(agg_report, traj_ids)
    if patch is None:
        logger.info("Policy compile returned None — no policy needed")
        return None

    # Store
    from app.auto_replay import trigger_auto_replay
    from app.orchestrator import AgentOrchestrator

    version_display = await store.next_version_display()
    policy = await store.create_policy(
        version_display=version_display,
        parent_version=None,
        patch=patch.patch,
        rationale=patch.rationale,
        expected_impact=patch.expected_impact,
        confidence=patch.confidence,
        source_trajectories=patch.source_trajectories,
    )

    # needs_human_review -> skip auto-replay, stay as pending_review
    if patch.needs_human_review:
        logger.info(
            "Policy %s needs human review — skipping auto-replay",
            version_display,
        )
        await session.commit()
        return policy

    # Build orchestrator for replay
    orchestrator = AgentOrchestrator(settings)

    # Collect original scores
    original_scores: dict[str, float] = {}
    for traj in trajectories:
        if traj.id in patch.source_trajectories and traj.score is not None:
            original_scores[traj.id] = traj.score

    # All policies go through replay verification
    logger.info(
        "Starting auto-replay for policy %s with %d source trajectories",
        version_display,
        len(patch.source_trajectories),
    )
    await trigger_auto_replay(
        orchestrator=orchestrator,
        policy=policy,
        trajectory_ids=patch.source_trajectories,
        original_scores=original_scores,
        store=store,
        session=session,
    )
    logger.info(
        "Auto-replay completed for policy %s",
        version_display,
    )

    await session.commit()
    return policy
