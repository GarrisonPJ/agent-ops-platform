"""Agent runner — orchestrates the full agent lifecycle.

Responsible for: run loop, persist, score, finalize.

The repository layer stays pure CRUD — all orchestration logic lives here.
"""

from __future__ import annotations

from logging import getLogger

from app.config import settings
from app.policy_compiler import Policy
from app.serializer import render_scoring_view, render_step

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

    traj_dict = render_scoring_view(trajectory, settings.llm_max_steps)
    result = compute_score(traj_dict)
    await repo.set_score(trajectory_id, result["score"], result["breakdown"])


async def execute_agent(
    task: str,
    tool_schemas: list,
    llm,
    context_manager,
    runtime,
    trajectory_id: str,
    *,
    publish_sse: bool = False,
    policy: Policy | None = None,
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
            runtime.apply_policy(policy.patch)
        else:
            runtime.reset_policy()

        # ── Long-term memory: retrieve similar past trajectories ────
        enriched_task = task
        try:
            from app.memory import Embedder, MemoryRetriever

            async with Embedder(
                settings.llm_base_url,
                settings.llm_api_key,
                settings.llm_embedding_model,
            ) as embedder:
                task_embedding = await embedder.embed(task)
                if task_embedding:
                    retriever = MemoryRetriever(session)
                    similar = await retriever.query(task_embedding)
                    if similar:
                        prefix = "Relevant past sessions:\n"
                        prefix += "\n".join(f"- {s}" for s in similar)
                        enriched_task = f"{prefix}\n\nCurrent task: {task}"
        except Exception:
            logger.exception("Memory retrieval failed (non-fatal)")
            enriched_task = task

        try:
            async for step in runtime.run(
                task=enriched_task,
                tools=tool_schemas,
                llm=llm,
                context_manager=context_manager,
                max_steps=settings.llm_max_steps,
                max_tokens=settings.llm_max_tokens,
                event_bus=event_bus if publish_sse else None,
                trajectory_id=trajectory_id,
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

            # ── Long-term memory: store trajectory embedding ─────────
            if final_status == "success":
                try:
                    from app.memory import Embedder, store_trajectory_memory

                    # Before embedding for storage, get original task from DB
                    trajectory_record = await repo.get_trajectory(trajectory_id)
                    summary = trajectory_record.task if trajectory_record else task

                    async with Embedder(
                        settings.llm_base_url,
                        settings.llm_api_key,
                        settings.llm_embedding_model,
                    ) as embedder:
                        embedding = await embedder.embed(summary)
                        if embedding:
                            await store_trajectory_memory(
                                session, trajectory_id, embedding, summary
                            )
                            await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to store trajectory memory (non-fatal)"
                    )

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
    policy: Policy | None = None,
) -> None:
    """Run the agent loop as a background task.

    Persists each step to the database, publishes events to SSE subscribers,
    updates the final status, computes an automatic score, and publishes a
    terminal event (``done`` / ``error``).

    If a *policy* is provided, it is injected into the runtime for
    prompt-suffix, context-strategy, and tool-priority-bias modifications.
    After the agent completes, the closed-loop pipeline is triggered.
    """
    await execute_agent(
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
        from app.database import async_session
        from app.policy_pipeline import run_closed_loop

        async with async_session() as session:
            result = await run_closed_loop(session, trajectory_id)
            if result:
                logger.info(
                    "Closed-loop pipeline created policy %s",
                    result.version_display,
                )
    except Exception:
        logger.exception("Closed-loop trigger failed (non-fatal)")


async def run_benchmark_task(task: str) -> str:
    """Run one agent for a benchmark, return the ``trajectory_id`` on completion.

    This function is **awaited** and returns only after the agent loop finishes.
    It creates its own database session so that multiple calls can be
    ``asyncio.gather``-ed concurrently.
    """
    from app.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(settings)
    return await orchestrator.run_benchmark(task)
