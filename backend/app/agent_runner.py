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
) -> None:
    """Run the agent loop as a background task.

    Persists each step to the database, publishes events to SSE subscribers,
    updates the final status, computes an automatic score, and publishes a
    terminal event (``done`` / ``error``).
    """
    await _execute_agent(
        task=task,
        tool_schemas=tool_schemas,
        llm=llm,
        context_manager=context_manager,
        runtime=runtime,
        trajectory_id=trajectory_id,
        publish_sse=True,
    )


async def run_benchmark_task(task: str) -> str:
    """Run one agent for a benchmark, return the ``trajectory_id`` on completion.

    This function is **awaited** and returns only after the agent loop finishes.
    It creates its own database session so that multiple calls can be
    ``asyncio.gather``-ed concurrently.
    """
    from app.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(settings)
    return await orchestrator.run_benchmark(task)

    return trajectory.id
