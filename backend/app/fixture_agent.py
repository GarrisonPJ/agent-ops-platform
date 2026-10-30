"""Deterministic MCP Agent-under-test for the canonical H1 Environments.

The Runner invokes this process with one bounded EvaluationSpec; it discovers
and calls the loopback Environment, which owns observations and trusted facts.
It must not import worlds, score a task, or emit trusted RunEvents.

Read ``app.mcp_agent_client`` for transport and ``app.reference_agent`` for
provider-backed decisions.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.mcp_agent_client import MCPAgentClient, MCPAgentError

MAX_SPEC_BYTES = 65_536
CANONICAL_SCENARIOS = frozenset(
    {
        "checkout-api-latency.v2",
        "multi-step-research.v2",
        "api-fault-orchestration.v2",
        "order-support.v1",
    }
)


class AgentInputError(ValueError):
    """Safe protocol failure without echoing input or secrets."""


@dataclass(frozen=True)
class AgentSpec:
    task: str
    scenario_id: str
    max_steps: int
    timeout_ms: int
    policy: bool
    scenario_params: dict[str, str]
    instruction_patch: tuple[str, ...] = ()


# Wire rules stay Agent-owned: no CP/world/registry imports.
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]+\.v[0-9]+", re.ASCII)
PARAM_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}", re.ASCII)
POLICY_TOOLS = frozenset({
    "check_service_health", "query_service_metrics", "fetch_service_logs",
    "search_documents", "fetch_document", "submit_answer", "invoke_service",
    "retry_call", "degrade_route",
})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Limits(_StrictModel):
    timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)
    max_output_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)


class _Policy(_StrictModel):
    instruction_patch: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )
    tool_priority: dict[str, float] = Field(default_factory=dict)
    max_steps: int = Field(ge=3, le=20)

    @field_validator("tool_priority", mode="before")
    @classmethod
    def validate_priority(cls, value: object) -> object:
        if not isinstance(value, dict) or any(
            name not in POLICY_TOOLS or type(weight) not in (int, float)
            or not math.isfinite(weight) or not 0 <= weight <= 1
            for name, weight in value.items()
        ):
            raise ValueError("invalid priority")
        return value


class _EvaluationSpec(_StrictModel):
    schema_version: int = 1
    run_id: str
    experiment_id: str
    scenario_id: str = Field(min_length=2, max_length=64)
    task: str = Field(min_length=1, max_length=4_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    execution_mode: Literal["fixture", "provider"] = "fixture"
    agent_id: str | None = Field(default=None, min_length=1, max_length=64)
    policy: _Policy | None = None
    limits: _Limits = Field(default_factory=_Limits)
    scenario_params: dict[str, str] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("invalid version")
        return value

    @field_validator("scenario_id", "agent_id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is not None and not ID_PATTERN.fullmatch(value):
            raise ValueError("invalid identity")
        return value

    @field_validator("scenario_params")
    @classmethod
    def validate_params(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20 or any(
            not PARAM_PATTERN.fullmatch(key) or not 1 <= len(item) <= 200
            for key, item in value.items()
        ):
            raise ValueError("invalid parameters")
        return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite number")


def _check_json_depth(text: str) -> None:
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("excessive depth")
        elif char in "]}":
            depth -= 1


def _validate_json_values(value: object) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite number")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_values(key)
            _validate_json_values(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json_values(item)


def read_spec() -> AgentSpec:
    raw = sys.stdin.buffer.readline(MAX_SPEC_BYTES + 1)
    try:
        if not raw or len(raw) > MAX_SPEC_BYTES:
            raise ValueError("invalid size")
        text = raw.decode("utf-8", errors="strict")
        _check_json_depth(text)
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        _validate_json_values(value)
        parsed = _EvaluationSpec.model_validate(value)
    except (ValueError, TypeError, RecursionError):
        raise AgentInputError("INVALID_SPEC") from None
    if parsed.scenario_id not in CANONICAL_SCENARIOS:
        raise AgentInputError("UNSUPPORTED_SCENARIO")
    environment_task = os.environ.get("AGENTOPS_TASK_PROMPT")
    if environment_task is not None and environment_task != parsed.task:
        raise AgentInputError("TASK_MISMATCH")
    return AgentSpec(
        parsed.task, parsed.scenario_id,
        parsed.policy.max_steps if parsed.policy is not None else 6,
        parsed.limits.timeout_ms, parsed.policy is not None, parsed.scenario_params,
        tuple(parsed.policy.instruction_patch) if parsed.policy is not None else (),
    )


def _topic(spec: AgentSpec) -> str:
    return spec.scenario_params.get("topic", "checkout latency")


def _plans(spec: AgentSpec) -> list[tuple[str, dict[str, Any]]]:
    if spec.scenario_id == "checkout-api-latency.v2":
        if not spec.policy:
            return [("fetch_service_logs", {"service": "checkout-api", "window": "5m"})] * 6
        return [
            ("check_service_health", {"service": "checkout-api"}),
            ("query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
            ("fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
        ]
    if spec.scenario_id == "multi-step-research.v2":
        if not spec.policy:
            return [("search_documents", {"query": _topic(spec)})] * 6
        return [
            ("search_documents", {"query": _topic(spec)}),
            ("fetch_document", {"document_id": "doc-dependency-latency"}),
            ("submit_answer", {"answer": "Checkout latency is caused by payment-gateway connection pool saturation."}),
        ]
    if spec.scenario_id == "api-fault-orchestration.v2":
        service = spec.scenario_params.get("service", "payments-api")
        if not spec.policy:
            return [("invoke_service", {"service": service})] * 6
        return [
            ("retry_call", {"service": service, "attempts": 2}),
            ("degrade_route", {"service": service, "mode": "cached-read"}),
            ("invoke_service", {"service": service}),
        ]
    if not spec.policy:
        return [("inspect_order", {"order_id": "order-1001"})] * 6
    return [
        ("inspect_order", {"order_id": "order-1001"}),
        ("authorize_payment", {"order_id": "order-1001"}),
        ("ship_order", {"order_id": "order-1001"}),
        ("request_refund", {"order_id": "order-1001", "reason": "shipping_failed"}),
        ("request_refund", {"order_id": "order-1001", "reason": "shipping_failed"}),
        ("inspect_order", {"order_id": "order-1001"}),
    ]


async def run(spec: AgentSpec) -> int:
    endpoint = os.environ.get("AGENTOPS_ENV_MCP_URL", "")
    token = os.environ.get("AGENTOPS_ENV_TOKEN", "")
    try:
        async with MCPAgentClient(endpoint, token, timeout_seconds=spec.timeout_ms / 1000) as client:
            names = client.world_tool_names
            for name, arguments in _plans(spec)[: spec.max_steps]:
                if name not in names:
                    raise MCPAgentError("MCP_CALL_INVALID")
                await client.call_tool(name, arguments)
            submitted = await client.call_tool("submit_result", {})
            return 0 if not submitted.is_error else 1
    except (MCPAgentError, asyncio.TimeoutError):
        return 1


def main() -> int:
    try:
        spec = read_spec()
        return asyncio.run(asyncio.wait_for(run(spec), timeout=spec.timeout_ms / 1000))
    except (AgentInputError, asyncio.TimeoutError):
        print("INVALID_SPEC", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
