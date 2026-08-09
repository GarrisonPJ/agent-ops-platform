"""Plan and execute guarded retention of complete Experiment aggregates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.phase1_database import make_engine
from app.phase1_models import (
    Experiment,
    Policy,
    Run,
    RunAnalysis,
    RunEvent,
    RunnerAttempt,
    RunnerJob,
)
from app.phase1_schemas import PolicyStatus, TERMINAL_RUN_STATUSES


EXECUTE_CONFIRMATION = "DELETE_ELIGIBLE_EXPERIMENTS"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
RETENTION_PLAN_VERSION = 1
RETENTION_PLAN_STALE = 'RETENTION_PLAN_STALE'
RETENTION_PLAN_INVALID = 'RETENTION_PLAN_INVALID'
RETENTION_LOCK_TIMEOUT = 'RETENTION_LOCK_TIMEOUT'
RETENTION_STATEMENT_TIMEOUT = 'RETENTION_STATEMENT_TIMEOUT'
RETENTION_LOCK_CONFLICT = 'RETENTION_LOCK_CONFLICT'
POSTGRES_RETENTION_LOCK_TIMEOUT = '2s'
POSTGRES_RETENTION_STATEMENT_TIMEOUT = '30s'
POSTGRES_RETENTION_LOCK_TIMEOUT_STATEMENT = (
    f'SET LOCAL lock_timeout = \'{POSTGRES_RETENTION_LOCK_TIMEOUT}\''
)
POSTGRES_RETENTION_STATEMENT_TIMEOUT_STATEMENT = (
    f'SET LOCAL statement_timeout = \'{POSTGRES_RETENTION_STATEMENT_TIMEOUT}\''
)
POSTGRES_RETENTION_LOCK = (
    "LOCK TABLE experiments, runs, policies IN SHARE ROW EXCLUSIVE MODE"
)
PROTECTED_POLICY_STATUSES = (
    PolicyStatus.CANDIDATE.value,
    PolicyStatus.REPLAYING.value,
    PolicyStatus.VALIDATED.value,
    PolicyStatus.ACTIVE.value,
)


class RetentionError(RuntimeError):
    """Raised when retention inputs or safety guards are invalid."""


class RetentionPlanStaleError(RetentionError):
    '''Raised when a reviewed plan no longer describes current candidates.'''

    def __init__(self, detail: str) -> None:
        super().__init__(f'{RETENTION_PLAN_STALE}: {detail}')


@dataclass(frozen=True)
class RetentionUnit:
    experiment_id: str
    run_count: int
    event_count: int
    runner_job_count: int
    runner_attempt_count: int
    analysis_count: int
    policy_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "row_counts": {
                "runs": self.run_count,
                "run_events": self.event_count,
                "runner_jobs": self.runner_job_count,
                "runner_attempts": self.runner_attempt_count,
                "run_analyses": self.analysis_count,
                "policies": self.policy_count,
            },
        }


_PLAN_ROW_COUNT_KEYS = (
    'runs',
    'run_events',
    'runner_jobs',
    'runner_attempts',
    'run_analyses',
    'policies',
)


def _blocked_reason_items(
    values: Mapping[object, object],
) -> tuple[tuple[str, int], ...]:
    items: list[tuple[str, int]] = []
    for code, count in values.items():
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: invalid blocked reason count'
            )
        items.append((code, count))
    return tuple(sorted(items))


def _retention_plan_payload(
    *,
    version: int,
    terminal_before: datetime,
    limit: int,
    experiment_ids: tuple[str, ...],
    units: tuple[RetentionUnit, ...],
    blocked_reason_items: tuple[tuple[str, int], ...],
) -> dict[str, object]:
    return {
        'version': version,
        'terminal_before': _utc_text(terminal_before),
        'limit': limit,
        'experiment_ids': list(experiment_ids),
        'units': [unit.as_dict() for unit in units],
        'blocked_reason_counts': dict(blocked_reason_items),
    }


def _retention_plan_digest(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return f'sha256:{hashlib.sha256(canonical).hexdigest()}'


@dataclass(frozen=True)
class RetentionPlan:
    '''Immutable, reviewable description of one bounded operation.'''

    version: int
    terminal_before: datetime
    limit: int
    experiment_ids: tuple[str, ...]
    units: tuple[RetentionUnit, ...]
    digest: str
    _blocked_reason_items: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            '_blocked_reason_items',
            _blocked_reason_items(dict(self._blocked_reason_items)),
        )
        if self.version != RETENTION_PLAN_VERSION:
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: unsupported plan version'
            )
        cutoff = validate_terminal_before(self.terminal_before)
        bounded_limit = validate_limit(self.limit)
        if cutoff != self.terminal_before:
            object.__setattr__(self, 'terminal_before', cutoff)
        if bounded_limit != self.limit:
            object.__setattr__(self, 'limit', bounded_limit)
        if not isinstance(self.experiment_ids, tuple) or not isinstance(
            self.units, tuple
        ):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: plan collections must be immutable'
            )
        if any(
            not isinstance(experiment_id, str) or not experiment_id
            for experiment_id in self.experiment_ids
        ):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid experiment id')
        if self.experiment_ids != tuple(sorted(set(self.experiment_ids))):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: experiment_ids must be sorted and unique'
            )
        if len(self.units) > self.limit:
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: plan exceeds its bounded limit'
            )
        if tuple(unit.experiment_id for unit in self.units) != self.experiment_ids:
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: units do not match experiment_ids'
            )
        if self.digest != _retention_plan_digest(self.canonical_payload()):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: digest mismatch')

    @classmethod
    def from_units(
        cls,
        terminal_before: datetime,
        limit: int,
        units: tuple[RetentionUnit, ...] | list[RetentionUnit],
        blocked_reason_counts: Mapping[str, object] | None = None,
    ) -> 'RetentionPlan':
        cutoff = validate_terminal_before(terminal_before)
        bounded_limit = validate_limit(limit)
        sorted_units = tuple(sorted(units, key=lambda unit: unit.experiment_id))
        experiment_ids = tuple(unit.experiment_id for unit in sorted_units)
        blocked_items = _blocked_reason_items(blocked_reason_counts or {})
        payload = _retention_plan_payload(
            version=RETENTION_PLAN_VERSION,
            terminal_before=cutoff,
            limit=bounded_limit,
            experiment_ids=experiment_ids,
            units=sorted_units,
            blocked_reason_items=blocked_items,
        )
        return cls(
            version=RETENTION_PLAN_VERSION,
            terminal_before=cutoff,
            limit=bounded_limit,
            experiment_ids=experiment_ids,
            units=sorted_units,
            digest=_retention_plan_digest(payload),
            _blocked_reason_items=blocked_items,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> 'RetentionPlan':
        raw = value.get('plan', value)
        if not isinstance(raw, Mapping):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: plan must be an object')
        if raw.get('version') != RETENTION_PLAN_VERSION:
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: unsupported plan version'
            )

        raw_cutoff = raw.get('terminal_before')
        if not isinstance(raw_cutoff, str):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: terminal_before is required'
            )
        normalized = (
            raw_cutoff[:-1] + '+00:00'
            if raw_cutoff.endswith('Z')
            else raw_cutoff
        )
        try:
            cutoff = validate_terminal_before(datetime.fromisoformat(normalized))
        except (RetentionError, ValueError) as exc:
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid cutoff') from exc

        raw_limit = raw.get('limit')
        if not isinstance(raw_limit, int) or isinstance(raw_limit, bool):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid limit')
        try:
            bounded_limit = validate_limit(raw_limit)
        except RetentionError as exc:
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid limit') from exc

        raw_ids = raw.get('experiment_ids')
        raw_units = raw.get('units')
        if not isinstance(raw_ids, list) or not isinstance(raw_units, list):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: experiment_ids and units are required'
            )
        if any(not isinstance(item, str) or not item for item in raw_ids):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid experiment id')
        experiment_ids = tuple(raw_ids)
        if experiment_ids != tuple(sorted(set(experiment_ids))):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: experiment_ids must be sorted and unique'
            )

        units: list[RetentionUnit] = []
        for raw_unit in raw_units:
            if not isinstance(raw_unit, Mapping):
                raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid unit')
            experiment_id = raw_unit.get('experiment_id')
            row_counts = raw_unit.get('row_counts')
            if not isinstance(experiment_id, str) or not isinstance(
                row_counts, Mapping
            ):
                raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid unit')
            if set(row_counts) != set(_PLAN_ROW_COUNT_KEYS):
                raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid row counts')
            counts = [row_counts[key] for key in _PLAN_ROW_COUNT_KEYS]
            if any(
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                for count in counts
            ):
                raise RetentionError(f'{RETENTION_PLAN_INVALID}: invalid row counts')
            units.append(
                RetentionUnit(
                    experiment_id=experiment_id,
                    run_count=int(row_counts['runs']),
                    event_count=int(row_counts['run_events']),
                    runner_job_count=int(row_counts['runner_jobs']),
                    runner_attempt_count=int(row_counts['runner_attempts']),
                    analysis_count=int(row_counts['run_analyses']),
                    policy_count=int(row_counts['policies']),
                )
            )
        parsed_units = tuple(units)
        if tuple(unit.experiment_id for unit in parsed_units) != experiment_ids:
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: units do not match experiment_ids'
            )

        digest = raw.get('digest')
        if not isinstance(digest, str):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: digest is required')
        raw_blocked = raw.get('blocked_reason_counts', {})
        if not isinstance(raw_blocked, Mapping):
            raise RetentionError(
                f'{RETENTION_PLAN_INVALID}: blocked_reason_counts must be an object'
            )
        blocked_items = _blocked_reason_items(raw_blocked)
        payload = _retention_plan_payload(
            version=RETENTION_PLAN_VERSION,
            terminal_before=cutoff,
            limit=bounded_limit,
            experiment_ids=experiment_ids,
            units=parsed_units,
            blocked_reason_items=blocked_items,
        )
        if digest != _retention_plan_digest(payload):
            raise RetentionError(f'{RETENTION_PLAN_INVALID}: digest mismatch')

        return cls(
            version=RETENTION_PLAN_VERSION,
            terminal_before=cutoff,
            limit=bounded_limit,
            experiment_ids=experiment_ids,
            units=parsed_units,
            digest=digest,
            _blocked_reason_items=blocked_items,
        )

    @property
    def blocked_reason_counts(self) -> dict[str, int]:
        return dict(self._blocked_reason_items)

    def canonical_payload(self) -> dict[str, object]:
        return _retention_plan_payload(
            version=self.version,
            terminal_before=self.terminal_before,
            limit=self.limit,
            experiment_ids=self.experiment_ids,
            units=self.units,
            blocked_reason_items=self._blocked_reason_items,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            'status': 'ok',
            'mode': 'plan',
            **self.canonical_payload(),
            'unit_count': len(self.units),
            'digest': self.digest,
        }


@dataclass(frozen=True)
class RetentionReport:
    mode: Literal["plan", "execute"]
    terminal_before: datetime
    units: tuple[RetentionUnit, ...]
    blocked_reason_counts: dict[str, int]
    plan_digest: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": "ok",
            "mode": self.mode,
            "terminal_before": _utc_text(self.terminal_before),
            "unit_count": len(self.units),
            "units": [unit.as_dict() for unit in self.units],
            "blocked_reason_counts": self.blocked_reason_counts,
        }
        if self.plan_digest is not None:
            payload["plan_digest"] = self.plan_digest
        return payload


@dataclass(frozen=True)
class _RetentionPredicates:
    has_runs: object
    nonterminal_run: object
    terminal_after_cutoff: object
    protected_policy: object
    cross_experiment_reference: object

    @property
    def eligible(self) -> tuple[object, ...]:
        return (
            self.has_runs,
            ~self.nonterminal_run,
            ~self.terminal_after_cutoff,
            ~self.protected_policy,
            ~self.cross_experiment_reference,
        )


def validate_terminal_before(
    value: datetime, *, now: datetime | None = None
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RetentionError("terminal cutoff must include a timezone")
    normalized = value.astimezone(timezone.utc)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if normalized > current:
        raise RetentionError("terminal cutoff must not be in the future")
    return normalized


def validate_limit(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= MAX_LIMIT:
        raise RetentionError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _retention_sqlstate(error: SQLAlchemyError) -> str | None:
    candidate: object = error
    for _ in range(3):
        code = getattr(candidate, 'sqlstate', None) or getattr(
            candidate, 'pgcode', None
        )
        if isinstance(code, str):
            return code
        candidate = getattr(candidate, 'orig', None)
        if candidate is None:
            break
    return None


def _retention_database_error(error: SQLAlchemyError) -> RetentionError | None:
    sqlstate = _retention_sqlstate(error)
    if sqlstate == '55P03':
        return RetentionError(
            f'{RETENTION_LOCK_TIMEOUT}: maintenance lock was not available'
        )
    if sqlstate == '57014':
        return RetentionError(
            f'{RETENTION_STATEMENT_TIMEOUT}: maintenance statement timed out'
        )
    if sqlstate == '40P01':
        return RetentionError(
            f'{RETENTION_LOCK_CONFLICT}: maintenance lock deadlock'
        )
    return None


async def _acquire_retention_maintenance_lock(db: AsyncSession) -> None:
    """Block control-plane writes to retention reference tables on PostgreSQL.

    The lock is transaction-scoped and conflicts with PostgreSQL's write lock
    mode, covering the non-FK Run.policy_id reference as well as the other
    cross-aggregate pointers. SQLite intentionally skips this PostgreSQL-only
    maintenance operation so the portable test database remains usable.
    """
    if db.get_bind().dialect.name != 'postgresql':
        return
    try:
        await db.execute(text(POSTGRES_RETENTION_LOCK_TIMEOUT_STATEMENT))
        await db.execute(text(POSTGRES_RETENTION_STATEMENT_TIMEOUT_STATEMENT))
        await db.execute(text(POSTGRES_RETENTION_LOCK))
    except SQLAlchemyError as exc:
        mapped = _retention_database_error(exc)
        if mapped is not None:
            raise mapped from exc
        raise


def _retention_predicates(terminal_before: datetime) -> _RetentionPredicates:
    target_run_ids = (
        select(Run.id)
        .where(Run.experiment_id == Experiment.id)
        .correlate(Experiment)
    )
    target_policy_ids = (
        select(Policy.id)
        .where(Policy.experiment_id == Experiment.id)
        .correlate(Experiment)
    )
    external_run = aliased(Run)
    external_policy = aliased(Policy)
    has_runs = exists(
        select(Run.id).where(Run.experiment_id == Experiment.id)
    ).correlate(Experiment)
    nonterminal_run = exists(
        select(Run.id).where(
            Run.experiment_id == Experiment.id,
            ~Run.status.in_(tuple(TERMINAL_RUN_STATUSES)),
        )
    ).correlate(Experiment)
    terminal_after_cutoff = exists(
        select(Run.id).where(
            Run.experiment_id == Experiment.id,
            Run.status.in_(tuple(TERMINAL_RUN_STATUSES)),
            or_(Run.completed_at.is_(None), Run.completed_at > terminal_before),
        )
    ).correlate(Experiment)
    protected_policy = exists(
        select(Policy.id).where(
            Policy.experiment_id == Experiment.id,
            Policy.status.in_(PROTECTED_POLICY_STATUSES),
        )
    ).correlate(Experiment)
    external_run_reference = exists(
        select(external_run.id).where(
            external_run.experiment_id != Experiment.id,
            or_(
                external_run.source_run_id.in_(target_run_ids),
                external_run.policy_id.in_(target_policy_ids),
            ),
        )
    ).correlate(Experiment)
    external_policy_reference = exists(
        select(external_policy.id).where(
            external_policy.experiment_id != Experiment.id,
            or_(
                external_policy.source_run_id.in_(target_run_ids),
                external_policy.replay_run_id.in_(target_run_ids),
                external_policy.parent_policy_id.in_(target_policy_ids),
            ),
        )
    ).correlate(Experiment)
    return _RetentionPredicates(
        has_runs=has_runs,
        nonterminal_run=nonterminal_run,
        terminal_after_cutoff=terminal_after_cutoff,
        protected_policy=protected_policy,
        cross_experiment_reference=or_(
            external_run_reference, external_policy_reference
        ),
    )


async def _eligible_experiments(
    db: AsyncSession,
    terminal_before: datetime,
    limit: int,
    *,
    lock: bool,
) -> list[Experiment]:
    predicates = _retention_predicates(terminal_before)
    statement = (
        select(Experiment)
        .where(*predicates.eligible)
        .order_by(Experiment.created_at.asc(), Experiment.id.asc())
        .limit(limit)
    )
    if lock:
        statement = statement.with_for_update()
    return list((await db.execute(statement)).scalars().all())


async def _blocked_reason_counts(
    db: AsyncSession, terminal_before: datetime
) -> dict[str, int]:
    predicates = _retention_predicates(terminal_before)
    reasons = {
        "no_runs": ~predicates.has_runs,
        "nonterminal_run": predicates.nonterminal_run,
        "terminal_after_cutoff": predicates.terminal_after_cutoff,
        "protected_policy": predicates.protected_policy,
        "cross_experiment_reference": predicates.cross_experiment_reference,
    }
    counts: dict[str, int] = {}
    for code, predicate in reasons.items():
        counts[code] = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Experiment)
                    .where(predicate)
                )
            ).scalar_one()
        )
    return counts


async def _grouped_counts(
    db: AsyncSession, experiment_ids: list[str]
) -> tuple[RetentionUnit, ...]:
    if not experiment_ids:
        return ()

    async def grouped(statement) -> dict[str, int]:
        return {
            str(experiment_id): int(count)
            for experiment_id, count in (await db.execute(statement)).all()
        }

    runs = await grouped(
        select(Run.experiment_id, func.count(Run.id))
        .where(Run.experiment_id.in_(experiment_ids))
        .group_by(Run.experiment_id)
    )
    events = await grouped(
        select(Run.experiment_id, func.count(RunEvent.id))
        .join(RunEvent, RunEvent.run_id == Run.id)
        .where(Run.experiment_id.in_(experiment_ids))
        .group_by(Run.experiment_id)
    )
    jobs = await grouped(
        select(Run.experiment_id, func.count(RunnerJob.run_id))
        .join(RunnerJob, RunnerJob.run_id == Run.id)
        .where(Run.experiment_id.in_(experiment_ids))
        .group_by(Run.experiment_id)
    )
    attempts = await grouped(
        select(Run.experiment_id, func.count(RunnerAttempt.id))
        .join(RunnerAttempt, RunnerAttempt.run_id == Run.id)
        .where(Run.experiment_id.in_(experiment_ids))
        .group_by(Run.experiment_id)
    )
    analyses = await grouped(
        select(Run.experiment_id, func.count(RunAnalysis.run_id))
        .join(RunAnalysis, RunAnalysis.run_id == Run.id)
        .where(Run.experiment_id.in_(experiment_ids))
        .group_by(Run.experiment_id)
    )
    policies = await grouped(
        select(Policy.experiment_id, func.count(Policy.id))
        .where(Policy.experiment_id.in_(experiment_ids))
        .group_by(Policy.experiment_id)
    )
    return tuple(
        RetentionUnit(
            experiment_id=experiment_id,
            run_count=runs.get(experiment_id, 0),
            event_count=events.get(experiment_id, 0),
            runner_job_count=jobs.get(experiment_id, 0),
            runner_attempt_count=attempts.get(experiment_id, 0),
            analysis_count=analyses.get(experiment_id, 0),
            policy_count=policies.get(experiment_id, 0),
        )
        for experiment_id in experiment_ids
    )


async def plan_retention(
    db: AsyncSession, terminal_before: datetime, *, limit: int = DEFAULT_LIMIT
) -> RetentionPlan:
    cutoff = validate_terminal_before(terminal_before)
    bounded_limit = validate_limit(limit)
    async with db.begin():
        experiments = await _eligible_experiments(
            db, cutoff, bounded_limit, lock=False
        )
        experiment_ids = sorted(item.id for item in experiments)
        units = await _grouped_counts(db, experiment_ids)
        blocked = await _blocked_reason_counts(db, cutoff)
    return RetentionPlan.from_units(cutoff, bounded_limit, units, blocked)


def _coerce_retention_plan(
    value: RetentionPlan | Mapping[str, object],
) -> RetentionPlan:
    if isinstance(value, RetentionPlan):
        return value
    if isinstance(value, Mapping):
        return RetentionPlan.from_dict(value)
    raise RetentionError(f'{RETENTION_PLAN_INVALID}: reviewed plan is required')


async def execute_retention(
    db: AsyncSession,
    plan: RetentionPlan | Mapping[str, object],
    *,
    confirmation: str,
) -> RetentionReport:
    if confirmation != EXECUTE_CONFIRMATION:
        raise RetentionError(
            f'execute requires confirmation token {EXECUTE_CONFIRMATION}'
        )
    reviewed_plan = _coerce_retention_plan(plan)
    try:
        async with db.begin():
            await _acquire_retention_maintenance_lock(db)
            # Re-evaluate every guard after the maintenance lock is acquired.
            experiments = await _eligible_experiments(
                db,
                reviewed_plan.terminal_before,
                reviewed_plan.limit,
                lock=True,
            )
            current_ids = tuple(sorted(item.id for item in experiments))
            if current_ids != reviewed_plan.experiment_ids:
                raise RetentionPlanStaleError(
                    'eligible experiment IDs changed after review'
                )
            current_units = await _grouped_counts(db, list(current_ids))
            if current_units != reviewed_plan.units:
                raise RetentionPlanStaleError(
                    'eligible aggregate row counts changed after review'
                )
            blocked = await _blocked_reason_counts(
                db, reviewed_plan.terminal_before
            )
            experiments_by_id = {item.id: item for item in experiments}
            for experiment_id in reviewed_plan.experiment_ids:
                await db.delete(experiments_by_id[experiment_id])
    except SQLAlchemyError as exc:
        mapped = _retention_database_error(exc)
        if mapped is not None:
            raise mapped from exc
        raise
    return RetentionReport(
        'execute',
        reviewed_plan.terminal_before,
        reviewed_plan.units,
        blocked,
        plan_digest=reviewed_plan.digest,
    )


def _parse_terminal_before(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return validate_terminal_before(datetime.fromisoformat(normalized))
    except (RetentionError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="environment variable containing the control-plane database URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument(
        "--terminal-before", type=_parse_terminal_before, required=True
    )
    plan_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--plan-file", type=Path, required=True)
    execute_parser.add_argument("--confirm", required=True)
    return parser


def _database_url(variable: str) -> str:
    value = os.getenv(variable)
    if not value:
        raise RetentionError(f"required environment variable is not set: {variable}")
    return value


def _load_plan_file(path: Path) -> RetentionPlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetentionError(
            f"failed to read retention plan file {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise RetentionError(
            f"{RETENTION_PLAN_INVALID}: plan file must contain an object"
        )
    return RetentionPlan.from_dict(payload)


async def _run(args: argparse.Namespace) -> RetentionPlan | RetentionReport:
    reviewed_plan: RetentionPlan | None = None
    if args.command == "execute":
        reviewed_plan = _load_plan_file(args.plan_file)
    engine = make_engine(_database_url(args.database_url_env))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            if args.command == "plan":
                return await plan_retention(
                    db, args.terminal_before, limit=args.limit
                )
            assert reviewed_plan is not None
            return await execute_retention(
                db,
                reviewed_plan,
                confirmation=args.confirm,
            )
    finally:
        await engine.dispose()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except (RetentionError, SQLAlchemyError, OSError) as exc:
        parser.exit(1, f"data retention failed: {exc}\n")
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
