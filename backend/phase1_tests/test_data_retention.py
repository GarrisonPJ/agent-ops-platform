from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.data_retention import (
    EXECUTE_CONFIRMATION,
    POSTGRES_RETENTION_LOCK,
    RetentionError,
    _acquire_retention_maintenance_lock,
    execute_retention,
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
            scenario_id="checkout-api-latency",
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

    async def execute(self, statement) -> None:
        self.statements.append(str(statement))


@pytest.mark.asyncio
async def test_maintenance_lock_is_postgres_only_and_table_scoped() -> None:
    sqlite = RecordingSession("sqlite")
    await _acquire_retention_maintenance_lock(sqlite)  # type: ignore[arg-type]
    assert sqlite.statements == []

    postgres = RecordingSession("postgresql")
    await _acquire_retention_maintenance_lock(postgres)  # type: ignore[arg-type]
    assert postgres.statements == [POSTGRES_RETENTION_LOCK]


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
    async with factory() as db:
        await execute_retention(
            db, CUTOFF, confirmation=EXECUTE_CONFIRMATION, limit=1
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


@pytest.mark.asyncio
async def test_execute_requires_exact_confirmation(api) -> None:
    _, factory = api
    experiment_id, _, _ = await add_experiment(factory, label="confirm")
    async with factory() as db:
        with pytest.raises(RetentionError, match="confirmation token"):
            await execute_retention(db, CUTOFF, confirmation="delete", limit=1)
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
        executed = await execute_retention(
            db, CUTOFF, confirmation=EXECUTE_CONFIRMATION
        )
    assert experiment_id not in {unit.experiment_id for unit in executed.units}
    async with factory() as db:
        assert await db.get(Experiment, experiment_id) is not None


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
        report = await execute_retention(
            db,
            CUTOFF,
            confirmation=EXECUTE_CONFIRMATION,
            limit=1,
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
