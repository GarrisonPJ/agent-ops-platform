"""Application services for experiments, runs, leases, and policy replay."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.failure_analyzer import analyze_trajectory
from app.phase1_models import Experiment, Policy, Run, RunAnalysis, RunEvent, RunnerJob, new_id, utcnow
from app.phase1_schemas import (
    AnalysisResponse,
    EvaluationLimits,
    EvaluationSpec,
    EventEnvelope,
    ExperimentCreate,
    ExperimentResponse,
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
        created_at=experiment.created_at,
        runs=[run_response(item) for item in runs],
        active_policy=policy_response(active) if active else None,
        candidate_policy=policy_response(candidate) if candidate else None,
    )


async def create_experiment(db: AsyncSession, data: ExperimentCreate) -> ExperimentResponse:
    experiment = Experiment(
        id=new_id(), name=data.name.strip(), task=data.task.strip(), scenario_id=data.scenario_id
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
    policy: PolicyPatch | None = None,
) -> EvaluationSpec:
    return EvaluationSpec(
        run_id=run_id,
        experiment_id=experiment.id,
        scenario_id=SCENARIO_ID,
        task=experiment.task,
        seed=seed,
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


async def claim_job(db: AsyncSession, runner_id: str) -> tuple[RunnerJob, Run] | None:
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
        claimed = await db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.QUEUED.value)
            .values(status=RunStatus.CLAIMED.value)
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
        job.lease_expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
        await db.commit()
        await db.refresh(job)
        await db.refresh(run)
        return job, run
    await db.rollback()
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
    if run.status == RunStatus.CLAIMED.value:
        run.status = RunStatus.RUNNING.value
        run.started_at = now
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    command = "cancel" if run.status == RunStatus.CANCELLING.value else "continue"
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
    for event in events:
        previous = existing_by_sequence.get(event.sequence)
        if previous and (previous.event_type != event.type or previous.payload != event.payload):
            raise DomainError(
                409,
                "EVENT_CONFLICT",
                "A different event already uses this sequence",
                {"sequence": event.sequence},
            )

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
        row = RunEvent(
            run_id=run_id,
            sequence=event.sequence,
            event_type=event.type,
            payload=event.payload,
            occurred_at=event.occurred_at,
        )
        db.add(row)
        new_payloads.append(event.model_dump(mode="json"))
        accepted_through = event.sequence
    await db.commit()
    return accepted_through, new_payloads


async def _trajectory_from_events(db: AsyncSession, run: Run) -> tuple[dict, dict]:
    events = list(
        (
            await db.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run.id, RunEvent.event_type == "step_completed")
                .order_by(RunEvent.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    steps: list[dict] = []
    for event in events:
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
    return trajectory, metrics


async def _analyze_and_score(
    db: AsyncSession, run: Run, runner_metrics: dict | None
) -> None:
    trajectory, computed_metrics = await _trajectory_from_events(db, run)
    score_result = compute_score(trajectory)
    run.score = float(score_result["score"])
    run.metrics = {
        **(runner_metrics or {}),
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
    if run.status == RunStatus.CANCELLING.value:
        status = RunStatus.CANCELLED.value
    run.status = status
    run.error = error
    run.completed_at = utcnow()
    if run.started_at is None:
        run.started_at = run.completed_at

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
