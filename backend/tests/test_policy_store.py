"""Tests for PolicyStore (DB integration)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.policy_store import PolicyStore


@pytest.fixture
def store(session: AsyncSession) -> PolicyStore:
    return PolicyStore(session)


class TestPolicyStore:
    """DB integration tests for PolicyStore CRUD."""

    @pytest.mark.asyncio
    async def test_create_first_policy(self, store: PolicyStore) -> None:
        """First policy gets version_display = 'v1'."""
        policy = await store.create_policy(
            version_display="v1",
            parent_version=None,
            patch={"system_prompt_suffix": "test"},
            rationale="Test policy",
            expected_impact=None,
            confidence="low",
            source_trajectories=[],
        )
        assert policy.version_display == "v1"
        assert policy.status == "pending_review"

    @pytest.mark.asyncio
    async def test_create_sequential_versions(self, store: PolicyStore) -> None:
        """Calling create twice gives distinct version_display."""
        p1 = await store.create_policy(
            version_display="v1",
            parent_version=None,
            patch={"system_prompt_suffix": "first"},
            rationale="First",
            expected_impact=None,
            confidence="low",
            source_trajectories=[],
        )
        p2 = await store.create_policy(
            version_display="v2",
            parent_version=None,
            patch={"system_prompt_suffix": "second"},
            rationale="Second",
            expected_impact=None,
            confidence="low",
            source_trajectories=[],
        )
        assert p1.version_display == "v1"
        assert p2.version_display == "v2"

    @pytest.mark.asyncio
    async def test_get_active_policy_returns_latest_active(
        self, store: PolicyStore
    ) -> None:
        """get_active_policy returns the most recent active policy."""
        p1 = await store.create_policy(
            version_display="v1", parent_version=None,
            patch={}, rationale="test",
            expected_impact=None, confidence="low",
            source_trajectories=[],
        )
        p2 = await store.create_policy(
            version_display="v2", parent_version=None,
            patch={}, rationale="test",
            expected_impact=None, confidence="low",
            source_trajectories=[],
        )
        # Activate p2
        await store.update_policy_status(p2.version_id, "active")
        await store.session.flush()

        active = await store.get_active_policy()
        assert active is not None
        assert active.version_id == p2.version_id

        # No active policies yet before activation
        # Deactivate p2
        await store.update_policy_status(p2.version_id, "reverted")
        await store.session.flush()
        active = await store.get_active_policy()
        assert active is None

    @pytest.mark.asyncio
    async def test_update_policy_status_changes_status(
        self, store: PolicyStore
    ) -> None:
        """update_policy_status correctly changes a policy's status."""
        policy = await store.create_policy(
            version_display="v1", parent_version=None,
            patch={}, rationale="test",
            expected_impact=None, confidence="low",
            source_trajectories=[],
        )
        assert policy.status == "pending_review"

        await store.update_policy_status(
            policy.version_id, "active", score_delta=0.15,
        )
        await store.session.flush()

        updated = await store.get_policy(policy.version_id)
        assert updated is not None
        assert updated.status == "active"
        assert updated.score_delta == 0.15

    @pytest.mark.asyncio
    async def test_create_policy_retry_on_integrity_error(
        self, store: PolicyStore
    ) -> None:
        """create_policy retries on IntegrityError (concurrent version conflict)."""
        # First creation succeeds
        p1 = await store.create_policy(
            version_display="v1", parent_version=None,
            patch={}, rationale="test",
            expected_impact=None, confidence="low",
            source_trajectories=[],
        )
        assert p1.version_display == "v1"

        # Try creating with same version_display — should trigger retry
        # Under the hood the session.add will fail on flush, retry with next_version
        p2 = await store.create_policy(
            version_display="v1", parent_version=None,
            patch={"system_prompt_suffix": "retry-test"},
            rationale="Retry test",
            expected_impact=None, confidence="low",
            source_trajectories=[],
        )
        assert p2 is not None
        # The retry gave it a new version_display
        assert p2.version_display != p1.version_display
