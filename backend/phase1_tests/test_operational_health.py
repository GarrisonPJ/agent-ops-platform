from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.migrations import application_alembic_head
from app.phase1_models import RunnerPresence, utcnow
from app.phase1_service import RUNNER_AVAILABILITY_SECONDS


AUTH = {"Authorization": "Bearer test-runner-token"}


@pytest.mark.asyncio
async def test_liveness_and_readiness_report_api_and_database_health(api) -> None:
    client, _ = api

    assert (await client.get("/api/health")).json() == {"status": "ok", "protocol_version": 1}
    assert (await client.get("/api/health/live")).json() == {"status": "ok"}

    ready = await client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "database": "ok",
        "schema": "ok",
        "schema_revision": application_alembic_head(),
        "expected_schema_revision": application_alembic_head(),
    }


@pytest.mark.asyncio
async def test_readiness_rejects_schema_drift(api) -> None:
    client, factory = api
    async with factory() as db:
        await db.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": "0005_runner_presence"},
        )
        await db.commit()

    ready = await client.get("/api/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unavailable",
        "database": "ok",
        "schema": "unavailable",
        "schema_revision": "0005_runner_presence",
        "expected_schema_revision": application_alembic_head(),
    }


@pytest.mark.asyncio
async def test_readiness_rejects_missing_migration_table(api) -> None:
    client, factory = api
    async with factory() as db:
        await db.execute(text("DROP TABLE alembic_version"))
        await db.commit()

    ready = await client.get("/api/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unavailable",
        "database": "ok",
        "schema": "unavailable",
        "schema_revision": None,
        "expected_schema_revision": application_alembic_head(),
    }


@pytest.mark.asyncio
async def test_runner_health_uses_durable_authenticated_presence(api) -> None:
    client, factory = api

    unavailable = await client.get("/api/health/runner")
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "status": "unavailable",
        "active_runner_count": 0,
        "freshest_heartbeat_at": None,
    }

    claim = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-health"},
    )
    assert claim.status_code == 204

    available = await client.get("/api/health/runner")
    assert available.status_code == 200
    assert available.json()["status"] == "ok"
    assert available.json()["active_runner_count"] == 1
    assert available.json()["freshest_heartbeat_at"] is not None

    async with factory() as db:
        presence = await db.get(RunnerPresence, "runner-health")
        assert presence is not None
        presence.last_seen_at = utcnow() - timedelta(seconds=RUNNER_AVAILABILITY_SECONDS + 1)
        await db.commit()

    stale = await client.get("/api/health/runner")
    assert stale.status_code == 503
    assert stale.json()["status"] == "unavailable"
    assert stale.json()["active_runner_count"] == 0
