from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data_retention import (
    EXECUTE_CONFIRMATION,
    RETENTION_CONFIRMATION_INVALID,
    RETENTION_DATABASE_ERROR,
    RETENTION_LOCK_CONFLICT,
    RETENTION_LOCK_TIMEOUT,
    RETENTION_PLAN_INVALID,
    RETENTION_STATEMENT_TIMEOUT,
    POSTGRES_RETENTION_LOCK,
    POSTGRES_RETENTION_LOCK_TIMEOUT_STATEMENT,
    POSTGRES_RETENTION_STATEMENT_TIMEOUT_STATEMENT,
    RetentionError,
    RetentionPlan,
    RetentionPlanStaleError,
    RetentionUnit,
    _acquire_retention_maintenance_lock,
    _load_plan_file,
    _parser,
    _retention_database_error,
    execute_retention,
    main,
    plan_retention,
    validate_limit,
    validate_terminal_before,
)
from app.phase1_models import (
    Experiment,
    Policy,
    Run,
    RunAnalysis,
    RunEvent,
    RunnerAttempt,
    RunnerJob,
)


OLD = datetime(2025, 1, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2025, 6, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def add_experiment(
    factory,
    *,
    label: str,
    run_status: str = "failed",
    completed_at: datetime | None = OLD,
    policy_status: str | None = None,
    with_evidence: bool = False,
) -> tuple[str, str, str | None]:
    async with factory() as db:
        experiment = Experiment(
            name=f"name-secret-{label}",
            task=f"task-secret-{label}",
            scenario_id="checkout-api-latency.v1",
            execution_mode="fixture",
            created_at=OLD,
        )
        db.add(experiment)
        await db.flush()
        run = Run(
            experiment_id=experiment.id,
            kind="baseline",
            source_run_id=None,
            policy_id=None,
            status=run_status,
            score=0.0,
            metrics={},
            evaluation_spec={"execution_mode": "fixture"},
            error="terminal" if run_status == "failed" else None,
            queued_at=OLD,
            claimed_at=OLD,
            started_at=OLD,
            completed_at=completed_at,
        )
        db.add(run)
        await db.flush()
        db.add(RunnerJob(run_id=run.id, attempt=1))
        policy_id = None
        if policy_status is not None:
            policy = Policy(
                experiment_id=experiment.id,
                source_run_id=run.id,
                parent_policy_id=None,
                replay_run_id=None,
                status=policy_status,
                patch={"max_steps": 6},
                rationale="retention fixture",
                score_delta=None,
                reject_reason=None,
                created_at=OLD,
            )
            db.add(policy)
            await db.flush()
            policy_id = policy.id
        if with_evidence:
            db.add_all(
                [
                    RunEvent(
                        run_id=run.id,
                        sequence=1,
                        event_type="run_failed",
                        payload={"content": f"event-secret-{label}"},
                        occurred_at=OLD,
                    ),
                    RunnerAttempt(
                        run_id=run.id,
                        attempt=1,
                        lease_id=f"lease-{label}",
                        runner_id=f"runner-{label}",
                        lease_expires_at=OLD,
                        recovery_reason="lease_expired",
                        outcome="failed",
                        recorded_at=OLD,
                    ),
                    RunAnalysis(
                        run_id=run.id,
                        dimensions={"planning": 1.0},
                        evidence=[{"secret": f"analysis-secret-{label}"}],
                        dominant_type="planning",
                        failure_rate=1.0,
                    ),
                ]
            )
        await db.commit()
        return experiment.id, run.id, policy_id


def test_retention_inputs_require_safe_bounds() -> None:
    with pytest.raises(RetentionError, match="timezone"):
        validate_terminal_before(datetime(2025, 1, 1), now=NOW)
    with pytest.raises(RetentionError, match="future"):
        validate_terminal_before(NOW + timedelta(seconds=1), now=NOW)
    assert validate_terminal_before(OLD, now=NOW) == OLD
    for invalid in (0, 501, True):
        with pytest.raises(RetentionError, match="limit"):
            validate_limit(invalid)


class RecordingSession:
    def __init__(self, dialect_name: str) -> None:
        self._bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name)
        )
        self.statements: list[str] = []

    def get_bind(self) -> SimpleNamespace:
        return self._bind

    async def execute(self, statement, parameters=None) -> None:
        self.statements.append(str(statement))


@pytest.mark.asyncio
async def test_maintenance_lock_is_postgres_only_and_table_scoped() -> None:
    sqlite = RecordingSession("sqlite")
    await _acquire_retention_maintenance_lock(sqlite)  # type: ignore[arg-type]
    assert sqlite.statements == []

    postgres = RecordingSession("postgresql")
    await _acquire_retention_maintenance_lock(postgres)  # type: ignore[arg-type]
    assert postgres.statements == [
        POSTGRES_RETENTION_LOCK_TIMEOUT_STATEMENT,
        POSTGRES_RETENTION_STATEMENT_TIMEOUT_STATEMENT,
        POSTGRES_RETENTION_LOCK,
    ]


@pytest.mark.asyncio
async def test_execute_rechecks_candidates_after_maintenance_lock(
    api, monkeypatch
) -> None:
    _, factory = api
    order: list[object] = []

    async def record_lock(db) -> None:
        order.append("lock")

    async def record_candidates(db, terminal_before, limit, *, lock):
        order.append(("candidates", lock))
        return []

    monkeypatch.setattr(
        "app.data_retention._acquire_retention_maintenance_lock", record_lock
    )
    monkeypatch.setattr(
        "app.data_retention._eligible_experiments", record_candidates
    )
    reviewed_plan = RetentionPlan.from_units(CUTOFF, 1, ())
    async with factory() as db:
        await execute_retention(
            db, reviewed_plan, confirmation=EXECUTE_CONFIRMATION
        )
    assert order == ["lock", ("candidates", True)]


@pytest.mark.asyncio
async def test_plan_is_bounded_content_free_and_has_no_side_effect(api) -> None:
    _, factory = api
    experiment_id, _, _ = await add_experiment(
        factory,
        label="dry-run",
        policy_status="rejected",
        with_evidence=True,
    )
    async with factory() as db:
        report = await plan_retention(db, CUTOFF, limit=1)
    payload = report.as_dict()
    serialized = json.dumps(payload)
    assert payload["mode"] == "plan"
    assert payload["unit_count"] == 1
    assert payload["units"][0]["experiment_id"] == experiment_id
    assert payload["units"][0]["row_counts"] == {
        "runs": 1,
        "run_events": 1,
        "runner_jobs": 1,
        "runner_attempts": 1,
        "run_analyses": 1,
        "policies": 1,
    }
    assert "name-secret" not in serialized
    assert "task-secret" not in serialized
    assert "event-secret" not in serialized
    assert "analysis-secret" not in serialized
    async with factory() as db:
        assert await db.get(Experiment, experiment_id) is not None


def test_retention_plan_digest_is_canonical_and_tamper_evident() -> None:
    units = (
        RetentionUnit("experiment-z", 1, 2, 1, 0, 0, 0),
        RetentionUnit("experiment-a", 1, 0, 1, 0, 0, 0),
    )
    plan = RetentionPlan.from_units(
        CUTOFF,
        2,
        units,
        {"protected_policy": 3},
    )
    reversed_plan = RetentionPlan.from_units(
        CUTOFF,
        2,
        tuple(reversed(units)),
        {"protected_policy": 3},
    )
    assert plan.experiment_ids == ("experiment-a", "experiment-z")
    assert plan.units == tuple(sorted(units, key=lambda unit: unit.experiment_id))
    assert plan.digest == reversed_plan.digest
    assert plan.digest.startswith("sha256:")
    assert RetentionPlan.from_dict(json.loads(json.dumps(plan.as_dict()))) == plan

    tampered = json.loads(json.dumps(plan.as_dict()))
    tampered["units"][0]["row_counts"]["runs"] += 1
    with pytest.raises(RetentionError, match=RETENTION_PLAN_INVALID):
        RetentionPlan.from_dict(tampered)


def test_retention_plan_file_and_cli_serialization(tmp_path) -> None:
    plan = RetentionPlan.from_units(CUTOFF, 1, ())
    path = tmp_path / "retention-plan.json"
    path.write_text(json.dumps(plan.as_dict()), encoding="utf-8")

    assert _load_plan_file(path) == plan
    execute_args = _parser().parse_args(
        [
            "execute",
            "--plan-file",
            str(path),
            "--confirm",
            EXECUTE_CONFIRMATION,
        ]
    )
    assert execute_args.plan_file == path
    assert execute_args.confirm == EXECUTE_CONFIRMATION
    plan_args = _parser().parse_args(
        [
            "plan",
            "--terminal-before",
            "2025-06-01T00:00:00Z",
            "--limit",
            "2",
        ]
    )
    assert plan_args.limit == 2
    assert plan_args.terminal_before == CUTOFF


def assert_cli_failure(capsys, expected_message: str, *forbidden: str) -> None:
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == f'data retention failed: {expected_message}\n'
    for value in (*forbidden, 'Traceback'):
        assert value not in captured.err


def test_cli_plan_database_failure_hides_database_details(
    monkeypatch, capsys
) -> None:
    marker = 'PLAN_DATABASE_MALICIOUS_MARKER'
    raw_sql = f'SELECT {marker} FROM retention_secrets WHERE token = :token'
    credentials = 'postgresql+asyncpg://attacker:secret@database/retention'
    raw_params = {'token': credentials}

    class InjectedDatabaseFailure(Exception):
        sqlstate = 'P0001'

    async def fail_plan_query(*_args, **_kwargs) -> None:
        raise OperationalError(
            raw_sql,
            raw_params,
            InjectedDatabaseFailure(marker),
        )

    monkeypatch.setenv('RETENTION_TEST_DATABASE_URL', 'sqlite+aiosqlite://')
    monkeypatch.setattr(
        'app.data_retention._eligible_experiments', fail_plan_query
    )
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'data-retention',
            '--database-url-env',
            'RETENTION_TEST_DATABASE_URL',
            'plan',
            '--terminal-before',
            '2025-06-01T00:00:00Z',
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 1
    assert_cli_failure(
        capsys,
        f'{RETENTION_DATABASE_ERROR}: retention database operation failed',
        marker,
        raw_sql,
        str(raw_params),
        credentials,
        'InjectedDatabaseFailure',
        'OperationalError',
    )


@pytest.mark.parametrize(
    'failure_kind',
    ('missing', 'invalid_utf8', 'invalid_json'),
)
def test_cli_plan_file_failures_hide_input_details(
    tmp_path, monkeypatch, capsys, failure_kind: str
) -> None:
    marker = f'PLAN_FILE_{failure_kind.upper()}_MALICIOUS_MARKER'
    plan_path = tmp_path / f'{marker}.json'
    forbidden = [marker, str(plan_path)]
    if failure_kind == 'invalid_utf8':
        raw_bytes = b'\xff' + marker.encode('ascii')
        plan_path.write_bytes(raw_bytes)
        forbidden.extend((repr(raw_bytes), '0xff', 'invalid start byte'))
    elif failure_kind == 'invalid_json':
        raw_json = f'[{marker}'
        plan_path.write_text(raw_json, encoding='utf-8')
        forbidden.append(raw_json)
    else:
        forbidden.append('No such file')

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'data-retention',
            'execute',
            '--plan-file',
            str(plan_path),
            '--confirm',
            EXECUTE_CONFIRMATION,
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 1
    assert_cli_failure(
        capsys,
        f'{RETENTION_PLAN_INVALID}: unable to load plan file',
        *forbidden,
    )


def test_cli_invalid_confirmation_hides_both_tokens(
    tmp_path, monkeypatch, capsys
) -> None:
    plan = RetentionPlan.from_units(CUTOFF, 1, ())
    plan_path = tmp_path / 'confirmation-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
    marker = 'INVALID_CONFIRMATION_MALICIOUS_MARKER'
    monkeypatch.setenv('RETENTION_TEST_DATABASE_URL', 'sqlite+aiosqlite://')
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'data-retention',
            '--database-url-env',
            'RETENTION_TEST_DATABASE_URL',
            'execute',
            '--plan-file',
            str(plan_path),
            '--confirm',
            marker,
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 1
    assert_cli_failure(
        capsys,
        f'{RETENTION_CONFIRMATION_INVALID}: confirmation token is invalid',
        marker,
        EXECUTE_CONFIRMATION,
    )


def test_retention_database_timeout_mapping() -> None:
    class PostgresError:
        def __init__(self, sqlstate: str) -> None:
            self.sqlstate = sqlstate

    for sqlstate, code in (
        ("55P03", RETENTION_LOCK_TIMEOUT),
        ("57014", RETENTION_STATEMENT_TIMEOUT),
        ("40P01", RETENTION_LOCK_CONFLICT),
    ):
        mapped = _retention_database_error(
            OperationalError("retention", {}, PostgresError(sqlstate))
        )
        assert mapped is not None
        assert str(mapped).startswith(code)


def test_retention_database_error_mapping_hides_database_details() -> None:
    class PostgresError:
        sqlstate = 'P0001'

    raw_sql = 'DELETE FROM run_events WHERE payload = RETENTION_TEST_MARKER'
    mapped = _retention_database_error(
        OperationalError(
            raw_sql,
            {'marker': 'RETENTION_TEST_MARKER'},
            PostgresError(),
        )
    )

    assert mapped is not None
    assert str(mapped) == (
        f'{RETENTION_DATABASE_ERROR}: retention database operation failed'
    )
    assert raw_sql not in str(mapped)
    assert 'RETENTION_TEST_MARKER' not in str(mapped)


@pytest.mark.asyncio
async def test_execute_requires_exact_confirmation(api) -> None:
    _, factory = api
    experiment_id, _, _ = await add_experiment(factory, label="confirm")
    async with factory() as db:
        plan = await plan_retention(db, CUTOFF, limit=1)
        with pytest.raises(RetentionError, match="confirmation token"):
            await execute_retention(db, plan, confirmation="delete")
    async with factory() as db:
        assert await db.get(Experiment, experiment_id) is not None


@pytest.mark.asyncio
async def test_nonterminal_recent_and_protected_units_are_blocked(api) -> None:
    _, factory = api
    nonterminal, _, _ = await add_experiment(
        factory, label="nonterminal", run_status="running", completed_at=None
    )
    recent, _, _ = await add_experiment(
        factory,
        label="recent",
        completed_at=datetime(2025, 7, 1, tzinfo=timezone.utc),
    )
    protected, _, _ = await add_experiment(
        factory, label="protected", policy_status="active"
    )
    async with factory() as db:
        report = await plan_retention(db, CUTOFF)
    ids = {unit.experiment_id for unit in report.units}
    assert {nonterminal, recent, protected}.isdisjoint(ids)
    assert report.blocked_reason_counts["nonterminal_run"] == 1
    assert report.blocked_reason_counts["terminal_after_cutoff"] == 1
    assert report.blocked_reason_counts["protected_policy"] == 1


@pytest.mark.parametrize(
    "reference_kind",
    [
        "run_source",
        "run_policy",
        "policy_source",
        "policy_replay",
        "policy_parent",
    ],
)
@pytest.mark.asyncio
async def test_cross_experiment_references_block_deletion(
    api, reference_kind: str
) -> None:
    _, factory = api
    target_experiment, target_run, target_policy = await add_experiment(
        factory, label=f"target-{reference_kind}", policy_status="rejected"
    )
    external_experiment, external_run, _ = await add_experiment(
        factory,
        label=f"external-{reference_kind}",
        run_status="running",
        completed_at=None,
    )
    assert target_policy is not None
    async with factory() as db:
        run = await db.get(Run, external_run)
        assert run is not None
        if reference_kind == "run_source":
            run.source_run_id = target_run
        elif reference_kind == "run_policy":
            run.policy_id = target_policy
        else:
            policy = Policy(
                experiment_id=external_experiment,
                source_run_id=(
                    target_run if reference_kind == "policy_source" else external_run
                ),
                parent_policy_id=(
                    target_policy if reference_kind == "policy_parent" else None
                ),
                replay_run_id=(
                    target_run if reference_kind == "policy_replay" else None
                ),
                status="rejected",
                patch={"max_steps": 6},
                rationale="cross aggregate fixture",
                score_delta=None,
                reject_reason="fixture",
                created_at=OLD,
            )
            db.add(policy)
        await db.commit()

    async with factory() as db:
        report = await plan_retention(db, CUTOFF)
    assert target_experiment not in {unit.experiment_id for unit in report.units}
    assert report.blocked_reason_counts["cross_experiment_reference"] >= 1


@pytest.mark.asyncio
async def test_execute_revalidates_a_stale_plan(api) -> None:
    _, factory = api
    experiment_id, run_id, _ = await add_experiment(factory, label="stale")
    async with factory() as db:
        plan = await plan_retention(db, CUTOFF)
    assert experiment_id in {unit.experiment_id for unit in plan.units}
    async with factory() as db:
        db.add(
            Policy(
                experiment_id=experiment_id,
                source_run_id=run_id,
                parent_policy_id=None,
                replay_run_id=None,
                status="validated",
                patch={"max_steps": 6},
                rationale="created after dry-run",
                score_delta=1.0,
                reject_reason=None,
                created_at=OLD,
            )
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(
            RetentionPlanStaleError, match="RETENTION_PLAN_STALE"
        ):
            await execute_retention(
                db, plan, confirmation=EXECUTE_CONFIRMATION
            )
    async with factory() as db:
        assert await db.get(Experiment, experiment_id) is not None


@pytest.mark.asyncio
async def test_stale_plan_aborts_atomically_when_new_candidate_appears(api) -> None:
    _, factory = api
    first_experiment, _, _ = await add_experiment(factory, label="planned")
    async with factory() as db:
        plan = await plan_retention(db, CUTOFF, limit=2)
    second_experiment, _, _ = await add_experiment(factory, label="unplanned")

    async with factory() as db:
        with pytest.raises(
            RetentionPlanStaleError, match="RETENTION_PLAN_STALE"
        ):
            await execute_retention(
                db, plan, confirmation=EXECUTE_CONFIRMATION
            )
    async with factory() as db:
        assert await db.get(Experiment, first_experiment) is not None
        assert await db.get(Experiment, second_experiment) is not None


@pytest.mark.asyncio
async def test_stale_plan_aborts_when_aggregate_row_counts_change(api) -> None:
    _, factory = api
    experiment_id, run_id, _ = await add_experiment(factory, label="count-stale")
    async with factory() as db:
        plan = await plan_retention(db, CUTOFF)
    async with factory() as db:
        db.add(
            RunEvent(
                run_id=run_id,
                sequence=1,
                event_type="run_failed",
                payload={"content": "count-change"},
                occurred_at=OLD,
            )
        )
        await db.commit()

    async with factory() as db:
        with pytest.raises(
            RetentionPlanStaleError, match="RETENTION_PLAN_STALE"
        ):
            await execute_retention(
                db, plan, confirmation=EXECUTE_CONFIRMATION
            )
    async with factory() as db:
        assert await db.get(Experiment, experiment_id) is not None
        assert (
            int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(RunEvent)
                        .where(RunEvent.run_id == run_id)
                    )
                ).scalar_one()
            )
            == 1
        )


@pytest.mark.asyncio
async def test_execute_rolls_back_after_delete_failure(api, monkeypatch) -> None:
    _, factory = api
    first_experiment, first_run, first_policy = await add_experiment(
        factory,
        label='rollback-a',
        policy_status='superseded',
        with_evidence=True,
    )
    second_experiment, second_run, second_policy = await add_experiment(
        factory,
        label='rollback-b',
        policy_status='superseded',
        with_evidence=True,
    )
    assert first_policy is not None
    assert second_policy is not None

    async with factory() as db:
        plan = await plan_retention(db, CUTOFF, limit=2)
    assert set(plan.experiment_ids) == {first_experiment, second_experiment}

    original_delete = AsyncSession.delete

    async def delete_then_fail(session, instance) -> None:
        await original_delete(session, instance)
        await session.flush()
        raise RuntimeError('forced retention delete failure')

    monkeypatch.setattr(AsyncSession, 'delete', delete_then_fail)
    async with factory() as db:
        with pytest.raises(
            RuntimeError, match='forced retention delete failure'
        ):
            await execute_retention(
                db,
                plan,
                confirmation=EXECUTE_CONFIRMATION,
            )

    async with factory() as db:
        for experiment_id in (first_experiment, second_experiment):
            assert await db.get(Experiment, experiment_id) is not None
        for run_id in (first_run, second_run):
            assert await db.get(Run, run_id) is not None
            for model in (RunEvent, RunnerJob, RunnerAttempt, RunAnalysis):
                count = int(
                    (
                        await db.execute(
                            select(func.count())
                            .select_from(model)
                            .where(model.run_id == run_id)
                        )
                    ).scalar_one()
                )
                assert count == 1
        assert await db.get(Policy, first_policy) is not None
        assert await db.get(Policy, second_policy) is not None


@pytest.mark.asyncio
async def test_execute_deletes_only_the_complete_eligible_aggregate(api) -> None:
    _, factory = api
    target_experiment, target_run, target_policy = await add_experiment(
        factory,
        label="delete",
        policy_status="superseded",
        with_evidence=True,
    )
    retained_experiment, _, _ = await add_experiment(
        factory, label="retain", run_status="queued", completed_at=None
    )
    async with factory() as db:
        plan = await plan_retention(db, CUTOFF, limit=1)
        report = await execute_retention(
            db,
            plan,
            confirmation=EXECUTE_CONFIRMATION,
        )
    assert [unit.experiment_id for unit in report.units] == [target_experiment]
    async with factory() as db:
        assert await db.get(Experiment, target_experiment) is None
        assert await db.get(Experiment, retained_experiment) is not None
        assert await db.get(Run, target_run) is None
        assert await db.get(Policy, target_policy) is None
        for model in (RunEvent, RunnerJob, RunnerAttempt, RunAnalysis):
            count = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(model)
                        .where(model.run_id == target_run)
                    )
                ).scalar_one()
            )
            assert count == 0
