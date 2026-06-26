""""Policy Store — CRUD for policy_versions and trajectory_policy_map tables.

Part of the policy pipeline split: this module holds the persistence layer;
``policy_compiler.py`` holds the compile logic and constants.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class PolicyStore:
    """CRUD for policy_versions and trajectory_policy_map tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_policy(self) -> "Policy | None":
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
        return self._to_policy(row) if row else None

    async def create_policy(
        self,
        version_display: str,
        parent_version: str | None,
        patch: dict,
        rationale: str,
        expected_impact: dict | None,
        confidence: str,
        source_trajectories: list[str],
    ) -> "Policy":
        """Create a new policy version and return it as a dict.

        Retries once on IntegrityError (concurrent version_display conflict).
        """
        from app.models import PolicyVersion
        from sqlalchemy.exc import IntegrityError

        last_error: Exception | None = None
        for attempt in range(2):
            version_display_to_use = version_display
            if attempt > 0:
                version_display_to_use = await self.next_version_display()

            try:
                async with self.session.begin_nested():
                    policy = PolicyVersion(
                        version_id=str(uuid.uuid4()),
                        version_display=version_display_to_use,
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

                return self._to_policy(policy)
            except IntegrityError as exc:
                last_error = exc
                continue  # retry once

        # Second attempt also failed
        raise last_error  # type: ignore[misc]

    async def update_policy_status(
        self,
        version_id: str,
        status: str,
        score_delta: float | None = None,
        reject_reason: str | None = None,
    ) -> "Policy | None":
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
        return self._to_policy(policy)

    async def list_policies(self, status: str | None = None) -> list["Policy"]:
        """List policy versions, optionally filtered by status."""
        from app.models import PolicyVersion

        stmt = select(PolicyVersion).order_by(PolicyVersion.created_at.desc())
        if status:
            stmt = stmt.where(PolicyVersion.status == status)

        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        return [self._to_policy(r) for r in rows]

    async def get_policy(self, version_id: str) -> "Policy | None":
        """Get a single policy by version_id."""
        from app.models import PolicyVersion

        policy = await self.session.get(PolicyVersion, version_id)
        return self._to_policy(policy) if policy else None

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

    async def deactivate_active_policy(self) -> None:
        """Deactivate the currently active policy before activating a successor.

        Sets the old active to ``"reverted"``.  Within the three-status
        vocabulary (active / pending_review / reverted) this is the closest
        fit: the policy is no longer active and is being replaced by a
        successor, whether that successor was approved manually or promoted
        by the auto-replay evaluator.
        """
        active = await self.get_active_policy()
        if active:
            await self.update_policy_status(
                active.version_id, "reverted"
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
    def _to_policy(row: Any) -> "Policy":
        """Convert a PolicyVersion ORM instance to a Policy dataclass."""
        from app.policy_compiler import Policy, PolicyPatch

        patch_obj = PolicyPatch(
            parent_version=row.parent_version,
            patch=row.patch,
            rationale=row.rationale,
            expected_impact=row.expected_impact,
            confidence=row.confidence,
        )
        return Policy(
            version_id=row.version_id,
            version_display=row.version_display,
            patch=patch_obj,
            status=row.status,
            score_delta=row.score_delta,
            parent_version=row.parent_version,
            created_at=row.created_at.isoformat() if row.created_at else None,
            reject_reason=row.reject_reason,
        )

    @staticmethod
    def _map_row(trajectory_id: str, policy_version_id: str) -> Any:
        """Create a TrajectoryPolicyMap row without committing."""
        from app.models import TrajectoryPolicyMap

        return TrajectoryPolicyMap(
            trajectory_id=trajectory_id,
            policy_version_id=policy_version_id,
        )
