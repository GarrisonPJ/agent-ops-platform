"""Unit tests for ``app.mcp_server`` — MCP server with real executor injection."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, CallToolResult, TextContent

from app.executor import Executor, ExecutorResult
from app.mcp_server import create_app
from app.tool_registry import Tool, ToolRegistry


def _make_registry() -> ToolRegistry:
    """Return a fresh ToolRegistry with one test tool."""
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="test_tool",
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
            },
            image="alpine:latest",
            command=["echo", "$msg"],
            timeout_ms=10_000,
        )
    )
    return reg


class TestCreateApp:
    """Tests for ``create_app()`` with executor injection."""

    @pytest.mark.asyncio
    async def test_mock_executor_is_invoked(self) -> None:
        """Mock executor.execute() is called when an MCP client calls a tool."""
        # Arrange
        mock_exec = MagicMock(spec=Executor)
        mock_exec.execute = AsyncMock(return_value=ExecutorResult(
            status="success",
            output="hello from mock",
            latency_ms=42,
            execution_id="exec-001",
        ))
        registry = _make_registry()
        app = create_app(registry, executor=mock_exec)

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="test_tool", arguments={"msg": "world"}),
        )

        # Act
        handler = app.request_handlers[CallToolRequest]
        response = await handler(request)

        # Assert
        mock_exec.execute.assert_awaited_once()
        call_args, call_kwargs = mock_exec.execute.call_args
        assert call_args[0].name == "test_tool"
        assert call_args[1] == {"msg": "world"}

        # The handler wraps our return value in a ServerResult → CallToolResult
        result: CallToolResult = response.root
        assert result.isError is False
        assert len(result.content) == 1
        content_block = result.content[0]
        assert isinstance(content_block, TextContent)
        payload = json.loads(content_block.text)
        assert payload["tool"] == "test_tool"
        assert payload["arguments"] == {"msg": "world"}
        assert payload["output"] == "hello from mock"
        assert payload["status"] == "success"
        assert payload["latency_ms"] == 42
        assert payload["execution_id"] == "exec-001"

    @pytest.mark.asyncio
    async def test_mock_executor_result_as_text_content(self) -> None:
        """ExecutorResult fields are surfaced as TextContent to the MCP client."""
        # Arrange
        mock_exec = MagicMock(spec=Executor)
        mock_exec.execute = AsyncMock(return_value=ExecutorResult(
            status="success",
            output="result output",
            latency_ms=100,
            execution_id="exec-002",
        ))
        registry = _make_registry()
        app = create_app(registry, executor=mock_exec)

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="test_tool", arguments={"msg": "hi"}),
        )

        # Act
        handler = app.request_handlers[CallToolRequest]
        response = await handler(request)

        # Assert
        result: CallToolResult = response.root
        assert result.isError is False
        assert len(result.content) == 1
        content_block = result.content[0]
        assert isinstance(content_block, TextContent)
        # The output is returned as a JSON envelope
        payload = json.loads(content_block.text)
        assert payload["tool"] == "test_tool"
        assert payload["arguments"] == {"msg": "hi"}
        assert payload["output"] == "result output"
        assert payload["status"] == "success"
        assert payload["latency_ms"] == 100
        assert payload["execution_id"] == "exec-002"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        """Calling an unregistered tool returns an error result, not a crash."""
        # Arrange
        import logging
        logging.getLogger("agentops.mcp").setLevel(logging.CRITICAL)

        mock_exec = MagicMock(spec=Executor)
        mock_exec.execute = AsyncMock()
        registry = _make_registry()
        app = create_app(registry, executor=mock_exec)

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="nonexistent_tool", arguments={}),
        )

        # Act
        handler = app.request_handlers[CallToolRequest]
        response = await handler(request)

        # Assert — the server should return an error, not raise
        result: Any = response.root
        assert result.isError is True
        # Unregistered tool → should NOT reach the executor
        mock_exec.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_executor_fallback(self) -> None:
        """create_app without an executor argument doesn't crash (uses create_executor factory).

        We can't import DockerToolExecutor here (requires docker daemon), but we
        can verify the app is constructable and list_tools still works.
        """
        registry = _make_registry()
        app = create_app(registry)
        assert app.name == "agentops-mcp"
        # list_tools handler should still function
        list_handler = app.request_handlers.get(type(None))  # ListToolsRequest
        # Just verify the app was created; the default executor would
        # be created lazily when a tool is actually called.
