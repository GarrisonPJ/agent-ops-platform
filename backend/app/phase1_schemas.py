"""Versioned HTTP and runner protocol schemas for Phase 1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCENARIO_ID = "checkout-api-latency"
MAX_PROVIDER_LATENCY_MS = 86_400_000
MAX_PROVIDER_REQUEST_COUNT = 1_000
MAX_PROVIDER_TOKENS = 1_000_000_000
MAX_PROVIDER_TOTAL_TOKENS = 2_000_000_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class PolicyStatus(StrEnum):
    CANDIDATE = "candidate"
    REPLAYING = "replaying"
    VALIDATED = "validated"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ExecutionMode(StrEnum):
    FIXTURE = "fixture"
    PROVIDER = "provider"


TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
    RunStatus.TIMED_OUT.value,
}


class PolicyPatch(StrictModel):
    instruction_patch: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )
    tool_priority: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(
        default_factory=dict
    )
    max_steps: int = Field(ge=3, le=20)

    @field_validator("tool_priority")
    @classmethod
    def validate_tool_names(cls, value: dict[str, float]) -> dict[str, float]:
        allowed = {"check_service_health", "query_service_metrics", "fetch_service_logs"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(sorted(unknown))}")
        return value


class EvaluationLimits(StrictModel):
    timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)
    max_output_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)


class EvaluationSpec(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    experiment_id: str
    scenario_id: Literal["checkout-api-latency"] = SCENARIO_ID
    task: str = Field(min_length=1, max_length=4_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    execution_mode: ExecutionMode = ExecutionMode.FIXTURE
    policy: PolicyPatch | None = None
    limits: EvaluationLimits = Field(default_factory=EvaluationLimits)


class EventEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    sequence: int = Field(ge=1)
    occurred_at: datetime
    type: Literal[
        "run_started",
        "step_completed",
        "process_output",
        "run_completed",
        "run_failed",
        "run_cancelled",
    ]
    payload: dict = Field(default_factory=dict)


class ExperimentCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    task: str = Field(min_length=1, max_length=4_000)
    scenario_id: Literal["checkout-api-latency"] = SCENARIO_ID
    execution_mode: ExecutionMode = ExecutionMode.FIXTURE


class RunCreate(StrictModel):
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_id: str
    kind: Literal["baseline", "replay"]
    source_run_id: str | None
    policy_id: str | None
    status: RunStatus
    score: float | None
    metrics: dict
    evaluation_spec: EvaluationSpec
    error: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

class RunCorrelation(StrictModel):
    experiment_id: str
    run_id: str
    execution_mode: ExecutionMode
    status: RunStatus


class JobCorrelation(StrictModel):
    run_id: str
    lease_id: str | None
    runner_id: str | None
    attempt: int = Field(ge=1)
    recovery_reason: str | None


class AttemptCorrelation(StrictModel):
    attempt: int = Field(ge=1)
    lease_id: str
    runner_id: str
    lease_expires_at: datetime
    recovery_reason: str
    outcome: Literal["recovered", "failed", "cancelled"]
    recorded_at: datetime


class ProviderTelemetryInput(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    latency_ms: int = Field(ge=0, le=MAX_PROVIDER_LATENCY_MS)
    request_count: int = Field(ge=0, le=MAX_PROVIDER_REQUEST_COUNT)
    token_prompt: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    token_completion: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    request_id: str | None = Field(default=None, min_length=1, max_length=500)
    request_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class ProviderTelemetryCorrelation(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    latency_ms: int = Field(ge=0, le=MAX_PROVIDER_LATENCY_MS)
    request_count: int = Field(ge=0, le=MAX_PROVIDER_REQUEST_COUNT)
    token_prompt: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    token_completion: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    request_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class ProviderErrorInput(StrictModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    attempts: int = Field(ge=0, le=100)
    request_id: str | None = Field(default=None, min_length=1, max_length=500)
    request_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class ProviderErrorCorrelation(StrictModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    attempts: int = Field(ge=0, le=100)
    request_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class ProviderTelemetryMetrics(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    latency_ms: int = Field(ge=0, le=MAX_PROVIDER_LATENCY_MS)
    request_count: int = Field(ge=0, le=MAX_PROVIDER_REQUEST_COUNT)
    token_prompt: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    token_completion: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    total_tokens: int = Field(ge=0, le=MAX_PROVIDER_TOTAL_TOKENS)
    request_fingerprints: list[
        Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    ] = Field(default_factory=list, max_length=20)


class ProviderCorrelation(StrictModel):
    model: str | None
    request_fingerprints: list[str] = Field(default_factory=list, max_length=20)
    error: ProviderErrorCorrelation | None


class RunTiming(StrictModel):
    queue_latency_ms: int | None = Field(default=None, ge=0)
    run_duration_ms: int | None = Field(default=None, ge=0)


class RunOperationalMetrics(StrictModel):
    event_count: int = Field(ge=0)
    event_retries: int = Field(ge=0)
    lease_recoveries: int = Field(ge=0)
    provider_latency_ms: int = Field(ge=0)
    provider_tokens: int = Field(ge=0)


class TerminalRunOutcome(StrictModel):
    status: RunStatus
    error: str | None


class RunDiagnosticsResponse(StrictModel):
    run: RunCorrelation
    job: JobCorrelation
    attempts: list[AttemptCorrelation]
    provider: ProviderCorrelation | None
    timing: RunTiming
    metrics: RunOperationalMetrics
    terminal: TerminalRunOutcome | None


class OperationsOverviewResponse(StrictModel):
    generated_at: datetime
    queue_depth: int = Field(ge=0)
    runs_by_status: dict[str, int]
    terminal_outcomes: dict[str, int]
    expired_lease_count: int = Field(ge=0)
    lease_recoveries: int = Field(ge=0)
    event_retries: int = Field(ge=0)


class ReadinessResponse(StrictModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


class RunnerAvailabilityResponse(StrictModel):
    status: Literal["ok", "unavailable"]
    active_runner_count: int = Field(ge=0)
    freshest_heartbeat_at: datetime | None = None


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_id: str
    source_run_id: str
    parent_policy_id: str | None
    replay_run_id: str | None
    status: PolicyStatus
    patch: PolicyPatch
    rationale: str
    score_delta: float | None
    reject_reason: str | None
    created_at: datetime


class ExperimentResponse(BaseModel):
    id: str
    name: str
    task: str
    scenario_id: str
    execution_mode: ExecutionMode = ExecutionMode.FIXTURE
    created_at: datetime
    runs: list[RunResponse] = Field(default_factory=list)
    active_policy: PolicyResponse | None = None
    candidate_policy: PolicyResponse | None = None


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    dimensions: dict[str, float]
    evidence: list[dict]
    dominant_type: str | None
    failure_rate: float


class RejectPolicyRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=1_000)


class ClaimRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=100)


class ClaimedRun(StrictModel):
    run_id: str
    evaluation_spec: EvaluationSpec


class ClaimResponse(StrictModel):
    lease_id: str
    lease_expires_at: datetime
    attempt: int = Field(ge=1)
    next_sequence: int = Field(ge=1)
    recovery_reason: str | None = None
    run: ClaimedRun


class HeartbeatRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=100)


class HeartbeatResponse(StrictModel):
    command: Literal["continue", "cancel"]
    lease_expires_at: datetime


class EventUploadRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=100)
    lease_id: str
    events: list[EventEnvelope] = Field(min_length=1, max_length=100)


class EventUploadResponse(StrictModel):
    accepted_through: int


class CompleteRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=100)
    status: Literal["succeeded", "failed", "cancelled", "timed_out"]
    error: str | None = Field(default=None, max_length=4_000)
    metrics: dict | None = None


class ApiErrorPayload(BaseModel):
    code: str
    message: str
    details: object | None = None
