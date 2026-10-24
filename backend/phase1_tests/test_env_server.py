"""Contract tests for the bounded H1-2 Environment MCP server.

Fake MCP calls and isolated HTTP/processes exercise one Run without a model or DB.
Only Environment-owned observations become facts; Agent scoring/status is ignored.
Read test_env_server_persistence.py for the durable Control Plane reconstruction.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit

import httpx
import pytest

import app.env_server as env_server
from app.durable_events import MAX_CONTEXT_WINDOW_TOKENS, MAX_PROVIDER_TOKENS
from app.env_server import (
    MAX_REQUEST_BYTES,
    MAX_SPEC_BYTES,
    EnvironmentMCPServer,
    EnvironmentSession,
    ReadyServer,
    create_mcp_app,
    jsonl_emitter,
    read_spec,
    validate_token,
)
from app.phase1_schemas import EvaluationSpec, PolicyPatch

TOKEN = "f123456789abcdef" * 4
HEALTH = {"name": "check_service_health", "arguments": {"service": "checkout-api"}}
PROTO = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}


def make_spec(**kwargs) -> EvaluationSpec:
    return EvaluationSpec(
        run_id="run-env",
        experiment_id="experiment-env",
        task="Investigate latency",
        **kwargs,
    )


def make_session(**kwargs):
    output = io.StringIO()
    session = EnvironmentSession(make_spec(**kwargs), TOKEN, jsonl_emitter(output))
    return session, output


def records(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


async def rpc(client, method, params=None, **kwargs):
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {"_meta": PROTO, **(params or {})},
        },
        **kwargs,
    )


@pytest.mark.asyncio
async def test_env_server_one_fresh_seeded_world_and_one_trusted_jsonl_step(
    monkeypatch,
):
    original = env_server.create_environment
    factory = Mock(side_effect=original)
    monkeypatch.setattr(env_server, "create_environment", factory)
    session, output = make_session(seed=123)
    factory.assert_called_once()
    assert factory.call_args.args[0].seed == 123
    session.environment.check = Mock(wraps=session.environment.check)
    result = await session.dispatch(HEALTH)
    assert not result.is_error
    assert records(output) == [
        {
            "type": "step_completed",
            "payload": {
                "index": 0,
                "tool_call": HEALTH,
                "observation": "Service is healthy but checkout latency is elevated.",
                "environment": {
                    "kind": "world",
                    "result": "ok",
                    "finalized": False,
                    "check_called": False,
                },
            },
        }
    ]
    session.environment.check.assert_not_called()
    second, _ = make_session()
    assert session.environment is not second.environment


@pytest.mark.asyncio
async def test_env_server_invalid_unknown_and_over_budget_capture_once_without_mutation():
    session, output = make_session(policy=PolicyPatch(max_steps=3))
    initial = session.environment.check()
    for params in [
        {"name": "check_service_health", "arguments": {"service": TOKEN, "score": 999}},
        {"name": TOKEN, "arguments": {"secret": TOKEN}},
        {"name": "check_service_health", "arguments": []},
    ]:
        assert (await session.dispatch(params)).is_error
    assert session.environment.check() == initial
    assert (await session.dispatch(HEALTH)).is_error
    assert session.environment.check() == initial
    attempts = records(output)
    assert len(attempts) == 4
    assert [item["payload"]["environment"]["result"] for item in attempts] == [
        "invalid_arguments",
        "unknown_tool",
        "invalid_arguments",
        "max_steps",
    ]
    assert session.world_attempts == 4
    assert TOKEN not in output.getvalue()
    assert all(len(json.dumps(item)) < 1000 for item in attempts)
    # Exhaustion cannot prevent final submission, which does not spend a world step.
    session.environment.check = Mock(wraps=session.environment.check)
    assert not (await session.dispatch({"name": "submit_result"})).is_error
    session.environment.check.assert_called_once_with()
    assert session.world_attempts == 4


@pytest.mark.asyncio
async def test_env_server_budget_boundary_concurrency_and_finalization():
    session, output = make_session(policy=PolicyPatch(max_steps=3))
    session.environment.call = Mock(wraps=session.environment.call)
    results = await asyncio.gather(*(session.dispatch(HEALTH) for _ in range(5)))
    assert [result.is_error for result in results] == [False, False, False, True, True]
    assert session.environment.call.call_count == 3
    session.environment.check = Mock(wraps=session.environment.check)
    assert not (
        await session.dispatch({"name": "submit_result", "arguments": {}})
    ).is_error
    for _ in range(2):
        assert (await session.dispatch({"name": "submit_result"})).is_error
    assert (await session.dispatch(HEALTH)).is_error
    assert session.environment.call.call_count == 3
    session.environment.check.assert_called_once_with()
    captured = records(output)
    assert len(captured) == 9
    assert [item["payload"]["index"] for item in captured] == list(range(9))
    assert [item["payload"]["environment"]["result"] for item in captured[-4:]] == [
        "ok",
        "duplicate_submit",
        "duplicate_submit",
        "finalized",
    ]
    assert captured[5]["payload"]["environment"]["check_called"] is True
    assert session.world_attempts == 6


@pytest.mark.asyncio
async def test_env_server_malicious_submit_fields_do_not_score_or_finalize():
    session, output = make_session()
    session.environment.check = Mock(wraps=session.environment.check)
    assert (
        await session.dispatch(
            {
                "name": "submit_result",
                "arguments": {
                    "score": 1,
                    "status": "succeeded",
                    "expected": "anything",
                    "step_completed": HEALTH,
                },
            }
        )
    ).is_error
    session.environment.check.assert_not_called()
    assert not (
        await session.dispatch(
            {**HEALTH, "score": 1, "status": "succeeded", "step_completed": {}}
        )
    ).is_error
    assert not (await session.dispatch({"name": "submit_result"})).is_error
    session.environment.check.assert_called_once_with()
    assert session.world_attempts == 1
    text = output.getvalue()
    for field in ('"score"', '"status"', '"expected"'):
        assert field not in text


@pytest.mark.asyncio
async def test_env_server_failed_check_is_called_once_and_freezes_world():
    session, output = make_session()
    session.environment.check = Mock(side_effect=RuntimeError(TOKEN))
    assert (await session.dispatch({"name": "submit_result"})).is_error
    assert (await session.dispatch({"name": "submit_result"})).is_error
    assert (await session.dispatch(HEALTH)).is_error
    session.environment.check.assert_called_once_with()
    assert records(output)[0]["payload"]["environment"]["check_called"] is True
    assert TOKEN not in output.getvalue()


@pytest.mark.asyncio
async def test_env_server_usage_unknown_cumulative_replacement_and_bounds():
    session, output = make_session()
    assert session.usage.token_prompt is None and session.usage.token_completion is None
    assert session.usage.context_window is None
    for args in [{"token_prompt": 20, "token_completion": 5}, {"token_prompt": 30}, {}]:
        assert not (
            await session.dispatch({"name": "report_usage", "arguments": args})
        ).is_error
        assert session.usage.token_prompt == args.get("token_prompt")
        assert session.usage.token_completion == args.get("token_completion")
    assert session.world_attempts == 0
    args = {
        "token_prompt": MAX_PROVIDER_TOKENS,
        "token_completion": 0,
        "context_window": {
            "used": MAX_CONTEXT_WINDOW_TOKENS,
            "limit": MAX_CONTEXT_WINDOW_TOKENS,
        },
    }
    assert not (
        await session.dispatch({"name": "report_usage", "arguments": args})
    ).is_error
    saved = session.usage
    for invalid in [
        {"token_prompt": -1},
        {"token_prompt": MAX_PROVIDER_TOKENS + 1},
        {"token_prompt": True},
        {"token_prompt": "3"},
        {"token_prompt": 3.0},
        {"context_window": {"used": -1, "limit": 1}},
        {"context_window": {"used": 1, "limit": MAX_CONTEXT_WINDOW_TOKENS + 1}},
        {"context_window": {"used": 1}},
        {"source": "measured"},
        {"score": 1},
        {"token_completion": MAX_PROVIDER_TOKENS + 1},
    ]:
        assert (
            await session.dispatch({"name": "report_usage", "arguments": invalid})
        ).is_error
        assert session.usage == saved
    assert session.world_attempts == 0
    assert len(records(output)) == 15


@pytest.mark.asyncio
async def test_env_server_timeout_refuses_and_captures_without_mutation():
    now = [0.0]
    output = io.StringIO()
    session = EnvironmentSession(
        make_spec(), TOKEN, jsonl_emitter(output), clock=lambda: now[0]
    )
    session.environment.call = Mock(wraps=session.environment.call)
    now[0] = 60.0
    assert (await session.dispatch(HEALTH)).is_error
    assert records(output)[0]["payload"]["environment"]["result"] == "timed_out"
    session.environment.call.assert_not_called()


@pytest.mark.parametrize("collision", ["submit_result", "report_usage", "duplicate"])
def test_env_server_rejects_reserved_and_duplicate_world_tools(collision, monkeypatch):
    world = Mock()
    name = "check_service_health" if collision == "duplicate" else collision
    definitions = [{"function": {"name": name}}]
    world.tool_definitions.return_value = definitions * (
        2 if collision == "duplicate" else 1
    )
    monkeypatch.setattr(env_server, "create_environment", Mock(return_value=world))
    with pytest.raises(ValueError, match="collision"):
        make_session()


@pytest.mark.asyncio
async def test_env_server_http_auth_origin_size_hidden_controls_and_raw_invalid_capture():
    session, output = make_session()
    app, http_app = create_mcp_app(session, TOKEN)
    async with http_app.router.lifespan_context(http_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:12345",
            headers={"Accept": "application/json, text/event-stream"},
        ) as client:
            for authorization in [None, "Bearer wrong", "Basic " + TOKEN]:
                response = await rpc(
                    client,
                    "tools/call",
                    HEALTH,
                    headers=(
                        {}
                        if authorization is None
                        else {"Authorization": authorization}
                    ),
                )
                assert response.status_code == 401
                assert TOKEN not in response.text
            duplicate_auth = await rpc(
                client,
                "tools/call",
                HEALTH,
                headers=[
                    ("Authorization", "Bearer " + TOKEN),
                    ("Authorization", "Bearer " + TOKEN),
                ],
            )
            assert duplicate_auth.status_code == 401
            assert not records(output)
            client.headers["Authorization"] = "Bearer " + TOKEN
            response = await rpc(
                client, "tools/call", HEALTH, headers={"Origin": "https://evil.invalid"}
            )
            assert response.status_code == 403
            response = await rpc(
                client, "tools/call", HEALTH, headers={"Host": "evil.invalid"}
            )
            assert response.status_code == 421
            response = await client.post("/sse")
            assert response.status_code == 404
            assert not records(output)
            response = await client.post("/mcp?token=" + TOKEN, content="{}")
            assert response.status_code == 400
            response = await client.post("/mcp", content="x" * (MAX_REQUEST_BYTES + 1))
            assert response.status_code == 413
            listed = await rpc(client, "tools/list")
            assert listed.status_code == 200, listed.text
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            assert names == session.names
            assert not names.intersection({"submit_result", "report_usage"})
            for params in [
                HEALTH,
                {"name": "check_service_health", "arguments": []},
                {"name": [TOKEN]},
                {"name": "run_shell", "arguments": {"command": TOKEN}},
            ]:
                response = await rpc(client, "tools/call", params)
                assert response.status_code == 200, response.text
                assert TOKEN not in response.text
            assert len(records(output)) == 4
            response = await rpc(client, "tools/call", {"name": "submit_result"})
            assert (
                response.status_code == 200 and not response.json()["result"]["isError"]
            )
            assert len(records(output)) == 5
            assert TOKEN not in output.getvalue()


def test_env_server_stdin_is_one_bounded_spec_line():
    first = make_spec().model_dump_json().encode()
    stream = io.BytesIO(first + b"\n" + b"not another spec\n")
    assert read_spec(stream) == make_spec()
    assert stream.readline() == b"not another spec\n"
    for invalid in [b"", b"{}", b"x" * (MAX_SPEC_BYTES + 1), b"{}\n"]:
        with pytest.raises(ValueError):
            read_spec(io.BytesIO(invalid))


@pytest.mark.asyncio
async def test_env_server_failed_listener_never_emits_ready(monkeypatch):
    output = io.StringIO()

    async def failed_startup(self, sockets=None):
        self.started = False
        self.should_exit = True

    monkeypatch.setattr(env_server.uvicorn.Server, "startup", failed_startup)
    server = ReadyServer(env_server.uvicorn.Config(Mock()), jsonl_emitter(output))
    await server.startup()
    assert not output.getvalue()


def test_env_server_process_ready_is_token_free_and_listener_is_already_usable():
    environment = {"PATH": os.environ["PATH"], "AGENTOPS_ENV_TOKEN": TOKEN}
    process = subprocess.Popen(
        [sys.executable, "-m", "app.env_server"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(make_spec().model_dump_json().encode() + b"\n")
        process.stdin.flush()  # No EOF is needed for readiness.
        assert select.select([process.stdout], [], [], 10)[
            0
        ], "no env_ready within test bound"
        ready_line = process.stdout.readline()
        ready = json.loads(ready_line)
        assert ready["type"] == "env_ready"
        assert set(ready["payload"]) == {"mcp_url"}
        url = urlsplit(ready["payload"]["mcp_url"])
        assert url.scheme == "http" and url.hostname == "127.0.0.1" and url.port > 0
        assert (
            url.path == "/mcp"
            and not url.query
            and not url.fragment
            and not url.username
        )
        with httpx.Client(trust_env=False, timeout=5) as client:
            assert client.post(url.geturl(), json={}).status_code == 401
            response = client.post(
                url.geturl(),
                headers={
                    "Authorization": "Bearer " + TOKEN,
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"_meta": PROTO, **HEALTH},
                },
            )
            assert response.status_code == 200, response.text
        process.terminate()
        remaining, stderr = process.communicate(timeout=5)
        stdout = ready_line + remaining
        lines = [json.loads(line) for line in stdout.splitlines()]
        assert [line["type"] for line in lines] == ["env_ready", "step_completed"]
        assert TOKEN.encode() not in stdout + stderr
        assert stderr == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize(
    "token,stdin", [(None, b"{}\n"), (TOKEN, b"invalid-" + TOKEN.encode() + b"\n")]
)
def test_env_server_startup_failure_is_bounded_and_never_ready(token, stdin):
    environment = {"PATH": os.environ["PATH"]}
    if token:
        environment["AGENTOPS_ENV_TOKEN"] = token
    result = subprocess.run(
        [sys.executable, "-m", "app.env_server"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        input=stdin,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert TOKEN.encode() not in result.stderr


@pytest.mark.parametrize("length", [32, 64, 512])
def test_env_server_capability_accepts_bounded_printable_tokens(length):
    token = ("f123456789abcdef" * 32)[:length]
    assert validate_token(token) is None
    EnvironmentSession(make_spec(), token, Mock())


@pytest.mark.parametrize(
    "token",
    [
        "",
        "a" * 31,
        "a" * 513,
        "a" * 31 + " ",
        "a" * 31 + "\n",
        "a" * 31 + "\x00",
        "a" * 31 + "\x7f",
        "a" * 31 + "é",
    ],
)
def test_env_server_capability_rejects_invalid_values_before_world_creation(
    token, monkeypatch
):
    factory = Mock()
    monkeypatch.setattr(env_server, "create_environment", factory)
    with pytest.raises(ValueError, match="invalid capability"):
        EnvironmentSession(make_spec(), token, Mock())
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_env_server_sdk_client_tools_and_direct_controls(mode):
    from mcp.client import Client

    session, output = make_session()
    async with Client(EnvironmentMCPServer(session), mode=mode) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == session.names
        assert not (
            await client.call_tool(HEALTH["name"], HEALTH["arguments"])
        ).is_error
        assert not (await client.call_tool("report_usage", {})).is_error
        assert not (await client.call_tool("submit_result", {})).is_error
    assert len(records(output)) == 3
