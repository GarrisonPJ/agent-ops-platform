from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.migrations import application_alembic_head
from app.phase1_models import RunnerJob, utcnow


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_baseline(client: AsyncClient) -> tuple[dict, dict]:
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Operational state checkout",
                "task": "Investigate operational classification",
                "scenario_id": "checkout-api-latency",
                "execution_mode": "provider",
            },
        )
    ).json()
    response = await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    assert response.status_code == 201
    return experiment, response.json()


async def claim(client: AsyncClient, *, runner_id: str = "runner-ops", require_job: bool = True) -> dict | None:
    response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": runner_id},
    )
    if require_job:
        assert response.status_code == 200
        return response.json()
    assert response.status_code in {200, 204}
    return response.json() if response.content else None


async def register_runner(client: AsyncClient, *, runner_id: str = "runner-ops") -> None:
    await claim(client, runner_id=runner_id, require_job=False)


async def complete_failed(
    client: AsyncClient,
    job: dict,
    *,
    runner_id: str = "runner-ops",
    provider_error_code: str | None = None,
    event_retries: int | None = None,
) -> dict:
    run_id = job["run"]["run_id"]
    uploaded = await client.post(
        f"/api/internal/runner/runs/{run_id}/events",
        headers=AUTH,
        json={
            "runner_id": runner_id,
            "lease_id": job["lease_id"],
            "events": [
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "sequence": job["next_sequence"],
                    "type": "run_started",
                    "occurred_at": utcnow().isoformat(),
                    "payload": {"attempt": job["attempt"]},
                }
            ],
        },
    )
    assert uploaded.status_code == 200
    metrics: dict[str, object] = {}
    if provider_error_code is not None:
        metrics["provider_error"] = {
            "code": provider_error_code,
            "message": f"Provider fault {provider_error_code}",
            "retryable": True,
            "attempts": 1,
        }
    if event_retries is not None:
        metrics["event_retries"] = event_retries
    completed = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": runner_id,
            "status": "failed",
            "error": "agent failed",
            "metrics": metrics or None,
        },
    )
    assert completed.status_code == 200
    return completed.json()


def state_codes(snapshot: dict) -> dict[str, str]:
    return {entry["code"]: entry["status"] for entry in snapshot["states"]}


@pytest.mark.asyncio
async def test_operations_state_reports_clear_when_healthy(api) -> None:
    client, _ = api
    await register_runner(client)

    response = await client.get("/api/operations/state")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["schema_version"] == 1
    assert snapshot["status"] == "ok"
    assert snapshot["primary_state"] == "ok"
    assert state_codes(snapshot) == {
        "database_unavailable": "clear",
        "schema_drift": "clear",
        "runner_unavailable": "clear",
        "lease_expired": "clear",
        "provider_rate_limited": "clear",
        "provider_unavailable": "clear",
    }
    assert snapshot["incidents"] == []
    assert snapshot["next_cursor"] is None
    assert snapshot["summary"]["queue_depth"] == 0


@pytest.mark.asyncio
async def test_operations_state_detects_schema_drift(api) -> None:
    client, factory = api
    async with factory() as db:
        await db.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": "0005_runner_presence"},
        )
        await db.commit()

    response = await client.get("/api/operations/state")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["status"] == "degraded"
    assert snapshot["primary_state"] == "schema_drift"
    assert state_codes(snapshot)["schema_drift"] == "active"
    assert snapshot["states"][1]["details"]["expected_schema_revision"] == application_alembic_head()


@pytest.mark.asyncio
async def test_operations_state_detects_missing_runner(api) -> None:
    client, _ = api

    response = await client.get("/api/operations/state")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["primary_state"] == "runner_unavailable"
    assert state_codes(snapshot)["runner_unavailable"] == "active"


@pytest.mark.asyncio
async def test_operations_state_detects_expired_lease(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    job = await claim(client)

    async with factory() as db:
        runner_job = await db.get(RunnerJob, run["id"])
        assert runner_job is not None
        runner_job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    response = await client.get("/api/operations/state")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["primary_state"] == "lease_expired"
    assert state_codes(snapshot)["lease_expired"] == "active"
    assert snapshot["summary"]["expired_lease_count"] == 1
    assert snapshot["incidents"][0]["kind"] == "expired_lease"
    assert snapshot["incidents"][0]["run_id"] == run["id"]


@pytest.mark.asyncio
async def test_operations_state_distinguishes_provider_outage_and_rate_limit(api) -> None:
    client, _ = api
    await create_baseline(client)
    job_a = await claim(client, runner_id="runner-provider-a")
    assert job_a is not None
    await complete_failed(client, job_a, runner_id="runner-provider-a", provider_error_code="PROVIDER_UNAVAILABLE")

    await create_baseline(client)
    job_b = await claim(client, runner_id="runner-provider-b")
    assert job_b is not None
    await complete_failed(client, job_b, runner_id="runner-provider-b", provider_error_code="PROVIDER_RATE_LIMITED")

    response = await client.get("/api/operations/state")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["status"] == "degraded"
    assert snapshot["primary_state"] == "provider_rate_limited"
    codes = state_codes(snapshot)
    assert codes["provider_rate_limited"] == "active"
    assert codes["provider_unavailable"] == "active"
    fault_codes = {item["fault_code"] for item in snapshot["incidents"]}
    assert fault_codes == {"PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED"}


@pytest.mark.asyncio
async def test_operations_state_prefers_schema_drift_over_runner_unavailable(api) -> None:
    client, factory = api
    async with factory() as db:
        await db.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": "0005_runner_presence"},
        )
        await db.commit()

    response = await client.get("/api/operations/state")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["primary_state"] == "schema_drift"
    assert state_codes(snapshot)["schema_drift"] == "active"


@pytest.mark.asyncio
async def test_operations_state_rejects_invalid_cursor(api) -> None:
    client, _ = api

    response = await client.get("/api/operations/state", params={"cursor": "not-a-cursor"})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_operations_overview_uses_bounded_aggregation(api) -> None:
    client, factory = api
    await create_baseline(client)
    job = await claim(client)
    assert job is not None
    await complete_failed(client, job, event_retries=3)
    await create_baseline(client)

    overview = await client.get("/api/operations/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["runs_by_status"] == {"failed": 1, "queued": 1}
    assert body["terminal_outcomes"] == {"failed": 1}
    assert body["event_retries"] == 3

    async with factory() as db:
        run_count = len((await db.execute(text("SELECT id FROM runs"))).all())
        assert run_count == 2
