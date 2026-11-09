"""Focused tests for the independent H1 Agent transport and input boundary.

These tests exercise the Agent-owned validation and a real local MCP roundtrip,
without a model, database, control plane, or external network.
Read ``app/mcp_agent_client`` for the implementation under test.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from mcp import types

from app import fixture_agent, mcp_agent_client, reference_agent
from app.fixture_agent import AgentInputError, AgentSpec, read_spec
from app.mcp_agent_client import (
    MCPAgentClient,
    MCPAgentError,
    MCPToolResult,
    validate_endpoint,
    validate_token,
)
from app.phase1_provider import ProviderError, ProviderResponse, ProviderToolCall, ProviderUsage


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 64
SUBMIT_SCHEMA = mcp_agent_client.CONTROL_TOOLS["submit_result"]["function"]["parameters"]


def test_loopback_endpoint_and_token_fail_closed() -> None:
    assert validate_endpoint("http://127.0.0.1:1234/mcp")
    assert validate_token(TOKEN) == TOKEN
    for endpoint in ("https://127.0.0.1:1234/mcp", "http://localhost:1234/mcp", "http://127.0.0.1:0/mcp", "http://127.0.0.1:1234/other"):
        with pytest.raises(MCPAgentError):
            validate_endpoint(endpoint)
    with pytest.raises(MCPAgentError):
        validate_token("secret")


def test_spec_requires_runner_task_agreement(monkeypatch, capsys) -> None:
    payload = {
        "schema_version": 1,
        "run_id": "run-1",
        "experiment_id": "experiment-1",
        "scenario_id": "checkout-api-latency.v2",
        "task": "investigate latency",
        "execution_mode": "fixture",
    }
    monkeypatch.setenv("AGENTOPS_TASK_PROMPT", "different task")
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": __import__("io").BytesIO((json.dumps(payload) + "\n").encode())})())
    with pytest.raises(AgentInputError, match="TASK_MISMATCH"):
        read_spec()
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_client_real_local_mcp_roundtrip(monkeypatch) -> None:
    # A recording local proxy must see no capability, even without NO_PROXY.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    requests = []
    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(dict(self.headers))
            self.send_error(502)
        def log_message(self, *args): pass
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, f"http://127.0.0.1:{proxy.server_port}")
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    spec = {
        "schema_version": 1, "run_id": "run-1", "experiment_id": "experiment-1",
        "scenario_id": "checkout-api-latency.v2", "task": "investigate latency",
        "execution_mode": "fixture", "limits": {"timeout_ms": 20_000},
    }
    environment = None
    try:
        environment = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.env_server", cwd=ROOT,
            env={**os.environ, "AGENTOPS_ENV_TOKEN": TOKEN},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert environment.stdin is not None and environment.stdout is not None
        environment.stdin.write((json.dumps(spec) + "\n").encode())
        await environment.stdin.drain()
        ready = json.loads(await asyncio.wait_for(environment.stdout.readline(), timeout=5))
        endpoint = ready["payload"]["mcp_url"]
        with pytest.raises(MCPAgentError, match="MCP_CONNECTION_FAILED"):
            async with MCPAgentClient(endpoint, "b" * 64, timeout_seconds=1):
                pytest.fail("wrong capability authenticated")
        async with MCPAgentClient(endpoint, TOKEN) as client:
            assert "fetch_service_logs" in client.world_tool_names
            observation = await client.call_tool("fetch_service_logs", {"service": "checkout-api", "window": "5m"})
            assert observation.text and not observation.is_error
            assert TOKEN not in observation.text
        assert requests == []
    finally:
        if environment is not None:
            if environment.returncode is None:
                environment.terminate()
            try:
                await asyncio.wait_for(environment.communicate(), timeout=5)
            except asyncio.TimeoutError:
                environment.kill()
                await environment.communicate()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_sdk_error_flag_regression() -> None:
    from app.mcp_agent_client import CONTROL_TOOLS

    class Session:
        async def call_tool(self, name, arguments):
            return types.CallToolResult(content=[], is_error=True)

    client = MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN)
    client._session = Session()
    client._tools = list(CONTROL_TOOLS.values())
    assert (await client.call_tool("submit_result", {})).is_error


def test_strict_spec_regressions(monkeypatch) -> None:
    payload = {"run_id": "run-1", "experiment_id": "experiment-1", "task": "t",
               "scenario_id": "checkout-api-latency.v2"}
    for replacement in ({"schema_version": True}, {"limits": {"unexpected": 1}},
                        {"agent_id": []}, {"limits": []}):
        monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(json.dumps(payload | replacement).encode())})())
        with pytest.raises(AgentInputError):
            read_spec()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_steps", [3, 6, 20])
async def test_reference_reserves_submit_regression(monkeypatch, max_steps) -> None:

    calls = []
    class Environment:
        world_tool_names = {"world"}
        tools = []
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def call_tool(self, name, args):
            calls.append(name)
            return MCPToolResult("literal observation", False)

    class Provider:
        count = 0
        def __init__(self, *args): pass
        async def chat(self, *args):
            self.count += 1
            name = "world" if self.count <= max_steps else "submit_result"
            return SimpleNamespace(content=None, usage=ProviderUsage(1, 2), tool_calls=[ProviderToolCall("call", name, {}, {})])

    monkeypatch.setattr(reference_agent, "MCPAgentClient", Environment)
    monkeypatch.setattr(reference_agent, "OpenAICompatibleProvider", Provider)
    monkeypatch.setattr(reference_agent.ProviderSettings, "from_env", lambda: SimpleNamespace(for_run=lambda run_id: None))
    spec = AgentSpec("t", "checkout-api-latency.v2", max_steps, 1000, False, {})
    assert await reference_agent.run(spec) == 0
    assert calls.count("world") == max_steps
    assert calls[-1] == "submit_result"


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(exits=[], entered=[], kwargs={}, block=None, fail=None,
                            cursor=None, error=False, names=["world", "submit_result"], called=[], schemas={})

    class HTTP:
        def __init__(self, **kwargs): state.kwargs = kwargs
        async def __aenter__(self):
            state.entered.append("http")
            return self
        async def __aexit__(self, *args): state.exits.append("http")

    @asynccontextmanager
    async def streams(*args, **kwargs):
        state.entered.append("streams")
        try:
            yield (None, None)
        finally:
            state.exits.append("streams")

    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self):
            state.entered.append("session")
            return self
        async def __aexit__(self, *args): state.exits.append("session")
        async def stage(self, name):
            if state.block == name:
                state.reached.set()
                await asyncio.Event().wait()
            if state.fail == name:
                logging.getLogger("mcp.client.streamable_http").exception("RAW_SECRET " + TOKEN)
                logging.getLogger("httpx2").warning("RAW_SECRET endpoint")
                raise RuntimeError("RAW_SECRET " + TOKEN)
        async def initialize(self): await self.stage("initialize")
        async def list_tools(self):
            await self.stage("discovery")
            return types.ListToolsResult(tools=[types.Tool(name=n, input_schema=state.schemas.get(n, SUBMIT_SCHEMA if n == "submit_result" else {"type": "object"})) for n in state.names], next_cursor=state.cursor)
        async def call_tool(self, name, arguments):
            await self.stage("call")
            state.called.append((name, arguments))
            return types.CallToolResult(content=[types.TextContent(type="text", text="literal " + TOKEN)], is_error=state.error)

    monkeypatch.setattr(mcp_agent_client.httpx2, "AsyncClient", HTTP)
    monkeypatch.setattr(mcp_agent_client, "streamable_http_client", streams)
    monkeypatch.setattr(mcp_agent_client, "ClientSession", Session)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("name", ["world", "submit_result", "report_usage"])
async def test_transport_sdk_results_and_proxy_disabled(transport, is_error, name):
    transport.error = is_error
    async with MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN) as client:
        result = await client.call_tool(name, {})
        assert result.is_error is is_error
        assert result.text == "literal [redacted]"
        assert client.world_tool_names == {"world"}
    assert transport.kwargs["trust_env"] is False
    assert transport.kwargs["headers"] == {"Authorization": "Bearer " + TOKEN}
    assert transport.exits == ["session", "streams", "http"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initialize", "discovery"])
async def test_client_entry_cancellation_exits_all_resources_once(transport, stage):
    transport.block = stage
    transport.reached = asyncio.Event()
    client = MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN)
    task = asyncio.create_task(client.__aenter__())
    await asyncio.wait_for(transport.reached.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.__aexit__(None, None, None)
    assert transport.exits == ["session", "streams", "http"]
    assert client._stack is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initialize", "discovery", "call"])
async def test_sdk_raw_diagnostics_never_reach_drains(transport, stage, capsys, caplog):
    transport.fail = stage
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(MCPAgentError) as error:
            async with MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN) as client:
                await client.call_tool("world", {})
    assert str(error.value) in {"MCP_CONNECTION_FAILED", "MCP_CALL_FAILED"}
    assert "RAW_SECRET" not in caplog.text + str(capsys.readouterr())
    assert TOKEN not in caplog.text + str(error.value)
    assert transport.exits == ["session", "streams", "http"]


@pytest.mark.asyncio
@pytest.mark.parametrize("names,cursor", [(["world", "submit_result"], "page2"),
                                            (["world", "submit_result"], ""),
                                            (["world", "world", "submit_result"], None),
                                            (["world"], None),
                                            (["world", "submit_result", "submit_result"], None),
                                            (["world", "submit_result", "report_usage"], None),
                                            ([f"tool{i}" for i in range(65)] + ["submit_result"], None)])
async def test_discovery_rejects_partial_duplicate_reserved_excess_tools(transport, names, cursor):
    transport.names, transport.cursor = names, cursor
    with pytest.raises(MCPAgentError, match="MCP_DISCOVERY_INVALID"):
        async with MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN):
            pytest.fail("invalid discovery entered")
    assert transport.exits == ["session", "streams", "http"]


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [{"type": "object"},
                                    {"type": "object", "properties": {"answer": {"type": "string"}}}])
async def test_discovery_rejects_submit_result_with_arguments(transport, schema):
    transport.schemas = {"submit_result": schema}
    with pytest.raises(MCPAgentError, match="MCP_DISCOVERY_INVALID"):
        async with MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN):
            pytest.fail("submit_result with arguments entered")


@pytest.mark.asyncio
async def test_discovered_submit_keeps_client_controls_and_world_names(transport):
    async with MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN) as client:
        names = [tool["function"]["name"] for tool in client.tools]
        assert names == ["world", "submit_result", "report_usage"]
        assert client.tools[1] == mcp_agent_client.CONTROL_TOOLS["submit_result"]
        assert client.world_tool_names == {"world"}


BASE_INPUT = {"run_id": "run-1", "experiment_id": "experiment-1", "task": "t",
              "scenario_id": "checkout-api-latency.v2"}
BAD_FIELDS = [
    {"schema_version": True}, {"schema_version": 1.0}, {"schema_version": 2},
    {"run_id": 1}, {"experiment_id": []}, {"task": ""}, {"task": "t" * 4001},
    {"extra": 1}, {"scenario_id": " checkout-api-latency.v2"},
    {"agent_id": []}, {"agent_id": "not_an_id"}, {"agent_id": "a" * 65 + ".v1"},
    {"execution_mode": {}}, {"execution_mode": []}, {"execution_mode": False},
    {"seed": True}, {"seed": 1.0}, {"seed": -1}, {"seed": 2147483648},
    *[{"limits": item} for item in ([], False, 0, None, {"unexpected": 1},
       {"timeout_ms": True}, {"timeout_ms": 999}, {"timeout_ms": 300001},
       {"max_output_bytes": -1}, {"max_output_bytes": 1023},
       {"max_output_bytes": 10485761}, {"max_output_bytes": "1024"})],
    *[{"scenario_params": item} for item in ([], False, 0, None, {"bad_key": "v"},
       {"ok": ""}, {"ok": 1}, {"ok": "v" * 201}, {str(i): "v" for i in range(21)})],
    *[{"policy": item} for item in ([], False, {}, {"max_steps": True},
       {"max_steps": 2}, {"max_steps": 21}, {"max_steps": 6, "extra": 1},
       {"max_steps": 6, "instruction_patch": [""]},
       {"max_steps": 6, "instruction_patch": ["v"] * 11},
       {"max_steps": 6, "instruction_patch": ["v" * 501]},
       {"max_steps": 6, "tool_priority": {"unknown": 1}},
       {"max_steps": 6, "tool_priority": {"invoke_service": True}},
       {"max_steps": 6, "tool_priority": {"invoke_service": 1.1}})],
]
BAD_BYTES = [b'{"run_id":"\\ud800"}', b'{"run_id": 1e309}', b"\xff", b"{", b"[]", b"null", b"0", b"", b" " * 65537,
             b'{"run_id":"a","run_id":"b"}', b"[" * 1000 + b"]" * 1000,
             json.dumps(BASE_INPUT | {"agent_id": float("nan")}).encode(),
             json.dumps(BASE_INPUT | {"limits": {"max_output_bytes": float("inf")}}).encode(),
             json.dumps(BASE_INPUT | {"policy": {"max_steps": 6, "tool_priority": {"invoke_service": 1e309}}}).encode()]


@pytest.mark.parametrize("replacement", BAD_FIELDS)
def test_read_spec_rejects_exact_nested_invalid_fields(monkeypatch, replacement):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(BASE_INPUT | replacement).encode())))
    with pytest.raises(AgentInputError, match="INVALID_SPEC"):
        read_spec()


@pytest.mark.parametrize("raw", BAD_BYTES)
def test_read_spec_rejects_malformed_json_utf8_depth_size(monkeypatch, raw):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    with pytest.raises(AgentInputError, match="INVALID_SPEC"):
        read_spec()


@pytest.mark.parametrize("module", ["fixture_agent", "reference_agent", "pi_agent"])
@pytest.mark.parametrize("raw", [b"\xff", b"[" * 1000 + b"]" * 1000,
                                    json.dumps(BASE_INPUT | {"execution_mode": {}}).encode(),
                                    b'{"task": NaN}', b"x" * 65537])
def test_agent_subprocess_stdin_errors_are_fixed_without_traceback(module, raw):
    result = subprocess.run([sys.executable, "-m", "app." + module], input=raw,
                            capture_output=True, cwd=ROOT, timeout=5,
                            env={k: v for k, v in os.environ.items() if not k.startswith("AGENTOPS_")})
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == b"INVALID_SPEC\n"


def test_read_spec_strict_valid_bounds_and_exact_byte_limit(monkeypatch):
    payload = BASE_INPUT | {"schema_version": 1, "agent_id": "fixture.v1", "seed": 2147483647,
                           "limits": {"timeout_ms": 300000, "max_output_bytes": 10485760},
                           "policy": {"max_steps": 20, "instruction_patch": ["hello"],
                                      "tool_priority": {"invoke_service": 1}},
                           "scenario_params": {"service": "inventory-api"}}
    raw = json.dumps(payload).encode()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw + b" " * (65536 - len(raw)))))
    parsed = read_spec()
    assert parsed.max_steps == 20 and parsed.timeout_ms == 300000
    assert parsed.instruction_patch == ("hello",)
    assert parsed.scenario_params == {"service": "inventory-api"}


class FakeEnvironment:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.world_tool_names = frozenset({"world"})
        self.tools = [{"type": "function", "function": {"name": "world", "parameters": {"type": "object"}}}]
        self.errors = set()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def call_tool(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        return MCPToolResult("ACTUAL literal observation; not a world stub", name in self.errors)


@pytest.fixture
def agent_boundaries(monkeypatch):
    environment = FakeEnvironment()
    state = SimpleNamespace(replies=[], requests=[], environment=environment)
    class Provider:
        def __init__(self, *args): pass
        async def chat(self, messages, tools):
            state.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
            reply = state.replies.pop(0)
            if isinstance(reply, Exception): raise reply
            return reply
    monkeypatch.setattr(reference_agent, "MCPAgentClient", lambda *a, **kw: environment)
    monkeypatch.setattr(reference_agent, "OpenAICompatibleProvider", Provider)
    monkeypatch.setattr(reference_agent.ProviderSettings, "from_env", lambda: SimpleNamespace(for_run=lambda run_id: None))
    monkeypatch.setenv("AGENTOPS_ENV_TOKEN", TOKEN)
    return state


def reply(*names, usage=None, arguments=None, content=None):
    calls = [ProviderToolCall("id-" + str(i), name, arguments or {},
              {"id": "id-" + str(i), "type": "function", "function": {
                  "name": name, "arguments": json.dumps(arguments or {})}}) for i, name in enumerate(names)]
    return ProviderResponse(content, calls, usage if usage is not None else ProviderUsage(2, 3), 1, 1, None)


@pytest.mark.asyncio
async def test_reference_literal_observations_policy_usage_explicit_submit(agent_boundaries):
    state = agent_boundaries
    state.environment.errors.add("world")  # Tool errors are real observations, not scoring.
    state.replies = [reply("world", arguments={"literal": 17}), reply("submit_result")]
    spec = AgentSpec("t", "checkout-api-latency.v2", 3, 1000, True, {}, ("policy literal",))
    assert await reference_agent.run(spec) == 0
    assert state.environment.calls == [("report_usage", {"token_prompt": 2, "token_completion": 3}),
                                       ("world", {"literal": 17}),
                                       ("report_usage", {"token_prompt": 4, "token_completion": 6}),
                                       ("submit_result", {})]
    assert state.requests[1][0][-1]["content"] == "ACTUAL literal observation; not a world stub"
    assert "policy literal" in state.requests[0][0][0]["content"]
    assert all(request[1] == state.environment.tools for request in state.requests)
    assert TOKEN not in json.dumps(state.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("usages,expected", [
    ([ProviderUsage(0, 0), ProviderUsage(3, 4)], [(0, 0), (3, 4)]),
    ([ProviderUsage(2, 3), ProviderUsage(0, 4, False, True), ProviderUsage(7, 8)],
     [(2, 3), (None, 7), (None, 15)]),
    ([ProviderUsage(0, 0, False, False), ProviderUsage(2, 3)], [(None, None), (None, None)]),
    ([ProviderUsage(600000000, 4), ProviderUsage(600000000, 5), ProviderUsage(1, 6)],
     [(600000000, 4), (None, 9), (None, 15)]),
    ([ProviderUsage(2, 600000000), ProviderUsage(3, 600000000)], [(2, 600000000), (5, None)]),
])
async def test_reference_usage_unknown_is_sticky_per_dimension(agent_boundaries, usages, expected):
    state = agent_boundaries
    state.replies = [reply("world", usage=u) for u in usages[:-1]] + [reply("submit_result", usage=usages[-1])]
    assert await reference_agent.run(AgentSpec("t", "checkout-api-latency.v2", 6, 1000, False, {})) == 0
    reports = [args for name, args in state.environment.calls if name == "report_usage"]
    assert [(r["token_prompt"], r["token_completion"]) for r in reports] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code", [
    (reply(content="completed naturally"), "PROVIDER_SUBMIT_REQUIRED"),
    (reply("world", "submit_result"), "PROVIDER_SUBMIT_REQUIRED"),
    (reply("unknown"), "PROVIDER_UNSUPPORTED_TOOL"),
    (reply("report_usage"), "PROVIDER_UNSUPPORTED_TOOL"),
    (reply("submit_result", arguments={"score": 1}), "PROVIDER_SUBMIT_INVALID"),
    (ProviderError("PROVIDER_HTTP_ERROR", "RAW_SECRET", retryable=False, attempts=1), "PROVIDER_HTTP_ERROR"),
])
async def test_reference_rejects_nonexplicit_multiple_unknown_provider_errors(agent_boundaries, response, code, capsys):
    agent_boundaries.replies = [response]
    assert await reference_agent.run(AgentSpec("t", "checkout-api-latency.v2", 3, 1000, False, {})) == 1
    assert not any(name in {"world", "submit_result"} for name, _ in agent_boundaries.environment.calls)
    assert capsys.readouterr().err == code + "\n"


@pytest.mark.asyncio
async def test_reference_world_budget_does_not_allow_extra_world_call(agent_boundaries, capsys):
    agent_boundaries.replies = [reply("world")] * 4
    assert await reference_agent.run(AgentSpec("t", "checkout-api-latency.v2", 3, 1000, False, {})) == 1
    assert len(agent_boundaries.requests) == 4
    assert [n for n, _ in agent_boundaries.environment.calls].count("world") == 3
    assert "PROVIDER_STEP_LIMIT" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["submit_result", "report_usage"])
async def test_reference_control_errors_are_failures(agent_boundaries, control):
    agent_boundaries.environment.errors.add(control)
    agent_boundaries.replies = [reply("submit_result")]
    assert await reference_agent.run(AgentSpec("t", "checkout-api-latency.v2", 3, 1000, False, {})) == 1


FIXTURE_PLANS = {
    "checkout-api-latency.v2": (
        [("fetch_service_logs", {"service": "checkout-api", "window": "5m"})] * 6,
        [("check_service_health", {"service": "checkout-api"}),
         ("query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
         ("fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"})]),
    "multi-step-research.v2": (
        [("search_documents", {"query": "custom topic"})] * 6,
        [("search_documents", {"query": "custom topic"}),
         ("fetch_document", {"document_id": "doc-dependency-latency"}),
         ("submit_answer", {"answer": "Checkout latency is caused by payment-gateway connection pool saturation."})]),
    "api-fault-orchestration.v2": (
        [("invoke_service", {"service": "inventory-api"})] * 6,
        [("retry_call", {"service": "inventory-api", "attempts": 2}),
         ("degrade_route", {"service": "inventory-api", "mode": "cached-read"}),
         ("invoke_service", {"service": "inventory-api"})]),
    "order-support.v1": (
        [("inspect_order", {"order_id": "order-1001"})] * 6,
        [("inspect_order", {"order_id": "order-1001"}),
         ("authorize_payment", {"order_id": "order-1001"}),
         ("ship_order", {"order_id": "order-1001"}),
         ("request_refund", {"order_id": "order-1001", "reason": "shipping_failed"}),
         ("request_refund", {"order_id": "order-1001", "reason": "shipping_failed"}),
         ("inspect_order", {"order_id": "order-1001"})]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", FIXTURE_PLANS)
@pytest.mark.parametrize("policy", [False, True])
@pytest.mark.parametrize("max_steps", [3, 6])
async def test_fixture_all_plans_submit_without_local_scoring(monkeypatch, scenario, policy, max_steps):
    environment = FakeEnvironment()
    plan = FIXTURE_PLANS[scenario][int(policy)]
    environment.world_tool_names = frozenset(name for name, _ in plan)
    environment.errors = set(environment.world_tool_names)  # Refused world facts do not infer score.
    monkeypatch.setattr(fixture_agent, "MCPAgentClient", lambda *a, **kw: environment)
    spec = AgentSpec("t", scenario, max_steps, 1000, policy, {"service": "inventory-api", "topic": "custom topic"})
    assert await fixture_agent.run(spec) == 0
    assert environment.calls == plan[:max_steps] + [("submit_result", {})]


@pytest.mark.parametrize("policy", [False, True])
def test_fixture_fault_default_service(policy):
    spec = AgentSpec("t", "api-fault-orchestration.v2", 6, 1000, policy, {})
    assert all(args["service"] == "payments-api" for _, args in fixture_agent._plans(spec))


@pytest.mark.asyncio
async def test_fixture_submission_error_and_missing_tool_fail(monkeypatch):
    environment = FakeEnvironment()
    monkeypatch.setattr(fixture_agent, "MCPAgentClient", lambda *a, **kw: environment)
    spec = AgentSpec("t", "checkout-api-latency.v2", 3, 1000, False, {})
    assert await fixture_agent.run(spec) == 1
    assert environment.calls == []
    environment.world_tool_names = {"fetch_service_logs"}
    environment.errors.add("submit_result")
    assert await fixture_agent.run(spec) == 1
    assert environment.calls[-1] == ("submit_result", {})


def test_agent_sources_do_not_import_world_or_scoring_modules():
    import ast
    for name in ("fixture_agent", "reference_agent", "mcp_agent_client"):
        tree = ast.parse((ROOT / "app" / (name + ".py")).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not set(imports) & {"app.phase1_schemas", "app.scenario_registry", "app.environment",
                                   "app.scenarios", "app.agent_registry", "app.phase1_scoring"}


@pytest.mark.asyncio
async def test_real_sdk_malformed_http_diagnostics_are_secret_free(capsys, caplog):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Malformed(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/RAW_SECRET_" + TOKEN)
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Malformed)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(MCPAgentError, match="MCP_CONNECTION_FAILED"):
                async with MCPAgentClient(f"http://127.0.0.1:{server.server_port}/mcp", TOKEN, timeout_seconds=1):
                    pytest.fail("malformed endpoint entered")
        assert "RAW_SECRET" not in caplog.text + str(capsys.readouterr())
        assert TOKEN not in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_reference_lowlevel_fake_http_usage_parser_is_used(monkeypatch, agent_boundaries):
    import httpx
    from app.phase1_provider import OpenAICompatibleProvider, ProviderSettings
    settings = ProviderSettings("http://provider.invalid", "PRIVATE_PROVIDER_KEY", "fake", 1000, 0)
    replies = [
        {"choices": [{"message": {"tool_calls": [reply("world").tool_calls[0].raw]}}],
         "usage": {"prompt_tokens": 2, "completion_tokens": 3}},
        {"choices": [{"message": {"tool_calls": [reply("world").tool_calls[0].raw]}}],
         "usage": {"completion_tokens": 4}},
        {"choices": [{"message": {"tool_calls": [reply("submit_result").tool_calls[0].raw]}}],
         "usage": {"prompt_tokens": 9, "completion_tokens": 5}},
    ]
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider(settings, client=client)
        monkeypatch.setattr(reference_agent, "OpenAICompatibleProvider", lambda *a: provider)
        assert await reference_agent.run(AgentSpec("t", "checkout-api-latency.v2", 3, 1000, False, {})) == 0
    reports = [args for name, args in agent_boundaries.environment.calls if name == "report_usage"]
    assert reports == [{"token_prompt": 2, "token_completion": 3},
                       {"token_prompt": None, "token_completion": 7},
                       {"token_prompt": None, "token_completion": 12}]
    assert requests[1]["messages"][-1]["content"] == "ACTUAL literal observation; not a world stub"
    assert TOKEN not in json.dumps(requests)


@pytest.mark.asyncio
async def test_reference_explicit_submit_without_world_work(agent_boundaries):
    agent_boundaries.replies = [reply("submit_result")]
    assert await reference_agent.run(AgentSpec("t", "checkout-api-latency.v2", 3, 1000, False, {})) == 0
    assert [name for name, _ in agent_boundaries.environment.calls] == ["report_usage", "submit_result"]


@pytest.mark.asyncio
async def test_successful_client_entry_cancellation_exits_all_resources_once(transport):
    reached = asyncio.Event()
    async def work():
        async with MCPAgentClient("http://127.0.0.1:1234/mcp", TOKEN):
            reached.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(work())
    await asyncio.wait_for(reached.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.exits == ["session", "streams", "http"]


@pytest.mark.parametrize("scenario", [
    "checkout-api-latency.v1", "multi-step-research.v1",
    "api-fault-orchestration.v1", "unknown.v2",
])
def test_spec_unsupported_scenarios_fail_closed(monkeypatch, scenario):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(BASE_INPUT | {"scenario_id": scenario}).encode())))
    with pytest.raises(AgentInputError, match="UNSUPPORTED_SCENARIO"):
        read_spec()


def test_usage_control_schema_matches_nullable_bounded_contract():
    schema = mcp_agent_client.CONTROL_TOOLS["report_usage"]["function"]["parameters"]
    assert "required" not in schema
    for name in ("token_prompt", "token_completion"):
        assert schema["properties"][name] == {"type": ["integer", "null"], "minimum": 0, "maximum": 1000000000}
    context = schema["properties"]["context_window"]["anyOf"][0]
    assert context["properties"]["used"]["maximum"] == 10000000
    assert context["properties"]["limit"]["minimum"] == 0
