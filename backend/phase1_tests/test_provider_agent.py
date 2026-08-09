from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.durable_events import MAX_PROVIDER_LATENCY_MS
from app.phase1_provider import (
    ProviderError,
    _emit_provider_error,
    run_provider_agent,
)
from app.phase1_schemas import EvaluationSpec, ExecutionMode


@dataclass(frozen=True)
class FakeReply:
    status: int
    body: dict[str, object]
    delay_seconds: float = 0.0
    headers: dict[str, str] | None = None


class FakeOpenAICompatibleServer:
    def __init__(self, replies: list[FakeReply]) -> None:
        self.base_url = ""
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.request_received = threading.Event()
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                with owner._lock:
                    owner.requests.append(
                        {
                            "path": self.path,
                            "headers": dict(self.headers.items()),
                            "json": json.loads(raw_body),
                        }
                    )
                    reply = (
                        owner.replies.pop(0)
                        if owner.replies
                        else FakeReply(500, {"error": "no fake response configured"})
                    )
                owner.request_received.set()
                if reply.delay_seconds:
                    time.sleep(reply.delay_seconds)
                encoded = json.dumps(reply.body).encode()
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                for name, value in (reply.headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)


@pytest.fixture
def fake_provider_server():
    servers: list[FakeOpenAICompatibleServer] = []

    def create(replies: list[FakeReply]) -> FakeOpenAICompatibleServer:
        server = FakeOpenAICompatibleServer(replies)
        servers.append(server)
        return server

    yield create

    for server in servers:
        server.close()


def provider_spec() -> EvaluationSpec:
    return EvaluationSpec(
        run_id="run-provider",
        experiment_id="experiment-provider",
        task="Investigate checkout API latency",
        execution_mode=ExecutionMode.PROVIDER,
    )


def fingerprint(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def configure_provider(
    monkeypatch: pytest.MonkeyPatch,
    server: FakeOpenAICompatibleServer,
    *,
    timeout_ms: int = 1_000,
    max_retries: int = 0,
) -> None:
    monkeypatch.setenv("AGENTOPS_PROVIDER_BASE_URL", server.base_url)
    monkeypatch.setenv("AGENTOPS_PROVIDER_API_KEY", "test-provider-secret")
    monkeypatch.setenv("AGENTOPS_PROVIDER_MODEL", "fake-checkout-model")
    monkeypatch.setenv("AGENTOPS_PROVIDER_TIMEOUT_MS", str(timeout_ms))
    monkeypatch.setenv("AGENTOPS_PROVIDER_MAX_RETRIES", str(max_retries))


def completion(
    *,
    content: str | None,
    tool_calls: list[dict[str, object]] | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_content: str | None = None,
    response_id: str | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    result: dict[str, object] = {
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }
    if response_id is not None:
        result["id"] = response_id
    return result


@pytest.mark.asyncio
async def test_provider_agent_uses_local_compatible_server_without_emitting_raw_content(
    monkeypatch: pytest.MonkeyPatch, fake_provider_server
) -> None:
    server = fake_provider_server([])
    server.replies.extend(
        [
            FakeReply(
                200,
                completion(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call-health",
                            "type": "function",
                            "function": {
                                "name": "check_service_health",
                                "arguments": "{}",
                            },
                        }
                    ],
                    prompt_tokens=11,
                    completion_tokens=7,
                    response_id="chatcmpl-observe-1",
                ),
            ),
            FakeReply(
                200,
                completion(
                    content=(
                        "UNIQUE_RAW_PROVIDER_RESPONSE "
                        f"test-provider-secret {server.base_url} "
                        "Authorization: Bearer header-like-secret must never persist."
                    ),
                    prompt_tokens=19,
                    completion_tokens=5,
                    reasoning_content="hidden reasoning must never persist",
                    response_id="chatcmpl-observe-2",
                ),
            ),
        ]
    )
    configure_provider(monkeypatch, server)
    events: list[tuple[str, dict[str, object]]] = []

    status = await run_provider_agent(provider_spec(), lambda kind, payload: events.append((kind, payload)))

    assert status == 0
    assert [request["path"] for request in server.requests] == [
        "/chat/completions",
        "/chat/completions",
    ]
    assert server.requests[0]["headers"]["Authorization"] == "Bearer test-provider-secret"
    assert all(request["json"]["model"] == "fake-checkout-model" for request in server.requests)
    assert all(
        "reasoning_content" not in message
        for request in server.requests
        for message in request["json"]["messages"]
    )

    process_outputs = [
        payload for kind, payload in events if kind == "process_output"
    ]
    assert len(process_outputs) == 2
    assert all(payload["kind"] == "provider" for payload in process_outputs)
    assert all(
        set(payload) <= {"kind", "stream", "provider", "provider_error"}
        and "content" not in payload
        for payload in process_outputs
    )
    provider_events = [payload["provider"] for kind, payload in events if kind == "process_output" and "provider" in payload]
    assert sum(event["request_count"] for event in provider_events) == 2
    assert sum(event["token_prompt"] for event in provider_events) == 30
    assert sum(event["token_completion"] for event in provider_events) == 12
    assert {event["model"] for event in provider_events} == {"fake-checkout-model"}
    assert [event["request_fingerprint"] for event in provider_events] == [
        fingerprint("chatcmpl-observe-1"),
        fingerprint("chatcmpl-observe-2"),
    ]
    assert [payload["tool_call"]["name"] for kind, payload in events if kind == "step_completed"] == [
        "check_service_health"
    ]

    persisted_event_data = json.dumps(events)
    assert "Provider returned a final response." not in persisted_event_data
    assert "Provider request completed." not in persisted_event_data
    assert "UNIQUE_RAW_PROVIDER_RESPONSE" not in persisted_event_data
    assert "test-provider-secret" not in persisted_event_data
    assert server.base_url not in persisted_event_data
    assert "Authorization: Bearer header-like-secret" not in persisted_event_data
    assert "hidden reasoning" not in persisted_event_data


@pytest.mark.asyncio
async def test_provider_agent_retries_a_retryable_response_with_a_bound(
    monkeypatch: pytest.MonkeyPatch, fake_provider_server
) -> None:
    server = fake_provider_server(
        [
            FakeReply(503, {"error": {"message": "try again"}}),
            FakeReply(200, completion(content="Finished.")),
        ]
    )
    configure_provider(monkeypatch, server, max_retries=1)
    events: list[tuple[str, dict[str, object]]] = []

    status = await run_provider_agent(provider_spec(), lambda kind, payload: events.append((kind, payload)))

    assert status == 0
    assert len(server.requests) == 2
    provider_event = next(payload["provider"] for kind, payload in events if kind == "process_output")
    assert provider_event["request_count"] == 2
    process_output = next(
        payload for kind, payload in events if kind == "process_output"
    )
    assert process_output["kind"] == "provider"
    assert "content" not in process_output


@pytest.mark.asyncio
async def test_provider_agent_reports_timeout_without_endpoint_or_secret(
    monkeypatch: pytest.MonkeyPatch, fake_provider_server
) -> None:
    server = fake_provider_server(
        [FakeReply(200, completion(content="Too late."), delay_seconds=0.3)]
    )
    configure_provider(monkeypatch, server, timeout_ms=100)
    events: list[tuple[str, dict[str, object]]] = []

    status = await run_provider_agent(provider_spec(), lambda kind, payload: events.append((kind, payload)))

    assert status == 1
    error = next(payload["provider_error"] for kind, payload in events if kind == "process_output")
    assert error == {
        "code": "PROVIDER_TIMEOUT",
        "message": "Provider request timed out",
        "retryable": True,
        "attempts": 1,
    }
    process_output = next(
        payload for kind, payload in events if kind == "process_output"
    )
    assert process_output["kind"] == "provider"
    assert "content" not in process_output
    assert set(process_output) == {
        "kind",
        "stream",
        "provider",
        "provider_error",
    }
    persisted_event_data = json.dumps(events)
    assert server.base_url not in persisted_event_data
    assert "test-provider-secret" not in persisted_event_data


@pytest.mark.asyncio
async def test_provider_agent_reports_invalid_response_without_raw_provider_data(
    monkeypatch: pytest.MonkeyPatch, fake_provider_server
) -> None:
    marker = "UNIQUE_INVALID_RESPONSE_MARKER"
    server = fake_provider_server(
        [
            FakeReply(
                200,
                {
                    "choices": [],
                    "raw_content": marker,
                    "endpoint": "https://provider.invalid/v1",
                    "headers": {"Authorization": "Bearer raw-secret"},
                    "prompt": "UNIQUE_RAW_PROVIDER_PROMPT",
                    "stderr": "UNIQUE_RAW_PROVIDER_STDERR",
                    "secret": "UNIQUE_RAW_PROVIDER_SECRET",
                },
                headers={"X-Request-ID": "raw-invalid-request-id"},
            )
        ]
    )
    configure_provider(monkeypatch, server)
    events: list[tuple[str, dict[str, object]]] = []

    status = await run_provider_agent(
        provider_spec(), lambda kind, payload: events.append((kind, payload))
    )

    assert status == 1
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "process_output"
    assert set(payload) == {"kind", "stream", "provider", "provider_error"}
    assert payload["kind"] == "provider"
    assert "content" not in payload
    assert payload["provider_error"] == {
        "code": "PROVIDER_INVALID_RESPONSE",
        "message": "Provider returned an invalid response",
        "retryable": False,
        "attempts": 1,
        "request_fingerprint": fingerprint("raw-invalid-request-id"),
    }
    assert payload["provider"]["request_count"] == 1
    assert set(payload["provider"]) == {
        "model",
        "latency_ms",
        "request_count",
        "token_prompt",
        "token_completion",
        "request_fingerprint",
    }
    assert payload["provider"]["model"] == "fake-checkout-model"
    assert payload["provider"]["token_prompt"] == 0
    assert payload["provider"]["token_completion"] == 0
    assert 0 <= payload["provider"]["latency_ms"] <= MAX_PROVIDER_LATENCY_MS
    assert payload["provider"]["request_fingerprint"] == fingerprint(
        "raw-invalid-request-id"
    )
    serialized = json.dumps(events)
    assert marker not in serialized
    assert "provider.invalid" not in serialized
    assert "raw-secret" not in serialized
    assert "UNIQUE_RAW_PROVIDER_PROMPT" not in serialized
    assert "UNIQUE_RAW_PROVIDER_STDERR" not in serialized
    assert "UNIQUE_RAW_PROVIDER_SECRET" not in serialized
    assert "raw-invalid-request-id" not in serialized


def test_provider_error_producer_normalizes_unknown_codes_and_fingerprints_ids() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    _emit_provider_error(
        lambda kind, payload: events.append((kind, payload)),
        ProviderError(
            "VENDOR_SECRET_FAILURE",
            "UNIQUE_DYNAMIC_ERROR_MESSAGE",
            retryable=False,
            attempts=2,
            request_id="raw-unknown-request-id",
        ),
    )

    assert events == [
        (
            "process_output",
            {
                "kind": "provider",
                "stream": "stderr",
                "provider_error": {
                    "code": "PROVIDER_UNKNOWN",
                    "message": "Provider request failed",
                    "retryable": False,
                    "attempts": 2,
                    "request_fingerprint": fingerprint("raw-unknown-request-id"),
                },
            },
        )
    ]
    serialized = json.dumps(events)
    assert "UNIQUE_DYNAMIC_ERROR_MESSAGE" not in serialized
    assert "raw-unknown-request-id" not in serialized
    assert "content" not in serialized


@pytest.mark.asyncio
async def test_provider_agent_propagates_cancellation_to_the_request(
    monkeypatch: pytest.MonkeyPatch, fake_provider_server
) -> None:
    server = fake_provider_server(
        [FakeReply(200, completion(content="Too late."), delay_seconds=1.0)]
    )
    configure_provider(monkeypatch, server, timeout_ms=5_000)
    events: list[tuple[str, dict[str, object]]] = []

    task = asyncio.create_task(
        run_provider_agent(provider_spec(), lambda kind, payload: events.append((kind, payload)))
    )
    assert await asyncio.to_thread(server.request_received.wait, 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == []


@pytest.mark.asyncio
async def test_provider_agent_rejects_a_tool_outside_the_allowlist_once(
    monkeypatch: pytest.MonkeyPatch, fake_provider_server
) -> None:
    server = fake_provider_server(
        [
            FakeReply(
                200,
                completion(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call-unknown",
                            "type": "function",
                            "function": {
                                "name": "delete_checkout_data",
                                "arguments": "{}",
                            },
                        }
                    ],
                    prompt_tokens=7,
                    completion_tokens=3,
                ),
            )
        ]
    )
    configure_provider(monkeypatch, server)
    events: list[tuple[str, dict[str, object]]] = []

    status = await run_provider_agent(provider_spec(), lambda kind, payload: events.append((kind, payload)))

    assert status == 1
    provider_events = [payload["provider"] for kind, payload in events if "provider" in payload]
    assert len(provider_events) == 1
    assert provider_events[0]["request_count"] == 1
    error = next(payload["provider_error"] for kind, payload in events if "provider_error" in payload)
    assert error["code"] == "PROVIDER_UNSUPPORTED_TOOL"
    process_output = next(
        payload for kind, payload in events if kind == "process_output"
    )
    assert process_output["kind"] == "provider"
    assert "content" not in process_output
