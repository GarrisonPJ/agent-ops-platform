"""API fault orchestration — deterministic injected failures and recovery."""

from __future__ import annotations

from app.phase1_provider import ProviderError, run_provider_agent
from app.phase1_schemas import EvaluationSpec, PolicyPatch
from app.scenario_assertions import AssertionCombination, ScenarioAssertion
from app.scenario_registry import (
    EmitFn,
    ScenarioHandler,
    ScenarioMetadata,
    ScenarioParamDef,
    register_scenario,
)
from app.scenarios._helpers import emit_step

from app.scenario_ids import API_FAULT_ORCHESTRATION_SCENARIO_ID as SCENARIO_ID
TOOLS = ("invoke_service", "retry_call", "degrade_route")
FAULT_PROVIDER_INSTRUCTIONS = (
    "You recover a failing API dependency using only the provided fault tools.",
    "Use at most one tool call per response.",
    "Do not request shell commands, network access, or arbitrary code execution.",
)


def _provider_tool_definitions() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "invoke_service",
                "description": "Invoke the deterministic service and observe its current response.",
                "parameters": {
                    "type": "object",
                    "properties": {"service": {"type": "string"}},
                    "required": ["service"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "retry_call",
                "description": "Retry the deterministic service call with a bounded attempt count.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "attempts": {"type": "integer"},
                    },
                    "required": ["service", "attempts"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "degrade_route",
                "description": "Route the deterministic service through a bounded degraded mode.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                    "required": ["service", "mode"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _provider_tool_result(spec: EvaluationSpec, name: str) -> tuple[dict[str, str], str]:
    service = _target_service(spec)
    if name == "retry_call":
        return (
            {"service": service, "attempts": "2"},
            "Retry succeeded after one transient 503 response.",
        )
    if name == "degrade_route":
        return (
            {"service": service, "mode": "cached-read"},
            "Fallback route returned stale but acceptable responses.",
        )
    if name == "invoke_service":
        return (
            {"service": service},
            "Service responded 200 OK through the degraded route.",
        )
    raise ProviderError(
        "PROVIDER_UNSUPPORTED_TOOL",
        "Provider selected a tool outside the allowlist",
        retryable=False,
        attempts=1,
    )


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
        return await run_provider_agent(
            spec,
            emit,
            tool_definitions=_provider_tool_definitions,
            tool_result=_provider_tool_result,
            instructions=FAULT_PROVIDER_INSTRUCTIONS,
            scenario_label="fault orchestration",
        )

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


FAULT_ASSERTIONS = (
    ScenarioAssertion(type="tool-used", tool="retry_call", weight=1.0),
    ScenarioAssertion(type="tool-used", tool="degrade_route", weight=1.0),
    ScenarioAssertion(
        type="tool-sequence",
        sequence=("retry_call", "degrade_route", "invoke_service"),
        weight=2.0,
    ),
    ScenarioAssertion(type="step-count", max_steps=4, weight=1.0),
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
        assertions=FAULT_ASSERTIONS,
        assertion_combination=AssertionCombination.WEIGHTED,
    ),
    ApiFaultOrchestrationScenario(),
)
