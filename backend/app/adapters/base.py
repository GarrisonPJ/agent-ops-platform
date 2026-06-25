"""Abstract base class for framework adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.tool_registry import Tool


class AgentAdapter(ABC):
    """Abstract adapter that converts platform ``Tool`` objects into
    framework-specific tool representations.

    Subclasses must implement ``to_tools`` and ``from_framework_result``.
    """

    @abstractmethod
    def to_tools(self, tools: list[Tool]) -> list[Any]:
        """Convert a list of platform ``Tool`` objects into framework-native
        tool definitions (e.g. LangChain ``StructuredTool`` or OpenAI Agents
        SDK ``FunctionTool``).

        Parameters
        ----------
        tools:
            Platform tools from ``ToolRegistry.list_all()``.

        Returns
        -------
        list[Any]
            Framework-specific tool objects.
        """

    @abstractmethod
    def from_framework_result(self, result: Any) -> str:
        """Extract a human-readable observation string from whatever the
        framework returned after executing a tool.

        Parameters
        ----------
        result:
            Raw execution result produced by the framework (exact type
            depends on the framework).

        Returns
        -------
        str
            Unified observation string suitable for the ReAct loop.
        """
