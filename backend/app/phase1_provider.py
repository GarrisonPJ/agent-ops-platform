"""Narrow OpenAI-compatible provider boundary for the Phase 1 Python agent."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from app.phase1_schemas import EvaluationSpec


DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_MAX_RETRIES = 1
MAX_RETRIES = 3
MAX_PROVIDER_CONTENT_CHARS = 500


@dataclass(frozen=True)
class ProviderSettings:
    """Server-side configuration inherited by the supervised Python agent."""

    base_url: str
    api_key: str
    model: str
    timeout_ms: int
    max_retries: int

    @classmethod
    def from_env(cls) -> ProviderSettings:
        values = {
            "base_url": os.getenv("AGENTOPS_PROVIDER_BASE_URL", "").strip(),
            "api_key": os.getenv("AGENTOPS_PROVIDER_API_KEY", "").strip(),
            "model": os.getenv("AGENTOPS_PROVIDER_MODEL", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ProviderError(
                "PROVIDER_NOT_CONFIGURED",
                "Provider execution is not configured on the Runner",
                retryable=False,
                attempts=0,
            )

        parsed = urlparse(values["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderError(
                "PROVIDER_CONFIGURATION_ERROR",
                "Provider base URL must be an absolute HTTP(S) URL",
                retryable=False,
                attempts=0,
            )

        return cls(
            base_url=values["base_url"].rstrip("/"),
            api_key=values["api_key"],
            model=values["model"],
            timeout_ms=_integer_env(
                "AGENTOPS_PROVIDER_TIMEOUT_MS",
                DEFAULT_TIMEOUT_MS,
                minimum=100,
                maximum=60_000,
            ),
            max_retries=_integer_env(
                "AGENTOPS_PROVIDER_MAX_RETRIES",
                DEFAULT_MAX_RETRIES,
                minimum=0,
                maximum=MAX_RETRIES,
            ),
        )


class ProviderError(Exception):
    """A safe-to-persist provider failure without endpoint or credential detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        attempts: int,
        latency_ms: int = 0,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.attempts = attempts
        self.latency_ms = latency_ms
        self.request_id = request_id

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "attempts": self.attempts,
        }
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, object]


@dataclass(frozen=True)
class ProviderResponse:
    content: str | None
    tool_calls: list[ProviderToolCall]
    usage: ProviderUsage
    latency_ms: int
    request_count: int
    request_id: str | None


class OpenAICompatibleProvider:
    """One bounded ``/chat/completions`` client for the Phase 1 agent only."""

    def __init__(
        self,
        settings: ProviderSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    async def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderResponse:
        if self._client is not None:
            return await self._chat_with_client(self._client, messages, tools)
        timeout = httpx.Timeout(self.settings.timeout_ms / 1_000)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await self._chat_with_client(client, messages, tools)

    async def _chat_with_client(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderResponse:
        started_at = perf_counter()
        for attempt in range(1, self.settings.max_retries + 2):
            try:
                response = await client.post(
                    f"{self.settings.base_url}/chat/completions",
                    json={
                        "model": self.settings.model,
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                    },
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.settings.timeout_ms / 1_000,
                )
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                error = ProviderError(
                    "PROVIDER_TIMEOUT",
                    "Provider request timed out",
                    retryable=True,
                    attempts=attempt,
                    latency_ms=_elapsed_ms(started_at),
                )
            except httpx.HTTPError:
                error = ProviderError(
                    "PROVIDER_UNAVAILABLE",
                    "Provider request could not be completed",
                    retryable=True,
                    attempts=attempt,
                    latency_ms=_elapsed_ms(started_at),
                )
            else:
                if response.is_success:
                    return _parse_response(
                        response,
                        latency_ms=_elapsed_ms(started_at),
                        request_count=attempt,
                    )
                status_code = response.status_code
                error = ProviderError(
                    "PROVIDER_RATE_LIMITED"
                    if status_code == 429
                    else "PROVIDER_HTTP_ERROR",
                    f"Provider request failed with HTTP {status_code}",
                    retryable=status_code in {408, 429} or status_code >= 500,
                    attempts=attempt,
                    latency_ms=_elapsed_ms(started_at),
                    request_id=_provider_request_id(response),
                )

            if not error.retryable or attempt > self.settings.max_retries:
                raise error
            await asyncio.sleep(0.05 * attempt)

        raise AssertionError("provider retry loop must return or raise")


def provider_tool_definitions() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "check_service_health",
                "description": "Check checkout-api health in the deterministic scenario.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_service_metrics",
                "description": "Read checkout-api dependency latency metrics.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_service_logs",
                "description": "Read the evidence-backed checkout-api logs.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
    ]


async def run_provider_agent(
    spec: EvaluationSpec,
    emit: Callable[[str, dict[str, object]], None],
) -> int:
    """Run the safe fixture tools selected by an OpenAI-compatible model."""

    try:
        settings = ProviderSettings.from_env()
    except ProviderError as error:
        _emit_provider_error(emit, error)
        return 1

    provider = OpenAICompatibleProvider(settings)
    instructions = [
        "You investigate checkout API latency using only the provided tools.",
        "Use at most one tool call per response.",
        "Do not request shell commands, network access, or arbitrary code execution.",
    ]
    if spec.policy is not None:
        instructions.extend(spec.policy.instruction_patch)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "\n".join(instructions)},
        {"role": "user", "content": spec.task},
    ]
    max_steps = spec.policy.max_steps if spec.policy is not None else 6
    step_index = 0

    try:
        while step_index < max_steps:
            response = await provider.chat(messages, provider_tool_definitions())
            provider_payload: dict[str, object] = {
                "model": settings.model,
                "latency_ms": response.latency_ms,
                "request_count": response.request_count,
                "token_prompt": response.usage.prompt_tokens,
                "token_completion": response.usage.completion_tokens,
            }
            if response.request_id is not None:
                provider_payload["request_id"] = response.request_id
            emit(
                "process_output",
                {
                    "stream": "stdout",
                    "content": "Provider request completed.",
                    "provider": provider_payload,
                },
            )
            if not response.tool_calls:
                if response.content:
                    emit(
                        "process_output",
                        {
                            "stream": "stdout",
                            "content": _redact(
                                response.content,
                                settings.api_key,
                                settings.base_url,
                            ),
                        },
                    )
                return 0
            if len(response.tool_calls) != 1:
                _emit_provider_error(
                    emit,
                    ProviderError(
                        "PROVIDER_INVALID_RESPONSE",
                        "Provider must return at most one tool call per response",
                        retryable=False,
                        attempts=response.request_count,
                        latency_ms=response.latency_ms,
                    ),
                )
                return 1

            tool_call = response.tool_calls[0]
            try:
                arguments, observation = _fixture_tool_result(tool_call.name)
            except ProviderError as error:
                _emit_provider_error(emit, error)
                return 1
            emit(
                "step_completed",
                {
                    "index": step_index,
                    "decision_summary": f"Provider selected {tool_call.name} for the checkout scenario.",
                    "tool_call": {"name": tool_call.name, "arguments": arguments},
                    "observation": observation,
                    "latency_ms": response.latency_ms,
                    "token_prompt": response.usage.prompt_tokens,
                    "token_completion": response.usage.completion_tokens,
                    "context_window": None,
                },
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [tool_call.raw],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "content": observation,
                }
            )
            step_index += 1
    except ProviderError as error:
        _emit_provider_error(emit, error, model=settings.model)
        return 1

    _emit_provider_error(
        emit,
        ProviderError(
            "PROVIDER_STEP_LIMIT",
            "Provider exhausted the configured step limit",
            retryable=False,
            attempts=0,
        ),
        model=settings.model,
    )
    return 1


def _integer_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderError(
            "PROVIDER_CONFIGURATION_ERROR",
            f"{name} must be an integer",
            retryable=False,
            attempts=0,
        ) from exc
    if not minimum <= value <= maximum:
        raise ProviderError(
            "PROVIDER_CONFIGURATION_ERROR",
            f"{name} must be between {minimum} and {maximum}",
            retryable=False,
            attempts=0,
        )
    return value


def _parse_response(
    response: httpx.Response, *, latency_ms: int, request_count: int
) -> ProviderResponse:
    try:
        data = response.json()
        choices = data["choices"]
        message = choices[0]["message"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned an invalid chat completion response",
            retryable=False,
            attempts=request_count,
            latency_ms=latency_ms,
        ) from exc
    if not isinstance(message, dict):
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned an invalid chat completion message",
            retryable=False,
            attempts=request_count,
            latency_ms=latency_ms,
        )

    raw_tool_calls = message.get("tool_calls") or []
    if not isinstance(raw_tool_calls, list):
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned invalid tool calls",
            retryable=False,
            attempts=request_count,
            latency_ms=latency_ms,
        )
    tool_calls = [_parse_tool_call(item, request_count, latency_ms) for item in raw_tool_calls]
    usage = data.get("usage") if isinstance(data, dict) else None
    return ProviderResponse(
        content=message.get("content") if isinstance(message.get("content"), str) else None,
        tool_calls=tool_calls,
        usage=ProviderUsage(
            prompt_tokens=_nonnegative_int(usage.get("prompt_tokens") if isinstance(usage, dict) else 0),
            completion_tokens=_nonnegative_int(
                usage.get("completion_tokens") if isinstance(usage, dict) else 0
            ),
        ),
        latency_ms=latency_ms,
        request_count=request_count,
        request_id=_provider_request_id(response, data),
    )


def _provider_request_id(response: httpx.Response, data: object | None = None) -> str | None:
    values = [response.headers.get("x-request-id"), response.headers.get("request-id")]
    if isinstance(data, dict):
        values.append(data.get("id"))
    for value in values:
        if isinstance(value, str):
            value = value.strip()
            if value and "://" not in value:
                return value[:200]
    return None


def _parse_tool_call(item: object, attempts: int, latency_ms: int) -> ProviderToolCall:
    if not isinstance(item, dict):
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned an invalid tool call",
            retryable=False,
            attempts=attempts,
            latency_ms=latency_ms,
        )
    call_id = item.get("id")
    function = item.get("function")
    if not isinstance(call_id, str) or not call_id or not isinstance(function, dict):
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned an invalid tool call",
            retryable=False,
            attempts=attempts,
            latency_ms=latency_ms,
        )
    name = function.get("name")
    arguments = function.get("arguments", "{}")
    if not isinstance(name, str) or not name or not isinstance(arguments, str):
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned an invalid tool call",
            retryable=False,
            attempts=attempts,
            latency_ms=latency_ms,
        )
    try:
        parsed_arguments = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned invalid tool arguments",
            retryable=False,
            attempts=attempts,
            latency_ms=latency_ms,
        ) from exc
    if not isinstance(parsed_arguments, dict):
        raise ProviderError(
            "PROVIDER_INVALID_RESPONSE",
            "Provider returned invalid tool arguments",
            retryable=False,
            attempts=attempts,
            latency_ms=latency_ms,
        )
    return ProviderToolCall(
        id=call_id,
        name=name,
        arguments=parsed_arguments,
        raw={
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        },
    )


def _fixture_tool_result(name: str) -> tuple[dict[str, str], str]:
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


def _emit_provider_error(
    emit: Callable[[str, dict[str, object]], None],
    error: ProviderError,
    *,
    model: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "stream": "stderr",
        "content": "Provider execution failed.",
        "provider_error": error.payload(),
    }
    if model is not None:
        provider_payload: dict[str, object] = {
            "model": model,
            "latency_ms": error.latency_ms,
            "request_count": error.attempts,
            "token_prompt": 0,
            "token_completion": 0,
        }
        if error.request_id is not None:
            provider_payload["request_id"] = error.request_id
        payload["provider"] = provider_payload
    emit("process_output", payload)


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1_000))


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _redact(content: str, *values: str) -> str:
    for value in values:
        if value:
            content = content.replace(value, "[redacted]")
    return content[:MAX_PROVIDER_CONTENT_CHARS]
