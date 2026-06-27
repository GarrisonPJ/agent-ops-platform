"""Single source of truth for API response shapes.

Usage::

    from app.serializer import render_step, render_trajectory

    # SSE streaming events
    stream_dict = render_step(runtime_step, view="full")

    # Scoring / failure analysis input
    scoring_dict = render_scoring_view(orm_trajectory, max_steps=15)

    # Compare endpoint
    compare_dict = render_step(orm_step, view="compare")

    # Trace detail
    detail_dict = render_trajectory(orm_trajectory)
"""

from __future__ import annotations


# ── Private helpers ──────────────────────────────────────────────────────────


def _action_to_dict(action) -> dict | None:
    """Convert a ``ToolCall`` to a plain dict, or ``None``.

    If *action* is already a ``dict`` (e.g. read from JSONB), return it as-is.
    """
    if action is None:
        return None
    if isinstance(action, dict):
        return action
    return {
        "id": action.id,
        "name": action.name,
        "arguments": action.arguments,
    }


# ── Public API ───────────────────────────────────────────────────────────────


def render_step(step, *, view: str = "full") -> dict:
    """Convert a step (runtime ``Step`` or ORM ``Step``) to a JSON-serialisable dict.

    Parameters
    ----------
    view:
        ``"full"`` (default) — SSE streaming shape, includes context_window and
        token fields. Expects a runtime ``Step`` with an ``action`` ToolCall.
        ``"scoring"`` — minimal fields for ``compute_score``. Expects an ORM
        ``Step`` where ``action`` is already a plain dict.
        ``"compare"`` — compact shape for the compare endpoint. Expects an ORM
        ``Step``.
    """
    base = {
        "index": step.index,
        "thought": step.thought,
        "observation": step.observation,
        "latency_ms": step.latency_ms,
        "container_id": getattr(step, "container_id", None),
        "started_at": getattr(step, "started_at", None),
    }

    if view == "scoring":
        base["action"] = step.action  # already a dict (JSONB)
        return base

    # "full" — action is a ToolCall object, convert it
    base["action"] = _action_to_dict(step.action) if hasattr(step, "action") else step.action

    if view == "compare":
        return base

    # "full" (SSE) — add context window + token fields
    cw = getattr(step, "context_window", None)
    if cw:
        base["context_window"] = {
            "used": cw.used if hasattr(cw, "used") else cw["used"],
            "limit": cw.limit if hasattr(cw, "limit") else cw["limit"],
        }
    else:
        base["context_window"] = None
    base["token_prompt"] = getattr(step, "token_prompt", None)
    base["token_completion"] = getattr(step, "token_completion", None)
    return base


def render_scoring_view(trajectory, max_steps: int | None = None) -> dict:
    """Convert an ORM ``Trajectory`` instance to a scoring/analysis dict.

    If *max_steps* is not provided, ``settings.llm_max_steps`` is used.
    Callers with an active policy should pass the effective max_steps
    (after override) so the analysis layer uses the correct budget baseline.
    """
    effective_max = max_steps if max_steps is not None else settings.llm_max_steps
    return {
        "steps": [render_step(s, view="scoring") for s in trajectory.steps],
        "status": trajectory.status,
        "total_tokens": trajectory.total_tokens or 0,
        "total_latency_ms": sum(s.latency_ms for s in trajectory.steps),
        "max_steps": effective_max,
    }


def render_trajectory(trajectory) -> dict:
    """Convert an ORM ``Trajectory`` instance to the detail-dict format.

    Matches the structure returned by ``GET /api/traces/{id}`` and used by
    the export endpoints.
    """
    return {
        "id": trajectory.id,
        "task": trajectory.task,
        "status": trajectory.status,
        "created_at": trajectory.created_at,
        "total_tokens": trajectory.total_tokens,
        "context_window_peak": trajectory.context_window_peak,
        "score": trajectory.score,
        "score_breakdown": trajectory.score_breakdown,
        "steps": [render_step(s, view="full") for s in trajectory.steps],
    }
