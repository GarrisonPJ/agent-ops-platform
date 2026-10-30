"""Typed, loss-limiting normalization for facts that may become durable.

Runner uploads and Environment capture use this projection before persistence.
Final-check operands must survive exactly or fail, never be silently repaired.
Read final_evidence.py for operands and env_server.py for trusted generation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.final_evidence import FinalCheck, validate_final_check


MAX_PROVIDER_LATENCY_MS = 86_400_000
MAX_PROVIDER_REQUEST_COUNT = 1_000
MAX_PROVIDER_TOKENS = 1_000_000_000
MAX_PROVIDER_TOTAL_TOKENS = 2_000_000_000
MAX_EVENT_CONTENT = 4_000
MAX_ATTEMPT = 100
MAX_STEP_INDEX = 1_000_000
MAX_EXIT_CODE = 2_147_483_647
MAX_CONTEXT_WINDOW_TOKENS = 10_000_000
MAX_TOOL_ARGUMENTS = 4
MAX_TOOL_ARGUMENT_VALUE = 200


class DurableModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderFailureKind(StrEnum):
    NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    CONFIGURATION_ERROR = "PROVIDER_CONFIGURATION_ERROR"
    TIMEOUT = "PROVIDER_TIMEOUT"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    HTTP_ERROR = "PROVIDER_HTTP_ERROR"
    INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    STEP_LIMIT = "PROVIDER_STEP_LIMIT"
    UNSUPPORTED_TOOL = "PROVIDER_UNSUPPORTED_TOOL"
    UNKNOWN = "PROVIDER_UNKNOWN"


class TerminalFailureKind(StrEnum):
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    AGENT_EXIT = "agent_exit"
    PROVIDER_FAILURE = "provider_failure"
    INTERNAL_FAILURE = "internal_failure"


class ProviderTelemetrySummary(DurableModel):
    model: str = Field(min_length=1, max_length=200)
    latency_ms: int = Field(ge=0, le=MAX_PROVIDER_LATENCY_MS)
    request_count: int = Field(ge=0, le=MAX_PROVIDER_REQUEST_COUNT)
    token_prompt: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    token_completion: int = Field(ge=0, le=MAX_PROVIDER_TOKENS)
    request_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class ProviderFailureSummary(DurableModel):
    code: ProviderFailureKind
    message: str = Field(min_length=1, max_length=200)
    retryable: bool
    attempts: int = Field(ge=0, le=100)
    request_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class ProviderProcessOutput(DurableModel):
    """The only new durable representation of Provider output."""

    kind: Literal["provider"] = "provider"
    stream: Literal["stdout", "stderr"] | None = None
    provider: ProviderTelemetrySummary | None = None
    provider_error: ProviderFailureSummary | None = None


class AgentProcessOutput(DurableModel):
    kind: Literal["agent"] = "agent"
    stream: Literal["stdout", "stderr"] | None = None
    content: str = Field(max_length=MAX_EVENT_CONTENT)


class ToolCallSummary(DurableModel):
    name: Literal[
        "check_service_health",
        "query_service_metrics",
        "fetch_service_logs",
        "search_documents",
        "fetch_document",
        "submit_answer",
        "invoke_service",
        "retry_call",
        "degrade_route",
        "inspect_order",
        "authorize_payment",
        "ship_order",
        "request_refund",
    ]
    arguments: dict[str, str | int] = Field(
        default_factory=dict, max_length=MAX_TOOL_ARGUMENTS
    )


class ContextWindowSummary(DurableModel):
    used: int = Field(ge=0, le=MAX_CONTEXT_WINDOW_TOKENS)
    limit: int = Field(ge=0, le=MAX_CONTEXT_WINDOW_TOKENS)


class AgentReportedUsage(DurableModel):
    """Cumulative Agent claims, not measured provider telemetry; null is unknown."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["agent_reported"] = "agent_reported"
    token_prompt: int | None = Field(default=None, ge=0, le=MAX_PROVIDER_TOKENS)
    token_completion: int | None = Field(default=None, ge=0, le=MAX_PROVIDER_TOKENS)
    context_window: ContextWindowSummary | None = None


class EnvironmentStepProvenance(DurableModel):
    """Trusted only on the Environment pipe, never on Agent stdout (ADR-0008)."""

    kind: Literal["world", "control"]
    result: Literal[
        "ok",
        "invalid_arguments",
        "unknown_tool",
        "max_steps",
        "finalized",
        "duplicate_submit",
        "timed_out",
        "internal_error",
    ]
    control: Literal["submit_result", "report_usage"] | None = None
    finalized: bool = False
    check_called: bool = False
    usage: AgentReportedUsage | None = None
    final_check: FinalCheck | None = None


_PROVIDER_FAILURE_MESSAGES = {
    ProviderFailureKind.NOT_CONFIGURED: "Provider execution is not configured on the Runner",
    ProviderFailureKind.CONFIGURATION_ERROR: "Provider configuration is invalid",
    ProviderFailureKind.TIMEOUT: "Provider request timed out",
    ProviderFailureKind.UNAVAILABLE: "Provider request could not be completed",
    ProviderFailureKind.RATE_LIMITED: "Provider request was rate limited",
    ProviderFailureKind.HTTP_ERROR: "Provider request failed",
    ProviderFailureKind.INVALID_RESPONSE: "Provider returned an invalid response",
    ProviderFailureKind.STEP_LIMIT: "Provider exhausted the configured step limit",
    ProviderFailureKind.UNSUPPORTED_TOOL: "Provider selected an unsupported tool",
    ProviderFailureKind.UNKNOWN: "Provider request failed",
}

_TERMINAL_FAILURE_MESSAGES = {
    TerminalFailureKind.CANCELLED: "Run cancelled",
    TerminalFailureKind.TIMED_OUT: "Execution timed out",
    TerminalFailureKind.OUTPUT_LIMIT_EXCEEDED: "Agent output exceeded the configured limit",
    TerminalFailureKind.AGENT_EXIT: "Agent process exited unsuccessfully",
    TerminalFailureKind.PROVIDER_FAILURE: "Provider request failed",
    TerminalFailureKind.INTERNAL_FAILURE: "Agent execution failed",
}

_TERMINAL_FAILURE_VALUES = {item.value: item for item in TerminalFailureKind}
_ALLOWED_TOOL_ARGUMENT_VALUES = {
    "service": frozenset({"checkout-api", "payments-api"}),
    "metric": frozenset({"dependency_latency"}),
    "window": frozenset({"5m"}),
    "query": frozenset({"checkout latency", "payment dependency timeout"}),
    "document_id": frozenset({"doc-dependency-latency"}),
    "answer": frozenset(
        {"Checkout latency is caused by payment-gateway connection pool saturation."}
    ),
    "attempts": frozenset({"2"}),
    "mode": frozenset({"cached-read"}),
}


def normalize_provider_failure_kind(value: object) -> ProviderFailureKind:
    if isinstance(value, ProviderFailureKind):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        for kind in ProviderFailureKind:
            if kind.value == normalized:
                return kind
    return ProviderFailureKind.UNKNOWN


def provider_failure_message(value: object) -> str:
    return _PROVIDER_FAILURE_MESSAGES[normalize_provider_failure_kind(value)]


def normalize_terminal_failure_kind(
    value: object,
    *,
    status: str | None = None,
    provider_failure: bool = False,
) -> TerminalFailureKind | None:
    if status == "succeeded":
        return None
    if status == "cancelled":
        return TerminalFailureKind.CANCELLED
    if status == "timed_out":
        return TerminalFailureKind.TIMED_OUT
    if provider_failure and status == "failed":
        return TerminalFailureKind.PROVIDER_FAILURE
    if isinstance(value, TerminalFailureKind):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TERMINAL_FAILURE_VALUES:
            return _TERMINAL_FAILURE_VALUES[normalized]
        if "cancel" in normalized:
            return TerminalFailureKind.CANCELLED
        if "timed out" in normalized or "timeout" in normalized:
            return TerminalFailureKind.TIMED_OUT
        if "output" in normalized and "limit" in normalized:
            return TerminalFailureKind.OUTPUT_LIMIT_EXCEEDED
        if "agent exited" in normalized or "exit status" in normalized:
            return TerminalFailureKind.AGENT_EXIT
        if "provider" in normalized:
            return TerminalFailureKind.PROVIDER_FAILURE
    if status == "failed":
        return TerminalFailureKind.INTERNAL_FAILURE
    return None


def terminal_failure_message(value: object) -> str:
    kind = normalize_terminal_failure_kind(value, status="failed")
    return _TERMINAL_FAILURE_MESSAGES[kind or TerminalFailureKind.INTERNAL_FAILURE]


def request_fingerprint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 500:
        return None
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _existing_fingerprint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) == 71 and normalized.startswith("sha256:"):
        digest = normalized[7:]
        if all(character in "0123456789abcdef" for character in digest):
            return normalized
    return None


def _bounded_nonnegative_int(value: object, maximum: int) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return min(value, maximum)


def _safe_stream(value: object) -> str | None:
    return value if value in {"stdout", "stderr"} else None


def _normalize_provider_telemetry(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    model = value.get("model")
    if not isinstance(model, str) or not model.strip():
        return None
    model = model.strip()[:200]
    latency_ms = _bounded_nonnegative_int(
        value.get("latency_ms"), MAX_PROVIDER_LATENCY_MS
    )
    request_count = _bounded_nonnegative_int(
        value.get("request_count"), MAX_PROVIDER_REQUEST_COUNT
    )
    token_prompt = _bounded_nonnegative_int(
        value.get("token_prompt"), MAX_PROVIDER_TOKENS
    )
    token_completion = _bounded_nonnegative_int(
        value.get("token_completion"), MAX_PROVIDER_TOKENS
    )
    if None in {latency_ms, request_count, token_prompt, token_completion}:
        return None
    fingerprint = request_fingerprint(value.get("request_id"))
    if fingerprint is None:
        fingerprint = _existing_fingerprint(value.get("request_fingerprint"))
    return ProviderTelemetrySummary(
        model=model,
        latency_ms=latency_ms,
        request_count=request_count,
        token_prompt=token_prompt,
        token_completion=token_completion,
        request_fingerprint=fingerprint,
    ).model_dump(mode="json", exclude_none=True)


def _normalize_provider_failure(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    code = value.get("code", value.get("kind"))
    retryable = value.get("retryable")
    attempts = _bounded_nonnegative_int(value.get("attempts"), 100)
    if code is None or not isinstance(retryable, bool) or attempts is None:
        return None
    kind = normalize_provider_failure_kind(code)
    fingerprint = request_fingerprint(value.get("request_id"))
    if fingerprint is None:
        fingerprint = _existing_fingerprint(value.get("request_fingerprint"))
    return ProviderFailureSummary(
        code=kind,
        message=_PROVIDER_FAILURE_MESSAGES[kind],
        retryable=retryable,
        attempts=attempts,
        request_fingerprint=fingerprint,
    ).model_dump(mode="json", exclude_none=True)


def _normalize_process_output(payload: Mapping[str, object]) -> dict[str, object]:
    kind = payload.get("kind")
    if kind is not None and kind not in {"provider", "agent"}:
        return {}
    has_provider_metadata = (
        kind == "provider"
        or "provider" in payload
        or "provider_error" in payload
    )
    if has_provider_metadata:
        provider = _normalize_provider_telemetry(payload.get("provider"))
        provider_error = _normalize_provider_failure(payload.get("provider_error"))
        if kind == "provider":
            normalized = ProviderProcessOutput(
                stream=_safe_stream(payload.get("stream")),
                provider=provider,
                provider_error=provider_error,
            )
            return normalized.model_dump(mode="json", exclude_none=True)

        # Legacy payloads are kept idempotent in their old shape, but the
        # content is replaced whenever Provider metadata was present.
        normalized = {"content": "Provider execution output redacted."}
        stream = _safe_stream(payload.get("stream"))
        if stream is not None:
            normalized["stream"] = stream
        if provider is not None:
            normalized["provider"] = provider
        if provider_error is not None:
            normalized["provider_error"] = provider_error
        return normalized

    if payload.get("kind") == "agent":
        content = payload.get("content")
        normalized = AgentProcessOutput(
            stream=_safe_stream(payload.get("stream")),
            content=content[:MAX_EVENT_CONTENT] if isinstance(content, str) else "",
        )
        return normalized.model_dump(mode="json", exclude_none=True)

    # Existing agents use the pre-discriminator shape. Keep it compatible and
    # drop fields that were never part of ordinary process output.
    normalized: dict[str, object] = {}
    stream = _safe_stream(payload.get("stream"))
    content = payload.get("content")
    if stream is not None:
        normalized["stream"] = stream
    if isinstance(content, str):
        normalized["content"] = content[:MAX_EVENT_CONTENT]
    return normalized


def _normalize_tool_call(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    if not isinstance(name, str):
        return None
    if name in {"inspect_order", "authorize_payment", "ship_order", "request_refund"}:
        raw_arguments = value.get("arguments", {})
        if not isinstance(raw_arguments, Mapping):
            return None
        # This fixed order-only projection must not broaden the legacy nine-tool
        # argument surface or retain agent-supplied customer data.
        order_arguments: dict[str, str | int] = {}
        if raw_arguments.get("order_id") == "order-1001":
            order_arguments["order_id"] = "order-1001"
        if name == "request_refund" and raw_arguments.get("reason") == "shipping_failed":
            order_arguments["reason"] = "shipping_failed"
        return ToolCallSummary(name=name, arguments=order_arguments).model_dump(
            mode="json", exclude_none=True
        )
    if name not in {
        "check_service_health",
        "query_service_metrics",
        "fetch_service_logs",
        "search_documents",
        "fetch_document",
        "submit_answer",
        "invoke_service",
        "retry_call",
        "degrade_route",
    }:
        return None
    raw_arguments = value.get("arguments", {})
    if not isinstance(raw_arguments, Mapping):
        return None
    arguments: dict[str, str | int] = {}
    for key, allowed_values in _ALLOWED_TOOL_ARGUMENT_VALUES.items():
        argument = raw_arguments.get(key)
        if (
            isinstance(argument, str)
            and len(argument) <= MAX_TOOL_ARGUMENT_VALUE
            and argument in allowed_values
        ):
            arguments[key] = argument
        elif (
            isinstance(argument, int)
            and not isinstance(argument, bool)
            and str(argument) in allowed_values
        ):
            arguments[key] = argument
    return ToolCallSummary(name=name, arguments=arguments).model_dump(
        mode="json", exclude_none=True
    )


def _normalize_context_window(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    used = _bounded_nonnegative_int(
        value.get("used"), MAX_CONTEXT_WINDOW_TOKENS
    )
    limit = _bounded_nonnegative_int(
        value.get("limit"), MAX_CONTEXT_WINDOW_TOKENS
    )
    if used is None or limit is None:
        return None
    return ContextWindowSummary(used=used, limit=limit).model_dump(
        mode="json", exclude_none=True
    )


def _normalize_environment_step(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    # Explicitly sieve fields: Agent-authored score/status/check evaluations are
    # never an operand at the durable seam, even when nested under provenance.
    fields = {
        key: value[key]
        for key in (
            "kind",
            "result",
            "control",
            "finalized",
            "check_called",
        )
        if key in value
    }
    if "final_check" in value:
        # Presence, including explicit null, is fail-closed. Validate the raw
        # tuple before legacy diagnostics can default or repair any flags.
        if not (
            value.get("kind") == "control"
            and value.get("control") == "submit_result"
            and value.get("result") == "ok"
            and value.get("finalized") is True
            and value.get("check_called") is True
        ):
            raise ValueError("final-check provenance mismatch")
        fields["final_check"] = validate_final_check(value["final_check"])
    try:
        parsed = EnvironmentStepProvenance.model_validate(fields, strict=True)
    except ValidationError:
        if "final_check" in value:
            raise
        return None
    if (parsed.kind == "control") != (parsed.control is not None):
        return None
    parsed.check_called = (
        parsed.control == "submit_result"
        and parsed.result in {"ok", "internal_error"}
        and parsed.finalized
        and parsed.check_called
    )
    if parsed.control == "report_usage" and parsed.result == "ok":
        usage = value.get("usage")
        if isinstance(usage, Mapping):
            try:
                parsed.usage = AgentReportedUsage.model_validate(
                    dict(usage), strict=True
                )
            except ValidationError:
                pass
    normalized = parsed.model_dump(mode="json", exclude_none=True)
    if parsed.final_check is not None:
        # Null is a required operand for trajectory-only suites, not an absent
        # optional field. Recursive exclude_none would otherwise erase it.
        normalized["final_check"] = parsed.final_check.model_dump(mode="json")
    return normalized


def _normalize_step_completed(payload: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, maximum in (
        ("index", MAX_STEP_INDEX),
        ("latency_ms", MAX_PROVIDER_LATENCY_MS),
        ("token_prompt", MAX_PROVIDER_TOKENS),
        ("token_completion", MAX_PROVIDER_TOKENS),
    ):
        value = _bounded_nonnegative_int(payload.get(key), maximum)
        if value is not None:
            normalized[key] = value
    for key, maximum in (("decision_summary", 500), ("observation", 4_000)):
        value = payload.get(key)
        if isinstance(value, str):
            normalized[key] = value[:maximum]
    tool_call = _normalize_tool_call(payload.get("tool_call"))
    if tool_call is not None:
        normalized["tool_call"] = tool_call
    environment = _normalize_environment_step(payload.get("environment"))
    if environment is not None:
        normalized["environment"] = environment
    context_window = _normalize_context_window(payload.get("context_window"))
    if context_window is not None:
        normalized["context_window"] = context_window
    attempt = _bounded_nonnegative_int(payload.get("attempt"), MAX_ATTEMPT)
    if attempt is not None and attempt >= 1:
        normalized["attempt"] = attempt
    return normalized


def _normalize_terminal_event(payload: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    attempt = _bounded_nonnegative_int(payload.get("attempt"), MAX_ATTEMPT)
    if attempt is not None and attempt >= 1:
        normalized["attempt"] = attempt
    status = payload.get("status")
    if isinstance(status, str) and status in {
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    }:
        normalized["status"] = status
    failure_kind = normalize_terminal_failure_kind(
        payload.get("failure_kind", payload.get("error")),
        status=status if isinstance(status, str) else None,
    )
    if failure_kind is not None:
        normalized["failure_kind"] = failure_kind.value
    exit_code = _bounded_nonnegative_int(payload.get("exit_code"), MAX_EXIT_CODE)
    if exit_code is not None:
        normalized["exit_code"] = exit_code
    return normalized


def _normalize_run_started(payload: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    attempt = _bounded_nonnegative_int(payload.get("attempt"), MAX_ATTEMPT)
    if attempt is not None and attempt >= 1:
        normalized["attempt"] = attempt
    if payload.get("source") == "environment":
        normalized["source"] = "environment"
    return normalized


def _normalize_run_completed(payload: Mapping[str, object]) -> dict[str, object]:
    return _normalize_terminal_event(payload)


def _normalize_run_failed(payload: Mapping[str, object]) -> dict[str, object]:
    return _normalize_terminal_event(payload)


def _normalize_run_cancelled(payload: Mapping[str, object]) -> dict[str, object]:
    return _normalize_terminal_event(payload)


_EVENT_NORMALIZERS: dict[
    str, Callable[[Mapping[str, object]], dict[str, object]]
] = {
    "run_started": _normalize_run_started,
    "step_completed": _normalize_step_completed,
    "process_output": _normalize_process_output,
    "run_completed": _normalize_run_completed,
    "run_failed": _normalize_run_failed,
    "run_cancelled": _normalize_run_cancelled,
}


SUPPORTED_EVENT_TYPES = frozenset(_EVENT_NORMALIZERS)


def normalize_event_payload(event_type: str, payload: object) -> dict[str, object]:
    """Return the only payload form allowed to cross the durable event seam."""

    if not isinstance(event_type, str) or not isinstance(payload, Mapping):
        return {}
    normalizer = _EVENT_NORMALIZERS.get(event_type)
    if normalizer is None:
        return {}
    return normalizer(payload)
