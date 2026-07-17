from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.phase1_models import RunnerJob, utcnow


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_baseline(client: AsyncClient) -> dict:
    experiment_response = await client.post(
        "/api/experiments",
        json={
            "name": "Lease invariants",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency",
        },
    )
    assert experiment_response.status_code == 201
    experiment = experiment_response.json()
    run_response = await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    assert run_response.status_code == 201
    return run_response.json()


async def claim(client: AsyncClient, runner_id: str = "runner-1"):
    return await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": runner_id},
    )


@pytest.mark.asyncio
async def test_two_runners_cannot_claim_the_same_run(api) -> None:
    client, _ = api
    run = await create_baseline(client)

    first, second = await asyncio.gather(
        claim(client, "runner-a"),
        claim(client, "runner-b"),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 204]
    claimed = first.json() if first.status_code == 200 else second.json()
    assert claimed["run"]["run_id"] == run["id"]


@pytest.mark.asyncio
async def test_expired_lease_cannot_upload_or_complete(api) -> None:
    client, factory = api
    run = await create_baseline(client)
    claim_response = await claim(client)
    assert claim_response.status_code == 200
    claimed = claim_response.json()

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        assert job is not None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    event_response = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": claimed["lease_id"],
            "events": [
                {
                    "schema_version": 1,
                    "run_id": run["id"],
                    "sequence": 1,
                    "occurred_at": utcnow().isoformat(),
                    "type": "run_started",
                    "payload": {},
                }
            ],
        },
    )
    assert event_response.status_code == 409
    assert event_response.json()["code"] == "LEASE_EXPIRED"

    complete_response = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "failed"},
    )
    assert complete_response.status_code == 409
    assert complete_response.json()["code"] == "LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_complete_and_replay_requests_are_idempotent(api) -> None:
    client, _ = api
    run = await create_baseline(client)
    claim_response = await claim(client)
    assert claim_response.status_code == 200
    claimed = claim_response.json()

    complete_url = f"/api/internal/runner/jobs/{claimed['lease_id']}/complete"
    payload = {"runner_id": "runner-1", "status": "failed", "error": "budget exhausted"}
    first = await client.post(complete_url, headers=AUTH, json=payload)
    second = await client.post(complete_url, headers=AUTH, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == run["id"]
    assert first.json()["completed_at"] == second.json()["completed_at"]

    experiment = await client.get(f"/api/experiments/{run['experiment_id']}")
    policy = experiment.json()["candidate_policy"]
    assert policy is not None
    first_replay = await client.post(f"/api/policies/{policy['id']}/replay")
    second_replay = await client.post(f"/api/policies/{policy['id']}/replay")
    assert first_replay.status_code == second_replay.status_code == 201
    assert first_replay.json()["replay_run_id"] == second_replay.json()["replay_run_id"]


@pytest.mark.asyncio
async def test_running_cancel_is_acknowledged_by_heartbeat(api) -> None:
    client, _ = api
    run = await create_baseline(client)
    claim_response = await claim(client)
    assert claim_response.status_code == 200
    claimed = claim_response.json()
    heartbeat_url = f"/api/internal/runner/jobs/{claimed['lease_id']}/heartbeat"

    running = await client.post(
        heartbeat_url,
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert running.status_code == 200
    assert running.json()["command"] == "continue"

    cancelling = await client.post(f"/api/runs/{run['id']}/cancel")
    assert cancelling.status_code == 200
    assert cancelling.json()["status"] == "cancelling"

    cancel_command = await client.post(
        heartbeat_url,
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert cancel_command.status_code == 200
    assert cancel_command.json()["command"] == "cancel"

    completed = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "cancelled"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_policy_cannot_activate_before_successful_replay(api) -> None:
    client, _ = api
    run = await create_baseline(client)
    claim_response = await claim(client)
    claimed = claim_response.json()
    completed = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "failed"},
    )
    assert completed.status_code == 200
    experiment = (await client.get(f"/api/experiments/{run['experiment_id']}")).json()
    policy = experiment["candidate_policy"]

    activation = await client.post(f"/api/policies/{policy['id']}/activate")
    assert activation.status_code == 409
    assert activation.json()["code"] == "POLICY_NOT_VALIDATED"
