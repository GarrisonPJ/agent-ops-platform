"""Application services for experiments, runs, leases, and policy replay."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.failure_analyzer import analyze_trajectory
from app.phase1_models import (
    Experiment,
    Policy,
    Run,
    RunAnalysis,
    RunEvent,
    RunnerAttempt,
    RunnerJob,
    RunnerPresence,
    new_id,
    utcnow,
)
from app.phase1_schemas import (
    AnalysisResponse,
    AttemptCorrelation,
    EvaluationLimits,
    EvaluationSpec,
    EventEnvelope,
    ExecutionMode,
    ExperimentCreate,
    ExperimentResponse,
    JobCorrelation,
    MAX_PROVIDER_LATENCY_MS,
    MAX_PROVIDER_REQUEST_COUNT,
    MAX_PROVIDER_TOKENS,
    OperationsOverviewResponse,
    ProviderCorrelation,
    ProviderErrorCorrelation,
    ProviderErrorInput,
    ProviderTelemetryCorrelation,
    ProviderTelemetryInput,
    ProviderTelemetryMetrics,
    RunCorrelation,
    RunDiagnosticsResponse,
    RunOperationalMetrics,
    RunTiming,
    TerminalRunOutcome,
    PolicyPatch,
    PolicyResponse,
    PolicyStatus,
    RunResponse,
    RunStatus,
    SCENARIO_ID,
    TERMINAL_RUN_STATUSES,
)
from app.scoring import compute_score


LEASE_SECONDS = 15
RUNNER_AVAILABILITY_SECONDS = 15
MAX_RUN_ATTEMPTS = 3
RECOVERABLE_RUN_STATUSES = {
    RunStatus.CLAIMED.value,
    RunStatus.RUNNING.value,
    RunStatus.CANCELLING.value,
}


class DomainError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def run_response(run: Run) -> RunResponse:
    return RunResponse.model_validate(run)


def policy_response(policy: Policy) -> PolicyResponse:
    return PolicyResponse.model_validate(policy)


async def require_experiment(db: AsyncSession, experiment_id: str) -> Experiment:
    experiment = await db.get(Experiment, experiment_id)
    if experiment is None:
        raise DomainError(404, "EXPERIMENT_NOT_FOUND", "Experiment not found")
    return experiment


async def require_run(db: AsyncSession, run_id: str) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise DomainError(404, "RUN_NOT_FOUND", "Run not found")
    return run


async def require_policy(db: AsyncSession, policy_id: str) -> Policy:
    policy = await db.get(Policy, policy_id)
    if policy is None:
        raise DomainError(404, "POLICY_NOT_FOUND", "Policy not found")
    return policy




def _milliseconds_between(start: datetime | None, end: datetime | None) -> int | None:
    start = _aware(start)
    end = _aware(end)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1_000))


def _provider_request_fingerprint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 500:
        return None
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _provider_telemetry_correlation(value: object) -> ProviderTelemetryCorrelation | None:
    try:
        return ProviderTelemetryCorrelation.model_validate(value)
    except ValidationError:
        return None


def _provider_telemetry_metrics(value: object) -> ProviderTelemetryMetrics | None:
    try:
        return ProviderTelemetryMetrics.model_validate(value)
    except ValidationError:
        return None


def _provider_error(value: object) -> dict[str, object] | None:
    try:
        parsed = ProviderErrorCorrelation.model_validate(value)
    except ValidationError:
        return None
    return parsed.model_dump(mode="json", exclude_none=True)


def _sanitize_provider_telemetry(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "model",
        "latency_ms",
        "request_count",
        "token_prompt",
        "token_completion",
        "request_id",
        "request_fingerprint",
    }
    try:
        parsed = ProviderTelemetryInput.model_validate(
            {key: value[key] for key in allowed if key in value}
        )
    except ValidationError:
        return None
    model = parsed.model.strip()
    if not model:
        return None
    return ProviderTelemetryCorrelation(
        model=model,
        latency_ms=parsed.latency_ms,
        request_count=parsed.request_count,
        token_prompt=parsed.token_prompt,
        token_completion=parsed.token_completion,
        request_fingerprint=(
            _provider_request_fingerprint(parsed.request_id)
            if parsed.request_id is not None
            else parsed.request_fingerprint
        ),
    ).model_dump(mode="json", exclude_none=True)


def _sanitize_provider_error(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "code",
        "message",
        "retryable",
        "attempts",
        "request_id",
        "request_fingerprint",
    }
    try:
        parsed = ProviderErrorInput.model_validate(
            {key: value[key] for key in allowed if key in value}
        )
    except ValidationError:
        return None
    return ProviderErrorCorrelation(
        code=parsed.code,
        message=parsed.message,
        retryable=parsed.retryable,
        attempts=parsed.attempts,
        request_fingerprint=(
            _provider_request_fingerprint(parsed.request_id)
            if parsed.request_id is not None
            else parsed.request_fingerprint
        ),
    ).model_dump(mode="json", exclude_none=True)


def _sanitize_event_payload(_event_type: str, payload: dict) -> dict:
    sanitized = dict(payload)
    if "provider" in payload:
        provider = _sanitize_provider_telemetry(payload.get("provider"))
        if provider is None:
            sanitized.pop("provider", None)
        else:
            sanitized["provider"] = provider
    if "provider_error" in payload:
        provider_error = _sanitize_provider_error(payload.get("provider_error"))
        if provider_error is None:
            sanitized.pop("provider_error", None)
        else:
            sanitized["provider_error"] = provider_error
    return sanitized


async def get_run_diagnostics(db: AsyncSession, run_id: str) -> RunDiagnosticsResponse:
    run = await require_run(db, run_id)
    job = await db.get(RunnerJob, run_id)
    if job is None:
        raise DomainError(500, "JOB_INVARIANT_BROKEN", "Run job is missing")
    events = list(
        (
            await db.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    attempts = list(
        (
            await db.execute(
                select(RunnerAttempt)
                .where(RunnerAttempt.run_id == run_id)
                .order_by(RunnerAttempt.attempt.asc())
            )
        )
        .scalars()
        .all()
    )
    latest_attempt_start = 0
    for index, event in enumerate(events):
        if event.event_type != "run_started":
            continue
        attempt = event.payload.get("attempt")
        if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1:
            latest_attempt_start = index
    event_metrics = _provider_metrics(events[latest_attempt_start:])
    run_metrics = run.metrics if isinstance(run.metrics, dict) else {}
    raw_provider = _provider_telemetry_metrics(event_metrics.get("provider"))
    if raw_provider is None:
        raw_provider = _provider_telemetry_metrics(run_metrics.get("provider"))
    raw_error = _provider_error(event_metrics.get("provider_error"))
    if raw_error is None:
        raw_error = _provider_error(run_metrics.get("provider_error"))
    provider = None
    if raw_provider is not None or raw_error is not None:
        provider = ProviderCorrelation(
            model=raw_provider.model if raw_provider is not None else None,
            request_fingerprints=(
                raw_provider.request_fingerprints if raw_provider is not None else []
            ),
            error=ProviderErrorCorrelation.model_validate(raw_error)
            if raw_error is not None
            else None,
        )
    provider_latency_ms = raw_provider.latency_ms if raw_provider is not None else 0
    provider_tokens = (
        raw_provider.token_prompt + raw_provider.token_completion
        if raw_provider is not None
        else 0
    )
    terminal = (
        TerminalRunOutcome(status=RunStatus(run.status), error=run.error)
        if run.status in TERMINAL_RUN_STATUSES
        else None
    )
    return RunDiagnosticsResponse(
        run=RunCorrelation(
            experiment_id=run.experiment_id,
            run_id=run.id,
            execution_mode=ExecutionMode(run.evaluation_spec.get("execution_mode", "fixture")),
            status=RunStatus(run.status),
        ),
        job=JobCorrelation(
            run_id=job.run_id,
            lease_id=job.lease_id,
            runner_id=job.runner_id,
            attempt=job.attempt,
            recovery_reason=job.recovery_reason,
        ),
        attempts=[
            AttemptCorrelation(
                attempt=item.attempt,
                lease_id=item.lease_id,
                runner_id=item.runner_id,
                lease_expires_at=item.lease_expires_at,
                recovery_reason=item.recovery_reason,
                outcome=item.outcome,
                recorded_at=item.recorded_at,
            )
            for item in attempts
        ],
        provider=provider,
        timing=RunTiming(
            queue_latency_ms=_milliseconds_between(run.queued_at, run.claimed_at),
            run_duration_ms=_milliseconds_between(run.started_at, run.completed_at),
        ),
        metrics=RunOperationalMetrics(
            event_count=len(events),
            event_retries=_nonnegative_metric(run_metrics.get("event_retries")),
            lease_recoveries=len(attempts),
            provider_latency_ms=provider_latency_ms,
            provider_tokens=provider_tokens,
        ),
        terminal=terminal,
    )


async def get_operations_overview(db: AsyncSession) -> OperationsOverviewResponse:
    runs = list((await db.execute(select(Run))).scalars().all())
    jobs = {
        item.run_id: item
        for item in (await db.execute(select(RunnerJob))).scalars().all()
    }
    now = utcnow()
    runs_by_status: dict[str, int] = {}
    terminal_outcomes: dict[str, int] = {}
    expired_lease_count = 0
    lease_recoveries = int(
        (await db.execute(select(func.count()).select_from(RunnerAttempt))).scalar_one()
    )
    event_retries = 0
    for run in runs:
        runs_by_status[run.status] = runs_by_status.get(run.status, 0) + 1
        if run.status in TERMINAL_RUN_STATUSES:
            terminal_outcomes[run.status] = terminal_outcomes.get(run.status, 0) + 1
        metrics = run.metrics if isinstance(run.metrics, dict) else {}
        event_retries += _nonnegative_metric(metrics.get("event_retries"))
        job = jobs.get(run.id)
        if job is None:
            continue
        expires_at = _aware(job.lease_expires_at)
        if (
            run.status in RECOVERABLE_RUN_STATUSES
            and job.lease_id is not None
            and expires_at is not None
            and expires_at <= now
        ):
            expired_lease_count += 1
    return OperationsOverviewResponse(
        generated_at=now,
        queue_depth=runs_by_status.get(RunStatus.QUEUED.value, 0),
        runs_by_status=dict(sorted(runs_by_status.items())),
        terminal_outcomes=dict(sorted(terminal_outcomes.items())),
        expired_lease_count=expired_lease_count,
        lease_recoveries=lease_recoveries,
        event_retries=event_retries,
    )


async def experiment_response(db: AsyncSession, experiment: Experiment) -> ExperimentResponse:
    runs = list(
        (
            await db.execute(
                select(Run)
                .where(Run.experiment_id == experiment.id)
                .order_by(Run.queued_at.desc())
            )
        )
        .scalars()
        .all()
    )
    policies = list(
        (
            await db.execute(
                select(Policy)
                .where(Policy.experiment_id == experiment.id)
                .order_by(Policy.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    active = next((item for item in policies if item.status == PolicyStatus.ACTIVE.value), None)
    candidate = next(
        (
            item
            for item in policies
            if item.status
            in {
                PolicyStatus.CANDIDATE.value,
                PolicyStatus.REPLAYING.value,
                PolicyStatus.VALIDATED.value,
            }
        ),
        None,
    )
    return ExperimentResponse(
        id=experiment.id,
        name=experiment.name,
        task=experiment.task,
        scenario_id=experiment.scenario_id,
        execution_mode=ExecutionMode(experiment.execution_mode),
        created_at=experiment.created_at,
        runs=[run_response(item) for item in runs],
        active_policy=policy_response(active) if active else None,
        candidate_policy=policy_response(candidate) if candidate else None,
    )


async def create_experiment(db: AsyncSession, data: ExperimentCreate) -> ExperimentResponse:
    experiment = Experiment(
        id=new_id(),
        name=data.name.strip(),
        task=data.task.strip(),
        scenario_id=data.scenario_id,
        execution_mode=data.execution_mode.value,
    )
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment)
    return await experiment_response(db, experiment)


async def list_experiments(db: AsyncSession, limit: int, offset: int) -> list[ExperimentResponse]:
    experiments = list(
        (
            await db.execute(
                select(Experiment)
                .order_by(Experiment.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [await experiment_response(db, item) for item in experiments]


def _evaluation_spec(
    *,
    run_id: str,
    experiment: Experiment,
    seed: int,
    execution_mode: ExecutionMode | None = None,
    policy: PolicyPatch | None = None,
) -> EvaluationSpec:
    return EvaluationSpec(
        run_id=run_id,
        experiment_id=experiment.id,
        scenario_id=SCENARIO_ID,
        task=experiment.task,
        seed=seed,
        execution_mode=execution_mode or ExecutionMode(experiment.execution_mode),
        policy=policy,
        limits=EvaluationLimits(),
    )


async def create_baseline_run(db: AsyncSession, experiment_id: str, seed: int) -> RunResponse:
    experiment = await require_experiment(db, experiment_id)
    run_id = new_id()
    spec = _evaluation_spec(run_id=run_id, experiment=experiment, seed=seed)
    run = Run(
        id=run_id,
        experiment_id=experiment.id,
        kind="baseline",
        status=RunStatus.QUEUED.value,
        evaluation_spec=spec.model_dump(mode="json"),
        metrics={},
    )
    db.add_all([run, RunnerJob(run_id=run_id, attempt=1)])
    await db.commit()
    await db.refresh(run)
    return run_response(run)


async def cancel_run(db: AsyncSession, run_id: str) -> RunResponse:
    run = await require_run(db, run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        return run_response(run)

    job = await db.get(RunnerJob, run.id)
    now = utcnow()
    if run.status == RunStatus.QUEUED.value:
        run.status = RunStatus.CANCELLED.value
        run.completed_at = now
    else:
        run.status = RunStatus.CANCELLING.value
    if job is not None:
        job.cancel_requested_at = now
    await db.commit()
    await db.refresh(run)
    return run_response(run)


async def _recover_expired_jobs(db: AsyncSession) -> int:
    now = utcnow()
    jobs = list(
        (
            await db.execute(
                select(RunnerJob)
                .join(Run, RunnerJob.run_id == Run.id)
                .where(
                    Run.status.in_(RECOVERABLE_RUN_STATUSES),
                    RunnerJob.lease_id.is_not(None),
                    RunnerJob.lease_expires_at.is_not(None),
                    RunnerJob.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    recovered = 0
    for job in jobs:
        run = await require_run(db, job.run_id)
        previous_status = run.status
        history_attempt = job.attempt
        lease_id = job.lease_id
        runner_id = job.runner_id
        lease_expires_at = job.lease_expires_at
        assert lease_id is not None
        assert runner_id is not None
        assert lease_expires_at is not None
        reason = f"runner lease expired during {previous_status} (attempt {history_attempt})"
        history_outcome = "recovered"
        if history_attempt >= MAX_RUN_ATTEMPTS:
            status = (
                RunStatus.CANCELLED.value
                if job.cancel_requested_at is not None
                else RunStatus.FAILED.value
            )
            history_outcome = status
            error = f"{reason}; maximum of {MAX_RUN_ATTEMPTS} attempts reached"
            run.status = status
            run.error = error
            run.completed_at = now
            if run.started_at is None:
                run.started_at = now
            await _record_terminal_effects(
                db,
                run,
                status,
                {
                    "recovery_reason": reason,
                    "attempt": history_attempt,
                },
            )
            job.recovery_reason = error
        else:
            run.status = RunStatus.QUEUED.value
            run.queued_at = now
            run.claimed_at = None
            job.attempt += 1
            job.recovery_reason = reason
        db.add(
            RunnerAttempt(
                run_id=job.run_id,
                attempt=history_attempt,
                lease_id=lease_id,
                runner_id=runner_id,
                lease_expires_at=lease_expires_at,
                recovery_reason=reason,
                outcome=history_outcome,
                recorded_at=now,
            )
        )
        job.lease_id = None
        job.runner_id = None
        job.lease_expires_at = None
        recovered += 1
    if recovered:
        await db.flush()
    return recovered


async def next_event_sequence(db: AsyncSession, run_id: str) -> int:
    maximum = (
        await db.execute(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id))
    ).scalar_one()
    return int(maximum or 0) + 1


async def _record_runner_presence(
    db: AsyncSession, runner_id: str, now: datetime | None = None
) -> None:
    recorded_at = now or utcnow()
    presence = await db.get(RunnerPresence, runner_id)
    if presence is None:
        db.add(RunnerPresence(runner_id=runner_id, last_seen_at=recorded_at))
    else:
        presence.last_seen_at = recorded_at


async def runner_availability(db: AsyncSession) -> tuple[int, datetime | None]:
    now = utcnow()
    cutoff = now - timedelta(seconds=RUNNER_AVAILABILITY_SECONDS)
    active_count = (
        await db.execute(
            select(func.count()).select_from(RunnerPresence).where(
                RunnerPresence.last_seen_at >= cutoff
            )
        )
    ).scalar_one()
    freshest = (
        await db.execute(select(func.max(RunnerPresence.last_seen_at)))
    ).scalar_one()
    return int(active_count), _aware(freshest)


async def claim_job(db: AsyncSession, runner_id: str) -> tuple[RunnerJob, Run] | None:
    await _record_runner_presence(db, runner_id)
    recovered = await _recover_expired_jobs(db)
    candidates = list(
        (
            await db.execute(
                select(Run.id)
                .join(RunnerJob, RunnerJob.run_id == Run.id)
                .where(Run.status == RunStatus.QUEUED.value)
                .order_by(Run.queued_at.asc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    for run_id in candidates:
        now = utcnow()
        claimed = await db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.QUEUED.value)
            .values(status=RunStatus.CLAIMED.value, claimed_at=now)
        )
        if claimed.rowcount != 1:
            continue
        job = await db.get(RunnerJob, run_id)
        run = await db.get(Run, run_id)
        if job is None or run is None:
            await db.rollback()
            raise DomainError(500, "JOB_INVARIANT_BROKEN", "Run job is missing")
        job.lease_id = str(uuid4())
        job.runner_id = runner_id
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        await db.commit()
        await db.refresh(job)
        await db.refresh(run)
        return job, run
    await db.commit()
    return None


async def _leased_job(
    db: AsyncSession,
    *,
    lease_id: str,
    runner_id: str,
    run_id: str | None = None,
    allow_expired: bool = False,
) -> tuple[RunnerJob, Run]:
    job = (
        await db.execute(select(RunnerJob).where(RunnerJob.lease_id == lease_id))
    ).scalar_one_or_none()
    if job is None or job.runner_id != runner_id or (run_id and job.run_id != run_id):
        raise DomainError(403, "INVALID_LEASE", "Lease does not belong to this runner or run")
    run = await require_run(db, job.run_id)
    expires_at = _aware(job.lease_expires_at)
    if not allow_expired and (expires_at is None or expires_at <= utcnow()):
        raise DomainError(409, "LEASE_EXPIRED", "Runner lease has expired")
    return job, run


async def heartbeat(
    db: AsyncSession, lease_id: str, runner_id: str
) -> tuple[str, datetime]:
    job, run = await _leased_job(db, lease_id=lease_id, runner_id=runner_id)
    if run.status in TERMINAL_RUN_STATUSES:
        raise DomainError(409, "RUN_TERMINAL", "Run is already terminal")
    now = utcnow()
    await _record_runner_presence(db, runner_id, now)
    if run.status == RunStatus.CLAIMED.value:
        run.status = RunStatus.RUNNING.value
        run.started_at = now
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    command = (
        "cancel"
        if run.status == RunStatus.CANCELLING.value or job.cancel_requested_at is not None
        else "continue"
    )
    await db.commit()
    return command, job.lease_expires_at


async def persist_events(
    db: AsyncSession,
    *,
    run_id: str,
    lease_id: str,
    runner_id: str,
    events: list[EventEnvelope],
) -> tuple[int, list[dict[str, Any]]]:
    _, run = await _leased_job(
        db, lease_id=lease_id, runner_id=runner_id, run_id=run_id
    )
    if run.status in TERMINAL_RUN_STATUSES:
        raise DomainError(409, "RUN_TERMINAL", "Cannot append events to a terminal run")
    if any(item.run_id != run_id for item in events):
        raise DomainError(422, "EVENT_RUN_MISMATCH", "Every event must target the URL run")

    supplied_sequences = [item.sequence for item in events]
    if supplied_sequences != sorted(set(supplied_sequences)):
        raise DomainError(422, "INVALID_EVENT_ORDER", "Events must have unique increasing sequences")

    maximum = (
        await db.execute(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id))
    ).scalar_one()
    accepted_through = int(maximum or 0)
    existing = list(
        (
            await db.execute(
                select(RunEvent).where(
                    RunEvent.run_id == run_id,
                    RunEvent.sequence.in_(supplied_sequences),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_sequence = {item.sequence: item for item in existing}
    sanitized_payloads = {
        event.sequence: _sanitize_event_payload(event.type, event.payload)
        for event in events
    }
    for event in events:
        previous = existing_by_sequence.get(event.sequence)
        if previous is None:
            continue
        previous_payload = _sanitize_event_payload(previous.event_type, previous.payload)
        if (
            previous.event_type != event.type
            or previous_payload != sanitized_payloads[event.sequence]
        ):
            raise DomainError(
                409,
                "EVENT_CONFLICT",
                "A different event already uses this sequence",
                {"sequence": event.sequence},
            )
        if previous.payload != previous_payload:
            previous.payload = previous_payload

    pending = [item for item in events if item.sequence > accepted_through]
    expected = accepted_through + 1
    for event in pending:
        if event.sequence != expected:
            raise DomainError(
                409,
                "EVENT_SEQUENCE_GAP",
                "Event sequence must be contiguous",
                {"expected": expected, "received": event.sequence},
            )
        expected += 1

    now = utcnow()
    if run.status == RunStatus.CLAIMED.value:
        run.status = RunStatus.RUNNING.value
        run.started_at = now
    new_payloads: list[dict[str, Any]] = []
    for event in pending:
        payload = sanitized_payloads[event.sequence]
        row = RunEvent(
            run_id=run_id,
            sequence=event.sequence,
            event_type=event.type,
            payload=payload,
            occurred_at=event.occurred_at,
        )
        db.add(row)
        published = event.model_dump(mode="json")
        published["payload"] = payload
        new_payloads.append(published)
        accepted_through = event.sequence
    await db.commit()
    return accepted_through, new_payloads


async def _trajectory_from_events(db: AsyncSession, run: Run) -> tuple[dict, dict]:
    events = list(
        (
            await db.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run.id)
                .order_by(RunEvent.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    steps: list[dict] = []
    latest_attempt_start = 0
    for index, event in enumerate(events):
        if event.event_type != "run_started":
            continue
        attempt = event.payload.get("attempt")
        if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1:
            latest_attempt_start = index

    latest_events = events[latest_attempt_start:]
    for event in latest_events:
        if event.event_type != "step_completed":
            continue
        payload = event.payload
        steps.append(
            {
                "index": payload.get("index", len(steps)),
                "action": payload.get("tool_call"),
                "observation": payload.get("observation", ""),
                "latency_ms": payload.get("latency_ms", 0),
                "context_window": payload.get("context_window"),
                "token_prompt": payload.get("token_prompt", 0),
                "token_completion": payload.get("token_completion", 0),
            }
        )
    prompt_tokens = sum(int(item.get("token_prompt") or 0) for item in steps)
    completion_tokens = sum(int(item.get("token_completion") or 0) for item in steps)
    total_latency = sum(int(item.get("latency_ms") or 0) for item in steps)
    max_steps = 6
    policy = run.evaluation_spec.get("policy")
    if isinstance(policy, dict):
        max_steps = int(policy.get("max_steps") or max_steps)
    trajectory = {
        "status": "success" if run.status == RunStatus.SUCCEEDED.value else "failed",
        "steps": steps,
        "max_steps": max_steps,
        "total_tokens": prompt_tokens + completion_tokens,
        "total_latency_ms": total_latency,
    }
    metrics = {
        "steps": len(steps),
        "latency_ms": total_latency,
        "token_prompt": prompt_tokens,
        "token_completion": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    metrics.update(_provider_metrics(latest_events))
    return trajectory, metrics


def _provider_metrics(events: list[RunEvent]) -> dict[str, object]:
    """Project safe provider telemetry from the latest immutable attempt."""

    model: str | None = None
    request_fingerprints: list[str] = []
    latency_ms = 0
    request_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    saw_provider = False
    provider_error: dict[str, object] | None = None

    for event in events:
        if event.event_type != "process_output":
            continue
        payload = event.payload
        parsed_provider = _provider_telemetry_correlation(payload.get("provider"))
        if parsed_provider is not None:
            saw_provider = True
            model = parsed_provider.model
            latency_ms = min(
                MAX_PROVIDER_LATENCY_MS, latency_ms + parsed_provider.latency_ms
            )
            request_count = min(
                MAX_PROVIDER_REQUEST_COUNT,
                request_count + parsed_provider.request_count,
            )
            prompt_tokens = min(
                MAX_PROVIDER_TOKENS, prompt_tokens + parsed_provider.token_prompt
            )
            completion_tokens = min(
                MAX_PROVIDER_TOKENS,
                completion_tokens + parsed_provider.token_completion,
            )
            fingerprint = parsed_provider.request_fingerprint
            if (
                fingerprint is not None
                and fingerprint not in request_fingerprints
                and len(request_fingerprints) < 20
            ):
                request_fingerprints.append(fingerprint)

        parsed_error = _provider_error(payload.get("provider_error"))
        if parsed_error is not None:
            provider_error = parsed_error
            fingerprint = parsed_error.get("request_fingerprint")
            if (
                isinstance(fingerprint, str)
                and fingerprint not in request_fingerprints
                and len(request_fingerprints) < 20
            ):
                request_fingerprints.append(fingerprint)

    metrics: dict[str, object] = {}
    if saw_provider:
        provider_metrics = ProviderTelemetryMetrics(
            model=model or "unknown",
            latency_ms=latency_ms,
            request_count=request_count,
            token_prompt=prompt_tokens,
            token_completion=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            request_fingerprints=request_fingerprints,
        )
        provider_payload = provider_metrics.model_dump(mode="json")
        if not provider_payload["request_fingerprints"]:
            provider_payload.pop("request_fingerprints")
        metrics["provider"] = provider_payload
    if provider_error is not None:
        metrics["provider_error"] = provider_error
    return metrics


def _nonnegative_metric(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _provider_terminal_error(metrics: dict) -> str | None:
    provider_error = metrics.get("provider_error")
    if not isinstance(provider_error, dict):
        return None
    code = provider_error.get("code")
    message = provider_error.get("message")
    if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
        return None
    return f"{code}: {message}"


async def _analyze_and_score(
    db: AsyncSession, run: Run, runner_metrics: dict | None
) -> None:
    trajectory, computed_metrics = await _trajectory_from_events(db, run)
    score_result = compute_score(trajectory)
    run.score = float(score_result["score"])
    safe_runner_metrics: dict[str, int] = {}
    if isinstance(runner_metrics, dict) and "event_retries" in runner_metrics:
        safe_runner_metrics["event_retries"] = _nonnegative_metric(
            runner_metrics.get("event_retries")
        )
    run.metrics = {
        **safe_runner_metrics,
        **computed_metrics,
        "score_breakdown": score_result["breakdown"],
    }
    report = analyze_trajectory(trajectory)
    dimensions = {key: float(value) for key, value in report.dimensions.items()}
    analysis = RunAnalysis(
        run_id=run.id,
        dimensions=dimensions,
        evidence=[asdict(item) for item in report.evidence],
        dominant_type=report.dominant,
        failure_rate=max(dimensions.values(), default=0.0),
    )
    db.add(analysis)


async def _create_candidate(db: AsyncSession, run: Run) -> None:
    existing = (
        await db.execute(select(Policy).where(Policy.source_run_id == run.id))
    ).scalar_one_or_none()
    if existing is not None:
        return
    active = (
        await db.execute(
            select(Policy).where(
                Policy.experiment_id == run.experiment_id,
                Policy.status == PolicyStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    patch = PolicyPatch(
        instruction_patch=[
            "Do not repeat a tool call with identical arguments.",
            "Check service health, then metrics, then fetch logs supported by evidence.",
        ],
        tool_priority={
            "check_service_health": 1.0,
            "query_service_metrics": 0.9,
            "fetch_service_logs": 0.3,
        },
        max_steps=6,
    )
    db.add(
        Policy(
            id=new_id(),
            experiment_id=run.experiment_id,
            source_run_id=run.id,
            parent_policy_id=active.id if active else None,
            status=PolicyStatus.CANDIDATE.value,
            patch=patch.model_dump(mode="json"),
            rationale=(
                "The baseline repeated the same log query until its step budget was "
                "exhausted. The candidate enforces an evidence-first tool order."
            ),
        )
    )


async def _record_terminal_effects(
    db: AsyncSession,
    run: Run,
    status: str,
    metrics: dict | None,
) -> None:
    await _analyze_and_score(db, run, metrics)
    if run.kind == "baseline" and status in {
        RunStatus.FAILED.value,
        RunStatus.TIMED_OUT.value,
    }:
        await _create_candidate(db, run)
    if run.kind == "replay" and run.policy_id:
        policy = await require_policy(db, run.policy_id)
        baseline = await require_run(db, run.source_run_id or policy.source_run_id)
        policy.score_delta = (run.score or 0.0) - (baseline.score or 0.0)
        policy.status = (
            PolicyStatus.VALIDATED.value
            if status == RunStatus.SUCCEEDED.value and policy.score_delta > 0
            else PolicyStatus.CANDIDATE.value
        )


async def complete_job(
    db: AsyncSession,
    *,
    lease_id: str,
    runner_id: str,
    status: str,
    error: str | None,
    metrics: dict | None,
) -> RunResponse:
    job, run = await _leased_job(
        db, lease_id=lease_id, runner_id=runner_id, allow_expired=True
    )
    if run.status in TERMINAL_RUN_STATUSES:
        return run_response(run)
    expires_at = _aware(job.lease_expires_at)
    if expires_at is None or expires_at <= utcnow():
        raise DomainError(409, "LEASE_EXPIRED", "Runner lease has expired")
    if run.status == RunStatus.CANCELLING.value or job.cancel_requested_at is not None:
        status = RunStatus.CANCELLED.value
    run.status = status
    run.error = error
    run.completed_at = utcnow()
    if run.started_at is None:
        run.started_at = run.completed_at

    await _record_terminal_effects(db, run, status, metrics)
    if status == RunStatus.FAILED.value and (
        run.error is None or run.error.startswith("agent exited with")
    ):
        run.error = _provider_terminal_error(run.metrics) or run.error
    await db.commit()
    await db.refresh(run)
    return run_response(run)


async def get_analysis(db: AsyncSession, run_id: str) -> AnalysisResponse:
    run = await require_run(db, run_id)
    analysis = await db.get(RunAnalysis, run_id)
    if analysis is None:
        if run.status not in TERMINAL_RUN_STATUSES:
            raise DomainError(409, "ANALYSIS_PENDING", "Analysis is not ready")
        raise DomainError(404, "ANALYSIS_NOT_FOUND", "No analysis exists for this run")
    return AnalysisResponse.model_validate(analysis)


async def replay_policy(db: AsyncSession, policy_id: str) -> PolicyResponse:
    policy = await require_policy(db, policy_id)
    if policy.status in {
        PolicyStatus.REJECTED.value,
        PolicyStatus.ACTIVE.value,
        PolicyStatus.SUPERSEDED.value,
    }:
        raise DomainError(409, "POLICY_NOT_REPLAYABLE", "Policy cannot be replayed in its current state")
    if policy.replay_run_id is not None:
        return policy_response(policy)

    baseline = await require_run(db, policy.source_run_id)
    experiment = await require_experiment(db, policy.experiment_id)
    baseline_spec = EvaluationSpec.model_validate(baseline.evaluation_spec)
    run_id = new_id()
    replay_spec = _evaluation_spec(
        run_id=run_id,
        experiment=experiment,
        seed=baseline_spec.seed,
        execution_mode=baseline_spec.execution_mode,
        policy=PolicyPatch.model_validate(policy.patch),
    )
    run = Run(
        id=run_id,
        experiment_id=experiment.id,
        kind="replay",
        source_run_id=baseline.id,
        policy_id=policy.id,
        status=RunStatus.QUEUED.value,
        evaluation_spec=replay_spec.model_dump(mode="json"),
        metrics={},
    )
    db.add_all([run, RunnerJob(run_id=run.id, attempt=1)])
    await db.flush()
    policy.status = PolicyStatus.REPLAYING.value
    policy.replay_run_id = run.id
    await db.commit()
    await db.refresh(policy)
    return policy_response(policy)


async def activate_policy(db: AsyncSession, policy_id: str) -> PolicyResponse:
    policy = await require_policy(db, policy_id)
    if policy.status != PolicyStatus.VALIDATED.value:
        raise DomainError(409, "POLICY_NOT_VALIDATED", "Only a validated policy can be activated")
    if policy.score_delta is None or policy.score_delta <= 0 or not policy.replay_run_id:
        raise DomainError(409, "POLICY_NOT_IMPROVED", "Replay must succeed with a positive score delta")
    replay = await require_run(db, policy.replay_run_id)
    if replay.status != RunStatus.SUCCEEDED.value:
        raise DomainError(409, "REPLAY_NOT_SUCCEEDED", "Replay did not succeed")
    await db.execute(
        update(Policy)
        .where(
            Policy.experiment_id == policy.experiment_id,
            Policy.status == PolicyStatus.ACTIVE.value,
            Policy.id != policy.id,
        )
        .values(status=PolicyStatus.SUPERSEDED.value)
    )
    policy.status = PolicyStatus.ACTIVE.value
    await db.commit()
    await db.refresh(policy)
    return policy_response(policy)


async def reject_policy(
    db: AsyncSession, policy_id: str, reason: str | None
) -> PolicyResponse:
    policy = await require_policy(db, policy_id)
    if policy.status not in {
        PolicyStatus.CANDIDATE.value,
        PolicyStatus.VALIDATED.value,
    }:
        raise DomainError(409, "POLICY_NOT_REJECTABLE", "Policy cannot be rejected in its current state")
    policy.status = PolicyStatus.REJECTED.value
    policy.reject_reason = reason
    await db.commit()
    await db.refresh(policy)
    return policy_response(policy)
