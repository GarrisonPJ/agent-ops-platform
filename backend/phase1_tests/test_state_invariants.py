from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.phase1_service as phase1_service
from app.phase1_main import create_app
from app.phase1_models import Base, Experiment, Policy, Run, RunnerJob, new_id, utcnow
from app.phase1_schemas import EvaluationSpec


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_baseline(client: AsyncClient) -> dict:
    experiment_response = await client.post(
        "/api/experiments",
        json={
            "name": "Lease invariants",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency.v1",
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
async def test_concurrent_replay_requests_are_idempotent_across_sessions(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'replay-race.db'}"
    engine = create_async_engine(database_url, connect_args={"timeout": 10})
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(Base.metadata.create_all)

    experiment_id = new_id()
    baseline_id = new_id()
    policy_id = new_id()
    experiment = Experiment(
        id=experiment_id,
        name="Replay race",
        task="Investigate checkout API latency",
        scenario_id="checkout-api-latency.v1",
        scenario_params={},
        execution_mode="fixture",
    )
    baseline_spec = EvaluationSpec(
        run_id=baseline_id,
        experiment_id=experiment_id,
        scenario_id=experiment.scenario_id,
        task=experiment.task,
    )
    baseline = Run(
        id=baseline_id,
        experiment_id=experiment_id,
        kind="baseline",
        status="failed",
        evaluation_spec=baseline_spec.model_dump(mode="json"),
        metrics={},
    )
    policy = Policy(
        id=policy_id,
        experiment_id=experiment_id,
        source_run_id=baseline_id,
        status="candidate",
        patch={"instruction_patch": [], "tool_priority": {}, "max_steps": 6},
        rationale="Test candidate",
    )
    async with factory() as db:
        db.add(experiment)
        await db.commit()
        db.add(baseline)
        await db.commit()
        db.add(policy)
        await db.commit()

    app = create_app(
        session_factory=factory,
        database_engine=engine,
        initialize_database=False,
    )
    original_require_policy = phase1_service.require_policy
    policy_reads = asyncio.Barrier(2)
    initial_policy_reads = 0

    async def require_policy_after_both_reads(db: AsyncSession, requested_id: str):
        nonlocal initial_policy_reads
        result = await original_require_policy(db, requested_id)
        if requested_id == policy_id and initial_policy_reads < 2:
            initial_policy_reads += 1
            await asyncio.wait_for(policy_reads.wait(), timeout=5)
        return result

    monkeypatch.setattr(phase1_service, "require_policy", require_policy_after_both_reads)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.post(f"/api/policies/{policy_id}/replay"),
                    client.post(f"/api/policies/{policy_id}/replay"),
                ),
                timeout=10,
            )

        assert [response.status_code for response in responses] == [201, 201]
        assert initial_policy_reads == 2
        replay_ids = [response.json()["replay_run_id"] for response in responses]
        assert replay_ids[0] == replay_ids[1]
        async with factory() as db:
            replay_runs = list(
                (
                    await db.execute(
                        select(Run).where(
                            Run.policy_id == policy_id, Run.kind == "replay"
                        )
                    )
                )
                .scalars()
                .all()
            )
            runner_jobs = list(
                (
                    await db.execute(
                        select(RunnerJob).where(
                            RunnerJob.run_id.in_([item.id for item in replay_runs])
                        )
                    )
                )
                .scalars()
                .all()
            )
            stored_policy = await db.get(Policy, policy_id)
        assert len(replay_runs) == len(runner_jobs) == 1
        assert replay_runs[0].id == replay_ids[0]
        assert stored_policy is not None
        assert stored_policy.status == "replaying"
        assert stored_policy.replay_run_id == replay_ids[0]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_status", ["rejected", "active", "superseded"])
async def test_replay_race_with_non_replayable_policy_keeps_existing_conflict(
    api, monkeypatch, blocked_status: str
) -> None:
    client, factory = api
    run = await create_baseline(client)
    claim_response = await claim(client)
    await client.post(
        f"/api/internal/runner/jobs/{claim_response.json()['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "failed"},
    )
    experiment = await client.get(f"/api/experiments/{run['experiment_id']}")
    policy = experiment.json()["candidate_policy"]
    assert policy is not None

    original_require_policy = phase1_service.require_policy
    policy_rejected = False

    async def reject_after_initial_policy_read(
        db: AsyncSession, requested_id: str
    ):
        nonlocal policy_rejected
        result = await original_require_policy(db, requested_id)
        if requested_id == policy["id"] and not policy_rejected:
            policy_rejected = True
            async with factory() as competing_db:
                competing_policy = await competing_db.get(Policy, requested_id)
                assert competing_policy is not None
                competing_policy.status = blocked_status
                await competing_db.commit()
        return result

    monkeypatch.setattr(phase1_service, "require_policy", reject_after_initial_policy_read)

    response = await client.post(f"/api/policies/{policy['id']}/replay")

    assert response.status_code == 409
    assert response.json()["code"] == "POLICY_NOT_REPLAYABLE"
    async with factory() as db:
        replay_runs = list(
            (
                await db.execute(
                    select(Run).where(
                        Run.policy_id == policy["id"], Run.kind == "replay"
                    )
                )
            )
            .scalars()
            .all()
        )
        stored_policy = await db.get(Policy, policy["id"])
    assert replay_runs == []
    assert stored_policy is not None
    assert stored_policy.status == blocked_status


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
