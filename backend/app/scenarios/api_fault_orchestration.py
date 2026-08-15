"""API fault orchestration — deterministic injected failures and recovery."""

from __future__ import annotations

from app.phase1_schemas import EvaluationSpec, PolicyPatch
from app.scenario_registry import (
    EmitFn,
    ScenarioHandler,
    ScenarioMetadata,
    ScenarioParamDef,
    register_scenario,
)
from app.scenarios._helpers import emit_step

SCENARIO_ID = "api-fault-orchestration"
TOOLS = ("invoke_service", "retry_call", "degrade_route")


def _target_service(spec: EvaluationSpec) -> str:
    return spec.scenario_params.get("service", "payments-api")


def _baseline(spec: EvaluationSpec, emit: EmitFn) -> int:
    service = _target_service(spec)
    emit("process_output", {"stream": "stdout", "content": "Starting fault baseline"})
    for index in range(6):
        emit_step(
            emit,
            index,
            "Invoke the failing service again without retry or degradation.",
            "invoke_service",
            {"service": service},
            "HTTP 503 Service Unavailable: upstream connection pool saturated.",
            90 + index * 6,
        )
    return 1


def _replay(spec: EvaluationSpec, emit: EmitFn) -> int:
    service = _target_service(spec)
    emit("process_output", {"stream": "stdout", "content": "Applying recovery policy"})
    emit_step(
        emit,
        0,
        "Retry the failing call with bounded backoff.",
        "retry_call",
        {"service": service, "attempts": 2},
        "Retry succeeded after one transient 503 response.",
        52,
    )
    emit_step(
        emit,
        1,
        "Route traffic through the degraded fallback path.",
        "degrade_route",
        {"service": service, "mode": "cached-read"},
        "Fallback route returned stale but acceptable responses.",
        38,
    )
    emit_step(
        emit,
        2,
        "Verify the orchestration path is healthy again.",
        "invoke_service",
        {"service": service},
        "Service responded 200 OK through the degraded route.",
        31,
    )
    return 0


class ApiFaultOrchestrationScenario:
    def run_fixture(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        return _replay(spec, emit) if spec.policy is not None else _baseline(spec, emit)

    async def run_provider(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        return self.run_fixture(spec, emit)

    def candidate_policy_patch(self) -> PolicyPatch:
        return PolicyPatch(
            instruction_patch=[
                "Retry transient failures before giving up.",
                "Degrade to a cached route when the primary service keeps failing.",
            ],
            tool_priority={
                "retry_call": 1.0,
                "degrade_route": 0.9,
                "invoke_service": 0.4,
            },
            max_steps=6,
        )


register_scenario(
    ScenarioMetadata(
        id=SCENARIO_ID,
        name="API Fault Orchestration",
        description=(
            "Recover from injected upstream failures using bounded retries and a "
            "degraded fallback route."
        ),
        default_task="Stabilize a failing API dependency without exhausting the step budget",
        allowed_tools=TOOLS,
        params=(
            ScenarioParamDef(
                name="service",
                description="Service name that receives deterministic fault injection.",
                required=False,
            ),
        ),
    ),
    ApiFaultOrchestrationScenario(),
)
