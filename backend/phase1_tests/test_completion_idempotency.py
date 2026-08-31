from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.phase1_service as phase1_service
from app.phase1_main import create_app
from app.phase1_models import (
    Base,
    Experiment,
    Policy,
    Run,
    RunAnalysis,
    RunnerJob,
    new_id,
    utcnow,
)


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
                "scenario_id": "checkout-api-latency.v1",
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


@pytest.mark.asyncio
async def test_concurrent_completion_records_terminal_effects_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNNER_TOKEN", "test-runner-token")
    database_path = tmp_path / "completion-idempotency.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        pool_size=2,
        max_overflow=0,
        connect_args={"timeout": 10},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(Base.metadata.create_all)

    app = create_app(
        session_factory=factory,
        database_engine=engine,
        initialize_database=False,
    )
    try:
        now = utcnow()
        lease_id = new_id()
        run_id = new_id()
        async with factory() as db:
            experiment = Experiment(
                name="Concurrent completion",
                task="Investigate checkout API latency",
                scenario_id="checkout-api-latency.v1",
                execution_mode="fixture",
            )
            db.add(experiment)
            await db.flush()
            run = Run(
                id=run_id,
                experiment_id=experiment.id,
                kind="baseline",
                status="claimed",
                metrics={},
                evaluation_spec={
                    "schema_version": 1,
                    "run_id": run_id,
                    "experiment_id": experiment.id,
                    "scenario_id": experiment.scenario_id,
                    "task": experiment.task,
                    "seed": 42,
                    "execution_mode": "fixture",
                    "policy": None,
                    "limits": {"timeout_ms": 60000, "max_output_bytes": 1048576},
                    "scenario_params": {},
                },
                queued_at=now,
                claimed_at=now,
            )
            db.add(run)
            await db.flush()
            db.add(
                RunnerJob(
                    run_id=run.id,
                    attempt=1,
                    lease_id=lease_id,
                    runner_id="runner-1",
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
            await db.commit()

        original_leased_job = phase1_service._leased_job
        both_reads_complete = asyncio.Barrier(2)
        initial_reads = 0

        async def wait_after_read(*args, **kwargs):
            nonlocal initial_reads
            result = await original_leased_job(*args, **kwargs)
            if kwargs.get("allow_expired") and initial_reads < 2:
                initial_reads += 1
                await asyncio.wait_for(both_reads_complete.wait(), timeout=5)
            return result

        monkeypatch.setattr(phase1_service, "_leased_job", wait_after_read)
        completion_url = f"/api/internal/runner/jobs/{lease_id}/complete"
        payload = {"runner_id": "runner-1", "status": "failed"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.post(completion_url, headers=AUTH, json=payload),
                    client.post(completion_url, headers=AUTH, json=payload),
                ),
                timeout=10,
            )

        assert [response.status_code for response in responses] == [200, 200]
        response_payloads = [response.json() for response in responses]
        assert response_payloads[0]["id"] == response_payloads[1]["id"] == run_id
        assert (
            response_payloads[0]["completed_at"]
            == response_payloads[1]["completed_at"]
        )
        async with factory() as db:
            persisted_run = await db.get(Run, run_id)
            assert persisted_run is not None
            assert persisted_run.status == "failed"
            assert persisted_run.completed_at is not None
            assert persisted_run.completed_at.isoformat() == response_payloads[0]["completed_at"]
            analyses = (
                await db.execute(select(RunAnalysis).where(RunAnalysis.run_id == run_id))
            ).scalars().all()
            policies = (
                await db.execute(select(Policy).where(Policy.source_run_id == run_id))
            ).scalars().all()
            assert len(analyses) == 1
            assert len(policies) == 1
            assert policies[0].source_run_id == persisted_run.id
            assert policies[0].status == "candidate"
    finally:
        await engine.dispose()
