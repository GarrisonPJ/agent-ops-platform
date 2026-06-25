"""LangChain Adapter — converts platform ``Tool`` objects to LangChain tools.

Usage::

    from langchain.agents import create_react_agent
    from langchain_openai import ChatOpenAI
    from app.adapters.langchain_adapter import LangChainAdapter

    adapter = LangChainAdapter()
    langchain_tools = adapter.to_tools(registry.list_all())

    llm = ChatOpenAI(model="gpt-4o")
    agent = create_react_agent(llm, langchain_tools)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, create_model

from app.adapters.base import AgentAdapter
from app.tool_registry import Tool

# ---------------------------------------------------------------------------
# Graceful degrade when langchain-core is not installed
# ---------------------------------------------------------------------------
try:
    from langchain_core.tools import StructuredTool

    _LANGCHAIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LANGCHAIN_AVAILABLE = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _schema_to_model(name: str, parameters: dict) -> type[BaseModel]:
    """Convert OpenAI JSON Schema ``parameters`` to a Pydantic model.

    The model class is named ``{name}_args`` (e.g. ``kubectl_get_pods_args``).
    """
    fields: dict[str, tuple[type, Any]] = {}
    for prop, schema in parameters.get("properties", {}).items():
        json_type = schema.get("type", "string")
        python_type = _JSON_TYPE_MAP.get(json_type, str)
        description = schema.get("description", "")
        fields[prop] = (python_type, Field(description=description))
    return create_model(f"{name}_args", **fields)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class LangChainAdapter(AgentAdapter):
    """Adapter that converts platform ``Tool`` objects into LangChain
    ``StructuredTool`` instances.

    Each converted tool, when invoked, executes through the platform's
    ``DockerToolExecutor`` under the hood.
    """

    def to_tools(self, tools: list[Tool]) -> list[Any]:
        if not _LANGCHAIN_AVAILABLE:
            raise ImportError(
                "langchain-core is required — install it with:\n"
                "  uv sync --extra langchain\n"
                "  # or:  uv pip install langchain-core langchain-openai"
            )

        result: list[StructuredTool] = []
        for t in tools:
            args_schema = _schema_to_model(t.name, t.parameters)
            st = StructuredTool(
                name=t.name,
                description=t.description,
                func=_build_tool_fn(t),
                args_schema=args_schema,
            )
            result.append(st)
        return result

    @staticmethod
    def from_framework_result(result: Any) -> str:
        """Extract string output from a LangChain tool result.

        Handles both ``AgentFinish`` (has ``.output``) and plain strings.
        """
        if hasattr(result, "output"):
            return str(result.output)
        return str(result)


# ---------------------------------------------------------------------------
# Internal helpers (module-level so every tool-call creates a fresh closure)
# ---------------------------------------------------------------------------


def _build_tool_fn(tool: Tool):
    """Return a synchronous callable that executes *tool* via DockerToolExecutor.

    The ``DockerToolExecutor`` import is deferred so this module loads even
    when :mod:`app.docker_executor` has not been implemented yet.
    """

    def fn(**kwargs: Any) -> str:
        from app.docker_executor import DockerToolExecutor

        executor = DockerToolExecutor()
        raw = executor.execute(tool, kwargs)
        return LangChainAdapter.from_framework_result(raw)

    fn.__name__ = tool.name
    fn.__doc__ = tool.description
    return fn
