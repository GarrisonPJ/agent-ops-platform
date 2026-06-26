""""Policy Pipeline — compile FailureReports into PolicyPatches and persist them.

Provides the PolicyCompiler (rule engine) and PolicyStore (CRUD for policy versions).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.failure import FailureEvidence, FailureReport

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

    version: str
    parent_version: str | None
    patch: dict  # {system_prompt_suffix, tool_priority_bias, context_strategy, max_steps_override}
    rationale: str
    expected_impact: dict | None
    confidence: str  # high / medium / low
    source_trajectories: list[str] = field(default_factory=list)


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

    # ≥3 dimensions with rate > 0 → needs human review, skip auto-compile
    active_dims = [d for d, r in report.dimensions.items() if r > 0]
    if len(active_dims) >= 3:
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

    # Expected impact estimate
    expected_impact = {
        "affected_dimensions": list(triggered_dims),
        "estimated_improvement": min(total_rate * 0.3, 0.5),
    }

    return PolicyPatch(
        version="v0",  # placeholder; PolicyStore will assign the real version
        parent_version=None,
        patch=combined_patch,
        rationale=rationale,
        expected_impact=expected_impact,
        confidence=confidence,
        source_trajectories=source_trajectory_ids,
    )


# ---------------------------------------------------------------------------
# Policy Store (CRUD)
# ---------------------------------------------------------------------------


class PolicyStore:
    """CRUD for policy_versions and trajectory_policy_map tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_policy(self) -> dict | None:
        """Return the latest active policy, or None."""
        from app.models import PolicyVersion

        stmt = (
            select(PolicyVersion)
            .where(PolicyVersion.status == "active")
            .order_by(PolicyVersion.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_dict(row) if row else None

    async def create_policy(
        self,
        version_display: str,
        parent_version: str | None,
        patch: dict,
        rationale: str,
        expected_impact: dict | None,
        confidence: str,
        source_trajectories: list[str],
    ) -> dict:
        """Create a new policy version and return it as a dict."""
        from app.models import PolicyVersion

        policy = PolicyVersion(
            version_id=str(uuid.uuid4()),
            version_display=version_display,
            parent_version=parent_version,
            patch=patch,
            rationale=rationale,
            expected_impact=expected_impact,
            confidence=confidence,
            status="pending_review",
        )
        self.session.add(policy)
        await self.session.flush()

        # Link source trajectories
        for tid in source_trajectories:
            self.session.add(
                self._map_row(tid, policy.version_id)
            )

        return self._to_dict(policy)

    async def update_policy_status(
        self,
        version_id: str,
        status: str,
        score_delta: float | None = None,
        reject_reason: str | None = None,
    ) -> dict | None:
        """Update the status of a policy version."""
        from app.models import PolicyVersion

        policy = await self.session.get(PolicyVersion, version_id)
        if policy is None:
            return None

        policy.status = status
        if score_delta is not None:
            policy.score_delta = score_delta
        if reject_reason is not None:
            policy.reject_reason = reject_reason
        return self._to_dict(policy)

    async def list_policies(self, status: str | None = None) -> list[dict]:
        """List policy versions, optionally filtered by status."""
        from app.models import PolicyVersion

        stmt = select(PolicyVersion).order_by(PolicyVersion.created_at.desc())
        if status:
            stmt = stmt.where(PolicyVersion.status == status)

        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_dict(r) for r in rows]

    async def get_policy(self, version_id: str) -> dict | None:
        """Get a single policy by version_id."""
        from app.models import PolicyVersion

        policy = await self.session.get(PolicyVersion, version_id)
        return self._to_dict(policy) if policy else None

    async def link_trajectory(
        self, trajectory_id: str, policy_version_id: str
    ) -> None:
        """Link a trajectory to a policy version."""
        self.session.add(self._map_row(trajectory_id, policy_version_id))

    async def get_warmup_status(self) -> dict:
        """Return warmup progress toward the first auto-compile threshold."""
        from app.models import Trajectory

        stmt = select(func.count(Trajectory.id))
        result = await self.session.execute(stmt)
        total = result.scalar() or 0
        threshold = 10
        return {
            "total_trajectories": total,
            "threshold": threshold,
            "ready": total >= threshold,
        }

    async def archive_active_policy(self) -> None:
        """Archive the currently active policy (before approving a new one)."""
        active = await self.get_active_policy()
        if active:
            await self.update_policy_status(
                active["version_id"], "archived"
            )

    async def next_version_display(self) -> str:
        """Generate the next version_display (v1, v2, v3, ...)."""
        from app.models import PolicyVersion

        stmt = select(func.count(PolicyVersion.version_id))
        result = await self.session.execute(stmt)
        count = (result.scalar() or 0) + 1
        return f"v{count}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dict(policy: Any) -> dict:
        """Convert a PolicyVersion ORM instance to a plain dict."""
        return {
            "version_id": policy.version_id,
            "version_display": policy.version_display,
            "parent_version": policy.parent_version,
            "patch": policy.patch,
            "rationale": policy.rationale,
            "expected_impact": policy.expected_impact,
            "confidence": policy.confidence,
            "status": policy.status,
            "score_delta": policy.score_delta,
            "reject_reason": policy.reject_reason,
            "created_at": policy.created_at.isoformat() if policy.created_at else None,
        }

    @staticmethod
    def _map_row(trajectory_id: str, policy_version_id: str) -> Any:
        """Create a TrajectoryPolicyMap row without committing."""
        from app.models import TrajectoryPolicyMap

        return TrajectoryPolicyMap(
            trajectory_id=trajectory_id,
            policy_version_id=policy_version_id,
        )
