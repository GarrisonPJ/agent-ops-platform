from __future__ import annotations

from datetime import timedelta

import pytest

from app.phase1_models import RunnerJob, utcnow


AUTH = {"Authorization": "Bearer test-runner-token"}


@pytest.mark.asyncio
async def test_terminal_complete_remains_idempotent_after_lease_expiry(api) -> None:
    client, factory = api
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Terminal idempotency",
                "task": "Investigate checkout API latency",
                "scenario_id": "checkout-api-latency",
            },
        )
    ).json()
    run = (
        await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    ).json()
    claim = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert claim.status_code == 200
    lease_id = claim.json()["lease_id"]
    completion_url = f"/api/internal/runner/jobs/{lease_id}/complete"
    payload = {"runner_id": "runner-1", "status": "failed", "error": "budget exhausted"}

    first = await client.post(completion_url, headers=AUTH, json=payload)
    assert first.status_code == 200

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        assert job is not None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    repeated = await client.post(completion_url, headers=AUTH, json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["completed_at"] == first.json()["completed_at"]
