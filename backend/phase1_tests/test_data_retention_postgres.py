from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
    POSTGRES_RETENTION_LOCK_TIMEOUT,
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


def _upgrade_to_head() -> None:
    environment = os.environ.copy()
    environment['DATABASE_URL'] = RETENTION_POSTGRES_URL
    existing_pythonpath = environment.get('PYTHONPATH')
    environment['PYTHONPATH'] = str(BACKEND_DIR)
    if existing_pythonpath:
        environment['PYTHONPATH'] += os.pathsep + existing_pythonpath
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
