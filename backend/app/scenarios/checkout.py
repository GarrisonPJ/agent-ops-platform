"""Checkout API latency — the original Phase 1 Golden Scenario."""

from __future__ import annotations

from app.environment import ToolDefinition, UnknownToolError
from app.phase1_provider import ProviderError, run_provider_agent
from app.phase1_schemas import EvaluationSpec, PolicyPatch
from app.scenario_assertions import AssertionCombination, AssertionSuite, ScenarioAssertion
from app.scenario_registry import EmitFn, ScenarioMetadata, register_scenario
from app.scenarios._helpers import TrajectoryEnvironment, emit_step, require_exact_arguments

from app.scenario_ids import CHECKOUT_SCENARIO_ID
CHECKOUT_TOOLS = (
    "check_service_health",
    "query_service_metrics",
    "fetch_service_logs",
)


def _provider_tool_definitions() -> list[ToolDefinition]:
    return [
        {
            "type": "function",
            "function": {
                "name": "check_service_health",
                "description": "Check checkout-api health in the deterministic scenario.",
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
                "name": "query_service_metrics",
                "description": "Read checkout-api dependency latency metrics.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "metric": {"type": "string"},
                    },
                    "required": ["service", "metric"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_service_logs",
                "description": "Read the evidence-backed checkout-api logs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["service", "query"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _tool_contract(name: str) -> tuple[dict[str, object], str]:
    if name == "check_service_health":
        return (
            {"service": "checkout-api"},
            "Service is healthy but checkout latency is elevated.",
        )
    if name == "query_service_metrics":
        return (
            {"service": "checkout-api", "metric": "dependency_latency"},
            "Payment dependency p95 increased from 110ms to 1.8s.",
        )
    if name == "fetch_service_logs":
        return (
            {"service": "checkout-api", "query": "payment dependency timeout"},
            "Requests are delayed by payment-gateway connection pool saturation.",
        )
    raise ProviderError(
        "PROVIDER_UNSUPPORTED_TOOL",
        "Provider selected a tool outside the allowlist",
        retryable=False,
        attempts=1,
    )


def _baseline(emit: EmitFn) -> int:
    emit("process_output", {"stream": "stdout", "content": "Starting baseline evaluation"})
    for index in range(6):
        emit_step(
            emit,
            index,
            "Fetch logs again without gathering a new signal.",
            "fetch_service_logs",
            {"service": "checkout-api", "window": "5m"},
            "The same noisy request samples are returned; the cause is still inconclusive.",
            85 + index * 4,
        )
    return 1


def _replay(emit: EmitFn) -> int:
    emit("process_output", {"stream": "stdout", "content": "Applying candidate policy"})
    emit_step(
        emit,
        0,
        "Establish whether the service is healthy before reading broad logs.",
        "check_service_health",
        {"service": "checkout-api"},
        "Service is healthy but checkout latency is elevated.",
        32,
    )
    emit_step(
        emit,
        1,
        "Use metrics to localize the latency bottleneck.",
        "query_service_metrics",
        {"service": "checkout-api", "metric": "dependency_latency"},
        "Payment dependency p95 increased from 110ms to 1.8s.",
        41,
    )
    emit_step(
        emit,
        2,
        "Fetch only the evidence-backed payment timeout logs.",
        "fetch_service_logs",
        {"service": "checkout-api", "query": "payment dependency timeout"},
        "Requests are delayed by payment-gateway connection pool saturation.",
        48,
    )
    return 0


class CheckoutScenario:
    def run_fixture(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        return _replay(emit) if spec.policy is not None else _baseline(emit)

    async def run_provider(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        return await run_provider_agent(spec, emit)

    def candidate_policy_patch(self) -> PolicyPatch:
        return PolicyPatch(
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


CHECKOUT_ASSERTIONS = (
    ScenarioAssertion(type="tool-used", tool="check_service_health", weight=1.0),
    ScenarioAssertion(type="tool-used", tool="query_service_metrics", weight=1.0),
    ScenarioAssertion(
        type="tool-sequence",
        sequence=(
            "check_service_health",
            "query_service_metrics",
            "fetch_service_logs",
        ),
        weight=2.0,
    ),
    ScenarioAssertion(type="step-count", max_steps=4, weight=1.0),
)


class CheckoutEnvironment(TrajectoryEnvironment):
    def __init__(self, params: dict[str, str]) -> None:
        super().__init__(AssertionSuite(AssertionCombination.WEIGHTED, CHECKOUT_ASSERTIONS))

    def tool_definitions(self) -> list[ToolDefinition]:
        definitions = _provider_tool_definitions()
        for definition in definitions:
            parameters = definition["function"]["parameters"]
            parameters["properties"]["service"]["const"] = "checkout-api"
        definitions[1]["function"]["parameters"]["properties"]["metric"]["const"] = (
            "dependency_latency"
        )
        # Both fixture and provider forms exist today. Do not route this expanded
        # schema into v1 provider execution before the promotion/version decision.
        definitions[2]["function"]["parameters"] = {
            "type": "object",
            "properties": {
                "service": {"type": "string", "const": "checkout-api"},
                "query": {"type": "string", "const": "payment dependency timeout"},
                "window": {"type": "string", "const": "5m"},
            },
            "required": ["service"],
            "additionalProperties": False,
            "oneOf": [{"required": ["query"]}, {"required": ["window"]}],
        }
        return definitions

    def call(self, name: str, args: dict[str, object]) -> str:
        if name not in CHECKOUT_TOOLS:
            raise UnknownToolError(name)
        expected, observation = _tool_contract(name)
        if name == "fetch_service_logs":
            broad_logs = {"service": "checkout-api", "window": "5m"}
            require_exact_arguments(name, args, expected, broad_logs)
            if args == broad_logs:
                observation = (
                    "The same noisy request samples are returned; the cause is still inconclusive."
                )
        else:
            require_exact_arguments(name, args, expected)
        return self._record(name, args, observation)


register_scenario(
    ScenarioMetadata(
        id=CHECKOUT_SCENARIO_ID,
        name="Checkout API Latency",
        description=(
            "Investigate elevated checkout latency using health checks, dependency "
            "metrics, and evidence-backed logs."
        ),
        default_task="Investigate checkout API latency",
        allowed_tools=CHECKOUT_TOOLS,
        assertions=CHECKOUT_ASSERTIONS,
        assertion_combination=AssertionCombination.WEIGHTED,
    ),
    CheckoutScenario(),
    CheckoutEnvironment,
)
