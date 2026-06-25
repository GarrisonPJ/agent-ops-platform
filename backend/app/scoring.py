"""Scoring engine — pure function for computing trajectory quality scores.

The ``compute_score()`` function takes a trajectory dict and optional custom
weights, then returns a score between -inf and 1.0 (higher = better).
"""

from __future__ import annotations

from typing import Any


DEFAULT_WEIGHTS: dict[str, float] = {
    "cost": 0.01,
    "latency": 0.01,
    "tool_failure": 0.5,
}


def compute_score(
    trajectory: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute a quality score for a trajectory.

    Parameters
    ----------
    trajectory:
        Must contain ``steps`` (list of dicts, each with ``action``,
        ``observation``, and ``latency_ms`` fields), ``status`` (str),
        ``total_tokens`` (int, optional), and ``total_latency_ms`` (int,
        optional).
    weights:
        Optional dict with keys ``cost``, ``latency``, and ``tool_failure``.
        Defaults to ``{"cost": 0.01, "latency": 0.01, "tool_failure": 0.5}``.

    Returns
    -------
    dict
        ``{"score": float, "breakdown": {...}}``

    Formula
    -------
    ::

        score = success_reward                         # 1.0 if status=="success"
              - (total_tokens / 1000) * cost_weight
              - (total_latency_ms / 1000) * latency_weight
              - (failures / total_calls) * tool_failure_weight
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    steps: list[dict[str, Any]] = trajectory.get("steps", [])
    status: str = trajectory.get("status", "")
    total_tokens: int = trajectory.get("total_tokens", 0) or 0
    total_latency_ms: int = trajectory.get("total_latency_ms", 0) or 0

    # If steps have individual latency_ms, prefer the sum
    if not total_latency_ms and steps:
        total_latency_ms = sum(s.get("latency_ms", 0) or 0 for s in steps)

    # --- success reward -------------------------------------------------------
    success_reward = 1.0 if status == "success" else 0.0

    # --- cost penalty ---------------------------------------------------------
    cost_penalty = (total_tokens / 1000.0) * w["cost"]

    # --- latency penalty ------------------------------------------------------
    latency_penalty = (total_latency_ms / 1000.0) * w["latency"]

    # --- tool failure penalty -------------------------------------------------
    total_calls = sum(
        1 for s in steps if s.get("action") is not None
    )
    if total_calls == 0:
        tool_failure_penalty = 0.0
    else:
        failures = 0
        for s in steps:
            if s.get("action") is not None:
                obs = (s.get("observation") or "").lower()
                obs_has_failure = any(
                    word in obs
                    for word in ("error", "exception", "timeout", "failed")
                )
                # Also check a status field on the step, if present
                step_status = s.get("status", "")
                if obs_has_failure or (step_status and step_status != "success"):
                    failures += 1
        tool_failure_penalty = (failures / total_calls) * w["tool_failure"]

    score = success_reward - cost_penalty - latency_penalty - tool_failure_penalty

    return {
        "score": score,
        "breakdown": {
            "success_reward": success_reward,
            "cost_penalty": -cost_penalty,
            "latency_penalty": -latency_penalty,
            "tool_failure_penalty": -tool_failure_penalty,
        },
    }
