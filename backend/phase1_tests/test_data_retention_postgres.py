from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.data_retention import (
    EXECUTE_CONFIRMATION,
    POSTGRES_RETENTION_LOCK,
    POSTGRES_RETENTION_LOCK_TIMEOUT,
    RETENTION_DATABASE_ERROR,
    RETENTION_LOCK_TIMEOUT,
    RETENTION_PLAN_STALE,
    RetentionError,
    RetentionPlanStaleError,
    RetentionUnit,
    execute_retention,
    plan_retention,
)
from app.migrations import application_alembic_head
from app.phase1_models import (
    Experiment,
    Policy,
    Run,
    RunAnalysis,
    RunEvent,
    RunnerAttempt,
    RunnerJob,
)

BACKEND_DIR = Path(__file__).parents[1]
RETENTION_POSTGRES_URL = os.getenv('RETENTION_POSTGRES_URL')
if not RETENTION_POSTGRES_URL:
    pytest.skip('RETENTION_POSTGRES_URL is absent', allow_module_level=True)
if not RETENTION_POSTGRES_URL.startswith('postgresql+asyncpg://'):
    raise RuntimeError('RETENTION_POSTGRES_URL must use postgresql+asyncpg://')

OLD = datetime(2025, 1, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2025, 6, 1, tzinfo=timezone.utc)
COMPLETE_COUNTS = {
    'experiments': 1,
    'runs': 1,
    'run_events': 1,
    'runner_jobs': 1,
    'runner_attempts': 1,
    'run_analyses': 1,
    'policies': 1,
}
RETENTION_TABLES = (
    'experiments, runs, policies, runner_jobs, runner_attempts, '
    'run_events, run_analyses, runner_presence'
)
DELETE_AUDIT_SEQUENCE = 'retention_test_delete_audit_sequence'
DELETE_AUDIT_EARLY_FUNCTION = 'retention_test_audit_run_event_delete'
DELETE_AUDIT_LATE_FUNCTION = 'retention_test_fail_experiment_delete'
DELETE_AUDIT_EARLY_TRIGGER = 'zz_retention_test_audit_run_event_delete'
DELETE_AUDIT_LATE_TRIGGER = 'zz_retention_test_fail_experiment_delete'
DELETE_AUDIT_ORDER_MARKER = 'RETENTION_TEST_DELETE_AUDIT_ORDER_INVALID'
DELETE_AUDIT_FAILURE_MARKER = 'RETENTION_TEST_LATE_DELETE_FAILURE'


def subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    existing_pythonpath = environment.get('PYTHONPATH')
    environment['PYTHONPATH'] = str(BACKEND_DIR)
    if existing_pythonpath:
        environment['PYTHONPATH'] += os.pathsep + existing_pythonpath
    return environment


def _upgrade_to_head() -> None:
    environment = subprocess_environment()
    environment['DATABASE_URL'] = RETENTION_POSTGRES_URL
    subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
        cwd=BACKEND_DIR,
        env=environment,
        check=True,
        timeout=90,
    )


@pytest_asyncio.fixture(name='session_factory')
async def postgres_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    _upgrade_to_head()
    engine = create_async_engine(RETENTION_POSTGRES_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            server_version = int(
                (await connection.execute(text('SHOW server_version_num'))).scalar_one()
            )
            assert 160000 <= server_version < 170000, server_version
            revision = str(
                (
                    await connection.execute(
                        text('SELECT version_num FROM alembic_version')
                    )
                ).scalar_one()
            )
            assert revision == application_alembic_head()
            await connection.execute(
                text(f'TRUNCATE TABLE {RETENTION_TABLES} RESTART IDENTITY CASCADE')
            )
        yield async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    finally:
        await engine.dispose()


async def add_experiment(
    factory: async_sessionmaker[AsyncSession],
    *,
    experiment_id: str,
    run_id: str,
    run_status: str = 'failed',
    completed_at: datetime | None = OLD,
    policy_status: str | None = None,
    policy_id: str | None = None,
    with_evidence: bool = True,
) -> str | None:
    async with factory.begin() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name=f'name-{experiment_id}',
                task=f'task-{experiment_id}',
                scenario_id='checkout-api-latency',
                execution_mode='fixture',
                created_at=OLD,
            )
        )
        await db.flush()
        db.add(
            Run(
                id=run_id,
                experiment_id=experiment_id,
                kind='baseline',
                source_run_id=None,
                policy_id=None,
                status=run_status,
                score=0.0,
                metrics={},
                evaluation_spec={'execution_mode': 'fixture'},
                error='terminal' if run_status == 'failed' else None,
                queued_at=OLD,
                claimed_at=OLD,
                started_at=OLD,
                completed_at=completed_at,
            )
        )
        await db.flush()
        db.add(RunnerJob(run_id=run_id, attempt=1))
        if with_evidence:
            db.add_all(
                [
                    RunEvent(
                        run_id=run_id,
                        sequence=1,
                        event_type='run_failed',
                        payload={'content': 'retention-test-event'},
                        occurred_at=OLD,
                    ),
                    RunnerAttempt(
                        run_id=run_id,
                        attempt=1,
                        lease_id=f'lease-{run_id}',
                        runner_id=f'runner-{run_id}',
                        lease_expires_at=OLD,
                        recovery_reason='lease_expired',
                        outcome='failed',
                        recorded_at=OLD,
                    ),
                    RunAnalysis(
                        run_id=run_id,
                        dimensions={'planning': 1.0},
                        evidence=[{'source': 'retention-test'}],
                        dominant_type='planning',
                        failure_rate=1.0,
                    ),
                ]
            )
        if policy_status is None:
            return None
        policy = Policy(
            id=policy_id or f'{experiment_id}-policy',
            experiment_id=experiment_id,
            source_run_id=run_id,
            parent_policy_id=None,
            replay_run_id=None,
            status=policy_status,
            patch={'max_steps': 6},
            rationale='retention integration fixture',
            score_delta=None,
            reject_reason='fixture' if policy_status == 'rejected' else None,
            created_at=OLD,
        )
        db.add(policy)
        await db.flush()
        return policy.id


async def aggregate_counts(
    db: AsyncSession, experiment_id: str, run_ids: tuple[str, ...]
) -> dict[str, int]:
    async def count(statement) -> int:
        return int((await db.execute(statement)).scalar_one())

    return {
        'experiments': await count(
            select(func.count())
            .select_from(Experiment)
            .where(Experiment.id == experiment_id)
        ),
        'runs': await count(
            select(func.count())
            .select_from(Run)
            .where(Run.experiment_id == experiment_id)
        ),
        'run_events': await count(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.run_id.in_(run_ids))
        ),
        'runner_jobs': await count(
            select(func.count())
            .select_from(RunnerJob)
            .where(RunnerJob.run_id.in_(run_ids))
        ),
        'runner_attempts': await count(
            select(func.count())
            .select_from(RunnerAttempt)
            .where(RunnerAttempt.run_id.in_(run_ids))
        ),
        'run_analyses': await count(
            select(func.count())
            .select_from(RunAnalysis)
            .where(RunAnalysis.run_id.in_(run_ids))
        ),
        'policies': await count(
            select(func.count())
            .select_from(Policy)
            .where(Policy.experiment_id == experiment_id)
        ),
    }


async def execute_plan_file(plan_path: Path) -> tuple[int, str, str]:
    environment = subprocess_environment()
    environment['RETENTION_POSTGRES_URL'] = RETENTION_POSTGRES_URL
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'app.data_retention',
        '--database-url-env',
        'RETENTION_POSTGRES_URL',
        'execute',
        '--plan-file',
        str(plan_path),
        '--confirm',
        EXECUTE_CONFIRMATION,
        cwd=BACKEND_DIR,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=15
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise
    return process.returncode, stdout.decode(), stderr.decode()


def assert_sanitized_operator_failure(
    result: tuple[int, str, str], expected_message: str, *markers: str
) -> None:
    returncode, stdout, stderr = result
    surfaced = stdout + stderr
    assert returncode == 1
    assert stdout == ''
    assert stderr == f'data retention failed: {expected_message}\n'
    for forbidden in (
        'asyncpg',
        'postgresql',
        'lock table',
        POSTGRES_RETENTION_LOCK,
        *RETENTION_TABLES.replace(',', '').split(),
        *markers,
    ):
        assert forbidden.lower() not in surfaced.lower()


async def install_failure_audit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory.begin() as db:
        await db.execute(
            text(
                f'''CREATE SEQUENCE {DELETE_AUDIT_SEQUENCE}
START WITH 1 INCREMENT BY 1 NO CYCLE'''
            )
        )
        await db.execute(
            text(
                f'''CREATE FUNCTION {DELETE_AUDIT_EARLY_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM nextval('{DELETE_AUDIT_SEQUENCE}');
    RETURN OLD;
END;
$$'''
            )
        )
        await db.execute(
            text(
                f'''CREATE TRIGGER {DELETE_AUDIT_EARLY_TRIGGER}
AFTER DELETE ON run_events
FOR EACH ROW EXECUTE FUNCTION {DELETE_AUDIT_EARLY_FUNCTION}()'''
            )
        )
        await db.execute(
            text(
                f'''CREATE FUNCTION {DELETE_AUDIT_LATE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    audit_step bigint;
BEGIN
    audit_step := nextval('{DELETE_AUDIT_SEQUENCE}');
    IF audit_step <> 3 THEN
        RAISE EXCEPTION '{DELETE_AUDIT_ORDER_MARKER}';
    END IF;
    RAISE EXCEPTION '{DELETE_AUDIT_FAILURE_MARKER}';
    RETURN OLD;
END;
$$'''
            )
        )
        await db.execute(
            text(
                f'''CREATE CONSTRAINT TRIGGER {DELETE_AUDIT_LATE_TRIGGER}
AFTER DELETE ON experiments
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION {DELETE_AUDIT_LATE_FUNCTION}()'''
            )
        )


async def drop_failure_audit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory.begin() as db:
        await db.execute(
            text(
                f'''DROP TRIGGER IF EXISTS {DELETE_AUDIT_LATE_TRIGGER}
ON experiments'''
            )
        )
        await db.execute(
            text(
                f'''DROP TRIGGER IF EXISTS {DELETE_AUDIT_EARLY_TRIGGER}
ON run_events'''
            )
        )
        await db.execute(
            text(f'DROP FUNCTION IF EXISTS {DELETE_AUDIT_LATE_FUNCTION}()')
        )
        await db.execute(
            text(f'DROP FUNCTION IF EXISTS {DELETE_AUDIT_EARLY_FUNCTION}()')
        )
        await db.execute(
            text(f'DROP SEQUENCE IF EXISTS {DELETE_AUDIT_SEQUENCE}')
        )


async def delete_audit_state(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[int, bool]:
    async with factory() as db:
        last_value, is_called = (
            await db.execute(
                text(
                    f'''SELECT last_value, is_called
FROM {DELETE_AUDIT_SEQUENCE}'''
                )
            )
        ).one()
    return int(last_value), bool(is_called)


async def delete_audit_object_counts(
    factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    async with factory() as db:
        sequence_count = int(
            (
                await db.execute(
                    text(
                        '''SELECT count(*) FROM pg_class
WHERE relkind = 'S' AND relname = :sequence'''
                    ),
                    {'sequence': DELETE_AUDIT_SEQUENCE},
                )
            ).scalar_one()
        )
        function_count = int(
            (
                await db.execute(
                    text(
                        '''SELECT count(*) FROM pg_proc
WHERE proname IN (:early_function, :late_function)'''
                    ),
                    {
                        'early_function': DELETE_AUDIT_EARLY_FUNCTION,
                        'late_function': DELETE_AUDIT_LATE_FUNCTION,
                    },
                )
            ).scalar_one()
        )
        trigger_count = int(
            (
                await db.execute(
                    text(
                        '''SELECT count(*) FROM pg_trigger
WHERE tgname IN (:early_trigger, :late_trigger)'''
                    ),
                    {
                        'early_trigger': DELETE_AUDIT_EARLY_TRIGGER,
                        'late_trigger': DELETE_AUDIT_LATE_TRIGGER,
                    },
                )
            ).scalar_one()
        )
    return {
        'sequences': sequence_count,
        'functions': function_count,
        'triggers': trigger_count,
    }


@pytest.mark.asyncio
async def test_postgres_execute_deletes_the_complete_aggregate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_experiment = 'cascade-target'
    target_run = 'cascade-target-run'
    retained_experiment = 'cascade-retained'
    retained_run = 'cascade-retained-run'
    target_policy = await add_experiment(
        session_factory,
        experiment_id=target_experiment,
        run_id=target_run,
        policy_status='superseded',
        policy_id='cascade-target-policy',
    )
    await add_experiment(
        session_factory,
        experiment_id=retained_experiment,
        run_id=retained_run,
        run_status='running',
        completed_at=None,
        with_evidence=False,
    )
    assert target_policy == 'cascade-target-policy'

    async with session_factory() as planner:
        plan = await plan_retention(planner, CUTOFF, limit=1)
    assert plan.units == (RetentionUnit(target_experiment, 1, 1, 1, 1, 1, 1),)

    async with session_factory() as executor:
        report = await execute_retention(
            executor, plan, confirmation=EXECUTE_CONFIRMATION
        )
    assert report.units == plan.units
    assert report.plan_digest == plan.digest

    async with session_factory() as db:
        assert await aggregate_counts(db, target_experiment, (target_run,)) == {
            key: 0 for key in COMPLETE_COUNTS
        }
        assert await db.get(Experiment, retained_experiment) is not None
        assert await db.get(Run, retained_run) is not None


@pytest.mark.asyncio
async def test_postgres_stale_plan_after_committed_cross_experiment_policy_reference(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_experiment = 'policy-target'
    target_run = 'policy-target-run'
    external_experiment = 'policy-external'
    external_run = 'policy-external-run'
    target_policy = await add_experiment(
        session_factory,
        experiment_id=target_experiment,
        run_id=target_run,
        policy_status='rejected',
        policy_id='policy-target-policy',
    )
    await add_experiment(
        session_factory,
        experiment_id=external_experiment,
        run_id=external_run,
        run_status='running',
        completed_at=None,
        with_evidence=False,
    )
    assert target_policy == 'policy-target-policy'

    async with session_factory() as planner:
        plan = await plan_retention(planner, CUTOFF, limit=1)
    assert plan.experiment_ids == (target_experiment,)

    async with session_factory.begin() as writer:
        await writer.execute(
            update(Run)
            .where(Run.id == external_run)
            .values(policy_id=target_policy)
        )

    async with session_factory() as executor:
        with pytest.raises(RetentionPlanStaleError) as raised:
            await execute_retention(
                executor, plan, confirmation=EXECUTE_CONFIRMATION
            )
    assert str(raised.value).startswith(RETENTION_PLAN_STALE)

    async with session_factory() as db:
        assert await aggregate_counts(
            db, target_experiment, (target_run,)
        ) == COMPLETE_COUNTS
        external = await db.get(Run, external_run)
        assert external is not None
        assert external.policy_id == target_policy


@pytest.mark.parametrize('protected_status', ['candidate', 'active'])
@pytest.mark.asyncio
async def test_postgres_stale_plan_after_committed_protected_policy(
    session_factory: async_sessionmaker[AsyncSession], protected_status: str
) -> None:
    target_experiment = f'protected-{protected_status}'
    target_run = f'protected-{protected_status}-run'
    existing_policy = await add_experiment(
        session_factory,
        experiment_id=target_experiment,
        run_id=target_run,
        policy_status='rejected',
        policy_id=f'protected-existing-{protected_status}',
    )
    assert existing_policy == f'protected-existing-{protected_status}'

    async with session_factory() as planner:
        plan = await plan_retention(planner, CUTOFF, limit=1)
    assert plan.experiment_ids == (target_experiment,)

    async with session_factory.begin() as writer:
        writer.add(
            Policy(
                id=f'protected-new-{protected_status}',
                experiment_id=target_experiment,
                source_run_id=target_run,
                parent_policy_id=None,
                replay_run_id=None,
                status=protected_status,
                patch={'max_steps': 6},
                rationale='created after retention review',
                score_delta=1.0 if protected_status == 'active' else None,
                reject_reason=None,
                created_at=OLD,
            )
        )

    async with session_factory() as executor:
        with pytest.raises(RetentionPlanStaleError) as raised:
            await execute_retention(
                executor, plan, confirmation=EXECUTE_CONFIRMATION
            )
    assert str(raised.value).startswith(RETENTION_PLAN_STALE)

    async with session_factory() as db:
        counts = await aggregate_counts(db, target_experiment, (target_run,))
        assert counts == {**COMPLETE_COUNTS, 'policies': 2}


@pytest.mark.asyncio
async def test_postgres_lock_timeout_is_sanitized_and_atomic(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    target_experiment = 'lock-target'
    target_run = 'lock-target-run'
    protected_experiment = 'lock-protected'
    protected_run = 'lock-protected-run'
    await add_experiment(
        session_factory,
        experiment_id=target_experiment,
        run_id=target_run,
        policy_status='superseded',
        policy_id='lock-target-policy',
    )
    await add_experiment(
        session_factory,
        experiment_id=protected_experiment,
        run_id=protected_run,
        run_status='running',
        completed_at=None,
        policy_status='active',
        policy_id='lock-protected-policy',
    )

    async with session_factory() as planner:
        plan = await plan_retention(planner, CUTOFF, limit=1)
    assert plan.experiment_ids == (target_experiment,)
    plan_path = tmp_path / 'lock-timeout-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')

    async with session_factory() as blocker:
        await blocker.begin()
        try:
            await blocker.execute(
                text('LOCK TABLE experiments IN ROW EXCLUSIVE MODE')
            )
            result = await execute_plan_file(plan_path)
        finally:
            await blocker.rollback()

    assert_sanitized_operator_failure(
        result,
        f'{RETENTION_LOCK_TIMEOUT}: maintenance lock was not available',
    )
    async with session_factory() as db:
        assert await aggregate_counts(
            db, target_experiment, (target_run,)
        ) == COMPLETE_COUNTS
        assert await aggregate_counts(
            db, protected_experiment, (protected_run,)
        ) == COMPLETE_COUNTS
        assert await db.get(Policy, 'lock-protected-policy') is not None


@pytest.mark.asyncio
async def test_postgres_late_trigger_failure_rolls_back_the_entire_plan_and_is_sanitized(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    target_experiments = ('rollback-target-a', 'rollback-target-b')
    target_runs = ('rollback-target-a-run', 'rollback-target-b-run')
    for experiment_id, run_id in zip(target_experiments, target_runs):
        await add_experiment(
            session_factory,
            experiment_id=experiment_id,
            run_id=run_id,
            policy_status='superseded',
            policy_id=f'{experiment_id}-policy',
        )

    try:
        await install_failure_audit(session_factory)
        async with session_factory() as planner:
            plan = await plan_retention(planner, CUTOFF, limit=2)
        assert plan.experiment_ids == target_experiments
        plan_path = tmp_path / 'trigger-failure-plan.json'
        plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
        result = await execute_plan_file(plan_path)
        assert_sanitized_operator_failure(
            result,
            f'{RETENTION_DATABASE_ERROR}: retention database operation failed',
            DELETE_AUDIT_FAILURE_MARKER,
            DELETE_AUDIT_ORDER_MARKER,
            DELETE_AUDIT_SEQUENCE,
            DELETE_AUDIT_EARLY_FUNCTION,
            DELETE_AUDIT_LATE_FUNCTION,
            DELETE_AUDIT_EARLY_TRIGGER,
            DELETE_AUDIT_LATE_TRIGGER,
            'CREATE TRIGGER',
            'CREATE CONSTRAINT TRIGGER',
            'CREATE FUNCTION',
            'nextval',
        )
        # Two child aggregate hooks are steps 1-2; deferred Experiment failure is 3.
        assert await delete_audit_state(session_factory) == (3, True)
        async with session_factory() as db:
            for experiment_id, run_id in zip(target_experiments, target_runs):
                assert (
                    await aggregate_counts(db, experiment_id, (run_id,))
                    == COMPLETE_COUNTS
                )
    finally:
        await drop_failure_audit(session_factory)

    assert await delete_audit_object_counts(session_factory) == {
        'sequences': 0,
        'functions': 0,
        'triggers': 0,
    }
