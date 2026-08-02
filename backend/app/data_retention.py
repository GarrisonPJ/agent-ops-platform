"""Plan and execute guarded retention of complete Experiment aggregates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass(frozen=True)
class RetentionReport:
    mode: Literal["plan", "execute"]
    terminal_before: datetime
    units: tuple[RetentionUnit, ...]
    blocked_reason_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ok",
            "mode": self.mode,
            "terminal_before": _utc_text(self.terminal_before),
            "unit_count": len(self.units),
            "units": [unit.as_dict() for unit in self.units],
            "blocked_reason_counts": self.blocked_reason_counts,
        }


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


async def _acquire_retention_maintenance_lock(db: AsyncSession) -> None:
    """Block control-plane writes to retention reference tables on PostgreSQL.

    The lock is transaction-scoped and conflicts with PostgreSQL's write lock
    mode, covering the non-FK Run.policy_id reference as well as the other
    cross-aggregate pointers. SQLite intentionally skips this PostgreSQL-only
    maintenance operation so the portable test database remains usable.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text(POSTGRES_RETENTION_LOCK))


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
) -> RetentionReport:
    cutoff = validate_terminal_before(terminal_before)
    bounded_limit = validate_limit(limit)
    async with db.begin():
        experiments = await _eligible_experiments(
            db, cutoff, bounded_limit, lock=False
        )
        units = await _grouped_counts(db, [item.id for item in experiments])
        blocked = await _blocked_reason_counts(db, cutoff)
    return RetentionReport("plan", cutoff, units, blocked)


async def execute_retention(
    db: AsyncSession,
    terminal_before: datetime,
    *,
    confirmation: str,
    limit: int = DEFAULT_LIMIT,
) -> RetentionReport:
    if confirmation != EXECUTE_CONFIRMATION:
        raise RetentionError(
            f"execute requires confirmation token {EXECUTE_CONFIRMATION}"
        )
    cutoff = validate_terminal_before(terminal_before)
    bounded_limit = validate_limit(limit)
    async with db.begin():
        await _acquire_retention_maintenance_lock(db)
        # Re-evaluate every guard after the maintenance lock is acquired.
        experiments = await _eligible_experiments(
            db, cutoff, bounded_limit, lock=True
        )
        units = await _grouped_counts(db, [item.id for item in experiments])
        blocked = await _blocked_reason_counts(db, cutoff)
        for experiment in experiments:
            await db.delete(experiment)
    return RetentionReport("execute", cutoff, units, blocked)


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
    for command in ("plan", "execute"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--terminal-before", type=_parse_terminal_before, required=True
        )
        command_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        if command == "execute":
            command_parser.add_argument("--confirm", required=True)
    return parser


def _database_url(variable: str) -> str:
    value = os.getenv(variable)
    if not value:
        raise RetentionError(f"required environment variable is not set: {variable}")
    return value


async def _run(args: argparse.Namespace) -> RetentionReport:
    engine = make_engine(_database_url(args.database_url_env))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            if args.command == "plan":
                return await plan_retention(
                    db, args.terminal_before, limit=args.limit
                )
            return await execute_retention(
                db,
                args.terminal_before,
                confirmation=args.confirm,
                limit=args.limit,
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
        parser.exit(1, f"data retention failed: {type(exc).__name__}\n")
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
