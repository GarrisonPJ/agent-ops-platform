"""Bounded MCP client used by the independent H1 agents.

The Runner starts the Agent, and this client calls only the Runner-provided
loopback Environment MCP endpoint.  Capability tokens and discovered facts
must never cross into model messages or trusted Agent stdout.

Read ``app.fixture_agent`` next for the deterministic caller and
``app.reference_agent`` for the provider-backed caller.
"""

from __future__ import annotations

import logging
import re
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client


MAX_OBSERVATION_CHARS = 4_000
MAX_TOOLS = 64
TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_TOOLS = {
    "submit_result": {
        "type": "function",
        "function": {
            "name": "submit_result",
            "description": "Submit the completed task to the Environment.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    "report_usage": {
        "type": "function",
        "function": {
            "name": "report_usage",
            "description": "Report cumulative provider usage to the Environment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "const": "agent_reported"},
                    "token_prompt": {"type": ["integer", "null"], "minimum": 0, "maximum": 1_000_000_000},
                    "token_completion": {"type": ["integer", "null"], "minimum": 0, "maximum": 1_000_000_000},
                    "context_window": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "used": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
                                    "limit": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
                                },
                                "required": ["used", "limit"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "additionalProperties": False,
            },
        },
    },
}


@contextmanager
def _quiet_protocol_diagnostics() -> Iterator[None]:
    """SDK logs may contain capabilities, bodies and URLs, unlike our codes.

    These are dedicated Agent processes with one transport; restore the host's
    logging configuration after all transport tasks and contexts have exited.
    """
    families = ("mcp", "httpx", "httpx2", "httpcore", "httpcore2")
    names = set(families) | {
        name for name in logging.Logger.manager.loggerDict
        if name.startswith(tuple(family + "." for family in families))
    }
    previous = []
    for name in names:
        logger = logging.getLogger(name)
        previous.append((logger, logger.handlers[:], logger.propagate))
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
    try:
        yield
    finally:
        for logger, handlers, propagate in previous:
            logger.handlers = handlers
            logger.propagate = propagate


class MCPAgentError(RuntimeError):
    """A fixed, safe error category for process diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_endpoint(url: str) -> str:
    """Accept only an explicit loopback Streamable HTTP MCP endpoint."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise MCPAgentError("MCP_ENDPOINT_INVALID") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65_535
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MCPAgentError("MCP_ENDPOINT_INVALID")
    return url


def validate_token(token: str) -> str:
    """Validate the Runner's per-Run capability without exposing it."""
    if not TOKEN_PATTERN.fullmatch(token):
        raise MCPAgentError("MCP_TOKEN_INVALID")
    return token


def _tool_definition(tool: types.Tool) -> dict[str, object]:
    name = tool.name if isinstance(tool.name, str) else ""
    description = tool.description if isinstance(tool.description, str) else ""
    raw_schema = getattr(tool, "inputSchema", None)
    if raw_schema is None:
        raw_schema = getattr(tool, "input_schema", None)
    schema = raw_schema if isinstance(raw_schema, dict) else {}
    if not name or len(name) > 128:
        raise MCPAgentError("MCP_DISCOVERY_INVALID")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description[:1_000],
            "parameters": schema,
        },
    }


@dataclass(frozen=True)
class MCPToolResult:
    """The bounded, non-secret projection returned to an Agent loop."""

    text: str
    is_error: bool


class MCPAgentClient:
    """Authenticated MCP 2.3 Streamable HTTP client with discovery first."""

    def __init__(self, endpoint: str, token: str, *, timeout_seconds: float = 10.0) -> None:
        self.endpoint = validate_endpoint(endpoint)
        self.token = validate_token(token)
        self.timeout_seconds = max(0.1, min(timeout_seconds, 300.0))
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: list[dict[str, object]] = []

    async def __aenter__(self) -> MCPAgentClient:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            stack.enter_context(_quiet_protocol_diagnostics())
            http_client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=self.timeout_seconds,
                    trust_env=False,
                )
            )
            streams = await stack.enter_async_context(
                streamable_http_client(self.endpoint, http_client=http_client)
            )
            session = await stack.enter_async_context(ClientSession(*streams))
            await session.initialize()
            listed = await session.list_tools()
            if listed.next_cursor is not None:
                raise MCPAgentError("MCP_DISCOVERY_INVALID")
            tools = [_tool_definition(item) for item in listed.tools]
            names = [item["function"]["name"] for item in tools]
            if len(tools) > MAX_TOOLS or len(names) != len(set(names)) or set(names) & set(CONTROL_TOOLS):
                raise MCPAgentError("MCP_DISCOVERY_INVALID")
            self._stack = stack
            self._session = session
            self._tools = tools + list(CONTROL_TOOLS.values())
            return self
        except BaseException as error:
            try:
                await stack.aclose()
            except Exception:
                # Cleanup diagnostics must not replace the original safe category.
                pass
            if not isinstance(error, Exception) or isinstance(error, MCPAgentError):
                raise
            raise MCPAgentError("MCP_CONNECTION_FAILED") from None

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                if exc is None:
                    raise MCPAgentError("MCP_CONNECTION_FAILED") from None

    @property
    def tools(self) -> list[dict[str, object]]:
        """Return caller-owned OpenAI-compatible definitions after discovery."""
        return [dict(tool) for tool in self._tools]

    @property
    def world_tool_names(self) -> frozenset[str]:
        return frozenset(tool["function"]["name"] for tool in self._tools[:-2])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if self._session is None or name not in {tool["function"]["name"] for tool in self._tools}:
            raise MCPAgentError("MCP_CALL_INVALID")
        if not isinstance(arguments, dict):
            raise MCPAgentError("MCP_CALL_INVALID")
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:
            del exc
            raise MCPAgentError("MCP_CALL_FAILED") from None
        text = _result_text(result)
        text = text.replace(self.token, "[redacted]")[:MAX_OBSERVATION_CHARS]
        return MCPToolResult(text=text, is_error=result.is_error)


def _result_text(result: object) -> str:
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return ""
    values: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            values.append(text)
    return "\n".join(values)
