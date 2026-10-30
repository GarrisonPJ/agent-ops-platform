from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.dml import Update

import app.phase1_service as phase1_service
from app.migrations import application_alembic_head
from app.phase1_events import event_notifier
from app.phase1_models import Experiment, Policy, Run, RunAnalysis, RunEvent, RunnerJob, RunnerAttempt, new_id, utcnow
from app.phase1_schemas import EvaluationSpec, EventEnvelope, ExecutionFacts
from test_cp_completion import facts


BACKEND_DIR = Path(__file__).parents[1]
POSTGRES_URL = os.getenv("RETENTION_POSTGRES_URL")
if not POSTGRES_URL:
    pytest.skip("RETENTION_POSTGRES_URL is absent", allow_module_level=True)
if not POSTGRES_URL.startswith("postgresql+asyncpg://"):
    raise RuntimeError("RETENTION_POSTGRES_URL must use postgresql+asyncpg://")

POSTGRES_TABLES = (
    "experiments, runs, policies, regression_cases, runner_jobs, runner_attempts, "
    "run_events, run_analyses, runner_presence"
)


def _upgrade_to_head() -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = POSTGRES_URL
    pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(BACKEND_DIR)
    if pythonpath:
        environment["PYTHONPATH"] += os.pathsep + pythonpath
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=environment,
        check=True,
        timeout=90,
    )


@pytest_asyncio.fixture(name="postgres_db")
async def postgres_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    _upgrade_to_head()
    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            server_version = int(
                (await connection.execute(text("SHOW server_version_num"))).scalar_one()
            )
            assert 160000 <= server_version < 170000, server_version
            revision = str(
                (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            )
            assert revision == application_alembic_head()
            await connection.execute(
                text(f"TRUNCATE TABLE {POSTGRES_TABLES} RESTART IDENTITY CASCADE")
            )
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed_claimed_run(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str]:
    experiment_id = new_id()
    run_id = new_id()
    lease_id = new_id()
    now = utcnow()
    evaluation_spec = EvaluationSpec(
        run_id=run_id,
        experiment_id=experiment_id,
        scenario_id="checkout-api-latency.v1",
        task="Investigate checkout API latency",
    )
    async with factory.begin() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name="PostgreSQL contention",
                task="Investigate checkout API latency",
                scenario_id="checkout-api-latency.v1",
                execution_mode="fixture",
                created_at=now,
            )
        )
        await db.flush()
        db.add(
            Run(
                id=run_id,
                experiment_id=experiment_id,
                kind="baseline",
                status="claimed",
                metrics={},
                evaluation_spec=evaluation_spec.model_dump(mode="json"),
                queued_at=now,
                claimed_at=now,
            )
        )
        await db.flush()
        db.add(
            RunnerJob(
                run_id=run_id,
                attempt=1,
                lease_id=lease_id,
                runner_id="postgres-runner",
                lease_expires_at=now + timedelta(minutes=1),
            )
        )
        db.add(RunEvent(run_id=run_id, sequence=1, event_type="run_started",
                        payload={"attempt": 1, "source": "environment"}, occurred_at=now))
    return run_id, lease_id, experiment_id


def _event(run_id: str, content: str) -> EventEnvelope:
    return EventEnvelope(
        run_id=run_id,
        sequence=2,
        occurred_at=datetime.now(timezone.utc),
        type="process_output",
        payload={"attempt": 1, "content": content},
    )


async def _synchronize_initial_lease_reads(monkeypatch, count: int = 2) -> None:
    original = phase1_service._leased_job
    barrier = asyncio.Barrier(count)
    reads = 0

    async def wait_after_read(*args, **kwargs):
        nonlocal reads
        result = await original(*args, **kwargs)
        if reads < count:
            reads += 1
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return result

    monkeypatch.setattr(phase1_service, "_leased_job", wait_after_read)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_writer", ["upload", "completion"])
async def test_postgres_upload_completion_contend_on_durable_prefix(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch, first_writer: str
) -> None:
    run_id, lease_id, _ = await _seed_claimed_run(postgres_db)
    first_ready = asyncio.Event()
    release_first = asyncio.Event()
    original_leased_job = phase1_service._leased_job
    queue = event_notifier.subscribe(run_id)
    try:
        async with postgres_db() as upload_db, postgres_db() as completion_db:
            upload_pid = (await upload_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            completion_pid = (await completion_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            first_db = upload_db if first_writer == "upload" else completion_db

            async def pause_first_after_job_lock(db, *args, **kwargs):
                result = await original_leased_job(db, *args, **kwargs)
                if db is first_db and kwargs.get("for_update") and not first_ready.is_set():
                    first_ready.set()
                    await asyncio.wait_for(release_first.wait(), timeout=5)
                return result

            monkeypatch.setattr(phase1_service, "_leased_job", pause_first_after_job_lock)

            async def upload():
                return await phase1_service.persist_events(
                    upload_db, run_id=run_id, lease_id=lease_id,
                    runner_id="postgres-runner", events=[_event(run_id, "contended upload")],
                )

            async def complete():
                return await phase1_service.complete_job(
                    completion_db, lease_id=lease_id, runner_id="postgres-runner",
                    status="failed", failure_kind=None, metrics=None,
                    execution=ExecutionFacts.model_validate(facts(run_id)),
                )

            operations = [upload, complete] if first_writer == "upload" else [complete, upload]
            tasks = [asyncio.create_task(operations[0]())]
            try:
                await asyncio.wait_for(first_ready.wait(), timeout=5)
                tasks.append(asyncio.create_task(operations[1]()))
                waiting, blocking = (
                    (completion_pid, upload_pid) if first_writer == "upload"
                    else (upload_pid, completion_pid)
                )
                await _wait_for_postgres_blocker(postgres_db, waiting, blocking)
                assert queue.empty()
                async with postgres_db() as observer:
                    assert (await observer.get(Run, run_id)).status == "claimed"
                    assert await observer.get(RunAnalysis, run_id) is None
            finally:
                release_first.set()
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=10
                )
        monkeypatch.setattr(phase1_service, "_leased_job", original_leased_job)
        completed = results[1 if first_writer == "upload" else 0]
        assert not isinstance(completed, Exception), results
        assert completed.status == "failed"
        sequence = 3 if first_writer == "upload" else 2
        assert completed.terminal_sequence == sequence
        uploaded = results[0 if first_writer == "upload" else 1]
        if first_writer == "upload":
            assert uploaded[0] == 2 and len(uploaded[1]) == 1
        else:
            # Completion occupies the proposed sequence: no client event can replace it.
            assert isinstance(uploaded, phase1_service.DomainError), results
            assert uploaded.code == "EVENT_CONFLICT"
            async with postgres_db() as db:
                late = _event(run_id, "late append")
                late.sequence = sequence + 1
                with pytest.raises(phase1_service.DomainError) as rejected:
                    await phase1_service.persist_events(
                        db, run_id=run_id, lease_id=lease_id,
                        runner_id="postgres-runner", events=[late],
                    )
                assert rejected.value.code == "RUN_TERMINAL"
        # Only committed terminalization publishes; retries do not publish or rescore.
        assert queue.get_nowait()["sequence"] == sequence
        assert queue.empty()
        async with postgres_db() as db:
            repeated = await phase1_service.complete_job(
                db, lease_id=lease_id, runner_id="postgres-runner",
                status="succeeded", failure_kind=None, metrics=None,
                execution=ExecutionFacts.model_validate(facts(run_id)),
            )
            assert repeated.model_dump() == completed.model_dump()
        assert queue.empty()
        async with postgres_db() as db:
            rows = list((await db.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
            )).all())
            assert [row.sequence for row in rows] == list(range(1, sequence + 1))
            assert [row.event_type for row in rows] == (
                ["run_started", "process_output", "run_failed"] if first_writer == "upload"
                else ["run_started", "run_failed"]
            )
            assert rows[-1].payload == {"attempt": 1, "status": "failed", "failure_kind": "internal_failure"}
            assert len((await db.scalars(select(RunAnalysis).where(RunAnalysis.run_id == run_id))).all()) == 1
            assert len((await db.scalars(select(Policy).where(Policy.source_run_id == run_id))).all()) == 1
    finally:
        event_notifier.unsubscribe(run_id, queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [1, 3])
async def test_postgres_recovery_snapshot_cannot_resurrect_completion(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch, attempt: int
) -> None:
    run_id, lease_id, _ = await _seed_claimed_run(postgres_db)
    expires_at = utcnow() + timedelta(minutes=1)
    async with postgres_db.begin() as db:
        job = await db.get(RunnerJob, run_id)
        job.attempt = attempt
        job.lease_expires_at = expires_at
        start = await db.scalar(select(RunEvent).where(RunEvent.run_id == run_id))
        start.payload = {"attempt": attempt, "source": "environment"}
    ready = asyncio.Event()
    release = asyncio.Event()
    original_projection = phase1_service._evaluation_projection
    queue = event_notifier.subscribe(run_id)
    gate_key = 6140359
    try:
        async with postgres_db() as completion_db, postgres_db() as recovery_db, postgres_db() as gate_db:
            gate_pid = (await gate_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            recovery_pid = (await recovery_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await gate_db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": gate_key})

            async def pause_after_lease_validation(db, run):
                if db is completion_db:
                    ready.set()
                    await asyncio.wait_for(release.wait(), timeout=5)
                return await original_projection(db, run)

            monkeypatch.setattr(phase1_service, "_evaluation_projection", pause_after_lease_validation)
            original_execute = recovery_db.execute
            gated = False

            async def gate_recovery_statement(statement, *args, **kwargs):
                nonlocal gated
                if getattr(statement, "_for_update_arg", None) is not None and not gated:
                    gated = True
                    # Freeze the real joined predicate's statement snapshot, then
                    # wait before LockRows can acquire Job. Completion leaves Job
                    # unchanged, so the old candidate survives lock acquisition.
                    sql = str(statement.compile(
                        dialect=recovery_db.bind.sync_engine.dialect,
                        compile_kwargs={"literal_binds": True},
                    ))
                    predicate = sql.rsplit(" FOR UPDATE", 1)[0]
                    wrapped = text(
                        "WITH candidates AS MATERIALIZED (" + predicate + "), "
                        "gate AS MATERIALIZED (SELECT pg_advisory_xact_lock(:gate) FROM candidates) "
                        "SELECT runner_jobs.* FROM runner_jobs JOIN candidates USING (run_id) "
                        "CROSS JOIN gate FOR UPDATE OF runner_jobs SKIP LOCKED"
                    )
                    statement = select(RunnerJob).from_statement(wrapped).execution_options(populate_existing=True)
                    return await original_execute(statement, {"gate": gate_key})
                return await original_execute(statement, *args, **kwargs)

            monkeypatch.setattr(recovery_db, "execute", gate_recovery_statement)
            completion_task = asyncio.create_task(phase1_service.complete_job(
                completion_db, lease_id=lease_id, runner_id="postgres-runner",
                status="failed", failure_kind=None, metrics=None,
                execution=ExecutionFacts.model_validate(facts(run_id, attempt)),
            ))
            tasks = [completion_task]
            try:
                await asyncio.wait_for(ready.wait(), timeout=5)
                # Logical expiry happens after completion's valid lease check;
                # no wall-clock sleep or lease-length race is needed.
                monkeypatch.setattr(phase1_service, "utcnow", lambda: expires_at + timedelta(seconds=1))
                recovery_task = asyncio.create_task(phase1_service._recover_expired_jobs(recovery_db))
                tasks.append(recovery_task)
                await _wait_for_postgres_blocker(postgres_db, recovery_pid, gate_pid)
                assert queue.empty()
                release.set()
                completed = await asyncio.wait_for(completion_task, timeout=5)
                # Only now may recovery lock the unchanged Job from its old snapshot.
                await gate_db.commit()
                recovered = await asyncio.wait_for(recovery_task, timeout=5)
                await phase1_service._commit_terminal_notifications(recovery_db)
            finally:
                release.set()
                await gate_db.rollback()
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)
        assert gated and completed.status == "failed" and recovered == 0
        assert queue.get_nowait()["sequence"] == 2
        assert queue.empty()
        async with postgres_db() as db:
            repeated = await phase1_service.complete_job(
                db, lease_id=lease_id, runner_id="postgres-runner",
                status="succeeded", failure_kind=None, metrics=None,
                execution=ExecutionFacts.model_validate(facts(run_id, attempt)),
            )
            assert repeated.model_dump() == completed.model_dump()
        assert queue.empty()
        async with postgres_db() as db:
            run = await db.get(Run, run_id)
            job = await db.get(RunnerJob, run_id)
            assert run.status == "failed" and job.attempt == attempt
            assert job.lease_id == lease_id and job.runner_id == "postgres-runner"
            rows = list((await db.scalars(select(RunEvent).where(
                RunEvent.run_id == run_id
            ).order_by(RunEvent.sequence))).all())
            assert [row.event_type for row in rows] == ["run_started", "run_failed"]
            assert rows[-1].sequence == repeated.terminal_sequence == 2
            assert rows[-1].payload["attempt"] == attempt
            assert await db.scalar(select(func.count()).select_from(RunnerAttempt).where(RunnerAttempt.run_id == run_id)) == 0
            assert len((await db.scalars(select(RunAnalysis).where(RunAnalysis.run_id == run_id))).all()) == 1
            assert len((await db.scalars(select(Policy).where(Policy.source_run_id == run_id))).all()) == 1
    finally:
        event_notifier.unsubscribe(run_id, queue)


@pytest.mark.asyncio
async def test_postgres_concurrent_duplicate_events_converge(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    run_id, lease_id, _ = await _seed_claimed_run(postgres_db)
    await _synchronize_initial_lease_reads(monkeypatch)
    event = _event(run_id, "same payload")

    async with postgres_db() as first_db, postgres_db() as second_db:
        results = await asyncio.wait_for(
            asyncio.gather(
                phase1_service.persist_events(
                    first_db,
                    run_id=run_id,
                    lease_id=lease_id,
                    runner_id="postgres-runner",
                    events=[event],
                ),
                phase1_service.persist_events(
                    second_db,
                    run_id=run_id,
                    lease_id=lease_id,
                    runner_id="postgres-runner",
                    events=[event],
                ),
            ),
            timeout=10,
        )

    assert [result[0] for result in results] == [2, 2]
    assert sum(bool(result[1]) for result in results) == 1
    async with postgres_db() as db:
        rows = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.event_type == "process_output"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"attempt": 1, "content": "same payload"}


@pytest.mark.asyncio
async def test_postgres_concurrent_conflicting_events_return_domain_conflict(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    run_id, lease_id, _ = await _seed_claimed_run(postgres_db)
    await _synchronize_initial_lease_reads(monkeypatch)

    async with postgres_db() as first_db, postgres_db() as second_db:
        results = await asyncio.wait_for(
            asyncio.gather(
                phase1_service.persist_events(
                    first_db,
                    run_id=run_id,
                    lease_id=lease_id,
                    runner_id="postgres-runner",
                    events=[_event(run_id, "first payload")],
                ),
                phase1_service.persist_events(
                    second_db,
                    run_id=run_id,
                    lease_id=lease_id,
                    runner_id="postgres-runner",
                    events=[_event(run_id, "second payload")],
                ),
                return_exceptions=True,
            ),
            timeout=10,
        )

    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [
        result
        for result in results
        if isinstance(result, phase1_service.DomainError)
    ]
    unexpected = [
        result
        for result in results
        if isinstance(result, Exception) and not isinstance(result, phase1_service.DomainError)
    ]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert not unexpected
    assert conflicts[0].code == "EVENT_CONFLICT"
    assert conflicts[0].details == {"sequence": 2}
    async with postgres_db() as db:
        rows = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.event_type == "process_output"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload in ({"attempt": 1, "content": "first payload"}, {"attempt": 1, "content": "second payload"})


@pytest.mark.asyncio
async def test_postgres_concurrent_policy_replay_creates_one_run_and_job(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    experiment_id = new_id()
    baseline_id = new_id()
    policy_id = new_id()
    baseline_spec = EvaluationSpec(
        run_id=baseline_id,
        experiment_id=experiment_id,
        scenario_id="checkout-api-latency.v1",
        task="Investigate checkout API latency",
    )
    async with postgres_db.begin() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name="PostgreSQL replay race",
                task="Investigate checkout API latency",
                scenario_id="checkout-api-latency.v1",
                execution_mode="fixture",
            )
        )
        await db.flush()
        db.add(
            Run(
                id=baseline_id,
                experiment_id=experiment_id,
                kind="baseline",
                status="failed",
                metrics={},
                evaluation_spec=baseline_spec.model_dump(mode="json"),
            )
        )
        await db.flush()
        db.add(
            Policy(
                id=policy_id,
                experiment_id=experiment_id,
                source_run_id=baseline_id,
                status="candidate",
                patch={"instruction_patch": [], "tool_priority": {}, "max_steps": 6},
                rationale="PostgreSQL replay race",
            )
        )

    original_require_policy = phase1_service.require_policy
    barrier = asyncio.Barrier(2)
    reads = 0

    async def synchronize_policy_reads(db: AsyncSession, requested_id: str):
        nonlocal reads
        result = await original_require_policy(db, requested_id)
        if requested_id == policy_id and reads < 2:
            reads += 1
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return result

    monkeypatch.setattr(phase1_service, "require_policy", synchronize_policy_reads)
    async with postgres_db() as first_db, postgres_db() as second_db:
        results = await asyncio.wait_for(
            asyncio.gather(
                phase1_service.replay_policy(first_db, policy_id),
                phase1_service.replay_policy(second_db, policy_id),
            ),
            timeout=10,
        )

    assert [result.replay_run_id for result in results]
    assert results[0].replay_run_id == results[1].replay_run_id
    async with postgres_db() as db:
        replay_runs = list(
            (
                await db.execute(
                    select(Run).where(Run.policy_id == policy_id, Run.kind == "replay")
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
        stored_policy = await db.get(Policy, policy_id)
    assert len(replay_runs) == len(runner_jobs) == 1
    assert stored_policy is not None
    assert stored_policy.status == "replaying"
    assert stored_policy.replay_run_id == replay_runs[0].id


@pytest.mark.asyncio
async def test_postgres_concurrent_different_policy_activation_is_serialized(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    experiment_id = new_id()
    baseline_id = new_id()
    policy_ids = [new_id(), new_id()]
    replay_ids = [new_id(), new_id()]
    baseline_spec = EvaluationSpec(
        run_id=baseline_id,
        experiment_id=experiment_id,
        scenario_id="checkout-api-latency.v1",
        task="Investigate checkout API latency",
    )
    async with postgres_db.begin() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name="PostgreSQL activation race",
                task="Investigate checkout API latency",
                scenario_id="checkout-api-latency.v1",
                execution_mode="fixture",
            )
        )
        await db.flush()
        db.add(
            Run(
                id=baseline_id,
                experiment_id=experiment_id,
                kind="baseline",
                status="failed",
                metrics={},
                evaluation_spec=baseline_spec.model_dump(mode="json"),
            )
        )
        await db.flush()
        db.add_all(
            [
                Policy(
                    id=policy_ids[index],
                    experiment_id=experiment_id,
                    source_run_id=baseline_id,
                    status="validated",
                    patch={"instruction_patch": [], "tool_priority": {}, "max_steps": 6},
                    rationale=f"Policy {index}",
                    score_delta=1.0,
                )
                for index in range(2)
            ]
        )
        await db.flush()
        db.add_all(
            [
                Run(
                    id=replay_ids[index],
                    experiment_id=experiment_id,
                    kind="replay",
                    source_run_id=baseline_id,
                    policy_id=policy_ids[index],
                    status="succeeded",
                    metrics={},
                    evaluation_spec=baseline_spec.model_copy(
                        update={"run_id": replay_ids[index]}
                    ).model_dump(mode="json"),
                )
                for index in range(2)
            ]
        )
        await db.flush()
        for index, policy_id in enumerate(policy_ids):
            policy = await db.get(Policy, policy_id)
            assert policy is not None
            policy.replay_run_id = replay_ids[index]

    original_require_policy = phase1_service.require_policy
    barrier = asyncio.Barrier(2)
    reads = 0

    async def synchronize_policy_reads(db: AsyncSession, requested_id: str):
        nonlocal reads
        result = await original_require_policy(db, requested_id)
        if requested_id in policy_ids and reads < 2:
            reads += 1
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return result

    monkeypatch.setattr(phase1_service, "require_policy", synchronize_policy_reads)
    async with postgres_db() as first_db, postgres_db() as second_db:
        results = await asyncio.wait_for(
            asyncio.gather(
                phase1_service.activate_policy(first_db, policy_ids[0]),
                phase1_service.activate_policy(second_db, policy_ids[1]),
            ),
            timeout=10,
        )

    assert sorted(result.status.value for result in results) == ["active", "superseded"]
    async with postgres_db() as db:
        statuses = [
            (await db.get(Policy, policy_id)).status for policy_id in policy_ids
        ]
    assert sorted(statuses) == ["active", "superseded"]


async def _wait_for_postgres_blocker(
    factory: async_sessionmaker[AsyncSession], waiting_pid: int, blocking_pid: int
) -> None:
    async with factory() as observer, asyncio.timeout(5):
        while True:
            blockers = (
                await observer.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting_pid}
                )
            ).scalar_one()
            if blocking_pid in blockers:
                return
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_writer", ["completion", "cancellation"])
async def test_postgres_cancel_and_complete_converge_without_duplicate_terminal_effects(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch, first_writer: str
) -> None:
    run_id, lease_id, _ = await _seed_claimed_run(postgres_db)
    first_ready = asyncio.Event()
    release_first = asyncio.Event()
    original_leased_job = phase1_service._leased_job

    async def pause_completion_after_job_lock(*args, **kwargs):
        result = await original_leased_job(*args, **kwargs)
        first_ready.set()
        await asyncio.wait_for(release_first.wait(), timeout=5)
        return result

    async with postgres_db() as cancel_db, postgres_db() as complete_db:
        cancel_pid = (await cancel_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        complete_pid = (await complete_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        if first_writer == "completion":
            monkeypatch.setattr(phase1_service, "_leased_job", pause_completion_after_job_lock)
        else:
            original_commit = cancel_db.commit

            async def pause_cancellation_before_commit():
                first_ready.set()
                await asyncio.wait_for(release_first.wait(), timeout=5)
                await original_commit()

            monkeypatch.setattr(cancel_db, "commit", pause_cancellation_before_commit)

        async def complete():
            return await phase1_service.complete_job(
                complete_db, lease_id=lease_id, runner_id="postgres-runner",
                status="failed", failure_kind=None, metrics=None,
                execution=ExecutionFacts.model_validate(facts(run_id)),
            )

        first_operation = complete if first_writer == "completion" else lambda: phase1_service.cancel_run(cancel_db, run_id)
        second_operation = (lambda: phase1_service.cancel_run(cancel_db, run_id)) if first_writer == "completion" else complete
        tasks = [asyncio.create_task(first_operation())]
        try:
            await asyncio.wait_for(first_ready.wait(), timeout=5)
            tasks.append(asyncio.create_task(second_operation()))
            waiting_pid, blocking_pid = (
                (cancel_pid, complete_pid) if first_writer == "completion" else (complete_pid, cancel_pid)
            )
            await _wait_for_postgres_blocker(postgres_db, waiting_pid, blocking_pid)
        finally:
            release_first.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)

    assert not any(isinstance(result, Exception) for result in results), results
    expected_status = "failed" if first_writer == "completion" else "cancelled"
    if first_writer == "completion":
        assert [result.status for result in results] == ["failed", "failed"]
    else:
        assert results[0].status.value in {"cancelling", "cancelled"}
        assert results[1].status == "cancelled"
    monkeypatch.setattr(phase1_service, "_leased_job", original_leased_job)
    completed_at = results[0 if first_writer == "completion" else 1].run.completed_at
    async with postgres_db() as retry_db:
        repeated = await phase1_service.complete_job(
            retry_db, lease_id=lease_id, runner_id="postgres-runner",
            status="failed", failure_kind=None, metrics=None,
            execution=ExecutionFacts.model_validate(facts(run_id)),
        )
        assert repeated.status == expected_status
        assert repeated.run.completed_at == completed_at

    async with postgres_db() as db:
        run = await db.get(Run, run_id)
        job = await db.get(RunnerJob, run_id)
        assert run is not None and run.status == expected_status
        assert run.completed_at == completed_at and completed_at is not None
        assert job is not None and job.lease_id == lease_id and job.runner_id == "postgres-runner"
        assert (job.cancel_requested_at is not None) == (first_writer == "cancellation")
        assert len((await db.scalars(select(RunAnalysis).where(RunAnalysis.run_id == run_id))).all()) == 1
        policies = list((await db.scalars(select(Policy).where(Policy.source_run_id == run_id))).all())
        terminal = list((await db.scalars(select(RunEvent).where(
            RunEvent.run_id == run_id,
            RunEvent.event_type.in_(["run_completed", "run_failed", "run_cancelled"]),
        ))).all())
        assert len(terminal) == 1
        assert terminal[0].payload["status"] == expected_status
        assert terminal[0].sequence == repeated.terminal_sequence == 2
        assert repeated.run_id == run_id and repeated.attempt == 1
        assert len(policies) == (1 if expected_status == "failed" else 0)
        assert all(policy.status == "candidate" for policy in policies)


@pytest.mark.asyncio
async def test_postgres_queued_cancel_and_claim_follow_the_same_lock_order(
    postgres_db: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    run_id, _, _ = await _seed_claimed_run(postgres_db)
    async with postgres_db.begin() as db:
        run = await db.get(Run, run_id)
        job = await db.get(RunnerJob, run_id)
        run.status = "queued"
        run.claimed_at = None
        job.lease_id = job.runner_id = job.lease_expires_at = None

    claim_ready = asyncio.Event()
    release_claim = asyncio.Event()
    async with postgres_db() as claim_db, postgres_db() as cancel_db:
        claim_pid = (await claim_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        cancel_pid = (await cancel_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        original_execute = claim_db.execute

        async def pause_after_claim_cas(statement, *args, **kwargs):
            result = await original_execute(statement, *args, **kwargs)
            if isinstance(statement, Update) and statement.table.name == "runs" and result.rowcount == 1:
                claim_ready.set()
                await asyncio.wait_for(release_claim.wait(), timeout=5)
            return result

        monkeypatch.setattr(claim_db, "execute", pause_after_claim_cas)
        tasks = [asyncio.create_task(phase1_service.claim_job(claim_db, "claim-runner"))]
        try:
            await asyncio.wait_for(claim_ready.wait(), timeout=5)
            tasks.append(asyncio.create_task(phase1_service.cancel_run(cancel_db, run_id)))
            await _wait_for_postgres_blocker(postgres_db, cancel_pid, claim_pid)
        finally:
            release_claim.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)

    assert not any(isinstance(result, Exception) for result in results), results
    claimed_job, claimed_run = results[0]
    assert claimed_run.id == run_id and claimed_run.status in {"claimed", "cancelling"}
    assert results[1].status.value == "cancelling"
    async with postgres_db() as db:
        job = await db.get(RunnerJob, run_id)
        run = await db.get(Run, run_id)
        assert run.status == "cancelling"
        assert job.cancel_requested_at is not None
        assert job.lease_id == claimed_job.lease_id and job.runner_id == "claim-runner"
    async with postgres_db() as db:
        completed = await phase1_service.complete_job(
            db, lease_id=claimed_job.lease_id, runner_id="claim-runner",
            status="succeeded", failure_kind=None, metrics=None,
            execution=ExecutionFacts.model_validate(facts(run_id)),
        )
        assert completed.status == "cancelled"
