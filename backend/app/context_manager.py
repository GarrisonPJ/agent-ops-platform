"""Context window management for LLM conversations.

Uses ``tiktoken`` to estimate token counts and a sliding-window eviction
strategy to keep the conversation within a configured token budget.
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

from app.llm import Message


@dataclass
class ContextInfo:
    """Token-usage information for a single context-window snapshot."""

    used: int
    limit: int


class ContextManager:
    """Manages the LLM context window via a sliding-window eviction policy.

    The policy:
    1. If total tokens <= *max_tokens*, messages are returned as-is.
    2. Otherwise the **system message** (if any) is pinned and the oldest
       non-system messages are trimmed until the budget is satisfied.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        context_limit: int | None = None,
    ) -> None:
        self._encoding = tiktoken.get_encoding(
            tiktoken.encoding_name_for_model(model)
        )
        self._context_limit = context_limit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Return the estimated token count for a list of messages."""
        return sum(self._tokens_for_message(m) for m in messages)

    def manage(
        self,
        messages: list[Message],
        max_tokens: int,
    ) -> tuple[list[Message], ContextInfo]:
        """Apply sliding-window eviction if the message list exceeds the budget.

        Returns a ``(trimmed_messages, context_info)`` tuple.
        The ``limit`` field of ``ContextInfo`` is set to the model's context
        window limit (``context_limit``) when available, otherwise to
        ``max_tokens``.
        """
        limit = self._context_limit or max_tokens
        total = self.estimate_tokens(messages)
        if total <= max_tokens:
            return messages, ContextInfo(used=total, limit=limit)

        # Pin the system message (first message with role="system")
        system: list[Message] = []
        rest: list[Message] = []
        for m in messages:
            if m.role == "system" and not system:
                system.append(m)
            else:
                rest.append(m)

        budget = max_tokens - self.estimate_tokens(system)
        if budget <= 0:
            # System alone exceeds budget — hard trim to just the system msg
            trimmed = system
            used = self.estimate_tokens(system)
            return trimmed, ContextInfo(used=used, limit=limit)

        # Walk from the newest message, keeping as many as fit in budget
        kept: list[Message] = []
        for m in reversed(rest):  # newest first
            mt = self._tokens_for_message(m)
            if budget - mt >= 0:
                kept.insert(0, m)
                budget -= mt
            # drop messages that don't fit

        trimmed = system + kept

        # Post-process: remove orphaned tool messages whose tool_call_id
        # doesn't match any tool_calls[].id in the kept assistant messages.
        valid_tool_call_ids: set[str] = set()
        for m in trimmed:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    tid = tc.get("id") if isinstance(tc, dict) else None
                    if tid:
                        valid_tool_call_ids.add(tid)

        if valid_tool_call_ids:
            trimmed = [
                m for m in trimmed
                if not (m.role == "tool" and m.tool_call_id
                        and m.tool_call_id not in valid_tool_call_ids)
            ]
        else:
            # No assistant with tool_calls kept — remove all tool messages
            trimmed = [m for m in trimmed if m.role != "tool"]

        used = self.estimate_tokens(trimmed)
        return trimmed, ContextInfo(used=used, limit=limit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tokens_for_message(self, message: Message) -> int:
        """Estimate tokens in a single message, including per-message overhead.

        Roughly follows the OpenAI tiktoken ``chatml`` style
        (``<|im_start|>role\\ncontent<|im_end|>``).
        """
        num_tokens = 3  # overhead per message
        if message.content:
            num_tokens += len(self._encoding.encode(message.content))
        if message.name:
            num_tokens += len(self._encoding.encode(message.name))
        return num_tokens
