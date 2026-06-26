""""Policy Compiler — pure functions to compile FailureReports into PolicyPatches.

Part of the policy pipeline split: this module holds the compile logic and
constants; ``policy_store.py`` holds the CRUD operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.failure_analyzer import FailureEvidence, FailureReport

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIMENSION_THRESHOLDS: dict[str, float] = {
    "execution": 0.25,
    "budget": 0.20,
    "planning": 0.35,
    "context": 0.40,
}

ROLLBACK_ACTIVATE = 0.10
ROLLBACK_REVERT = -0.05

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PolicyPatch:
    """A compiled policy patch produced from a FailureReport."""

    parent_version: str | None
    patch: dict  # {system_prompt_suffix, tool_priority_bias, context_strategy, max_steps_override}
    rationale: str
    expected_impact: dict | None
    confidence: str  # high / medium / low
    needs_human_review: bool = False
    source_trajectories: list[str] = field(default_factory=list)


@dataclass
class Policy:
    """A policy version with metadata, wrapping a compiled PolicyPatch.

    This is the typed interface used between PolicyStore, auto_replay,
    agent_runner, and AgentRuntime.  ``to_dict()`` produces a flat dict
    suitable for JSON serialization (API responses).
    """

    version_id: str
    version_display: str
    patch: PolicyPatch
    status: str
    score_delta: float | None = None
    parent_version: str | None = None
    created_at: str | None = None
    reject_reason: str | None = None

    def to_dict(self) -> dict:
        """Flatten to a raw dict for JSON serialization."""
        return {
            "version_id": self.version_id,
            "version_display": self.version_display,
            "parent_version": self.parent_version,
            "patch": self.patch.patch,
            "rationale": self.patch.rationale,
            "expected_impact": self.patch.expected_impact,
            "confidence": self.patch.confidence,
            "status": self.status,
            "score_delta": self.score_delta,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Combination rules (C1–C6)
# ---------------------------------------------------------------------------

_COMBINATION_RULES: list[dict[str, Any]] = [
    {
        "id": "C1",
        "dims": {"execution", "budget"},
        "patch": {
            "system_prompt_suffix": "Break complex tasks into smaller steps. Do not skip verification.",
            "max_steps_override": 3,
        },
        "rationale": "C1: Execution errors + budget exhaustion — reduce task scope",
    },
    {
        "id": "C2",
        "dims": {"execution", "planning"},
        "patch": {
            "system_prompt_suffix": "Before each tool call, explain why the previous result is insufficient.",
            "context_strategy": "increase_recent_weight",
        },
        "rationale": "C2: Execution errors + circular planning — force reasoning transparency",
    },
    {
        "id": "C3",
        "dims": {"execution", "context"},
        "patch": {
            "system_prompt_suffix": "Prefer concise observations. Focus on the most recent results.",
            "context_strategy": "aggressive_eviction",
        },
        "rationale": "C3: Execution errors + context pressure — stricter context management",
    },
    {
        "id": "C4",
        "dims": {"budget", "planning"},
        "patch": {
            "max_steps_override": 3,
            "context_strategy": "increase_recent_weight",
        },
        "rationale": "C4: Budget exhaustion + circular planning — extend steps and recent-weight context",
    },
    {
        "id": "C5",
        "dims": {"budget", "context"},
        "patch": {
            "max_steps_override": 3,
            "context_strategy": "aggressive_eviction",
        },
        "rationale": "C5: Budget exhaustion + context pressure — extend steps and evict aggressively",
    },
    {
        "id": "C6",
        "dims": {"planning", "context"},
        "patch": {
            "context_strategy": "aggressive_eviction",
        },
        "rationale": "C6: Circular planning + context pressure — aggressive context eviction",
    },
]


def _compute_max_steps_override(
    budget_rate: float,
    execution_rate: float,
    max_steps: int = 15,
) -> int:
    """Compute the effective max_steps given dimension rates."""
    delta = 0
    if budget_rate > 0.20:
        delta += 3
    if execution_rate > 0.40:
        delta += 2
    effective = min(max_steps + delta, int(max_steps * 1.5), 20)
    return effective


def _merge_patches(patches: list[dict]) -> dict:
    """Merge multiple patch dicts into one (later values override earlier)."""
    merged: dict = {}
    for p in patches:
        merged.update(p)
    return merged


# ---------------------------------------------------------------------------
# Compiler (pure function)
# ---------------------------------------------------------------------------


def compile_policy(
    report: FailureReport,
    source_trajectory_ids: list[str],
) -> PolicyPatch | None:
    """Compile a FailureReport into a PolicyPatch (or None if no action needed).

    Parameters
    ----------
    report:
        The failure analysis result from ``analyze_trajectory()``.
    source_trajectory_ids:
        Trajectory IDs that triggered this compilation (for provenance).

    Returns
    -------
    PolicyPatch or None
        ``None`` means no policy change is warranted.
    """
    if not report.dimensions:
        return None

    # Collect per-dimension severity (max severity of evidence in each dimension)
    dim_severity: dict[str, float] = {}
    for ev in report.evidence:
        dim_severity[ev.dimension] = max(
            dim_severity.get(ev.dimension, 0.0), ev.severity
        )

    # Check which single-dimension thresholds are exceeded
    triggered_dims: set[str] = set()
    single_patches: list[dict] = []
    system_prompt_parts: list[str] = []
    max_steps_override: int | None = None

    for dim, threshold in DIMENSION_THRESHOLDS.items():
        rate = report.dimensions.get(dim, 0.0)
        if rate > threshold:
            triggered_dims.add(dim)

            if dim == "execution":
                single_patches.append({
                    "system_prompt_suffix": "Verify each tool result before proceeding."
                })
                system_prompt_parts.append("execution errors detected")
                # Extend max_steps if execution failures → needs more room to recover
                exe_max = _compute_max_steps_override(
                    report.dimensions.get("budget", 0.0), rate
                )
                if max_steps_override is None or exe_max > max_steps_override:
                    max_steps_override = exe_max

            elif dim == "budget":
                single_patches.append({
                    "max_steps_override": _compute_max_steps_override(
                        rate, report.dimensions.get("execution", 0.0)
                    ),
                })
                system_prompt_parts.append("budget exhaustion")
                if max_steps_override is None:
                    max_steps_override = _compute_max_steps_override(
                        rate, report.dimensions.get("execution", 0.0)
                    )

            elif dim == "planning":
                single_patches.append({
                    "context_strategy": "increase_recent_weight",
                })
                system_prompt_parts.append("circular planning")

            elif dim == "context":
                single_patches.append({
                    "context_strategy": "aggressive_eviction",
                })
                system_prompt_parts.append("context pressure")

    # Check combination rules (≥2 triggered dims)
    applied_combination: dict | None = None
    for rule in _COMBINATION_RULES:
        if rule["dims"].issubset(triggered_dims):
            applied_combination = rule
            break

    if applied_combination:
        # Use the combination rule's patch and rationale
        combined_patch = _merge_patches(single_patches + [applied_combination["patch"]])
        rationale = applied_combination["rationale"]
    elif triggered_dims:
        combined_patch = _merge_patches(single_patches)
        parts = ", ".join(sorted(system_prompt_parts)) if system_prompt_parts else "performance degradation"
        rationale = f"Policy compiled for: {parts}"
    else:
        return None

    # Ensure max_steps_override in patch
    if max_steps_override is not None:
        combined_patch["max_steps_override"] = max_steps_override

    # Calculate confidence
    high_severity_count = sum(1 for s in dim_severity.values() if s > 0.5)
    total_rate = sum(report.dimensions.values())

    if high_severity_count >= 2:
        confidence = "high"
    elif total_rate > 1.0:
        confidence = "medium"
    else:
        confidence = "low"

    # ≥3 dimensions with rate > 0 → needs human review
    active_dims = [d for d, r in report.dimensions.items() if r > 0]
    needs_human_review = len(active_dims) >= 3

    # Expected impact estimate
    expected_impact = {
        "affected_dimensions": list(triggered_dims),
        "estimated_improvement": min(total_rate * 0.3, 0.5),
    }

    return PolicyPatch(
        parent_version=None,
        patch=combined_patch,
        rationale=rationale,
        expected_impact=expected_impact,
        confidence=confidence,
        needs_human_review=needs_human_review,
        source_trajectories=source_trajectory_ids,
    )
