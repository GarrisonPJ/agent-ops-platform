from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.phase1_service as phase1_service
from app.migrations import application_alembic_head
from app.phase1_main import create_app
from app.phase1_service import _is_regression_case_source_unique_violation
from app.phase1_models import Base, Policy, RegressionCase, Run, RunnerJob, new_id


AUTH = {"Authorization": "Bearer test-runner-token"}


def envelope(run_id: str, sequence: int, event_type: str, payload: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload or {},
    }


def step(run_id: str, sequence: int, index: int, tool: str, arguments: dict) -> dict:
    return envelope(
        run_id,
        sequence,
        "step_completed",
        {
            "index": index,
            "decision_summary": "Collect the next signal.",
            "tool_call": {"name": tool, "arguments": arguments},
            "observation": "The requested signal was recorded.",
            "latency_ms": 25,
            "token_prompt": 10,
            "token_completion": 5,
            "context_window": {"used": 100, "limit": 8192},
        },
    )


async def create_run(client: AsyncClient, *, execution_mode: str = "fixture") -> tuple[dict, dict]:
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Regression cases",
                "task": "Investigate checkout API latency",
                "scenario_id": "checkout-api-latency.v1",
                "execution_mode": execution_mode,
            },
        )
    ).json()
    run = (await client.post(f"/api/experiments/{experiment['id']}/runs", json={})).json()
    return experiment, run


async def claim(client: AsyncClient, runner_id: str = "runner-1") -> dict:
    return (
        await client.post(
            "/api/internal/runner/jobs/claim",
            headers=AUTH,
            json={"runner_id": runner_id},
        )
    ).json()


async def complete(client: AsyncClient, claimed: dict, status: str) -> dict:
    response = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": status},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def upload(client: AsyncClient, claimed: dict, events: list[dict]) -> None:
    response = await client.post(
        f"/api/internal/runner/runs/{claimed['run']['run_id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": claimed["lease_id"],
            "events": events,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_failed_fixture_run_can_be_saved_idempotently(api) -> None:
    client, _ = api
    experiment, run = await create_run(client)
    claimed = await claim(client)
    await complete(client, claimed, "failed")

    first = await client.post(f"/api/runs/{run['id']}/regression-case")
    second = await client.post(f"/api/runs/{run['id']}/regression-case")

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["reproducibility"] == "deterministic"
    assert first.json()["eligible_for_replay"] is True
    detail = (await client.get(f"/api/experiments/{experiment['id']}"))
    assert detail.json()["regression_cases"][0]["source_run_id"] == run["id"]
    assert detail.json()["runs"][0]["regression_case_id"] == first.json()["id"]
    listed = await client.get(f"/api/experiments/{experiment['id']}/regression-cases")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [first.json()["id"]]


@pytest.mark.asyncio
async def test_concurrent_regression_case_creation_returns_the_same_case(api, monkeypatch) -> None:
    client, _ = api
    _, run = await create_run(client)
    await complete(client, await claim(client), "failed")

    original_commit = AsyncSession.commit
    commits_reached = 0
    all_commits_reached = asyncio.Barrier(2)
    first_commit_done = asyncio.Event()

    async def commit_after_both_regression_cases_reach_commit(session: AsyncSession) -> None:
        nonlocal commits_reached
        if any(isinstance(item, RegressionCase) for item in session.new):
            commits_reached += 1
            commit_number = commits_reached
            await asyncio.wait_for(all_commits_reached.wait(), timeout=5)
            if commit_number == 1:
                await original_commit(session)
                first_commit_done.set()
                return
            await asyncio.wait_for(first_commit_done.wait(), timeout=5)
            raise IntegrityError(
                "INSERT INTO regression_cases",
                {},
                sqlite3.IntegrityError(
                    "UNIQUE constraint failed: regression_cases.source_run_id"
                ),
            )
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_after_both_regression_cases_reach_commit)

    responses = await asyncio.wait_for(
        asyncio.gather(
            client.post(f"/api/runs/{run['id']}/regression-case"),
            client.post(f"/api/runs/{run['id']}/regression-case"),
        ),
        timeout=5,
    )

    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    detail = await client.get(f"/api/experiments/{run['experiment_id']}")
    assert detail.json()["runs"][0]["regression_case_id"] == responses[0].json()["id"]
    listed = await client.get(f"/api/experiments/{run['experiment_id']}/regression-cases")
    assert [item["id"] for item in listed.json()] == [responses[0].json()["id"]]


def test_source_unique_conflict_detection_handles_wrapped_asyncpg_error() -> None:
    native_error = Exception(
        'duplicate key value violates unique constraint "uq_regression_cases_source_run"'
    )
    native_error.constraint_name = "uq_regression_cases_source_run"
    adapter_error = Exception("asyncpg adapter error")
    adapter_error.__cause__ = native_error
    error = IntegrityError("INSERT INTO regression_cases", {}, adapter_error)

    assert _is_regression_case_source_unique_violation(error) is True


@pytest.mark.asyncio
async def test_regression_case_creation_does_not_swallow_other_integrity_errors(
    api, monkeypatch
) -> None:
    client, _ = api
    _, run = await create_run(client)
    await complete(client, await claim(client), "failed")

    original_commit = AsyncSession.commit

    async def commit_with_unrelated_conflict(session: AsyncSession) -> None:
        if any(isinstance(item, RegressionCase) for item in session.new):
            raise IntegrityError(
                "INSERT INTO regression_cases",
                {},
                sqlite3.IntegrityError("UNIQUE constraint failed: regression_cases.id"),
            )
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_with_unrelated_conflict)

    with pytest.raises(IntegrityError, match="regression_cases.id"):
        await client.post(f"/api/runs/{run['id']}/regression-case")


@pytest.mark.asyncio
async def test_regression_case_rejects_non_terminal_and_successful_runs(api) -> None:
    client, _ = api
    _, run = await create_run(client)
    pending = await client.post(f"/api/runs/{run['id']}/regression-case")
    assert pending.status_code == 409
    assert pending.json()["code"] == "REGRESSION_CASE_SOURCE_NOT_ELIGIBLE"

    claimed = await claim(client)
    await complete(client, claimed, "succeeded")
    succeeded = await client.post(f"/api/runs/{run['id']}/regression-case")
    assert succeeded.status_code == 409


@pytest.mark.asyncio
async def test_provider_case_records_non_reproducible_boundary(api) -> None:
    client, _ = api
    _, run = await create_run(client, execution_mode="provider")
    await complete(client, await claim(client), "failed")

    response = await client.post(f"/api/runs/{run['id']}/regression-case")
    assert response.status_code == 201
    assert response.json()["reproducibility"] == "provider_boundary"
    assert response.json()["eligible_for_replay"] is False
    assert "cannot be completely reproduced" in response.json()["reproducibility_note"]


@pytest.mark.asyncio
async def test_activation_gates_on_regression_replay_without_overwriting_policy(api) -> None:
    client, _ = api
    experiment, baseline = await create_run(client)
    baseline_claim = await claim(client)
    await complete(client, baseline_claim, "failed")
    case = (await client.post(f"/api/runs/{baseline['id']}/regression-case")).json()

    detail = (await client.get(f"/api/experiments/{experiment['id']}"))
    policy = detail.json()["candidate_policy"]
    main_replay = (await client.post(f"/api/policies/{policy['id']}/replay")).json()
    main_claim = await claim(client)
    replay_id = main_claim["run"]["run_id"]
    await upload(
        client,
        main_claim,
        [
            envelope(replay_id, 1, "run_started"),
            step(replay_id, 2, 0, "check_service_health", {"service": "checkout-api"}),
            step(replay_id, 3, 1, "query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
            step(replay_id, 4, 2, "fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
            envelope(replay_id, 5, "run_completed", {"exit_code": 0}),
        ],
    )
    await complete(client, main_claim, "succeeded")
    updated_policy = (
        await client.get(f"/api/experiments/{experiment['id']}")
    ).json()["candidate_policy"]
    assert updated_policy["status"] == "validated"
    assert updated_policy["score_delta"] > 0

    activation = await client.post(f"/api/policies/{policy['id']}/activate")
    assert activation.status_code == 409
    assert activation.json()["code"] == "REGRESSION_REPLAY_PENDING"
    assert activation.json()["details"]["case_ids"] == [case["id"]]

    after_pending = (
        await client.get(f"/api/experiments/{experiment['id']}")
    ).json()
    pending_policy = after_pending["candidate_policy"]
    assert pending_policy["status"] == "validated"
    regression_run_id = after_pending["regression_cases"][0]["replay_run_id"]
    assert regression_run_id != main_replay["replay_run_id"]
    regression_claim = await claim(client)
    assert regression_claim["run"]["run_id"] == regression_run_id
    await complete(client, regression_claim, "succeeded")

    after_regression = (
        await client.get(f"/api/experiments/{experiment['id']}")
    ).json()["candidate_policy"]
    assert after_regression["status"] == "validated"
    assert after_regression["score_delta"] == pytest.approx(updated_policy["score_delta"])

    activated = await client.post(f"/api/policies/{policy['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["score_delta"] == pytest.approx(updated_policy["score_delta"])


@pytest.mark.asyncio
async def test_each_policy_gets_its_own_regression_case_replay(api) -> None:
    client, factory = api
    experiment, baseline = await create_run(client)
    await complete(client, await claim(client), "failed")
    case = (
        await client.post(f"/api/runs/{baseline['id']}/regression-case")
    ).json()
    policy_a = (await client.get(f"/api/experiments/{experiment['id']}")).json()[
        "candidate_policy"
    ]

    main_replay = (await client.post(f"/api/policies/{policy_a['id']}/replay")).json()
    main_claim = await claim(client)
    replay_id = main_claim["run"]["run_id"]
    await upload(
        client,
        main_claim,
        [
            envelope(replay_id, 1, "run_started"),
            step(replay_id, 2, 0, "check_service_health", {"service": "checkout-api"}),
            step(
                replay_id,
                3,
                1,
                "query_service_metrics",
                {"service": "checkout-api", "metric": "dependency_latency"},
            ),
            step(
                replay_id,
                4,
                2,
                "fetch_service_logs",
                {"service": "checkout-api", "query": "payment dependency timeout"},
            ),
            envelope(replay_id, 5, "run_completed", {"exit_code": 0}),
        ],
    )
    await complete(client, main_claim, "succeeded")
    policy_a = (await client.get(f"/api/experiments/{experiment['id']}")).json()[
        "candidate_policy"
    ]

    pending_a = await client.post(f"/api/policies/{policy_a['id']}/activate")
    assert pending_a.status_code == 409
    assert pending_a.json()["code"] == "REGRESSION_REPLAY_PENDING"
    async with factory() as db:
        case_row = await db.get(RegressionCase, case["id"])
        assert case_row is not None and case_row.replay_run_id is not None
        first_case_replay_id = case_row.replay_run_id
        first_case_replay = await db.get(Run, first_case_replay_id)
        assert first_case_replay is not None
        first_case_replay.status = "succeeded"
        await db.commit()

    activated_a = await client.post(f"/api/policies/{policy_a['id']}/activate")
    assert activated_a.status_code == 200

    policy_b_id = new_id()
    replay_b_id = new_id()
    async with factory() as db:
        policy_b = Policy(
            id=policy_b_id,
            experiment_id=experiment["id"],
            source_run_id=baseline["id"],
            status="validated",
            patch=policy_a["patch"],
            rationale="Second candidate policy",
            score_delta=1.0,
        )
        replay_b = Run(
            id=replay_b_id,
            experiment_id=experiment["id"],
            kind="replay",
            source_run_id=baseline["id"],
            policy_id=policy_b_id,
            status="succeeded",
            evaluation_spec={"run_id": replay_b_id},
            metrics={},
        )
        db.add(policy_b)
        await db.flush()
        db.add(replay_b)
        await db.commit()
        policy_b.replay_run_id = replay_b_id
        await db.commit()

    pending_b = await client.post(f"/api/policies/{policy_b_id}/activate")
    assert pending_b.status_code == 409
    assert pending_b.json()["code"] == "REGRESSION_REPLAY_PENDING"
    async with factory() as db:
        case_row = await db.get(RegressionCase, case["id"])
        assert case_row is not None
        assert case_row.replay_policy_id == policy_b_id
        assert case_row.replay_run_id != first_case_replay_id


@pytest.mark.asyncio
async def test_concurrent_activation_claims_one_regression_replay(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNNER_TOKEN", "test-runner-token")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'activation-race.db'}",
        connect_args={"timeout": 10},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": application_alembic_head()},
        )
    application = create_app(
        session_factory=factory,
        database_engine=engine,
        initialize_database=False,
    )
    client = AsyncClient(transport=ASGITransport(app=application), base_url="http://test")
    experiment, baseline = await create_run(client)
    baseline_claim = await claim(client)
    await complete(client, baseline_claim, "failed")
    case = (await client.post(f"/api/runs/{baseline['id']}/regression-case")).json()
    policy = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]

    main_replay = (await client.post(f"/api/policies/{policy['id']}/replay")).json()
    replay_claim = await claim(client)
    replay_id = replay_claim["run"]["run_id"]
    await upload(
        client,
        replay_claim,
        [
            envelope(replay_id, 1, "run_started"),
            step(replay_id, 2, 0, "check_service_health", {"service": "checkout-api"}),
            step(replay_id, 3, 1, "query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
            step(replay_id, 4, 2, "fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
            envelope(replay_id, 5, "run_completed", {"exit_code": 0}),
        ],
    )
    await complete(client, replay_claim, "succeeded")

    original_require_policy = phase1_service.require_policy
    barrier = asyncio.Barrier(2)
    reads = 0

    async def synchronize_initial_reads(db: AsyncSession, requested_id: str):
        nonlocal reads
        result = await original_require_policy(db, requested_id)
        if requested_id == policy["id"] and reads < 2:
            reads += 1
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return result

    monkeypatch.setattr(phase1_service, "require_policy", synchronize_initial_reads)
    responses = await asyncio.wait_for(
        asyncio.gather(
            client.post(f"/api/policies/{policy['id']}/activate"),
            client.post(f"/api/policies/{policy['id']}/activate"),
        ),
        timeout=10,
    )

    assert [response.status_code for response in responses] == [409, 409]
    assert all(response.json()["code"] == "REGRESSION_REPLAY_PENDING" for response in responses)
    async with factory() as db:
        replay_runs = list(
            (
                await db.execute(
                    select(Run).where(
                        Run.policy_id == policy["id"],
                        Run.regression_case_id == case["id"],
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
                        RunnerJob.run_id.in_([run.id for run in replay_runs])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(replay_runs) == len(runner_jobs) == 1
    assert replay_runs[0].id != main_replay["replay_run_id"]
    await client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_activation_reject_race_rolls_back_regression_replay(api, monkeypatch) -> None:
    client, factory = api
    experiment, baseline = await create_run(client)
    baseline_claim = await claim(client)
    await complete(client, baseline_claim, "failed")
    case = (await client.post(f"/api/runs/{baseline['id']}/regression-case")).json()
    policy = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]

    main_replay = (await client.post(f"/api/policies/{policy['id']}/replay")).json()
    replay_claim = await claim(client)
    replay_id = replay_claim["run"]["run_id"]
    await upload(
        client,
        replay_claim,
        [
            envelope(replay_id, 1, "run_started"),
            step(replay_id, 2, 0, "check_service_health", {"service": "checkout-api"}),
            step(replay_id, 3, 1, "query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
            step(replay_id, 4, 2, "fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
            envelope(replay_id, 5, "run_completed", {"exit_code": 0}),
        ],
    )
    await complete(client, replay_claim, "succeeded")

    original_require_policy = phase1_service.require_policy
    rejected = False

    async def reject_after_activation_read(db: AsyncSession, requested_id: str):
        nonlocal rejected
        result = await original_require_policy(db, requested_id)
        if requested_id == policy["id"] and not rejected:
            rejected = True
            async with factory() as competing_db:
                await phase1_service.reject_policy(competing_db, requested_id, "operator rejected")
        return result

    monkeypatch.setattr(phase1_service, "require_policy", reject_after_activation_read)
    response = await client.post(f"/api/policies/{policy['id']}/activate")

    assert response.status_code == 409
    assert response.json()["code"] == "POLICY_NOT_VALIDATED"
    async with factory() as db:
        replay_runs = list(
            (
                await db.execute(
                    select(Run).where(
                        Run.policy_id == policy["id"],
                        Run.regression_case_id == case["id"],
                    )
                )
            )
            .scalars()
            .all()
        )
        stored_policy = await db.get(Policy, policy["id"])
    assert replay_runs == []
    assert stored_policy is not None
    assert stored_policy.status == "rejected"
    assert stored_policy.replay_run_id == main_replay["replay_run_id"]
