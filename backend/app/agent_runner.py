"""Agent runner — orchestrates the full agent lifecycle.

Responsible for: run loop, persist, score, finalize.

The repository layer stays pure CRUD — all orchestration logic lives here.
"""

from __future__ import annotations

from logging import getLogger

from app.config import settings
from app.serializer import render_step

logger = getLogger(__name__)

# ── Cancellation support ──────────────────────────────────────────────────────
_cancelled_trajectories: set[str] = set()


def cancel_trajectory(trajectory_id: str) -> bool:
    """Mark a trajectory for cancellation.

    Returns True if it was newly cancelled, False if already cancelled.
    """
    if trajectory_id in _cancelled_trajectories:
        return False
    _cancelled_trajectories.add(trajectory_id)
    return True


def is_cancelled(trajectory_id: str) -> bool:
    """Check if a trajectory has been cancelled."""
    return trajectory_id in _cancelled_trajectories


async def _score_trajectory(repo, trajectory_id: str) -> None:
    """Compute and persist an automatic quality score for a trajectory."""
    from app.scoring import compute_score

    trajectory = await repo.get_trajectory(trajectory_id)
    if trajectory is None:
        return

    steps_dict = [render_step(s, view="scoring") for s in trajectory.steps]
    traj_dict = {
        "steps": steps_dict,
        "status": trajectory.status,
        "total_tokens": trajectory.total_tokens or 0,
        "total_latency_ms": sum(s.latency_ms for s in trajectory.steps),
    }
    result = compute_score(traj_dict)
    await repo.set_score(trajectory_id, result["score"], result["breakdown"])


async def _execute_agent(
    task: str,
    tool_schemas: list,
    llm,
    context_manager,
    runtime,
    trajectory_id: str,
    *,
    publish_sse: bool = False,
    policy: dict | None = None,
) -> None:
    """Shared agent execution lifecycle.

    Iterates ``runtime.run()``, persists steps, checks for failure conditions,
    scores the trajectory, and commits (or rolls back on error).

    Args:
        publish_sse: If True, publish per-step and terminal events to SSE
                     subscribers; exception is logged but not re-raised.
                     If False, exception is re-raised (caller handles it).
    """
    from app.database import async_session
    from app.trajectory_repo import TrajectoryRepository

    if publish_sse:
        from app.event_bus import event_bus

    async with async_session() as session:
        repo = TrajectoryRepository(session)
        final_status = "success"

        # ── Policy injection ─────────────────────────────────────────
        if policy:
            patch = policy.get("patch", {})
            runtime._policy_patch = patch
            runtime._context_strategy = patch.get("context_strategy")
            runtime._tool_priority_bias = patch.get("tool_priority_bias")

        try:
            async for step in runtime.run(
                task=task,
                tools=tool_schemas,
                llm=llm,
                context_manager=context_manager,
                max_steps=settings.llm_max_steps,
                max_tokens=settings.llm_max_tokens,
            ):
                # Check for cancellation before persisting the step
                if is_cancelled(trajectory_id):
                    logger.info("Trajectory %s cancelled by user", trajectory_id)
                    final_status = "failed"
                    break

                await repo.add_step(trajectory_id, step)

                if publish_sse:
                    step_dict = render_step(step, view="full")
                    await event_bus.publish(trajectory_id, step_dict)

                if step.action is None and step.observation and step.observation.startswith("Max steps exceeded"):
                    final_status = "failed"

                if step.action is None and step.observation and step.observation.startswith("[LLM"):
                    final_status = "failed"

            await repo.update_trajectory_status(trajectory_id, final_status)
            await _score_trajectory(repo, trajectory_id)
            await session.commit()

            if publish_sse:
                await event_bus.publish(
                    trajectory_id, {"type": "done", "trajectory_id": trajectory_id}
                )
        except Exception as exc:
            await session.rollback()
            if publish_sse:
                logger.exception("Background agent execution failed")
                await event_bus.publish(
                    trajectory_id, {"type": "error", "message": str(exc)}
                )
            else:
                raise


async def run_agent_background(
    task: str,
    tool_schemas: list,
    llm,
    context_manager,
    runtime,
    trajectory_id: str,
    *,
    policy: dict | None = None,
) -> None:
    """Run the agent loop as a background task.

    Persists each step to the database, publishes events to SSE subscribers,
    updates the final status, computes an automatic score, and publishes a
    terminal event (``done`` / ``error``).

    If a *policy* is provided, it is injected into the runtime for
    prompt-suffix, context-strategy, and tool-priority-bias modifications.
    After the agent completes, the closed-loop pipeline is triggered.
    """
    await _execute_agent(
        task=task,
        tool_schemas=tool_schemas,
        llm=llm,
        context_manager=context_manager,
        runtime=runtime,
        trajectory_id=trajectory_id,
        publish_sse=True,
        policy=policy,
    )

    # ── Trigger closed-loop pipeline after agent finishes ────────────
    try:
        await _maybe_trigger_closed_loop()
    except Exception:
        logger.exception("Closed-loop trigger failed (non-fatal)")


async def _maybe_trigger_closed_loop() -> None:
    """Check conditions and trigger the closed-loop pipeline.

    Conditions for automatic compilation:
    1. No active policy exists (cold-start) AND ≥10 trajectories exist, OR
    2. ≥10 new trajectories since last compilation, OR
    3. ≥30 minutes since last compilation.

    When conditions are met, runs: analyze → compile → store → replay.
    """
    from app.database import async_session
    from app.trajectory_repo import TrajectoryRepository
    from app.policy_store import PolicyStore

    async with async_session() as session:
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
                return

            logger.info("Cold-start threshold met — triggering auto-compile")
            await _run_compile_pipeline(repo, store, session)
            return

        # ── Periodic check: ≥10 new trajectories since last compile ──
        if policies:
            # Count trajectories created after the most recent policy
            from app.models import Trajectory
            from sqlalchemy import select, func

            latest_policy = policies[0]
            latest_ts = latest_policy.get("created_at")
            if latest_ts:
                stmt = select(func.count(Trajectory.id)).where(
                    Trajectory.created_at > latest_ts
                )
                result = await session.execute(stmt)
                new_count = result.scalar() or 0
                if new_count >= 10:
                    logger.info(
                        "%d new trajectories since last policy — triggering auto-compile",
                        new_count,
                    )
                    await _run_compile_pipeline(repo, store, session)
                    return

        logger.debug("Closed-loop conditions not met — skipping")


async def _run_compile_pipeline(
    repo: TrajectoryRepository,
    store: PolicyStore,
    session,
) -> None:
    """Run the full compile pipeline: analyze → compile → store.

    Iterates over all trajectories, analyzes them, and compiles a policy
    from the aggregate failure report.
    """
    from app.failure_analyzer import analyze_trajectory, FailureReport
    from app.serializer import render_step

    # Get all trajectories
    from app.models import Trajectory
    from sqlalchemy import select

    stmt = select(Trajectory).order_by(Trajectory.created_at.desc()).limit(50)
    result = await session.execute(stmt)
    trajectories = result.scalars().all()

    if not trajectories:
        return

    # Build trajectory dicts and analyze each
    all_evidence: list = []
    dim_totals: dict[str, float] = {}
    traj_ids: list[str] = []

    for traj in trajectories:
        steps_dict = [render_step(s, view="scoring") for s in traj.steps]
        traj_dict = {
            "steps": steps_dict,
            "status": traj.status,
            "total_tokens": traj.total_tokens or 0,
            "total_latency_ms": sum(s.latency_ms for s in traj.steps),
        }
        report = analyze_trajectory(traj_dict)

        for dim, rate in report.dimensions.items():
            dim_totals[dim] = dim_totals.get(dim, 0.0) + rate

        all_evidence.extend(report.evidence)
        traj_ids.append(traj.id)

    if not dim_totals:
        logger.info("No failures found in recent trajectories — skipping compile")
        return

    # Create aggregate failure report
    n = len(trajectories)
    agg_dims = {d: v / n for d, v in dim_totals.items()}
    agg_report = FailureReport(
        dimensions=agg_dims,
        evidence=all_evidence,
    )

    # Compile policy
    from app.policy_compiler import compile_policy

    patch = compile_policy(agg_report, traj_ids)
    if patch is None:
        logger.info("Policy compile returned None — no policy needed")
        return

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

    # needs_human_review → skip auto-replay, stay as pending_review
    if patch.needs_human_review:
        logger.info(
            "Policy %s needs human review — skipping auto-replay",
            version_display,
        )
        await session.commit()
        return

    # Build orchestrator for replay
    orchestrator = AgentOrchestrator(settings)

    # Collect original scores
    original_scores: dict[str, float] = {}
    for traj in trajectories:
        if traj.id in patch.source_trajectories and traj.score is not None:
            original_scores[traj.id] = traj.score

    # All policies go through replay verification (no shortcut for high confidence)
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


async def run_benchmark_task(task: str) -> str:
    """Run one agent for a benchmark, return the ``trajectory_id`` on completion.

    This function is **awaited** and returns only after the agent loop finishes.
    It creates its own database session so that multiple calls can be
    ``asyncio.gather``-ed concurrently.
    """
    from app.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(settings)
    return await orchestrator.run_benchmark(task)
