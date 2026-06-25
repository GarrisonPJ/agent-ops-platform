"""OpenAI Agents SDK Adapter — converts platform ``Tool`` to ``FunctionTool``.

Usage::

    from agents import Agent, Runner
    from app.adapters.openai_agents_adapter import OpenAIAgentsAdapter

    adapter = OpenAIAgentsAdapter()
    sdk_tools = adapter.to_tools(registry.list_all())

    agent = Agent(name="MyAgent", instructions="...", tools=sdk_tools)
    result = Runner.run_sync(agent, "Say hello")
    print(result.final_output)
"""

from __future__ import annotations

import json
from typing import Any

from app.adapters.base import AgentAdapter
from app.tool_registry import Tool

# ---------------------------------------------------------------------------
# Graceful degrade when openai-agents is not installed
# ---------------------------------------------------------------------------
try:
    from agents.tool import FunctionTool

    _OPENAI_AGENTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OPENAI_AGENTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class OpenAIAgentsAdapter(AgentAdapter):
    """Adapter that converts platform ``Tool`` objects into OpenAI Agents SDK
    ``FunctionTool`` instances.

    Each converted tool, when invoked, executes through the platform's
    ``DockerToolExecutor`` under the hood.
    """

    def to_tools(self, tools: list[Tool]) -> list[Any]:
        if not _OPENAI_AGENTS_AVAILABLE:
            raise ImportError(
                "openai-agents is required — install it with:\n"
                "  uv sync --extra openai-agents\n"
                "  # or:  uv pip install openai-agents"
            )

        result: list[FunctionTool] = []
        for t in tools:
            ft = FunctionTool(
                name=t.name,
                description=t.description,
                params_json_schema=t.parameters,
                on_invoke_tool=_build_invoker(t),
            )
            result.append(ft)
        return result

    @staticmethod
    def from_framework_result(result: Any) -> str:
        """Extract string output from an OpenAI Agents SDK tool result.

        The SDK typically returns result objects with a ``.output``
        attribute; falls back to ``str()`` for plain values.
        """
        if hasattr(result, "output"):
            return str(result.output)
        return str(result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_invoker(tool: Tool):
    """Return an async callable that executes *tool* via DockerToolExecutor.

    The import is deferred so this module loads even when
    :mod:`app.docker_executor` has not been implemented yet.
    """

    async def invoke(ctx: Any, arguments: str) -> str:
        from app.docker_executor import DockerToolExecutor

        kwargs = json.loads(arguments)
        executor = DockerToolExecutor()
        raw = await executor.execute(tool, kwargs)
        return OpenAIAgentsAdapter.from_framework_result(raw)

    return invoke
