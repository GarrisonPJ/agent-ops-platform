from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.phase1_provider import run_provider_agent
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
async def test_provider_agent_uses_local_compatible_server_and_redacts_secrets(
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
                    content=f"The test-provider-secret and {server.base_url} must never persist.",
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

    provider_events = [payload["provider"] for kind, payload in events if kind == "process_output" and "provider" in payload]
    assert sum(event["request_count"] for event in provider_events) == 2
    assert sum(event["token_prompt"] for event in provider_events) == 30
    assert sum(event["token_completion"] for event in provider_events) == 12
    assert {event["model"] for event in provider_events} == {"fake-checkout-model"}
    assert [event["request_id"] for event in provider_events] == [
        "chatcmpl-observe-1",
        "chatcmpl-observe-2",
    ]
    assert [payload["tool_call"]["name"] for kind, payload in events if kind == "step_completed"] == [
        "check_service_health"
    ]

    persisted_event_data = json.dumps(events)
    assert "test-provider-secret" not in persisted_event_data
    assert server.base_url not in persisted_event_data
    assert "hidden reasoning" not in persisted_event_data
    assert "[redacted]" in persisted_event_data


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
    persisted_event_data = json.dumps(events)
    assert server.base_url not in persisted_event_data
    assert "test-provider-secret" not in persisted_event_data


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
