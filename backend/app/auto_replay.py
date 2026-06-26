"""Auto-replay — re-run failed trajectories under a new policy to measure impact.

Provides:
- ``replay_trajectories()`` — replay failed trajectories with a new policy.
- ``evaluate_policy_effectiveness()`` — compare scores before/after.
- ``trigger_auto_replay()`` — full orchestration of replay → evaluate → activate/rollback.
"""

from __future__ import annotations

import asyncio
from logging import getLogger

from app.config import settings
from app.policy_compiler import ROLLBACK_ACTIVATE, ROLLBACK_REVERT, Policy

logger = getLogger(__name__)

# Replay concurrency limit (separate pool from benchmark's pool of 3)
_REPLAY_SEMAPHORE = asyncio.Semaphore(2)


async def replay_trajectories(
    orchestrator,
    policy: Policy,
    trajectory_ids: list[str],
) -> list[dict]:
    """Re-run failed trajectories under a given policy.

    Each trajectory is re-run with a new UUID and scored.  The original
    trajectory ID is preserved in the result for before/after comparison.

    Returns a list of ``{original_id, new_id, score}`` dicts.
    """
    results: list[dict] = []

    async def _replay_one(original_id: str) -> dict | None:
        from app.database import async_session
        from app.trajectory_repo import TrajectoryRepository

        async with _REPLAY_SEMAPHORE:
            try:
                async with async_session() as session:
                    repo = TrajectoryRepository(session)

                    # Get the original trajectory for its task
                    original = await repo.get_trajectory(original_id)
                    if original is None:
                        logger.warning("Original trajectory %s not found", original_id)
                        return None

                    # Create a new trajectory for replay
                    new_traj = await repo.create_trajectory(original.task)
                    await session.commit()

                    # Run with the new policy
                    await orchestrator.run_agent_with_policy(
                        task=original.task,
                        policy=policy,
                        trajectory_id=new_traj.id,
                    )

                    # Get the score
                    replayed = await repo.get_trajectory(new_traj.id)
                    new_score = replayed.score if replayed else None

                    return {
                        "original_id": original_id,
                        "new_id": new_traj.id,
                        "score": new_score,
                    }
            except Exception as exc:
                logger.exception("Replay failed for %s", original_id)
                return None

    tasks = [_replay_one(tid) for tid in trajectory_ids]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        if result is not None:
            results.append(result)

    return results


async def evaluate_policy_effectiveness(
    results: list[dict],
    original_scores: dict[str, float],
) -> float | None:
    """Compare replay scores against original scores.

    Parameters
    ----------
    results:
        List from ``replay_trajectories()``: ``{original_id, new_id, score}``.
    original_scores:
        Map of ``trajectory_id → score`` for the original runs.

    Returns
    -------
    float or None
        Average score delta (new - original), or None if no comparable results.
    """
    deltas: list[float] = []

    for r in results:
        orig_score = original_scores.get(r["original_id"])
        new_score = r.get("score")
        if orig_score is not None and new_score is not None:
            deltas.append(new_score - orig_score)

    if not deltas:
        return None

    avg_delta = sum(deltas) / len(deltas)
    return avg_delta


async def trigger_auto_replay(
    orchestrator,
    policy: Policy,
    trajectory_ids: list[str],
    original_scores: dict[str, float],
    store,
    session,
) -> None:
    """Full closed-loop: replay → evaluate → activate/rollback.

    Parameters
    ----------
    orchestrator:
        The ``AgentOrchestrator`` instance.
    policy:
        The ``Policy`` object to evaluate.
    trajectory_ids:
        Trajectory IDs to replay.
    original_scores:
        Map of ``trajectory_id → score`` for pre-policy scores.
    store:
        ``PolicyStore`` instance.
    session:
        DB session.
    """
    # ── Replay ──────────────────────────────────────────────────────
    logger.info(
        "Starting auto-replay for policy %s on %d trajectories",
        policy.version_display or "?",
        len(trajectory_ids),
    )

    results = await replay_trajectories(orchestrator, policy, trajectory_ids)
    if not results:
        logger.warning("No replay results — skipping evaluation")
        return

    # ── Evaluate ────────────────────────────────────────────────────
    avg_delta = await evaluate_policy_effectiveness(results, original_scores)

    if avg_delta is None:
        logger.info("No comparable scores — marking policy as pending_review")
        await store.update_policy_status(policy.version_id, "pending_review")
        await session.commit()
        return

    logger.info("Policy %s score delta: %+.4f", policy.version_display, avg_delta)

    # ── Activate or rollback ─────────────────────────────────────────
    if avg_delta >= ROLLBACK_ACTIVATE:
        logger.info("Policy %s activated (Δ ≥ %.0f%%)", policy.version_display, ROLLBACK_ACTIVATE * 100)
        await store.deactivate_active_policy()
        await store.update_policy_status(
            policy.version_id, "active", score_delta=avg_delta,
        )
    elif avg_delta <= ROLLBACK_REVERT:
        logger.info("Policy %s reverted (Δ ≤ %.0f%%)", policy.version_display, ROLLBACK_REVERT * 100)
        await store.update_policy_status(
            policy.version_id, "reverted", score_delta=avg_delta,
        )
    else:
        logger.info("Policy %s set to pending_review (Δ in middle range)", policy.version_display)
        await store.update_policy_status(
            policy.version_id, "pending_review", score_delta=avg_delta,
        )

    await session.commit()

    logger.info(
        "Auto-replay complete for policy %s. Average Δ = %+.4f",
        policy.version_display, avg_delta,
    )
