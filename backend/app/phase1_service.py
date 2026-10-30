"""Application services for experiments, runs, leases, and policy replay.

HTTP routes call these services to persist Run state and queue Runner jobs.
Run creation must validate the effective agent before committing queued work.
Read ``app.phase1_main`` next for the HTTP transaction and error boundary.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_registry import AgentRegistryError, load_registry
from app.failure_analyzer import analyze_trajectory
from app.final_evidence import FinalCheck, validate_final_check
from app.phase1_events import event_notifier
from app.scenario_assertions import evaluate_assertion_suite
from app.scoring import compute_score
import app.scenarios  # noqa: F401 — register built-in Scenarios
from app.scenario_registry import (
    ScenarioParamError,
    UnknownScenarioError,
    assertion_suite_for_scenario,
    candidate_policy_for_scenario,
    ensure_registered_scenario,
    list_scenarios,
)
from app.phase1_models import (
    Experiment,
    Policy,
    RegressionCase,
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
    ExecutionFacts,
    CompletionResponse,
    validate_runner_event,
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
    ProviderTelemetryCorrelation,
    ProviderTelemetryMetrics,
    RunCorrelation,
    RunDiagnosticsResponse,
    RunOperationalMetrics,
    RunTiming,
    TerminalRunOutcome,
    PolicyPatch,
    PolicyResponse,
    PolicyStatus,
    RegressionCaseResponse,
    RegressionReproducibility,
    RunResponse,
    RunStatus,
    ScenarioParamSummary,
    ScenarioSummary,
    TERMINAL_RUN_STATUSES,
)
from app.durable_events import (
    TerminalFailureKind,
    normalize_event_payload,
    normalize_terminal_failure_kind,
    provider_failure_message,
    request_fingerprint,
    terminal_failure_message,
)


LEASE_SECONDS = 15
RUNNER_AVAILABILITY_SECONDS = 15
MAX_RUN_ATTEMPTS = 3
ANALYSIS_METRIC_DIRECTIONS: dict[str, str] = {
    "score": "maximize",
    "failure_rate": "minimize",
    "latency_ms": "minimize",
    "total_tokens": "minimize",
    "planning": "minimize",
    "execution": "minimize",
    "context": "minimize",
    "budget": "minimize",
}
RECOVERABLE_RUN_STATUSES = {
    RunStatus.CLAIMED.value,
    RunStatus.RUNNING.value,
    RunStatus.CANCELLING.value,
}
NON_REPLAYABLE_POLICY_STATUSES = {
    PolicyStatus.REJECTED.value,
    PolicyStatus.ACTIVE.value,
    PolicyStatus.SUPERSEDED.value,
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


REGRESSION_CASE_SOURCE_UNIQUE_CONSTRAINT = "uq_regression_cases_source_run"
RUN_EVENT_SEQUENCE_UNIQUE_CONSTRAINT = "uq_run_events_run_sequence"


def _is_regression_case_source_unique_violation(exc: IntegrityError) -> bool:
    """Return whether the database rejected a duplicate regression source run."""
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        constraint_name = getattr(current, "constraint_name", None) or getattr(
            getattr(current, "diag", None), "constraint_name", None
        )
        if constraint_name == REGRESSION_CASE_SOURCE_UNIQUE_CONSTRAINT:
            return True
        message = str(current).lower()
        if message == "unique constraint failed: regression_cases.source_run_id":
            return True
        if f'unique constraint "{REGRESSION_CASE_SOURCE_UNIQUE_CONSTRAINT}"' in message:
            return True
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _is_run_event_sequence_unique_violation(exc: IntegrityError) -> bool:
    """Return whether the database rejected a duplicate run event sequence."""
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        constraint_name = getattr(current, "constraint_name", None) or getattr(
            getattr(current, "diag", None), "constraint_name", None
        )
        if constraint_name == RUN_EVENT_SEQUENCE_UNIQUE_CONSTRAINT:
            return True
        message = str(current).lower()
        if message in {
            "unique constraint failed: run_events.run_id, run_events.sequence",
            "unique constraint failed: run_events.run_id, run_events.sequence\n",
        }:
            return True
        if f'unique constraint "{RUN_EVENT_SEQUENCE_UNIQUE_CONSTRAINT}"' in message:
            return True
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def run_response(run: Run) -> RunResponse:
    return RunResponse.model_validate(run)


def _resolve_agent(agent_id: str | None, execution_mode: ExecutionMode) -> str:
    """Validate the effective operator registration before durable writes."""
    try:
        registry = load_registry()
    except AgentRegistryError as exc:
        raise DomainError(503, "AGENT_REGISTRY_INVALID", "Agent registry is invalid") from exc
    selected = agent_id or (
        "fixture-agent.v1"
        if execution_mode == ExecutionMode.FIXTURE
        else "reference-openai-compatible.v1"
    )
    try:
        return registry.resolve(selected, execution_mode.value)
    except AgentRegistryError as exc:
        raise DomainError(
            400,
            "AGENT_NOT_REGISTERED",
            "Agent is not registered",
            {"agent_id": selected},
        ) from exc


def list_registered_agents() -> list[dict[str, str]]:
    try:
        return [item.public_dict() for item in load_registry().metadata()]
    except AgentRegistryError as exc:
        raise DomainError(503, "AGENT_REGISTRY_INVALID", "Agent registry is invalid") from exc


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
    return request_fingerprint(value)


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
    normalized = normalize_event_payload(
        "process_output", {"kind": "provider", "provider_error": value}
    ).get("provider_error")
    if normalized is None:
        return None
    try:
        parsed = ProviderErrorCorrelation.model_validate(normalized)
    except ValidationError:
        return None
    return parsed.model_dump(mode="json", exclude_none=True)


def _sanitize_provider_telemetry(value: object) -> dict[str, object] | None:
    normalized = normalize_event_payload(
        "process_output", {"kind": "provider", "provider": value}
    ).get("provider")
    return normalized if isinstance(normalized, dict) else None


def _sanitize_provider_error(value: object) -> dict[str, object] | None:
    normalized = normalize_event_payload(
        'process_output', {'kind': 'provider', 'provider_error': value}
    ).get('provider_error')
    return normalized if isinstance(normalized, dict) else None


def _sanitize_event_payload(event_type: str, payload: object) -> dict[str, object]:
    normalized = normalize_event_payload(event_type, payload)
    if event_type == "process_output" and isinstance(payload, dict):
        attempt = payload.get("attempt")
        if type(attempt) is int and 1 <= attempt <= 100:
            normalized["attempt"] = attempt
    return normalized


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
    from app.operational_state import collect_operations_overview

    return await collect_operations_overview(db)


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
    regression_cases = list(
        (
            await db.execute(
                select(RegressionCase)
                .where(RegressionCase.experiment_id == experiment.id)
                .order_by(RegressionCase.created_at.desc())
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
        scenario_params=dict(experiment.scenario_params or {}),
        execution_mode=ExecutionMode(experiment.execution_mode),
        agent_id=experiment.agent_id,
        created_at=experiment.created_at,
        runs=[run_response(item) for item in runs],
        regression_cases=[RegressionCaseResponse.model_validate(item) for item in regression_cases],
        active_policy=policy_response(active) if active else None,
        candidate_policy=policy_response(candidate) if candidate else None,
    )


async def create_experiment(db: AsyncSession, data: ExperimentCreate) -> ExperimentResponse:
    _resolve_agent(data.agent_id, data.execution_mode)
    try:
        normalized_params = ensure_registered_scenario(data.scenario_id, data.scenario_params)
    except UnknownScenarioError as exc:
        raise DomainError(
            400,
            "SCENARIO_NOT_REGISTERED",
            str(exc),
            {"scenario_id": exc.scenario_id},
        ) from exc
    except ScenarioParamError as exc:
        raise DomainError(
            400,
            "INVALID_SCENARIO_PARAMS",
            str(exc),
        ) from exc
    except ValueError as exc:
        raise DomainError(
            422,
            "INVALID_SCENARIO_ID",
            str(exc),
        ) from exc

    experiment = Experiment(
        id=new_id(),
        name=data.name.strip(),
        task=data.task.strip(),
        scenario_id=data.scenario_id,
        scenario_params=dict(normalized_params),
        execution_mode=data.execution_mode.value,
        agent_id=data.agent_id,
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


def list_registered_scenarios() -> list[ScenarioSummary]:
    return [
        ScenarioSummary(
            id=item.id,
            name=item.name,
            description=item.description,
            default_task=item.default_task,
            allowed_tools=list(item.allowed_tools),
            params=[
                ScenarioParamSummary(
                    name=param.name,
                    description=param.description,
                    required=param.required,
                )
                for param in item.params
            ],
        )
        for item in list_scenarios()
    ]


def _evaluation_spec(
    *,
    run_id: str,
    experiment: Experiment,
    seed: int,
    execution_mode: ExecutionMode | None = None,
    agent_id: str | None = None,
    policy: PolicyPatch | None = None,
) -> EvaluationSpec:
    selected_mode = execution_mode or ExecutionMode(experiment.execution_mode)
    selected_agent = _resolve_agent(
        experiment.agent_id if agent_id is None else agent_id,
        selected_mode,
    )
    return EvaluationSpec(
        run_id=run_id,
        experiment_id=experiment.id,
        scenario_id=experiment.scenario_id,
        task=experiment.task,
        seed=seed,
        execution_mode=selected_mode,
        agent_id=selected_agent,
        policy=policy,
        limits=EvaluationLimits(),
        scenario_params=dict(experiment.scenario_params or {}),
    )


def _ensure_experiment_scenario(experiment: Experiment) -> None:
    try:
        ensure_registered_scenario(
            experiment.scenario_id,
            dict(experiment.scenario_params or {}),
        )
    except UnknownScenarioError as exc:
        raise DomainError(
            400,
            "SCENARIO_NOT_REGISTERED",
            str(exc),
            {"scenario_id": exc.scenario_id},
        ) from exc
    except ScenarioParamError as exc:
        raise DomainError(
            400,
            "INVALID_SCENARIO_PARAMS",
            str(exc),
        ) from exc
    except ValueError as exc:
        raise DomainError(
            422,
            "INVALID_SCENARIO_ID",
            str(exc),
        ) from exc


async def create_baseline_run(db: AsyncSession, experiment_id: str, seed: int) -> RunResponse:
    experiment = await require_experiment(db, experiment_id)
    _ensure_experiment_scenario(experiment)
    _resolve_agent(experiment.agent_id, ExecutionMode(experiment.execution_mode))
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


async def list_regression_cases(
    db: AsyncSession, experiment_id: str
) -> list[RegressionCaseResponse]:
    await require_experiment(db, experiment_id)
    cases = list(
        (
            await db.execute(
                select(RegressionCase)
                .where(RegressionCase.experiment_id == experiment_id)
                .order_by(RegressionCase.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [RegressionCaseResponse.model_validate(item) for item in cases]


async def create_regression_case(
    db: AsyncSession, run_id: str
) -> RegressionCaseResponse:
    run = await require_run(db, run_id)
    existing = (
        await db.execute(
            select(RegressionCase).where(RegressionCase.source_run_id == run.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return RegressionCaseResponse.model_validate(existing)
    if run.status not in {RunStatus.FAILED.value, RunStatus.TIMED_OUT.value}:
        raise DomainError(
            409,
            "REGRESSION_CASE_SOURCE_NOT_ELIGIBLE",
            "Only failed or timed-out runs can be saved as regression cases",
        )

    spec = EvaluationSpec.model_validate(run.evaluation_spec)
    deterministic = spec.execution_mode == ExecutionMode.FIXTURE
    case = RegressionCase(
        id=new_id(),
        experiment_id=run.experiment_id,
        source_run_id=run.id,
        scenario_id=spec.scenario_id,
        seed=spec.seed,
        scenario_params=dict(spec.scenario_params),
        execution_mode=spec.execution_mode.value,
        reproducibility=(
            RegressionReproducibility.DETERMINISTIC.value
            if deterministic
            else RegressionReproducibility.PROVIDER_BOUNDARY.value
        ),
        reproducibility_note=(
            "Fixture execution is deterministic for the stored scenario, seed, and parameters."
            if deterministic
            else "Provider execution depends on external model behavior and cannot be completely reproduced."
        ),
        eligible_for_replay=deterministic,
    )
    db.add(case)
    run.regression_case_id = case.id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if not _is_regression_case_source_unique_violation(exc):
            raise
        existing = (
            await db.execute(
                select(RegressionCase).where(RegressionCase.source_run_id == run_id)
            )
        ).scalar_one()
        return RegressionCaseResponse.model_validate(existing)
    await db.refresh(case)
    return RegressionCaseResponse.model_validate(case)


async def cancel_run(db: AsyncSession, run_id: str) -> RunResponse:
    while True:
        # Match the Job -> Run lock order used by lease writers.
        job = (
            await db.execute(
                select(RunnerJob)
                .where(RunnerJob.run_id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        run = await require_run(db, run_id)
        await db.refresh(run)
        if run.status in TERMINAL_RUN_STATUSES:
            return run_response(run)

        if job is None:
            raise DomainError(500, "JOB_INVARIANT_BROKEN", "Run job is missing")
        job.cancel_requested_at = utcnow()
        if run.status == RunStatus.QUEUED.value:
            if not await _terminalize(
                db, job, run, "cancelled", TerminalFailureKind.CANCELLED, None
            ):
                await db.rollback()
                continue
        else:
            changed = await db.execute(
                update(Run)
                .where(Run.id == run.id, Run.status == run.status)
                .values(status=RunStatus.CANCELLING.value)
            )
            if changed.rowcount != 1:
                await db.rollback()
                continue
        await _commit_terminal_notifications(db)
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
                .with_for_update(of=RunnerJob, skip_locked=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    recovered = 0
    for job in jobs:
        run = await require_run(db, job.run_id)
        # The joined eligibility predicate came from an earlier statement
        # snapshot.  Job-first locking gives us ownership, but the Run and its
        # lease predicate must be revalidated after that lock is acquired.
        await db.refresh(run)
        lease_expires_at = _aware(job.lease_expires_at)
        if (
            run.status not in RECOVERABLE_RUN_STATUSES
            or job.lease_id is None
            or job.runner_id is None
            or lease_expires_at is None
            or lease_expires_at > now
        ):
            continue
        previous_status = run.status
        history_attempt = job.attempt
        lease_id = job.lease_id
        runner_id = job.runner_id
        assert lease_id is not None
        assert runner_id is not None
        assert lease_expires_at is not None
        reason = (
            f"runner lease expired during {previous_status} (attempt {history_attempt})"
        )
        history_outcome = "recovered"
        if history_attempt >= MAX_RUN_ATTEMPTS:
            status = (
                RunStatus.CANCELLED.value
                if job.cancel_requested_at is not None
                else RunStatus.FAILED.value
            )
            history_outcome = status
            error = f"{reason}; maximum of {MAX_RUN_ATTEMPTS} attempts reached"
            if not await _terminalize(
                db,
                job,
                run,
                status,
                (
                    TerminalFailureKind.CANCELLED
                    if status == "cancelled"
                    else TerminalFailureKind.INTERNAL_FAILURE
                ),
                None,
            ):
                raise DomainError(
                    409, "RUN_STATE_CHANGED", "Recovery lost terminal compare-and-set"
                )
            run.error = error
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
        if history_outcome == "recovered":
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
        # Claim and cancellation must acquire Job before updating Run.
        job = (
            await db.execute(
                select(RunnerJob)
                .where(RunnerJob.run_id == run_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if job is None:
            continue
        now = utcnow()
        claimed = await db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.QUEUED.value)
            .values(status=RunStatus.CLAIMED.value, claimed_at=now)
        )
        if claimed.rowcount != 1:
            continue
        run = await db.get(Run, run_id, populate_existing=True)
        if run is None:
            await db.rollback()
            raise DomainError(500, "JOB_INVARIANT_BROKEN", "Run job is missing")
        job.lease_id = str(uuid4())
        job.runner_id = runner_id
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        await _commit_terminal_notifications(db)
        await db.refresh(job)
        await db.refresh(run)
        return job, run
    await _commit_terminal_notifications(db)
    return None


async def _leased_job(
    db: AsyncSession,
    *,
    lease_id: str,
    runner_id: str,
    run_id: str | None = None,
    allow_expired: bool = False,
    for_update: bool = False,
) -> tuple[RunnerJob, Run]:
    statement = select(RunnerJob).where(RunnerJob.lease_id == lease_id).execution_options(populate_existing=True)
    if for_update:
        statement = statement.with_for_update()
    job = (await db.execute(statement)).scalar_one_or_none()
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
    while True:
        job, run = await _leased_job(
            db, lease_id=lease_id, runner_id=runner_id, for_update=True
        )
        await db.refresh(job)
        await db.refresh(run)
        if run.status in TERMINAL_RUN_STATUSES:
            raise DomainError(409, "RUN_TERMINAL", "Run is already terminal")
        now = utcnow()
        await _record_runner_presence(db, runner_id, now)
        if run.status == RunStatus.CLAIMED.value:
            claimed = await db.execute(
                update(Run)
                .where(Run.id == run.id, Run.status == RunStatus.CLAIMED.value)
                .values(status=RunStatus.RUNNING.value, started_at=now)
            )
            if claimed.rowcount != 1:
                await db.rollback()
                continue
            await db.refresh(run)
        refreshed_until = now + timedelta(seconds=LEASE_SECONDS)
        job.lease_expires_at = refreshed_until
        command = (
            "cancel"
            if run.status == RunStatus.CANCELLING.value or job.cancel_requested_at is not None
            else "continue"
        )
        await db.commit()
        return command, refreshed_until


async def persist_events(
    db: AsyncSession,
    *,
    run_id: str,
    lease_id: str,
    runner_id: str,
    events: list[EventEnvelope],
) -> tuple[int, list[dict[str, Any]]]:
    for event in events:
        try:
            validate_runner_event(event.type, event.payload)
        except ValueError as exc:
            raise DomainError(422, "INVALID_EVENT_GRAMMAR", str(exc)) from exc
    supplied_sequences = [item.sequence for item in events]
    if supplied_sequences != sorted(set(supplied_sequences)):
        raise DomainError(422, "INVALID_EVENT_ORDER", "Events must have unique increasing sequences")
    sanitized_payloads = {
        event.sequence: _sanitize_event_payload(event.type, event.payload)
        for event in events
    }

    while True:
        # Keep the initial read non-locking so concurrent duplicate uploads can
        # observe the same sequence state. Immediately reacquire the Job row
        # with a lock before inserting; recovery cannot clear this lease while
        # the event transaction is committing.
        _, run = await _leased_job(
            db, lease_id=lease_id, runner_id=runner_id, run_id=run_id
        )
        job, run = await _leased_job(
            db,
            lease_id=lease_id,
            runner_id=runner_id,
            run_id=run_id,
            for_update=True,
        )
        await db.refresh(run)
        if any(item.run_id != run_id for item in events):
            raise DomainError(422, "EVENT_RUN_MISMATCH", "Every event must target the URL run")

        if any(event.payload.get("attempt") != job.attempt for event in events):
            raise DomainError(409, "ATTEMPT_MISMATCH", "Event targets another Attempt")

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
        if not pending:
            await db.commit()
            return accepted_through, []
        if run.status in TERMINAL_RUN_STATUSES:
            raise DomainError(409, "RUN_TERMINAL", "Cannot append events to a terminal run")

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

        history = list((await db.execute(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
        )).scalars().all())
        current = [item for item in history if item.payload.get("attempt") == job.attempt]
        started = any(item.event_type == "run_started" for item in current)
        accepted_final = any("final_check" in item.payload.get("environment", {}) for item in current)
        for event in pending:
            if event.payload.get("attempt") != job.attempt:
                raise DomainError(409, "ATTEMPT_MISMATCH", "Event targets another Attempt")
            if event.type == "run_started":
                if started:
                    raise DomainError(409, "EVENT_GRAMMAR_CONFLICT", "Attempt already started")
                started = True
            elif not started:
                raise DomainError(422, "MISSING_RUN_STARTED", "Attempt must start before facts")
            provenance = event.payload.get("environment", {})
            if "final_check" in provenance:
                if accepted_final:
                    raise DomainError(409, "FINAL_CHECK_CONFLICT", "Attempt already has accepted evidence")
                try:
                    validate_final_check(provenance["final_check"], scenario_id=run.evaluation_spec["scenario_id"])
                except ValueError as exc:
                    raise DomainError(422, "INVALID_FINAL_CHECK", str(exc)) from exc
                accepted_final = True

        now = utcnow()
        if run.status == RunStatus.CLAIMED.value:
            claimed = await db.execute(
                update(Run)
                .where(Run.id == run.id, Run.status == RunStatus.CLAIMED.value)
                .values(status=RunStatus.RUNNING.value, started_at=now)
            )
            if claimed.rowcount != 1:
                await db.rollback()
                continue
            await db.refresh(run)
        else:
            guarded = await db.execute(
                update(Run)
                .where(Run.id == run.id, Run.status == run.status)
                .values(status=run.status)
            )
            if guarded.rowcount != 1:
                await db.rollback()
                continue

        new_payloads: list[dict[str, Any]] = []
        for event in pending:
            payload = sanitized_payloads[event.sequence]
            db.add(
                RunEvent(
                    run_id=run_id,
                    sequence=event.sequence,
                    event_type=event.type,
                    payload=payload,
                    occurred_at=event.occurred_at,
                )
            )
            published = event.model_dump(mode="json")
            published["payload"] = payload
            new_payloads.append(published)
            accepted_through = event.sequence
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            if not _is_run_event_sequence_unique_violation(exc):
                raise
            continue
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
    job = await db.get(RunnerJob, run.id)
    assert job is not None
    # A current-attempt interval starts at the last validated current
    # run_started marker.  Records before that boundary are historical, but a
    # malformed identity after it is corruption in the current durable prefix,
    # not an event that may be silently filtered out.
    current_start_sequence: int | None = None
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.event_type == "run_started"
            and type(payload.get("attempt")) is int
            and payload.get("attempt") == job.attempt
            and payload.get("source") == "environment"
        ):
            if current_start_sequence is None:
                current_start_sequence = event.sequence
    latest_events = (
        [event for event in events if event.sequence >= current_start_sequence]
        if current_start_sequence is not None
        else [
            event
            for event in events
            if type(event.payload.get("attempt")) is int
            and event.payload["attempt"] == job.attempt
        ]
    )
    # A start-less historical record remains inspectable for diagnostics, but
    # it can never authorize success because the boundary is unvalidated.
    evidence_invalid = current_start_sequence is None
    provider_error = any(
        event.event_type == "process_output"
        and isinstance(event.payload.get("provider_error"), dict)
        for event in latest_events
    )
    environment_seen = bool(
        latest_events
        and latest_events[0].event_type == "run_started"
        and latest_events[0].payload.get("source") == "environment"
    )
    environment_finalized = False
    environment_check_called = False
    final_check: FinalCheck | None = None
    evidence_invalid = evidence_invalid or not environment_seen
    start_count = 0
    final_count = 0
    for event in latest_events:
        if event.event_type in {"run_completed", "run_failed", "run_cancelled"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("attempt") != job.attempt:
            evidence_invalid = True
        try:
            validate_runner_event(event.event_type, event.payload)
        except ValueError:
            # Do not allow normalize_event_payload below to erase a malformed
            # attempt identity from the current durable sequence interval.
            evidence_invalid = True
        if event.event_type == "run_started":
            start_count += 1
            if start_count != 1:
                evidence_invalid = True
    agent_usage: dict[str, object] = {
        "source": "agent_reported",
        "token_prompt": None,
        "token_completion": None,
        "context_window": None,
    }
    for event in latest_events:
        if event.event_type != "step_completed":
            continue
        payload = event.payload
        try:
            environment = normalize_event_payload(event.event_type, payload).get(
                "environment"
            )
        except ValueError:
            evidence_invalid = True
            continue
        if isinstance(environment, dict):
            if "final_check" in environment:
                try:
                    candidate = validate_final_check(
                        environment["final_check"],
                        scenario_id=run.evaluation_spec["scenario_id"],
                    )
                    validate_runner_event(event.event_type, payload)
                    final_count += 1
                    if final_count != 1:
                        raise ValueError("conflicting accepted evidence")
                    final_check = candidate
                    environment_finalized = True
                    environment_check_called = True
                except ValueError:
                    # Corrupt durable required evidence must never become success.
                    evidence_invalid = True
                    final_check = None
                    environment_finalized = False
                    environment_check_called = False
            if environment.get("kind") == "control":
                usage = environment.get("usage")
                if isinstance(usage, dict):
                    # A report replaces cumulative totals, never sums per step.
                    agent_usage = {key: usage.get(key) for key in agent_usage}
                continue
            rejected = environment.get("result") != "ok"
            steps.append(
                {
                    "index": payload.get("index", len(steps)),
                    "action": (
                        {"name": "rejected_tool", "arguments": {}}
                        if rejected
                        else payload.get("tool_call")
                    ),
                    "status": "failed" if rejected else "success",
                    "rejection": environment.get("result") if rejected else None,
                    "observation": payload.get("observation", ""),
                    "latency_ms": payload.get("latency_ms", 0),
                    "context_window": None,
                    "token_prompt": None,
                    "token_completion": None,
                }
            )
    total_latency = sum(int(item.get("latency_ms") or 0) for item in steps)
    max_steps = 6
    policy = run.evaluation_spec.get("policy")
    if isinstance(policy, dict):
        max_steps = int(policy.get("max_steps") or max_steps)
    trajectory = {
        "status": "success" if run.status == RunStatus.SUCCEEDED.value else "failed",
        "run_status": run.status,
        "steps": steps,
        "max_steps": max_steps,
        "total_tokens": None,
        "total_latency_ms": total_latency,
        "provider_error": provider_error,
    }
    trajectory["evidence_valid"] = not evidence_invalid and final_check is not None
    # Outcomes are not world attempts and must not affect step-count/sequence.
    trajectory["final_observation"] = (
        final_check.final_observation if final_check is not None else ""
    )
    prompt = agent_usage.get("token_prompt")
    completion = agent_usage.get("token_completion")
    total = (
        prompt + completion
        if isinstance(prompt, int) and isinstance(completion, int)
        else None
    )
    trajectory["total_tokens"] = total
    trajectory["agent_usage"] = agent_usage
    metrics = {
        "steps": len(steps),
        "latency_ms": total_latency,
        "token_prompt": prompt,
        "token_completion": completion,
        "total_tokens": total,
        "agent_usage": agent_usage,
        "environment": {
            "finalized": environment_finalized,
            "check_called": environment_check_called,
        },
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
        payload = normalize_event_payload(event.event_type, event.payload)
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
    db: AsyncSession,
    run: Run,
    runner_metrics: dict | None,
    projection: tuple[dict, dict, dict] | None = None,
) -> None:
    trajectory, computed_metrics, assertion_result = (
        projection or await _evaluation_projection(db, run)
    )
    trajectory["status"] = "success" if run.status == "succeeded" else "failed"
    trajectory["run_status"] = run.status
    score_result = compute_score(trajectory, assertion_result=assertion_result)
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
    if "assertions" in score_result:
        run.metrics["assertions"] = score_result["assertions"]
    report = analyze_trajectory(trajectory, assertion_result=assertion_result)
    dimensions = {key: float(value) for key, value in report.dimensions.items()}

    evidence = []
    for item in report.evidence:
        serialized = asdict(item)
        if serialized.get("failure_type") is None:
            serialized.pop("failure_type", None)
        evidence.append(serialized)
    analysis = RunAnalysis(
        run_id=run.id,
        dimensions=dimensions,
        evidence=evidence,
        dominant_type=report.dominant_classification or report.dominant,
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
    patch = candidate_policy_for_scenario(
        (await require_experiment(db, run.experiment_id)).scenario_id
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
    projection: tuple[dict, dict, dict] | None = None,
) -> None:
    await _analyze_and_score(db, run, metrics, projection)
    if run.kind == "baseline" and status in {
        RunStatus.FAILED.value,
        RunStatus.TIMED_OUT.value,
    }:
        await _create_candidate(db, run)
    if run.kind == "replay" and run.policy_id and run.regression_case_id is None:
        policy = await require_policy(db, run.policy_id)
        baseline = await require_run(db, run.source_run_id or policy.source_run_id)
        policy.score_delta = (run.score or 0.0) - (baseline.score or 0.0)
        policy.status = (
            PolicyStatus.VALIDATED.value
            if status == RunStatus.SUCCEEDED.value and policy.score_delta > 0
            else PolicyStatus.CANDIDATE.value
        )


async def _evaluation_projection(db: AsyncSession, run: Run) -> tuple[dict, dict, dict]:
    trajectory, metrics = await _trajectory_from_events(db, run)
    suite = assertion_suite_for_scenario(run.evaluation_spec["scenario_id"])
    return trajectory, metrics, evaluate_assertion_suite(suite, trajectory)


async def _terminalize(
    db: AsyncSession,
    job: RunnerJob,
    run: Run,
    status: str,
    failure_kind: TerminalFailureKind | str | None,
    metrics: dict | None,
    projection: tuple[dict, dict, dict] | None = None,
) -> bool:
    """Job lock precedes this CAS; event, effects and state share one commit."""
    if run.status in TERMINAL_RUN_STATUSES:
        return False
    now = utcnow()
    failure = normalize_terminal_failure_kind(failure_kind, status=status)
    changed = await db.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == run.status)
        .values(
            status=status,
            completed_at=now,
            started_at=func.coalesce(Run.started_at, now),
            error=terminal_failure_message(failure) if failure else None,
        )
    )
    if changed.rowcount != 1:
        return False
    await db.refresh(run)
    sequence = await next_event_sequence(db, run.id)
    event_type = (
        "run_completed"
        if status == "succeeded"
        else "run_cancelled" if status == "cancelled" else "run_failed"
    )
    payload = {"attempt": job.attempt, "status": status}
    if failure is not None:
        payload["failure_kind"] = failure.value
    db.add(
        RunEvent(
            run_id=run.id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            occurred_at=now,
        )
    )
    await _record_terminal_effects(db, run, status, metrics, projection)
    if (
        status == "failed"
        and _provider_error(run.metrics.get("provider_error")) is not None
    ):
        run.error = _provider_terminal_error(run.metrics)
    published = EventEnvelope(
        run_id=run.id,
        sequence=sequence,
        type=event_type,
        payload=payload,
        occurred_at=now,
    ).model_dump(mode="json")
    db.info.setdefault("terminal_notifications", []).append((run.id, published))
    return True


async def _commit_terminal_notifications(db: AsyncSession) -> None:
    try:
        await db.commit()
    except BaseException:
        db.info.pop("terminal_notifications", None)
        raise
    for run_id, event in db.info.pop("terminal_notifications", []):
        await event_notifier.publish(run_id, [event])


async def _completion_response(
    db: AsyncSession, job: RunnerJob, run: Run
) -> CompletionResponse:
    terminal = (
        (
            await db.execute(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                    RunEvent.event_type.in_(
                        ["run_completed", "run_failed", "run_cancelled"]
                    ),
                )
                .order_by(RunEvent.sequence.desc())
            )
        )
        .scalars()
        .first()
    )
    if (
        terminal is None
        or terminal.payload.get("attempt") != job.attempt
        or terminal.payload.get("status") != run.status
    ):
        raise DomainError(
            500, "TERMINAL_INVARIANT_BROKEN", "Missing agreeing terminal event"
        )
    return CompletionResponse(
        run_id=run.id,
        attempt=job.attempt,
        terminal_sequence=terminal.sequence,
        status=run.status,
        run=run_response(run),
    )


async def complete_job(
    db: AsyncSession,
    *,
    lease_id: str,
    runner_id: str,
    status: str,
    failure_kind: TerminalFailureKind | str | None,
    metrics: dict | None,
    execution: ExecutionFacts,
) -> CompletionResponse:
    while True:
        job, run = await _leased_job(
            db,
            lease_id=lease_id,
            runner_id=runner_id,
            run_id=execution.run_id,
            allow_expired=True,
            for_update=True,
        )
        await db.refresh(run)
        if execution.attempt != job.attempt:
            raise DomainError(
                409, "ATTEMPT_MISMATCH", "Completion targets another Attempt"
            )
        if run.status in TERMINAL_RUN_STATUSES:
            return await _completion_response(db, job, run)
        expires_at = _aware(job.lease_expires_at)
        if expires_at is None or expires_at <= utcnow():
            raise DomainError(409, "LEASE_EXPIRED", "Runner lease has expired")
        projection = await _evaluation_projection(db, run)
        eligible = (
            execution.eligible
            and execution.submitted
            and execution.check_succeeded
            and execution.planned_environment_teardown
            and execution.failure is None
            and projection[0]["evidence_valid"]
            and projection[2]["aggregate"] >= 1.0
        )
        cancellation_requested = (
            run.status == RunStatus.CANCELLING.value
            or job.cancel_requested_at is not None
        )
        effective_status = status
        if cancellation_requested:
            effective_status = RunStatus.CANCELLED.value
        elif execution.failure is not None:
            effective_status = (
                RunStatus.TIMED_OUT.value
                if execution.failure == "timed_out"
                else RunStatus.CANCELLED.value
                if execution.failure == "cancelled"
                else RunStatus.FAILED.value
            )
        elif status == RunStatus.TIMED_OUT.value:
            effective_status = RunStatus.TIMED_OUT.value
        elif status == RunStatus.CANCELLED.value:
            effective_status = RunStatus.CANCELLED.value
        elif status == RunStatus.SUCCEEDED.value and not eligible:
            effective_status = RunStatus.FAILED.value
        if execution.failure is not None:
            failure_kind = {
                "cancelled": TerminalFailureKind.CANCELLED,
                "timed_out": TerminalFailureKind.TIMED_OUT,
                "agent_exit": TerminalFailureKind.AGENT_EXIT,
                "output_limit_exceeded": TerminalFailureKind.OUTPUT_LIMIT_EXCEEDED,
            }.get(execution.failure, TerminalFailureKind.INTERNAL_FAILURE)
        if effective_status == "failed" and failure_kind is None:
            failure_kind = TerminalFailureKind.INTERNAL_FAILURE
        if not await _terminalize(
            db, job, run, effective_status, failure_kind, metrics, projection
        ):
            await db.rollback()
            continue
        await _commit_terminal_notifications(db)
        await db.refresh(run)
        return await _completion_response(db, job, run)


async def get_analysis(db: AsyncSession, run_id: str) -> AnalysisResponse:
    run = await require_run(db, run_id)
    analysis = await db.get(RunAnalysis, run_id)
    if analysis is None:
        if run.status not in TERMINAL_RUN_STATUSES:
            raise DomainError(409, "ANALYSIS_PENDING", "Analysis is not ready")
        raise DomainError(404, "ANALYSIS_NOT_FOUND", "No analysis exists for this run")
    response = AnalysisResponse.model_validate(analysis)
    response.failure_types = _failure_type_rates(response.evidence)
    response.metric_directions = dict(ANALYSIS_METRIC_DIRECTIONS)
    return response


def _failure_type_rates(evidence: list[dict]) -> dict[str, float]:
    counts: dict[str, int] = {}
    total = len(evidence)
    if total == 0:
        return {}
    for item in evidence:
        failure_type = item.get("failure_type")
        if isinstance(failure_type, str) and failure_type:
            counts[failure_type] = counts.get(failure_type, 0) + 1
    return {key: value / total for key, value in counts.items()}


async def replay_policy(db: AsyncSession, policy_id: str) -> PolicyResponse:
    policy = await require_policy(db, policy_id)
    if policy.status in NON_REPLAYABLE_POLICY_STATUSES:
        raise DomainError(409, "POLICY_NOT_REPLAYABLE", "Policy cannot be replayed in its current state")
    if policy.replay_run_id is not None:
        return policy_response(policy)

    baseline = await require_run(db, policy.source_run_id)
    experiment = await require_experiment(db, policy.experiment_id)
    _ensure_experiment_scenario(experiment)
    baseline_spec = EvaluationSpec.model_validate(baseline.evaluation_spec)
    run_id = new_id()
    replay_spec = _evaluation_spec(
        run_id=run_id,
        experiment=experiment,
        seed=baseline_spec.seed,
        execution_mode=baseline_spec.execution_mode,
        agent_id=(
            baseline_spec.agent_id
            or _resolve_agent(None, baseline_spec.execution_mode)
        ),
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

    claimed = await db.execute(
        update(Policy)
        .where(
            Policy.id == policy_id,
            Policy.replay_run_id.is_(None),
            Policy.status.notin_(NON_REPLAYABLE_POLICY_STATUSES),
        )
        .values(
            status=PolicyStatus.REPLAYING.value,
            replay_run_id=run.id,
        )
    )
    if claimed.rowcount != 1:
        await db.rollback()
        current = await require_policy(db, policy_id)
        if (
            current.status in NON_REPLAYABLE_POLICY_STATUSES
            or current.replay_run_id is None
        ):
            raise DomainError(
                409,
                "POLICY_NOT_REPLAYABLE",
                "Policy cannot be replayed in its current state",
            )
        return policy_response(current)
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

    cases = list(
        (
            await db.execute(
                select(RegressionCase)
                .where(
                    RegressionCase.experiment_id == policy.experiment_id,
                    RegressionCase.eligible_for_replay.is_(True),
                )
                .order_by(RegressionCase.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    pending_case_ids: list[str] = []
    pending_run_ids: list[str] = []
    for case in cases:
        case_run = None
        replace_existing_claim = False
        if case.replay_policy_id == policy.id and case.replay_run_id:
            case_run = await require_run(db, case.replay_run_id)
            if case_run.status == RunStatus.SUCCEEDED.value:
                continue
            if case_run.status in {
                RunStatus.QUEUED.value,
                RunStatus.CLAIMED.value,
                RunStatus.RUNNING.value,
                RunStatus.CANCELLING.value,
            }:
                pending_case_ids.append(case.id)
                pending_run_ids.append(case_run.id)
                continue
            replace_existing_claim = True
        elif case.replay_run_id or case.replay_policy_id:
            # A case can only have one in-flight replay pointer, but a terminal
            # replay belongs to the policy that created it. A later candidate
            # must run the case against its own patch instead of treating the
            # previous policy's result as reusable.
            if case.replay_run_id:
                case_run = await require_run(db, case.replay_run_id)
                if case_run.status in {
                    RunStatus.QUEUED.value,
                    RunStatus.CLAIMED.value,
                    RunStatus.RUNNING.value,
                    RunStatus.CANCELLING.value,
                }:
                    pending_case_ids.append(case.id)
                    pending_run_ids.append(case_run.id)
                    continue
                replace_existing_claim = True
            else:
                pending_case_ids.append(case.id)
                continue
        source = await require_run(db, case.source_run_id)
        source_spec = EvaluationSpec.model_validate(source.evaluation_spec)
        run_id = new_id()
        regression_spec = EvaluationSpec(
            run_id=run_id,
            experiment_id=policy.experiment_id,
            scenario_id=case.scenario_id,
            task=source_spec.task,
            seed=case.seed,
            execution_mode=ExecutionMode(case.execution_mode),
            agent_id=_resolve_agent(source_spec.agent_id, ExecutionMode(case.execution_mode)),
            policy=PolicyPatch.model_validate(policy.patch),
            limits=source_spec.limits,
            scenario_params=dict(case.scenario_params or {}),
        )
        regression_run = Run(
            id=run_id,
            experiment_id=policy.experiment_id,
            kind="replay",
            source_run_id=case.source_run_id,
            policy_id=policy.id,
            regression_case_id=case.id,
            status=RunStatus.QUEUED.value,
            evaluation_spec=regression_spec.model_dump(mode="json"),
            metrics={},
        )
        db.add_all([regression_run, RunnerJob(run_id=run_id, attempt=1)])
        await db.flush()
        claim_scope = [RegressionCase.replay_run_id.is_(None)]
        if replace_existing_claim and case_run is not None:
            claim_scope.append(
                (RegressionCase.replay_run_id == case_run.id)
                & (
                    RegressionCase.replay_policy_id == case.replay_policy_id
                    if case.replay_policy_id is not None
                    else RegressionCase.replay_policy_id.is_(None)
                )
            )
        claimed = await db.execute(
            update(RegressionCase)
            .where(
                RegressionCase.id == case.id,
                or_(*claim_scope),
            )
            .values(replay_run_id=run_id, replay_policy_id=policy.id)
        )
        if claimed.rowcount != 1:
            # The run and job were created in this transaction. Rolling back the
            # losing claim removes both, preventing orphaned queued work.
            await db.rollback()
            return await _retry_activation_after_case_claim_loss(db, policy_id)
        pending_case_ids.append(case.id)
        pending_run_ids.append(run_id)

    if pending_case_ids:
        still_validated = await db.execute(
            update(Policy)
            .where(
                Policy.id == policy_id,
                Policy.status == PolicyStatus.VALIDATED.value,
            )
            .values(status=PolicyStatus.VALIDATED.value)
        )
        if still_validated.rowcount != 1:
            await db.rollback()
            current = await require_policy(db, policy_id)
            raise DomainError(
                409,
                "POLICY_NOT_VALIDATED",
                "Only a validated policy can be activated",
                {"status": current.status},
            )
        await db.commit()
        raise DomainError(
            409,
            "REGRESSION_REPLAY_PENDING",
            "Regression cases must replay successfully before policy activation",
            {"case_ids": pending_case_ids, "run_ids": pending_run_ids},
        )
    # Serialize activation across policies in one experiment. The partial
    # unique index protects the invariant, while this lock turns a concurrent
    # pair of supersede/activate transitions into an ordered handoff that can
    # be reread instead of surfacing an IntegrityError.
    await db.execute(
        select(Experiment)
        .where(Experiment.id == policy.experiment_id)
        .with_for_update()
    )
    await db.refresh(policy)
    if policy.status != PolicyStatus.VALIDATED.value:
        if policy.status == PolicyStatus.ACTIVE.value:
            return policy_response(policy)
        raise DomainError(
            409,
            "POLICY_NOT_VALIDATED",
            "Only a validated policy can be activated",
            {"status": policy.status},
        )
    await db.execute(
        update(Policy)
        .where(
            Policy.experiment_id == policy.experiment_id,
            Policy.status == PolicyStatus.ACTIVE.value,
            Policy.id != policy.id,
        )
        .values(status=PolicyStatus.SUPERSEDED.value)
    )
    activated = await db.execute(
        update(Policy)
        .where(Policy.id == policy.id, Policy.status == PolicyStatus.VALIDATED.value)
        .values(status=PolicyStatus.ACTIVE.value)
    )
    if activated.rowcount != 1:
        await db.rollback()
        current = await require_policy(db, policy_id)
        if current.status == PolicyStatus.ACTIVE.value:
            return policy_response(current)
        raise DomainError(409, "POLICY_NOT_VALIDATED", "Only a validated policy can be activated")
    await db.commit()
    await db.refresh(policy)
    return policy_response(policy)


async def _retry_activation_after_case_claim_loss(
    db: AsyncSession, policy_id: str
) -> PolicyResponse:
    """Re-read a losing activation race after its uncommitted work was rolled back."""

    policy = await require_policy(db, policy_id)
    if policy.status == PolicyStatus.ACTIVE.value:
        return policy_response(policy)
    cases = list(
        (
            await db.execute(
                select(RegressionCase)
                .where(
                    RegressionCase.experiment_id == policy.experiment_id,
                    RegressionCase.eligible_for_replay.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    pending_case_ids: list[str] = []
    pending_run_ids: list[str] = []
    for case in cases:
        if not case.replay_run_id:
            pending_case_ids.append(case.id)
            continue
        replay = await require_run(db, case.replay_run_id)
        if case.replay_policy_id == policy.id:
            if replay.status != RunStatus.SUCCEEDED.value:
                pending_case_ids.append(case.id)
                pending_run_ids.append(replay.id)
            continue
        if replay.status not in TERMINAL_RUN_STATUSES:
            pending_case_ids.append(case.id)
            pending_run_ids.append(replay.id)
    if pending_case_ids:
        raise DomainError(
            409,
            "REGRESSION_REPLAY_PENDING",
            "Regression cases must replay successfully before policy activation",
            {"case_ids": pending_case_ids, "run_ids": pending_run_ids},
        )
    return await activate_policy(db, policy_id)


async def reject_policy(
    db: AsyncSession, policy_id: str, reason: str | None
) -> PolicyResponse:
    policy = await require_policy(db, policy_id)
    if policy.status not in {
        PolicyStatus.CANDIDATE.value,
        PolicyStatus.VALIDATED.value,
    }:
        raise DomainError(409, "POLICY_NOT_REJECTABLE", "Policy cannot be rejected in its current state")
    rejected = await db.execute(
        update(Policy)
        .where(
            Policy.id == policy_id,
            Policy.status.in_((PolicyStatus.CANDIDATE.value, PolicyStatus.VALIDATED.value)),
        )
        .values(status=PolicyStatus.REJECTED.value, reject_reason=reason)
    )
    if rejected.rowcount != 1:
        await db.rollback()
        current = await require_policy(db, policy_id)
        if current.status == PolicyStatus.REJECTED.value:
            return policy_response(current)
        raise DomainError(409, "POLICY_NOT_REJECTABLE", "Policy cannot be rejected in its current state")
    await db.commit()
    await db.refresh(policy)
    return policy_response(policy)
