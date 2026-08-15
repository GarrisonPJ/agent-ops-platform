"""Bounded operational evaluation for Phase 1.3F alert classification."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.durable_events import ProviderFailureKind
from app.migrations import application_alembic_head
from app.phase1_models import Run, RunnerAttempt, RunnerJob, RunnerPresence, utcnow
from app.phase1_schemas import (
    OperationsOverviewResponse,
    OperationalIncident,
    OperationalStateEntry,
    OperationalStateSnapshot,
    OperationalWindow,
    RunStatus,
    TERMINAL_RUN_STATUSES,
)
from app.phase1_service import DomainError, RECOVERABLE_RUN_STATUSES, RUNNER_AVAILABILITY_SECONDS


OPERATIONAL_STATE_SCHEMA_VERSION = 1

OperationalStateCode = Literal[
    "database_unavailable",
    "schema_drift",
    "runner_unavailable",
    "lease_expired",
    "provider_rate_limited",
    "provider_unavailable",
]

OPERATIONAL_STATE_CODES: tuple[OperationalStateCode, ...] = (
    "database_unavailable",
    "schema_drift",
    "runner_unavailable",
    "lease_expired",
    "provider_rate_limited",
    "provider_unavailable",
)

PRIMARY_STATE_PRECEDENCE: tuple[OperationalStateCode | Literal["ok"], ...] = (
    *OPERATIONAL_STATE_CODES,
    "ok",
)

PROVIDER_UNAVAILABLE_CODES = frozenset(
    {
        ProviderFailureKind.TIMEOUT.value,
        ProviderFailureKind.UNAVAILABLE.value,
        ProviderFailureKind.HTTP_ERROR.value,
    }
)


@dataclass(frozen=True)
class OperationalStateQuery:
    observed_at: datetime
    window_seconds: int = 900
    limit: int = 50
    cursor: str | None = None


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DomainError(400, "INVALID_OBSERVED_AT", "observed_at must be timezone-aware UTC")
    normalized = value.astimezone(timezone.utc)
    if normalized.utcoffset() != timedelta(0):
        raise DomainError(400, "INVALID_OBSERVED_AT", "observed_at must use UTC")
    return normalized


def _entry(
    code: OperationalStateCode,
    *,
    status: Literal["active", "clear", "unknown"],
    count: int | None = None,
    details: dict[str, object] | None = None,
) -> OperationalStateEntry:
    return OperationalStateEntry(
        code=code,
        status=status,
        count=count,
        details=details or {},
    )


def _unknown_states() -> list[OperationalStateEntry]:
    return [_entry(code, status="unknown", count=None) for code in OPERATIONAL_STATE_CODES]


def _primary_state(states: list[OperationalStateEntry]) -> OperationalStateCode | Literal["ok"]:
    active = {item.code for item in states if item.status == "active"}
    for code in PRIMARY_STATE_PRECEDENCE:
        if code in active:
            return code
    return "ok"


def _decode_cursor(cursor: str) -> dict[str, object]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise DomainError(400, "INVALID_CURSOR", "Operational cursor is invalid") from exc
    if not isinstance(payload, dict):
        raise DomainError(400, "INVALID_CURSOR", "Operational cursor is invalid")
    return payload


def _encode_cursor(payload: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")


def _nonnegative_metric(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


async def collect_operations_overview(db: AsyncSession, *, now: datetime | None = None) -> OperationsOverviewResponse:
    observed_at = _require_utc(now or utcnow())

    status_rows = (
        await db.execute(select(Run.status, func.count()).group_by(Run.status))
    ).all()
    runs_by_status = {str(status): int(count) for status, count in status_rows}
    queue_depth = runs_by_status.get(RunStatus.QUEUED.value, 0)

    terminal_rows = (
        await db.execute(
            select(Run.status, func.count())
            .where(Run.status.in_(tuple(TERMINAL_RUN_STATUSES)))
            .group_by(Run.status)
        )
    ).all()
    terminal_outcomes = {str(status): int(count) for status, count in terminal_rows}

    expired_lease_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Run)
                .join(RunnerJob, RunnerJob.run_id == Run.id)
                .where(
                    Run.status.in_(tuple(RECOVERABLE_RUN_STATUSES)),
                    RunnerJob.lease_id.isnot(None),
                    RunnerJob.lease_expires_at.isnot(None),
                    RunnerJob.lease_expires_at <= observed_at,
                )
            )
        ).scalar_one()
    )

    lease_recoveries = int(
        (await db.execute(select(func.count()).select_from(RunnerAttempt))).scalar_one()
    )

    event_retries = await _sum_event_retries(db)

    return OperationsOverviewResponse(
        generated_at=observed_at,
        queue_depth=queue_depth,
        runs_by_status=dict(sorted(runs_by_status.items())),
        terminal_outcomes=dict(sorted(terminal_outcomes.items())),
        expired_lease_count=expired_lease_count,
        lease_recoveries=lease_recoveries,
        event_retries=event_retries,
    )


async def _sum_event_retries(db: AsyncSession) -> int:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        total = (
            await db.execute(
                select(
                    func.coalesce(
                        func.sum(
                            cast(
                                Run.metrics["event_retries"].as_string(),
                                Integer,
                            )
                        ),
                        0,
                    )
                )
            )
        ).scalar_one()
        return int(total or 0)

    rows = (await db.execute(select(Run.metrics))).all()
    return sum(_nonnegative_metric(metrics.get("event_retries")) for (metrics,) in rows if isinstance(metrics, dict))


async def _active_runner_count(db: AsyncSession, observed_at: datetime) -> int:
    cutoff = observed_at - timedelta(seconds=RUNNER_AVAILABILITY_SECONDS)
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(RunnerPresence)
                .where(RunnerPresence.last_seen_at >= cutoff)
            )
        ).scalar_one()
    )


async def _schema_revision(db: AsyncSession) -> str | None:
    try:
        return (
            await db.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
    except SQLAlchemyError:
        await db.rollback()
        return None


async def _provider_fault_counts(
    db: AsyncSession, window_from: datetime, window_to: datetime
) -> tuple[int, int]:
    bind = db.get_bind()
    unavailable = 0
    rate_limited = 0

    if bind.dialect.name == "postgresql":
        rows = (
            await db.execute(
                select(
                    Run.metrics["provider_error"]["code"].as_string(),
                    func.count(),
                )
                .where(
                    Run.status.in_(tuple(TERMINAL_RUN_STATUSES)),
                    Run.completed_at.isnot(None),
                    Run.completed_at >= window_from,
                    Run.completed_at <= window_to,
                    Run.metrics["provider_error"].isnot(None),
                )
                .group_by(Run.metrics["provider_error"]["code"].as_string())
            )
        ).all()
        for code, count in rows:
            if code == ProviderFailureKind.RATE_LIMITED.value:
                rate_limited += int(count)
            elif code in PROVIDER_UNAVAILABLE_CODES:
                unavailable += int(count)
        return unavailable, rate_limited

    rows = (
        await db.execute(
            select(Run.metrics, Run.completed_at).where(
                Run.status.in_(tuple(TERMINAL_RUN_STATUSES)),
                Run.completed_at.isnot(None),
                Run.completed_at >= window_from,
                Run.completed_at <= window_to,
            )
        )
    ).all()
    for metrics, completed_at in rows:
        if completed_at is None or not isinstance(metrics, dict):
            continue
        provider_error = metrics.get("provider_error")
        if not isinstance(provider_error, dict):
            continue
        code = provider_error.get("code")
        if code == ProviderFailureKind.RATE_LIMITED.value:
            rate_limited += 1
        elif code in PROVIDER_UNAVAILABLE_CODES:
            unavailable += 1
    return unavailable, rate_limited


async def _expired_lease_incidents(
    db: AsyncSession, observed_at: datetime, *, limit: int
) -> list[OperationalIncident]:
    rows = (
        await db.execute(
            select(Run.id, RunnerJob.lease_expires_at)
            .join(RunnerJob, RunnerJob.run_id == Run.id)
            .where(
                Run.status.in_(tuple(RECOVERABLE_RUN_STATUSES)),
                RunnerJob.lease_id.isnot(None),
                RunnerJob.lease_expires_at.isnot(None),
                RunnerJob.lease_expires_at <= observed_at,
            )
            .order_by(RunnerJob.lease_expires_at.asc(), Run.id.asc())
            .limit(limit)
        )
    ).all()
    return [
        OperationalIncident(
            kind="expired_lease",
            run_id=str(run_id),
            observed_at=expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc),
            fault_code="lease_expired",
            details={"lease_expires_at": expires_at.isoformat()},
        )
        for run_id, expires_at in rows
    ]


async def _provider_fault_incidents(
    db: AsyncSession,
    window_from: datetime,
    window_to: datetime,
    *,
    limit: int,
) -> list[OperationalIncident]:
    rows = (
        await db.execute(
            select(Run.id, Run.completed_at, Run.metrics)
            .where(
                Run.status.in_(tuple(TERMINAL_RUN_STATUSES)),
                Run.completed_at.isnot(None),
                Run.completed_at >= window_from,
                Run.completed_at <= window_to,
            )
            .order_by(Run.completed_at.desc(), Run.id.desc())
            .limit(limit)
        )
    ).all()
    incidents: list[OperationalIncident] = []
    for run_id, completed_at, metrics in rows:
        if not isinstance(metrics, dict):
            continue
        provider_error = metrics.get("provider_error")
        if not isinstance(provider_error, dict):
            continue
        code = provider_error.get("code")
        if code not in PROVIDER_UNAVAILABLE_CODES and code != ProviderFailureKind.RATE_LIMITED.value:
            continue
        incidents.append(
            OperationalIncident(
                kind="provider_fault",
                run_id=str(run_id),
                observed_at=completed_at if completed_at.tzinfo else completed_at.replace(tzinfo=timezone.utc),
                fault_code=str(code),
                details={},
            )
        )
    return incidents


async def evaluate_operational_state(
    db: AsyncSession,
    query: OperationalStateQuery,
    *,
    expected_schema_revision: str | None = None,
) -> OperationalStateSnapshot:
    observed_at = _require_utc(query.observed_at)
    if query.window_seconds < 60 or query.window_seconds > 86_400:
        raise DomainError(400, "INVALID_WINDOW", "window_seconds must be between 60 and 86400")
    if query.limit < 1 or query.limit > 100:
        raise DomainError(400, "INVALID_LIMIT", "limit must be between 1 and 100")
    if query.cursor is not None:
        _decode_cursor(query.cursor)

    expected = expected_schema_revision or application_alembic_head()
    window_from = observed_at - timedelta(seconds=query.window_seconds)
    window = OperationalWindow.model_validate(
        {"from": window_from, "to": observed_at},
        by_alias=True,
    )
    summary = await collect_operations_overview(db, now=observed_at)

    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        await db.rollback()
        states = [
            _entry("database_unavailable", status="active", count=1),
            *_unknown_states()[1:],
        ]
        return OperationalStateSnapshot(
            schema_version=OPERATIONAL_STATE_SCHEMA_VERSION,
            observed_at=observed_at,
            window=window,
            status="degraded",
            primary_state="database_unavailable",
            states=states,
            incidents=[],
            next_cursor=None,
            summary=summary,
        )

    live_revision = await _schema_revision(db)
    if live_revision != expected:
        states = [
            _entry("database_unavailable", status="clear", count=0),
            _entry(
                "schema_drift",
                status="active",
                count=1,
                details={
                    "schema_revision": live_revision,
                    "expected_schema_revision": expected,
                },
            ),
            *_unknown_states()[2:],
        ]
        return OperationalStateSnapshot(
            schema_version=OPERATIONAL_STATE_SCHEMA_VERSION,
            observed_at=observed_at,
            window=window,
            status="degraded",
            primary_state="schema_drift",
            states=states,
            incidents=[],
            next_cursor=None,
            summary=summary,
        )

    active_runners = await _active_runner_count(db, observed_at)
    provider_unavailable_count, provider_rate_limited_count = await _provider_fault_counts(
        db, window_from, observed_at
    )

    states = [
        _entry("database_unavailable", status="clear", count=0),
        _entry("schema_drift", status="clear", count=0),
        _entry(
            "runner_unavailable",
            status="clear" if active_runners else "active",
            count=0 if active_runners else 1,
            details={"active_runner_count": active_runners},
        ),
        _entry(
            "lease_expired",
            status="clear" if summary.expired_lease_count == 0 else "active",
            count=summary.expired_lease_count,
        ),
        _entry(
            "provider_rate_limited",
            status="clear" if provider_rate_limited_count == 0 else "active",
            count=provider_rate_limited_count,
        ),
        _entry(
            "provider_unavailable",
            status="clear" if provider_unavailable_count == 0 else "active",
            count=provider_unavailable_count,
        ),
    ]
    primary = _primary_state(states)

    lease_incidents = await _expired_lease_incidents(db, observed_at, limit=query.limit)
    provider_incidents = await _provider_fault_incidents(
        db, window_from, observed_at, limit=query.limit
    )
    incidents = sorted(
        [*lease_incidents, *provider_incidents],
        key=lambda item: (item.observed_at, item.run_id, item.fault_code or ""),
        reverse=True,
    )[: query.limit]
    next_cursor = (
        _encode_cursor(
            {
                "observed_at": observed_at.isoformat(),
                "window_seconds": query.window_seconds,
                "offset": query.limit,
            }
        )
        if len(lease_incidents) + len(provider_incidents) > query.limit
        else None
    )

    return OperationalStateSnapshot(
        schema_version=OPERATIONAL_STATE_SCHEMA_VERSION,
        observed_at=observed_at,
        window=window,
        status="ok" if primary == "ok" else "degraded",
        primary_state=primary,
        states=states,
        incidents=incidents,
        next_cursor=next_cursor,
        summary=summary,
    )
