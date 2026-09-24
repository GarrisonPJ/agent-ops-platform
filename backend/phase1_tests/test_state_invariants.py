from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.phase1_service as phase1_service
from app.phase1_main import create_app
from app.phase1_models import Base, Experiment, Policy, Run, RunEvent, RunnerJob, new_id, utcnow
from app.phase1_schemas import EvaluationSpec, EventEnvelope


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
async def test_activation_supersedes_existing_active_policy_before_claim(api) -> None:
    _client, factory = api
    experiment_id, baseline_id = new_id(), new_id()
    first_policy_id, first_replay_id = new_id(), new_id()
    second_policy_id, second_replay_id = new_id(), new_id()
    experiment = Experiment(
        id=experiment_id,
        name="Policy replacement",
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
    patch = {"instruction_patch": [], "tool_priority": {}, "max_steps": 6}
    first_policy = Policy(
        id=first_policy_id,
        experiment_id=experiment_id,
        source_run_id=baseline_id,
        replay_run_id=first_replay_id,
        status="active",
        patch=patch,
        rationale="Existing active policy",
        score_delta=1.0,
    )
    second_policy = Policy(
        id=second_policy_id,
        experiment_id=experiment_id,
        source_run_id=baseline_id,
        replay_run_id=second_replay_id,
        status="validated",
        patch=patch,
        rationale="Replacement policy",
        score_delta=1.0,
    )
    first_replay = Run(
        id=first_replay_id,
        experiment_id=experiment_id,
        kind="replay",
        source_run_id=baseline_id,
        policy_id=first_policy_id,
        status="succeeded",
        evaluation_spec=baseline_spec.model_copy(update={"run_id": first_replay_id}).model_dump(mode="json"),
        metrics={},
    )
    second_replay = Run(
        id=second_replay_id,
        experiment_id=experiment_id,
        kind="replay",
        source_run_id=baseline_id,
        policy_id=second_policy_id,
        status="succeeded",
        evaluation_spec=baseline_spec.model_copy(update={"run_id": second_replay_id}).model_dump(mode="json"),
        metrics={},
    )
    async with factory() as db:
        first_policy.replay_run_id = None
        second_policy.replay_run_id = None
        db.add(experiment)
        await db.commit()
        db.add(baseline)
        await db.commit()
        db.add_all([first_policy, second_policy])
        await db.commit()
        db.add_all([first_replay, second_replay])
        await db.commit()
        first_policy.replay_run_id = first_replay_id
        second_policy.replay_run_id = second_replay_id
        await db.commit()
        response = await phase1_service.activate_policy(db, second_policy_id)
        stored_first = await db.get(Policy, first_policy_id)
    assert response.status == "active"
    assert stored_first is not None
    assert stored_first.status == "superseded"


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
async def test_cancel_cannot_overwrite_terminal_status_from_stale_session(api) -> None:
    client, factory = api
    run = await create_baseline(client)
    claimed = (await claim(client)).json()

    async with factory() as stale_db:
        stale_run = await phase1_service.require_run(stale_db, run["id"])
        assert stale_run.status == "claimed"
        async with factory() as winner_db:
            completed = await phase1_service.complete_job(
                winner_db,
                lease_id=claimed["lease_id"],
                runner_id="runner-1",
                status="failed",
                failure_kind=None,
                metrics=None,
            )
            assert completed.status.value == "failed"
        cancelled = await phase1_service.cancel_run(stale_db, run["id"])

    assert cancelled.status.value == "failed"


@pytest.mark.asyncio
async def test_heartbeat_cannot_restore_running_after_terminal_completion(api) -> None:
    client, factory = api
    run = await create_baseline(client)
    claimed = (await claim(client)).json()

    async with factory() as stale_db:
        await phase1_service._leased_job(
            stale_db, lease_id=claimed["lease_id"], runner_id="runner-1"
        )
        async with factory() as winner_db:
            completed = await phase1_service.complete_job(
                winner_db,
                lease_id=claimed["lease_id"],
                runner_id="runner-1",
                status="failed",
                failure_kind=None,
                metrics=None,
            )
            assert completed.status.value == "failed"
        with pytest.raises(phase1_service.DomainError) as error:
            await phase1_service.heartbeat(stale_db, claimed["lease_id"], "runner-1")

    assert error.value.code == "RUN_TERMINAL"


@pytest.mark.asyncio
async def test_heartbeat_reads_committed_cancellation_from_stale_session(api) -> None:
    client, factory = api
    run = await create_baseline(client)
    claimed = (await claim(client)).json()
    running = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/heartbeat",
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert running.status_code == 200

    async with factory() as stale_db:
        await phase1_service._leased_job(
            stale_db, lease_id=claimed["lease_id"], runner_id="runner-1"
        )
        async with factory() as winner_db:
            cancelled = await phase1_service.cancel_run(winner_db, run["id"])
            assert cancelled.status.value == "cancelling"
        command, _ = await phase1_service.heartbeat(
            stale_db, claimed["lease_id"], "runner-1"
        )

    assert command == "cancel"


@pytest.mark.asyncio
async def test_concurrent_event_retry_converges_on_normalized_payload(api, monkeypatch) -> None:
    client, factory = api
    run = await create_baseline(client)
    claimed = (await claim(client)).json()
    event = EventEnvelope(
        run_id=run["id"],
        sequence=1,
        occurred_at=datetime.now(timezone.utc),
        type="process_output",
        payload={"content": "same", "unexpected": "ignored"},
    )
    original_leased_job = phase1_service._leased_job
    reads = 0
    both_read = asyncio.Barrier(2)

    async def pause_after_read(*args, **kwargs):
        nonlocal reads
        result = await original_leased_job(*args, **kwargs)
        if reads < 2:
            reads += 1
            await asyncio.wait_for(both_read.wait(), timeout=5)
        return result

    monkeypatch.setattr(phase1_service, "_leased_job", pause_after_read)
    async with factory() as first_db, factory() as second_db:
        results = await asyncio.wait_for(
            asyncio.gather(
                phase1_service.persist_events(
                    first_db,
                    run_id=run["id"],
                    lease_id=claimed["lease_id"],
                    runner_id="runner-1",
                    events=[event],
                ),
                phase1_service.persist_events(
                    second_db,
                    run_id=run["id"],
                    lease_id=claimed["lease_id"],
                    runner_id="runner-1",
                    events=[event],
                ),
            ),
            timeout=10,
        )

    assert [result[0] for result in results] == [1, 1]
    assert sum(bool(result[1]) for result in results) == 1
    async with factory() as db:
        rows = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run["id"]))
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_activation_case_claim_retry_is_idempotent_after_winner_activates(api) -> None:
    client, factory = api
    run = await create_baseline(client)
    claimed = (await claim(client)).json()
    completed = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "failed"},
    )
    assert completed.status_code == 200
    policy_data = (await client.get(f"/api/experiments/{run['experiment_id']}")).json()[
        "candidate_policy"
    ]
    assert policy_data is not None

    async with factory() as db:
        policy = await db.get(Policy, policy_data["id"])
        assert policy is not None
        policy.status = "active"
        await db.commit()
        result = await phase1_service._retry_activation_after_case_claim_loss(
            db, policy.id
        )

    assert result.status.value == "active"


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
