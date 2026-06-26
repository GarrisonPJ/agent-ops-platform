"""Failure visibility — root cause analysis for agent trajectories.

Provides pure functions for detecting failures across four dimensions:
planning, execution, context, and budget.  The main entry point is
``analyze_trajectory()`` which returns a ``FailureReport``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# ── Data structures ────────────────────────────────────────────────────────────────


@dataclass
class FailureEvidence:
    """A single piece of evidence for a failure in a specific dimension."""

    dimension: str  # planning / execution / context / budget
    step_index: int  # 0-based step index
    reason: str  # human-readable reason
    severity: float  # 0.0 - 1.0
    details: dict | None = None  # extra data (e.g. keyword matched, ratio)


@dataclass
class FailureReport:
    """Aggregated failure analysis result for a single trajectory."""

    dimensions: dict[str, float] = field(default_factory=dict)
    dominant: str | None = None
    evidence: list[FailureEvidence] = field(default_factory=list)
    needs_human_review: bool = False


# ── Error-keyword sets (case-insensitive) ──────────────────────────────────────

_ERROR_KEYWORDS = ("error", "exception", "timeout", "failed", "traceback")
_TIMEOUT_LATENCY_MS = 60_000
_CONTEXT_KEYWORDS = ("truncated", "context")


# ── Detection functions ──────────────────────────────────────────────────────────────


def _detect_planning(steps: list[dict]) -> list[FailureEvidence]:
    """Detect circular reasoning — same tool + same args for 3+ consecutive calls.

    Returns one evidence per *offending run* (the 3rd and subsequent steps
    in the streak).
    """
    evidence: list[FailureEvidence] = []

    streak_name: str | None = None
    streak_args: dict | None = None
    streak_length = 0

    for step in steps:
        action = step.get("action")
        if action is None:
            # Reset streak on non-tool-call steps
            streak_name = None
            streak_args = None
            streak_length = 0
            continue

        name = action.get("name")
        args = action.get("arguments")

        if name == streak_name and args == streak_args:
            streak_length += 1
            if streak_length >= 3:
                evidence.append(
                    FailureEvidence(
                        dimension="planning",
                        step_index=step.get("index", 0),
                        reason=(
                            f"Circular reasoning detected: tool '{name}' "
                            f"called {streak_length} times with identical arguments"
                        ),
                        severity=1.0,
                        details={"tool": name, "repetitions": streak_length},
                    )
                )
        else:
            streak_name = name
            streak_args = args
            streak_length = 1

    return evidence


def _detect_execution(steps: list[dict]) -> list[FailureEvidence]:
    """Detect execution errors — failure keywords in observations or timeouts.

    Checks each step's observation for error/exception/timeout/failed/traceback
    keywords (case-insensitive), and checks if ``latency_ms > 60000``.
    """
    evidence: list[FailureEvidence] = []

    for step in steps:
        obs = (step.get("observation") or "").lower()
        step_index = step.get("index", 0)
        latency = step.get("latency_ms", 0) or 0

        matched_keywords = [kw for kw in _ERROR_KEYWORDS if kw in obs]
        is_timeout = latency > _TIMEOUT_LATENCY_MS

        if matched_keywords or is_timeout:
            reasons: list[str] = []
            details: dict[str, Any] = {}

            if matched_keywords:
                reasons.append(f"Observation contains error keywords: {', '.join(matched_keywords)}")
                details["keywords"] = matched_keywords
            if is_timeout:
                reasons.append(f"Step latency {latency}ms exceeds {_TIMEOUT_LATENCY_MS}ms timeout")
                details["latency_ms"] = latency

            evidence.append(
                FailureEvidence(
                    dimension="execution",
                    step_index=step_index,
                    reason="; ".join(reasons),
                    severity=1.0,
                    details=details or None,
                )
            )

    return evidence


def _detect_context(steps: list[dict]) -> list[FailureEvidence]:
    """Detect context-window pressure.

    Triggers when ``context_window.used / context_window.limit > 0.95``.
    Also signals when the observation contains ``truncated`` or ``context``
    keywords.  Severity is set to the usage ratio (capped at 1.0).
    """
    evidence: list[FailureEvidence] = []

    for step in steps:
        step_index = step.get("index", 0)
        obs = (step.get("observation") or "").lower()
        cw = step.get("context_window")
        ratio: float | None = None
        obs_triggered = any(kw in obs for kw in _CONTEXT_KEYWORDS)

        if cw and isinstance(cw, dict):
            used = cw.get("used", 0) or 0
            limit = cw.get("limit", 0) or 1  # avoid div-by-zero
            ratio = used / limit

        if (ratio is not None and ratio > 0.95) or obs_triggered:
            severity = min(ratio, 1.0) if ratio is not None else 0.5
            reasons: list[str] = []
            details: dict[str, Any] = {}

            if ratio is not None and ratio > 0.95:
                reasons.append(f"Context window usage ratio {ratio:.2%} exceeds 95%")
                details["ratio"] = ratio
            if obs_triggered:
                reasons.append("Observation contains context/truncated signal")
                details["observation_signal"] = True

            evidence.append(
                FailureEvidence(
                    dimension="context",
                    step_index=step_index,
                    reason="; ".join(reasons),
                    severity=severity,
                    details=details or None,
                )
            )

    return evidence


def _detect_budget(steps: list[dict], trajectory: dict) -> list[FailureEvidence]:
    """Detect budget exhaustion — step count hit the maximum without success.

    Triggers when ``len(steps) >= trajectory.max_steps`` and
    ``trajectory.status != "success"``.
    """
    step_count = len(steps)
    max_steps = trajectory.get("max_steps", 15)
    status = trajectory.get("status", "")

    if step_count >= max_steps and status != "success":
        return [
            FailureEvidence(
                dimension="budget",
                step_index=step_count - 1,
                reason=(
                    f"Budget exhausted: {step_count} steps reached "
                    f"the maximum of {max_steps} without success"
                ),
                severity=1.0,
                details={"step_count": step_count, "max_steps": max_steps},
            )
        ]

    return []


# ── Analysis entry points ────────────────────────────────────────────────────────────


def analyze_trajectory(trajectory: dict) -> FailureReport:
    """Analyze a single trajectory for failures across all four dimensions.

    Parameters
    ----------
    trajectory:
        Dict with ``steps`` (list of step dicts), ``status`` (str), and
        optionally ``max_steps`` (int).  Step dicts should contain at least
        ``index``, ``action`` (dict or None), ``observation``, ``latency_ms``,
        and optionally ``context_window``.

    Returns
    -------
    FailureReport
        Aggregated failure analysis.
    """
    steps: list[dict] = trajectory.get("steps", [])
    total_steps = len(steps)

    if total_steps == 0:
        return FailureReport()

    # Gather evidence from all detectors
    all_evidence: list[FailureEvidence] = []
    all_evidence.extend(_detect_planning(steps))
    all_evidence.extend(_detect_execution(steps))
    all_evidence.extend(_detect_context(steps))
    all_evidence.extend(_detect_budget(steps, trajectory))

    if not all_evidence:
        return FailureReport()

    # Compute per-dimension failure rates
    dim_failure_steps: dict[str, set[int]] = {}
    for ev in all_evidence:
        dim_failure_steps.setdefault(ev.dimension, set()).add(ev.step_index)

    dimensions: dict[str, float] = {}
    for dim, step_indices in dim_failure_steps.items():
        dimensions[dim] = len(step_indices) / total_steps

    # Determine dominant dimension
    dominant: str | None = None
    if dimensions:
        dominant = max(dimensions, key=dimensions.get)  # type: ignore[arg-type]

    # Cross-dimensional review threshold
    active_dims = [d for d, r in dimensions.items() if r > 0]
    needs_human_review = len(active_dims) >= 3

    return FailureReport(
        dimensions=dimensions,
        dominant=dominant,
        evidence=all_evidence,
        needs_human_review=needs_human_review,
    )


def analyze_trajectories(trajectories: list[dict]) -> dict[str, float]:
    """Aggregate failure rates across multiple trajectories.

    For each dimension, the average failure rate is computed across all
    trajectories (trajectories without that dimension contribute 0.0).

    Returns a dict of ``{dimension: average_failure_rate}``.
    """
    ALL_DIMENSIONS = ("planning", "execution", "context", "budget")
    n = len(trajectories)
    if n == 0:
        return {}

    totals: dict[str, float] = {d: 0.0 for d in ALL_DIMENSIONS}

    for traj in trajectories:
        report = analyze_trajectory(traj)
        for dim in ALL_DIMENSIONS:
            totals[dim] += report.dimensions.get(dim, 0.0)

    return {dim: totals[dim] / n for dim in ALL_DIMENSIONS}
