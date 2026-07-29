from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.phase1_models import Run, RunnerJob, utcnow


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_baseline(client: AsyncClient) -> tuple[dict, dict]:
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Observability checkout",
                "task": "Investigate checkout API latency",
                "scenario_id": "checkout-api-latency",
                "execution_mode": "provider",
            },
        )
    ).json()
    response = await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    assert response.status_code == 201
    return experiment, response.json()


async def claim(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-observer"},
    )
    assert response.status_code == 200
    return response.json()


def event(run_id: str, sequence: int, event_type: str, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_run_diagnostics_projects_durable_correlation_and_metrics(api) -> None:
    client, factory = api
    experiment, run = await create_baseline(client)
    job = await claim(client)
    events = [
        event(run["id"], 1, "run_started", {"attempt": 1}),
        event(
            run["id"],
            2,
            "process_output",
            {
                "stream": "stdout",
                "content": "Provider request completed.",
                "provider": {
                    "model": "observability-model",
                    "request_id": "req-provider-123",
                    "latency_ms": 125,
                    "request_count": 2,
                    "token_prompt": 31,
                    "token_completion": 12,
                },
            },
        ),
        event(run["id"], 3, "run_failed", {"attempt": 1, "status": "failed"}),
    ]
    uploaded = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={"runner_id": "runner-observer", "lease_id": job["lease_id"], "events": events},
    )
    assert uploaded.status_code == 200
    completed = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "status": "failed",
            "error": "provider failed",
            "metrics": {"event_retries": 2},
        },
    )
    assert completed.status_code == 200

    now = datetime.now(timezone.utc).replace(microsecond=0)
    async with factory() as db:
        persisted = await db.get(Run, run["id"])
        assert persisted is not None
        persisted.queued_at = now - timedelta(seconds=9)
        persisted.claimed_at = now - timedelta(seconds=6)
        persisted.started_at = now - timedelta(seconds=4)
        persisted.completed_at = now
        await db.commit()

    response = await client.get(f"/api/operations/runs/{run['id']}")
    assert response.status_code == 200
    diagnostic = response.json()
    assert diagnostic["run"] == {
        "experiment_id": experiment["id"],
        "run_id": run["id"],
        "execution_mode": "provider",
        "status": "failed",
    }
    assert diagnostic["job"] == {
        "run_id": run["id"],
        "lease_id": job["lease_id"],
        "runner_id": "runner-observer",
        "attempt": 1,
        "recovery_reason": None,
    }
    assert diagnostic["provider"] == {
        "model": "observability-model",
        "request_ids": ["req-provider-123"],
        "error": None,
    }
    assert diagnostic["timing"] == {
        "queue_latency_ms": 3000,
        "run_duration_ms": 4000,
    }
    assert diagnostic["metrics"] == {
        "event_count": 3,
        "event_retries": 2,
        "lease_recoveries": 0,
        "provider_latency_ms": 125,
        "provider_tokens": 43,
    }
    assert diagnostic["terminal"] == {"status": "failed", "error": "provider failed"}


@pytest.mark.asyncio
async def test_operations_overview_reports_queue_and_terminal_signals(api) -> None:
    client, _ = api
    _, completed_run = await create_baseline(client)
    job = await claim(client)
    uploaded = await client.post(
        f"/api/internal/runner/runs/{completed_run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": job["lease_id"],
            "events": [event(completed_run["id"], 1, "run_started", {"attempt": 1})],
        },
    )
    assert uploaded.status_code == 200
    complete = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "status": "failed",
            "error": "agent failed",
            "metrics": {"event_retries": 3},
        },
    )
    assert complete.status_code == 200
    _, queued_run = await create_baseline(client)

    response = await client.get("/api/operations/overview")
    assert response.status_code == 200
    overview = response.json()
    assert overview["queue_depth"] == 1
    assert overview["runs_by_status"] == {"failed": 1, "queued": 1}
    assert overview["terminal_outcomes"] == {"failed": 1}
    assert overview["expired_lease_count"] == 0
    assert overview["lease_recoveries"] == 0
    assert overview["event_retries"] == 3
    assert queued_run["status"] == "queued"


@pytest.mark.asyncio
async def test_run_diagnostics_uses_only_the_latest_recovered_attempt(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    first = await claim(client)
    first_upload = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": first["lease_id"],
            "events": [
                event(run["id"], 1, "run_started", {"attempt": 1}),
                event(
                    run["id"],
                    2,
                    "process_output",
                    {
                        "stream": "stdout",
                        "content": "Provider request completed.",
                        "provider": {
                            "model": "observability-model",
                            "request_id": "req-abandoned-attempt",
                            "latency_ms": 10,
                            "request_count": 1,
                            "token_prompt": 1,
                            "token_completion": 1,
                        },
                    },
                ),
            ],
        },
    )
    assert first_upload.status_code == 200

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        assert job is not None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    replacement_response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-replacement"},
    )
    assert replacement_response.status_code == 200
    replacement = replacement_response.json()
    assert replacement["attempt"] == 2
    assert replacement["next_sequence"] == 3
    replacement_upload = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-replacement",
            "lease_id": replacement["lease_id"],
            "events": [
                event(run["id"], 3, "run_started", {"attempt": 2}),
                event(
                    run["id"],
                    4,
                    "process_output",
                    {
                        "stream": "stdout",
                        "content": "Provider request completed.",
                        "provider": {
                            "model": "observability-model",
                            "request_id": "req-current-attempt",
                            "latency_ms": 20,
                            "request_count": 1,
                            "token_prompt": 2,
                            "token_completion": 3,
                        },
                    },
                ),
                event(run["id"], 5, "run_failed", {"attempt": 2, "status": "failed"}),
            ],
        },
    )
    assert replacement_upload.status_code == 200
    completed = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-replacement",
            "status": "failed",
            "error": "provider failed",
            "metrics": {"event_retries": 0},
        },
    )
    assert completed.status_code == 200

    response = await client.get(f"/api/operations/runs/{run['id']}")
    assert response.status_code == 200
    diagnostic = response.json()
    assert diagnostic["job"]["attempt"] == 2
    assert diagnostic["metrics"]["lease_recoveries"] == 1
    assert diagnostic["provider"]["request_ids"] == ["req-current-attempt"]
    assert diagnostic["metrics"]["provider_latency_ms"] == 20
    assert diagnostic["metrics"]["provider_tokens"] == 5
