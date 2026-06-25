"""Framework adapters for the AgentOps platform.

Each adapter converts platform ``Tool`` objects (from :mod:`app.tool_registry`)
into framework-specific tool definitions so that external agent frameworks
can drive execution through the platform's ``DockerToolExecutor``.
"""

from app.adapters.base import AgentAdapter

# Concrete adapters are importable on demand:
#
#   from app.adapters.langchain_adapter import LangChainAdapter
#   from app.adapters.openai_agents_adapter import OpenAIAgentsAdapter

__all__ = ["AgentAdapter"]
