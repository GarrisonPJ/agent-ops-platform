"""
LangChain Adapter — Demo Script
================================

Demonstrates how to use the ``LangChainAdapter`` to bridge the platform's
``ToolRegistry`` with a LangChain ReAct agent.

Usage (with langchain-core installed)::

    uv sync --extra langchain
    uv run python examples/langchain_demo.py

What this script shows:
    1. Loading pre-registered demo tools from the ``ToolRegistry``.
    2. Converting them to LangChain ``StructuredTool`` objects via the adapter.
    3. Creating a LangChain ReAct agent with an OpenAI-compatible LLM.
    4. Running a trivial task and printing the result.

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
# 2. Adapter — convert platform tools to LangChain tools
# ---------------------------------------------------------------------------
from app.adapters.langchain_adapter import LangChainAdapter

adapter = LangChainAdapter()
langchain_tools = adapter.to_tools(platform_tools)

print(f"Converted to {len(langchain_tools)} LangChain StructuredTool(s)")

# ---------------------------------------------------------------------------
# 3. LangChain ReAct agent (uncomment when dependencies are installed)
# ---------------------------------------------------------------------------
# from langchain.agents import create_react_agent, AgentExecutor
# from langchain_openai import ChatOpenAI
# from langchain import hub
#
# llm = ChatOpenAI(model="gpt-4o", temperature=0)
# prompt = hub.pull("hwchase17/react")
# agent = create_react_agent(llm, langchain_tools, prompt)
# executor = AgentExecutor(agent=agent, tools=langchain_tools, verbose=True)
#
# result = executor.invoke({"input": "Run kubectl_get_pods with NAMESPACE=default "
#                                     "and tell me how many pods are running."})
#
# print("\n=== Final answer ===")
# print(result["output"])

# ---------------------------------------------------------------------------
# 4. Quick smoke-test without LangChain — just exercise the adapter logic
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    for t in langchain_tools:
        print(f"  - {t.name}: {t.description[:60]}...")
    print()
    print("Adapter works. To run the full ReAct agent:")
    print("  uv sync --extra langchain")
    print("  uv run python examples/langchain_demo.py")
    print()
    print("(You'll also need a valid OPENAI_API_KEY in your environment.)")
