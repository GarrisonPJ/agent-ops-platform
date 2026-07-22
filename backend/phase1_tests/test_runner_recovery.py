from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.phase1_models import RunEvent, RunnerJob, utcnow


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_run(client: AsyncClient) -> dict:
    experiment = await client.post(
        "/api/experiments",
        json={
            "name": "Runner recovery",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency",
        },
    )
    assert experiment.status_code == 201
    run = await client.post(f"/api/experiments/{experiment.json()['id']}/runs", json={})
    assert run.status_code == 201
    return run.json()


async def claim(client: AsyncClient, runner_id: str) -> dict:
    response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": runner_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def expire_lease(factory, run_id: str) -> None:
    async with factory() as db:
        job = await db.get(RunnerJob, run_id)
        assert job is not None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()


def event(run_id: str, sequence: int, event_type: str, payload: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": utcnow().isoformat(),
        "type": event_type,
        "payload": payload or {},
    }


async def upload(client: AsyncClient, claim_data: dict, events: list[dict]) -> None:
    response = await client.post(
        f"/api/internal/runner/runs/{claim_data['run']['run_id']}/events",
        headers=AUTH,
        json={
            "runner_id": claim_data["runner_id"],
            "lease_id": claim_data["lease_id"],
            "events": events,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_old_lease_is_fenced(api) -> None:
    client, factory = api
    run = await create_run(client)

    first = await claim(client, "runner-1")
    first["runner_id"] = "runner-1"
    assert first["attempt"] == 1
    assert first["next_sequence"] == 1
    await upload(client, first, [event(run["id"], 1, "run_started")])
    await expire_lease(factory, run["id"])

    replacement = await claim(client, "runner-2")
    replacement["runner_id"] = "runner-2"
    assert replacement["lease_id"] != first["lease_id"]
    assert replacement["attempt"] == 2
    assert replacement["next_sequence"] == 2
    assert "lease expired" in replacement["recovery_reason"]

    stale_event = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": first["lease_id"],
            "events": [event(run["id"], 2, "process_output")],
        },
    )
    assert stale_event.status_code == 403
    assert stale_event.json()["code"] == "INVALID_LEASE"

    await upload(
        client,
        replacement,
        [
            event(run["id"], 2, "run_started", {"attempt": 2}),
            event(
                run["id"],
                3,
                "step_completed",
                {
                    "index": 0,
                    "tool_call": {"name": "check_service_health", "arguments": {}},
                    "observation": "Service is healthy.",
                },
            ),
        ],
    )
    completed = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-2", "status": "succeeded"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["metrics"]["steps"] == 1

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        rows = list(
            (
                await db.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run["id"])
                    .order_by(RunEvent.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert job is not None
        assert job.attempt == 2
        assert job.runner_id == "runner-2"
        assert [row.sequence for row in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_cancel_intent_survives_runner_recovery(api) -> None:
    client, factory = api
    run = await create_run(client)
    first = await claim(client, "runner-1")

    heartbeat = await client.post(
        f"/api/internal/runner/jobs/{first['lease_id']}/heartbeat",
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert heartbeat.status_code == 200
    cancelling = await client.post(f"/api/runs/{run['id']}/cancel")
    assert cancelling.status_code == 200
    assert cancelling.json()["status"] == "cancelling"
    await expire_lease(factory, run["id"])

    replacement = await claim(client, "runner-2")
    cancel_command = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/heartbeat",
        headers=AUTH,
        json={"runner_id": "runner-2"},
    )
    assert cancel_command.status_code == 200
    assert cancel_command.json()["command"] == "cancel"

    completed = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-2", "status": "succeeded"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_expired_lease_exhaustion_marks_run_terminal(api) -> None:
    client, factory = api
    run = await create_run(client)

    for attempt, runner_id in enumerate(("runner-1", "runner-2", "runner-3"), start=1):
        claimed = await claim(client, runner_id)
        assert claimed["attempt"] == attempt
        await expire_lease(factory, run["id"])

    no_work = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "replacement"},
    )
    assert no_work.status_code == 204

    current = await client.get(f"/api/runs/{run['id']}")
    assert current.status_code == 200
    assert current.json()["status"] == "failed"
    assert "3 attempts" in current.json()["error"]

    analysis = await client.get(f"/api/runs/{run['id']}/analysis")
    assert analysis.status_code == 200

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        assert job is not None
        assert job.attempt == 3
        assert job.lease_id is None
        assert job.runner_id is None
