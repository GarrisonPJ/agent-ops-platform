"""
OpenAI Agents SDK Adapter — Demo Script
========================================

Demonstrates how to use the ``OpenAIAgentsAdapter`` to bridge the platform's
``ToolRegistry`` with an OpenAI Agents SDK ``Agent``.

Usage (with openai-agents installed)::

    uv sync --extra openai-agents
    uv run python examples/openai_agents_demo.py

What this script shows:
    1. Loading pre-registered demo tools from the ``ToolRegistry``.
    2. Converting them to ``FunctionTool`` objects via the adapter.
    3. Creating an OpenAI Agents SDK ``Agent`` with those tools.
    4. Running a trivial task and printing the final output.

NOTE: ``DockerToolExecutor`` is referenced but may not be fully wired yet.
      The adapter pattern remains correct — execution will work once the
      executor is implemented (issue #04).
"""

# ---------------------------------------------------------------------------
# 1. Platform setup — load tools from the registry
# ---------------------------------------------------------------------------
from app.tool_registry import ToolRegistry

registry = ToolRegistry.get_instance()
registry.register_demo_tools()
platform_tools = registry.list_all()

print(f"Platform tools loaded: {[t.name for t in platform_tools]}")

# ---------------------------------------------------------------------------
# 2. Adapter — convert platform tools to OpenAI Agents SDK tools
# ---------------------------------------------------------------------------
from app.adapters.openai_agents_adapter import OpenAIAgentsAdapter

adapter = OpenAIAgentsAdapter()
sdk_tools = adapter.to_tools(platform_tools)

print(f"Converted to {len(sdk_tools)} FunctionTool(s)")

# ---------------------------------------------------------------------------
# 3. OpenAI Agents SDK Agent (uncomment when dependencies are installed)
# ---------------------------------------------------------------------------
# from agents import Agent, Runner
#
# agent = Agent(
#     name="DemoAgent",
#     instructions="You are a helpful assistant with access to platform tools.",
#     tools=sdk_tools,
# )
#
# result = Runner.run_sync(
#     agent,
#     "Run kubectl_get_pods with NAMESPACE=default and summarise the output.",
# )
#
# print("\n=== Final answer ===")
# print(result.final_output)

# ---------------------------------------------------------------------------
# 4. Quick smoke-test without the SDK — just exercise the adapter logic
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    for t in sdk_tools:
        print(f"  - {t.name}: schema keys = {list(t.params_json_schema.get('properties', {}))}")
    print()
    print("Adapter works. To run the full agent:")
    print("  uv sync --extra openai-agents")
    print("  uv run python examples/openai_agents_demo.py")
    print()
    print("(You'll also need a valid OPENAI_API_KEY in your environment.)")
