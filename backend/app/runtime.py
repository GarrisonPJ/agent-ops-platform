"""Agent Runtime — ReAct loop (Think -> Act -> Observe).

The ``AgentRuntime`` class implements a ReAct-style agent loop as an async
generator.  Each iteration:

1. Calls the LLM with the conversation history and available tools.
2. Yields a ``Step`` — either a **tool-call step** (thought + action) or a
   **final-answer step** (thought + observation with ``action=None``).
3. Tool-call steps are executed via ``DockerToolExecutor`` (if available),
   populating ``observation`` and ``container_id`` with real execution data.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from logging import getLogger
from typing import TYPE_CHECKING

from app.context_manager import ContextInfo, ContextManager
from app.llm import (
    ChatResponse,
    LLMProvider,
    Message,
    ToolCall,
    ToolSchema,
)

if TYPE_CHECKING:
    from app.executor import Executor
    from app.tool_registry import ToolRegistry

logger = getLogger(__name__)

_REACT_SYSTEM_PROMPT = """\
You are an autonomous AI agent that follows the **ReAct** pattern:

1. **Think** — Reason about the current task and decide what to do next.
2. **Act** — Call one of the provided tools when you need information or want \
to perform an action.
3. **Observe** — Use the tool result to inform your next thought.

Always respond in one of two ways:

**With a tool call:**
{
  "thought": "I need to gather information first...",
  "action": "<tool_name>",
  "arguments": { ... }
}

**With a final answer:**
{
  "thought": "I have enough information to answer.",
  "answer": "The final answer to the user's request."
}

You have access to the following tools — use them when appropriate.
If you already know the answer, respond with a final answer directly.
"""


@dataclass
class Step:
    """A single step within a trajectory."""

    index: int
    thought: str
    action: ToolCall | None
    observation: str
    latency_ms: int
    started_at: float  # Unix timestamp
    context_window: ContextInfo = field(
        default_factory=lambda: ContextInfo(used=0, limit=0)
    )
    container_id: str | None = None
    token_prompt: int | None = None
    token_completion: int | None = None


class AgentRuntime:
    """ReAct agent runtime.

    Usage::

        runtime = AgentRuntime()
        async for step in runtime.run(
            task="Say hello",
            tools=[...],
            llm=provider,
            context_manager=cm,
        ):
            print(step)
    """

    def __init__(
        self,
        tool_executor: Executor | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._history: list[Message] = []
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry

    async def run(
        self,
        task: str,
        tools: list[ToolSchema],
        llm: LLMProvider,
        context_manager: ContextManager,
        max_steps: int = 15,
        max_tokens: int = 4096,
    ) -> AsyncIterator[Step]:
        """Execute the ReAct loop, yielding one ``Step`` at a time.

        Parameters
        ----------
        task:
            The user's instruction / task description.
        tools:
            List of tool schemas the agent can invoke.
        llm:
            LLM provider instance.
        context_manager:
            Context-window manager for token-budget tracking.
        max_steps:
            Maximum number of LLM calls before the loop is aborted.
        max_tokens:
            Maximum token budget for context window management.
        """
        self._history = [
            Message(role="system", content=_REACT_SYSTEM_PROMPT),
            Message(role="user", content=task),
        ]

        for step_index in range(max_steps):
            started_at = time.time()

            # ── Context window management ────────────────────────────────
            self._history, ctx_info = context_manager.manage(
                self._history, max_tokens
            )

            # ── Call LLM ────────────────────────────────────────────────
            t0 = time.perf_counter()
            response: ChatResponse = await llm.chat(
                messages=self._history,
                tools=tools,
            )

            elapsed = int((time.perf_counter() - t0) * 1000)

            # Extract token usage from the LLM response
            token_prompt: int | None = None
            token_completion: int | None = None
            if response.usage is not None:
                token_prompt = response.usage.prompt_tokens
                token_completion = response.usage.completion_tokens

            # ── Error handling ───────────────────────────────────────────
            if response.content and response.content.startswith("[LLM"):
                yield Step(
                    index=step_index,
                    thought="",
                    action=None,
                    observation=response.content,
                    latency_ms=elapsed,
                    started_at=started_at,
                    context_window=ctx_info,
                    token_prompt=token_prompt,
                    token_completion=token_completion,
                )
                return

            # ── Tool-call step ──────────────────────────────────────────
            if response.tool_calls:
                tc = response.tool_calls[0]

                # Store assistant message with tool_calls in history so the
                # LLM sees its own previous tool-call attempt on retries.
                self._history.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=[
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                        ],
                        reasoning_content=response.reasoning_content,
                    )
                )

                # ── Execute tool via executor (if available) ──────────
                observation: str = "Waiting for tool execution..."
                tool_latency_ms: int = elapsed
                container_id: str | None = None

                if self._tool_executor and self._tool_registry:
                    tool = self._tool_registry.get(tc.name)
                    if tool is not None and tool.enabled:
                        try:
                            result = await self._tool_executor.execute(
                                tool, tc.arguments
                            )
                            observation = result.output
                            tool_latency_ms = result.latency_ms
                            container_id = result.execution_id
                        except Exception as exc:
                            logger.exception("Tool execution failed")
                            observation = f"Tool execution error: {exc}"
                    elif tool is not None and not tool.enabled:
                        observation = (
                            f"Tool '{tc.name}' is currently disabled. "
                            f"Enable it in the Tools page."
                        )
                    else:
                        observation = (
                            f"Unknown tool '{tc.name}' — no matching Tool "
                            f"registered in ToolRegistry"
                        )

                yield Step(
                    index=step_index,
                    thought=response.content or "",
                    action=tc,
                    observation=observation,
                    latency_ms=tool_latency_ms,
                    started_at=started_at,
                    context_window=ctx_info,
                    container_id=container_id,
                    token_prompt=token_prompt,
                    token_completion=token_completion,
                )

                self._history.append(
                    Message(
                        role="tool",
                        content=observation,
                        tool_call_id=tc.id,
                    )
                )
                continue

            # ── Final answer step ────────────────────────────────────────
            answer = response.content or ""

            yield Step(
                index=step_index,
                thought=answer,
                action=None,
                observation=answer,
                latency_ms=elapsed,
                started_at=started_at,
                context_window=ctx_info,
                token_prompt=token_prompt,
                token_completion=token_completion,
            )

            self._history.append(
                Message(role="assistant", content=answer,
                        reasoning_content=response.reasoning_content)
            )
            return

        # ── Max steps exceeded ────────────────────────────────────────────
        logger.warning("Agent runtime exceeded max_steps=%d", max_steps)
        yield Step(
            index=max_steps,
            thought="",
            action=None,
            observation="Max steps exceeded — agent did not produce a final answer.",
            latency_ms=0,
            started_at=time.time(),
            context_window=ContextInfo(used=0, limit=0),
            token_prompt=None,
            token_completion=None,
        )
