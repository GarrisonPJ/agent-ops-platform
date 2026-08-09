from __future__ import annotations

import json
from hashlib import sha256

import pytest

from app.durable_events import (
    SUPPORTED_EVENT_TYPES,
    normalize_event_payload,
)


def fingerprint(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


@pytest.mark.parametrize("event_type", sorted(SUPPORTED_EVENT_TYPES))
def test_supported_event_types_drop_unknown_fields(event_type: str) -> None:
    assert normalize_event_payload(
        event_type,
        {"credential": "secret", "nested": {"endpoint": "https://provider.invalid"}},
    ) == {}


def test_unknown_event_type_never_preserves_payload() -> None:
    assert normalize_event_payload(
        "unknown_event",
        {"content": "raw content", "credential": "secret"},
    ) == {}


def test_provider_payload_redacts_sensitive_content_and_fingerprints_ids() -> None:
    raw = {
        "stream": "stderr",
        "content": (
            "https://provider.invalid/v1 "
            "Bearer raw-secret UNIQUE_RAW_PROVIDER_CONTENT "
            "UNIQUE_HIDDEN_REASONING"
        ),
        "endpoint": "https://provider.invalid/v1",
        "headers": {"Authorization": "Bearer raw-secret"},
        "hidden_reasoning": "UNIQUE_HIDDEN_REASONING",
        "provider": {
            "model": "safe-model",
            "latency_ms": 12,
            "request_count": 2,
            "token_prompt": 10,
            "token_completion": 5,
            "request_id": "raw-request-id",
            "raw_content": "UNIQUE_RAW_PROVIDER_CONTENT",
        },
        "provider_error": {
            "code": "PROVIDER_UNKNOWN_CODE",
            "message": "UNIQUE_ERROR_MESSAGE https://provider.invalid",
            "retryable": True,
            "attempts": 2,
            "request_id": "raw-error-request-id",
            "raw_headers": {"Authorization": "Bearer raw-secret"},
        },
    }

    normalized = normalize_event_payload("process_output", raw)

    assert normalized == {
        "stream": "stderr",
        "content": "Provider execution output redacted.",
        "provider": {
            "model": "safe-model",
            "latency_ms": 12,
            "request_count": 2,
            "token_prompt": 10,
            "token_completion": 5,
            "request_fingerprint": fingerprint("raw-request-id"),
        },
        "provider_error": {
            "code": "PROVIDER_UNKNOWN",
            "message": "Provider request failed",
            "retryable": True,
            "attempts": 2,
            "request_fingerprint": fingerprint("raw-error-request-id"),
        },
    }
    serialized = json.dumps(normalized)
    for secret in (
        "provider.invalid",
        "raw-secret",
        "UNIQUE_RAW_PROVIDER_CONTENT",
        "UNIQUE_HIDDEN_REASONING",
        "UNIQUE_ERROR_MESSAGE",
        "raw-request-id",
        "raw-error-request-id",
    ):
        assert secret not in serialized


def test_typed_provider_output_has_no_content_field() -> None:
    normalized = normalize_event_payload(
        "process_output",
        {
            "kind": "provider",
            "stream": "stdout",
            "content": "UNIQUE_RAW_PROVIDER_CONTENT",
            "provider": {
                "model": "safe-model",
                "latency_ms": 1,
                "request_count": 1,
                "token_prompt": 2,
                "token_completion": 3,
            },
        },
    )

    assert normalized == {
        "kind": "provider",
        "stream": "stdout",
        "provider": {
            "model": "safe-model",
            "latency_ms": 1,
            "request_count": 1,
            "token_prompt": 2,
            "token_completion": 3,
        },
    }
    assert "content" not in normalized


def test_malformed_provider_metadata_drops_untyped_provider_facts() -> None:
    normalized = normalize_event_payload(
        "process_output",
        {
            "stream": "stdout",
            "content": "UNIQUE_MALFORMED_RAW_CONTENT",
            "provider": {},
            "provider_error": {"code": "PROVIDER_TIMEOUT"},
        },
    )

    assert normalized == {
        "stream": "stdout",
        "content": "Provider execution output redacted.",
    }


def test_ordinary_agent_output_is_bounded_and_allowlisted() -> None:
    from app.durable_events import MAX_EVENT_CONTENT

    content = "ordinary output " * MAX_EVENT_CONTENT
    normalized = normalize_event_payload(
        "process_output",
        {
            "kind": "agent",
            "stream": "stdout",
            "content": content,
            "credential": "drop me",
            "headers": {"Authorization": "drop me"},
        },
    )

    assert normalized["kind"] == "agent"
    assert normalized["stream"] == "stdout"
    assert len(normalized["content"]) == MAX_EVENT_CONTENT
    assert set(normalized) == {"kind", "stream", "content"}


def test_step_completed_preserves_only_required_bounded_fields() -> None:
    normalized = normalize_event_payload(
        "step_completed",
        {
            "index": 2,
            "decision_summary": "Collect the next diagnostic signal.",
            "tool_call": {
                "name": "fetch_service_logs",
                "id": "raw-tool-call-id",
                "arguments": {
                    "service": "checkout-api",
                    "query": "payment dependency timeout",
                    "headers": {"Authorization": "raw-secret"},
                    "raw_content": "UNIQUE_RAW_PROVIDER_CONTENT",
                },
            },
            "observation": "Payment dependency p95 increased.",
            "latency_ms": 48,
            "token_prompt": 31,
            "token_completion": 12,
            "context_window": {
                "used": 8_191,
                "limit": 8_192,
                "headers": {"Authorization": "raw-secret"},
            },
            "attempt": 2,
            "credential": "drop me",
        },
    )

    assert normalized == {
        "index": 2,
        "decision_summary": "Collect the next diagnostic signal.",
        "tool_call": {
            "name": "fetch_service_logs",
            "arguments": {
                "service": "checkout-api",
                "query": "payment dependency timeout",
            },
        },
        "observation": "Payment dependency p95 increased.",
        "latency_ms": 48,
        "token_prompt": 31,
        "token_completion": 12,
        "context_window": {"used": 8_191, "limit": 8_192},
        "attempt": 2,
    }
    serialized = json.dumps(normalized)
    assert "raw-tool-call-id" not in serialized
    assert "raw-secret" not in serialized
    assert "UNIQUE_RAW_PROVIDER_CONTENT" not in serialized


def test_provider_and_context_numeric_fields_are_bounded() -> None:
    from app.durable_events import (
        MAX_CONTEXT_WINDOW_TOKENS,
        MAX_PROVIDER_LATENCY_MS,
        MAX_PROVIDER_TOKENS,
    )

    normalized = normalize_event_payload(
        "step_completed",
        {
            "index": 10_000_000,
            "latency_ms": MAX_PROVIDER_LATENCY_MS * 2,
            "token_prompt": MAX_PROVIDER_TOKENS * 2,
            "token_completion": MAX_PROVIDER_TOKENS * 2,
            "context_window": {
                "used": MAX_CONTEXT_WINDOW_TOKENS * 2,
                "limit": MAX_CONTEXT_WINDOW_TOKENS * 2,
            },
        },
    )

    assert normalized["latency_ms"] == MAX_PROVIDER_LATENCY_MS
    assert normalized["token_prompt"] == MAX_PROVIDER_TOKENS
    assert normalized["token_completion"] == MAX_PROVIDER_TOKENS
    assert normalized["context_window"] == {
        "used": MAX_CONTEXT_WINDOW_TOKENS,
        "limit": MAX_CONTEXT_WINDOW_TOKENS,
    }
