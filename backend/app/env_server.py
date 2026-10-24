"""Per-Run Environment MCP server with bounded, Environment-owned JSONL capture.

The Runner supplies one stdin spec/token, then relays this child's facts to the API.
Only world observations are trusted here; score and Run lifecycle stay in Python's Control Plane.
Read scenario_registry.py for fresh worlds and durable_events.py for the persisted seam.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from typing import BinaryIO, TextIO

import uvicorn
from mcp import types
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

import app.scenarios  # noqa: F401 — register built-in worlds
from app.durable_events import AgentReportedUsage, normalize_event_payload
from app.environment import InvalidToolArgumentsError, UnknownToolError
from app.phase1_schemas import EvaluationSpec
from app.scenario_registry import create_environment

MAX_SPEC_BYTES = 65_536
MAX_REQUEST_BYTES = 16_384
CONTROLS = frozenset({"submit_result", "report_usage"})
Emit = Callable[[str, dict], None]


def validate_token(token: str) -> None:
    """Runner supplies secrets.token_hex(32); never accept a trivial capability."""
    if not 32 <= len(token) <= 512 or any(
        ord(char) < 33 or ord(char) > 126 for char in token
    ):
        raise ValueError("invalid capability")


def read_spec(stream: BinaryIO) -> EvaluationSpec:
    """Consume one bounded JSON line, without waiting for stdin EOF from a Runner."""
    raw = stream.readline(MAX_SPEC_BYTES + 1)
    if not raw or len(raw) > MAX_SPEC_BYTES:
        raise ValueError("invalid spec input")
    return EvaluationSpec.model_validate_json(raw)


def jsonl_emitter(stream: TextIO) -> Emit:
    def emit(event_type: str, payload: dict) -> None:
        stream.write(
            json.dumps({"type": event_type, "payload": payload}, separators=(",", ":"))
            + "\n"
        )
        stream.flush()

    return emit


class EnvironmentSession:
    """Exactly one fresh world; serialize budget admission, mutations, and final check."""

    def __init__(
        self, spec: EvaluationSpec, token: str, emit: Emit, *, clock=time.monotonic
    ) -> None:
        validate_token(token)
        self._clock = clock
        self.deadline = clock() + spec.limits.timeout_ms / 1000
        # create_environment already resets with spec.seed; do not build/cache a second world.
        self.environment = create_environment(spec)
        self.definitions = self.environment.tool_definitions()
        names = [definition["function"]["name"] for definition in self.definitions]
        if len(set(names)) != len(names) or CONTROLS.intersection(names):
            raise ValueError("Environment tool collision")
        self.names = frozenset(names)
        self.max_steps = spec.policy.max_steps if spec.policy else 6
        self.world_attempts = 0
        self._index = 0
        self._finalized = False
        self._token = token
        self._emit = emit
        self._lock = asyncio.Lock()
        self.usage = AgentReportedUsage()

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self._token, "[redacted]")
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    async def dispatch(self, params: Mapping | None) -> types.CallToolResult:
        async with self._lock:
            raw = params if isinstance(params, Mapping) else {}
            name = raw.get("name")
            control = name if isinstance(name, str) and name in CONTROLS else None
            world_index = self.world_attempts
            if control is None:
                self.world_attempts += 1  # Invalid/unknown/refused attempts count too.
            arguments = raw.get("arguments", {})
            result = "ok"
            observation = ""
            check_called = False
            usage = None
            if self._finalized:
                result = (
                    "duplicate_submit" if control == "submit_result" else "finalized"
                )
            elif self._clock() >= self.deadline:
                result = "timed_out"
            elif control is None and world_index >= self.max_steps:
                result = "max_steps"
            elif not isinstance(name, str) or not isinstance(arguments, dict):
                result = "invalid_arguments"
            elif control == "submit_result":
                if arguments:
                    result = "invalid_arguments"
                else:
                    # Freeze even if check raises: neither repeated check nor mutation
                    # may turn a failed submission into a different world outcome.
                    self._finalized = True
                    try:
                        check_called = True
                        self.environment.check()
                        observation = "Environment finalized; Control Plane owns score and Run status."
                    except Exception:
                        result = "internal_error"
            elif control == "report_usage":
                try:
                    self.usage = AgentReportedUsage.model_validate(
                        arguments, strict=True
                    )
                    usage = self.usage.model_dump(mode="json")
                    observation = "Agent-reported cumulative usage recorded."
                except ValidationError:
                    result = "invalid_arguments"
            elif name not in self.names:
                result = "unknown_tool"
            else:
                try:
                    observation = self.environment.call(name, arguments)
                except InvalidToolArgumentsError:
                    result = "invalid_arguments"
                except UnknownToolError:
                    result = "unknown_tool"
                except Exception:
                    result = "internal_error"
                    self._finalized = True
            if result != "ok":
                observation = f"Error: {result}."
            provenance = {
                "kind": "control" if control else "world",
                "result": result,
                "finalized": self._finalized,
                "check_called": check_called,
            }
            if control:
                provenance["control"] = control
            if usage is not None:
                provenance["usage"] = usage
            # Rejected Agent arguments are diagnostic only: omit them before the
            # durable projection so arbitrary invalid input cannot overflow its cap.
            # Successful calls retain the existing allowlisted argument semantics.
            payload = normalize_event_payload(
                "step_completed",
                {
                    "index": self._index,
                    "tool_call": {
                        "name": name,
                        "arguments": arguments if result == "ok" else {},
                    },
                    "observation": observation,
                    "environment": provenance,
                },
            )
            # Redact only the bounded allowlisted projection, not arbitrary nested
            # Agent input (which may contain pathological depth or unknown fields).
            payload = normalize_event_payload("step_completed", self._redact(payload))
            self._index += 1
            self._emit("step_completed", payload)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=self._redact(observation)[:4000]
                    )
                ],
                is_error=result != "ok",
            )

    async def capture(
        self, ctx: ServerRequestContext, call_next: CallNext
    ) -> HandlerResult:
        if ctx.method == "tools/call":
            # SDK 2.3's provisional pre-validation middleware is deliberate: a
            # malformed or unknown call must be admitted/captured exactly once.
            return await self.dispatch(ctx.params)
        return await call_next(ctx)


class EnvironmentMCPServer(MCPServer):
    def __init__(self, session: EnvironmentSession) -> None:
        super().__init__(
            "agentops-environment", log_level="CRITICAL", subscriptions=False
        )
        self._environment_session = session
        self.middleware.insert(0, session.capture)

    async def list_tools(self) -> list[types.Tool]:
        # Controls are authenticated direct calls, not normal model discovery.
        return [
            types.Tool(
                name=item["function"]["name"],
                description=item["function"]["description"],
                input_schema=item["function"]["parameters"],
            )
            for item in self._environment_session.definitions
        ]


class BearerCapability:
    """Private capability check, not an OAuth issuer or discovery service."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        validate_token(token)
        self.app = app
        self._expected = b"Bearer " + token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            authorizations = [
                value
                for key, value in scope["headers"]
                if key.lower() == b"authorization"
            ]
            if len(authorizations) != 1 or not hmac.compare_digest(
                authorizations[0], self._expected
            ):
                return await Response("Unauthorized", status_code=401)(
                    scope, receive, send
                )
            # Do not accept token-in-URL alternatives (or log untrusted URLs).
            if scope.get("query_string"):
                return await Response("Query not allowed", status_code=400)(
                    scope, receive, send
                )
        await self.app(scope, receive, send)


def create_mcp_app(
    session: EnvironmentSession, token: str
) -> tuple[BearerCapability, ASGIApp]:
    server = EnvironmentMCPServer(session)
    http_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host="127.0.0.1",
        max_request_body_size=MAX_REQUEST_BYTES,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1", "127.0.0.1:*"],
            allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*"],
        ),
    )
    return BearerCapability(http_app, token), http_app


class ReadyServer(uvicorn.Server):
    """Publish readiness only after ASGI lifespan and the real listener succeed."""

    def __init__(self, config: uvicorn.Config, emit: Emit) -> None:
        super().__init__(config)
        self._emit = emit

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self.should_exit:
            port = self.servers[0].sockets[0].getsockname()[1]
            self._emit("env_ready", {"mcp_url": f"http://127.0.0.1:{port}/mcp"})


async def serve(spec: EvaluationSpec, token: str, emit: Emit) -> None:
    session = EnvironmentSession(spec, token, emit)
    app, _ = create_mcp_app(session, token)
    server = ReadyServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="critical",
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=1,
            ws="none",
        ),
        emit,
    )

    async def deadline() -> None:
        await asyncio.sleep(max(0, session.deadline - session._clock()))
        server.should_exit = True

    timer = asyncio.create_task(deadline())
    try:
        await server.serve()
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


def main() -> int:
    # Save the sole supervision writer before isolating all library output. SDK
    # diagnostics can contain raw headers; suppress them, not just their token.
    emit = jsonl_emitter(sys.stdout)
    logging.disable(logging.CRITICAL)
    try:
        token = os.environ.get("AGENTOPS_ENV_TOKEN", "")
        validate_token(token)
        spec = read_spec(sys.stdin.buffer)
        with redirect_stdout(sys.stderr):
            asyncio.run(serve(spec, token, emit))
        return 0
    except (Exception, KeyboardInterrupt):
        # Never print validation input, an exception, a URL, or the token.
        print("Environment server stopped or could not start.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
