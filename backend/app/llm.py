"""LLM Provider adapter layer.

Defines a provider-agnostic interface (``LLMProvider``) and one concrete
implementation for any OpenAI-compatible API (OpenAI, DeepSeek, Together, …).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx


# ── Data types ─────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """A tool-call instruction returned by the LLM."""

    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    """Token usage reported by an LLM API response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ChatResponse:
    """Parsed response from an LLM call."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    reasoning_content: str | None = None


@dataclass
class Message:
    """A single message in a chat conversation.

    For assistant messages with tool calls, ``tool_calls`` holds the
    OpenAI-format dict list (including ``id``, ``type``, ``function``).
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_content: str | None = None


ToolSchema = dict
"""An OpenAI-compatible tool/function definition dict.

Shape::

    {
        "type": "function",
        "function": {
            "name": "...",
            "description": "...",
            "parameters": {...JSON Schema...},
        }
    }
"""


# ── Provider protocol ──────────────────────────────────────────────────────


class LLMProvider(Protocol):
    """Protocol for a chat LLM provider."""

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> ChatResponse:
        """Send a chat completion request and return the parsed response."""
        ...


# ── OpenAI-compatible implementation ───────────────────────────────────────


class OpenAICompatibleProvider:
    """Wrapper around any OpenAI-compatible chat completions endpoint.

    Reads configuration from ``app.config.settings``:

    * ``llm_base_url`` — Base URL of the API (e.g. ``https://api.openai.com/v1``)
    * ``llm_api_key``  — API key / bearer token
    * ``llm_model``    — Model identifier (e.g. ``gpt-4o``, ``deepseek-chat``)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=60.0)

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> ChatResponse:
        """Call ``/chat/completions`` and return the parsed response."""
        body: dict = {
            "model": self.model,
            "messages": [_message_dict(m) for m in messages],
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            return ChatResponse(content="[LLM call timed out]")
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.text[:500]
            except Exception:
                pass
            import logging
            logging.getLogger("agentops.llm").error(
                "LLM API %d: %s", exc.response.status_code, detail
            )
            return ChatResponse(
                content=f"[LLM API error {exc.response.status_code}: {detail[:200]}]"
            )
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            return ChatResponse(content=f"[LLM call failed: {exc}]")

        choice = data["choices"][0]
        msg = choice["message"]

        content: str | None = msg.get("content")
        raw_tool_calls = msg.get("tool_calls")
        reasoning_content: str | None = msg.get("reasoning_content")

        tool_calls: list[ToolCall] | None = None
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                fn = tc["function"]
                tool_calls.append(
                    ToolCall(
                        id=tc["id"],
                        name=fn["name"],
                        arguments=_parse_json_args(fn.get("arguments", "{}")),
                    )
                )

        usage: Usage | None = None
        raw_usage = data.get("usage")
        if raw_usage:
            usage = Usage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0),
                completion_tokens=raw_usage.get("completion_tokens", 0),
            )

        return ChatResponse(
            content=content, tool_calls=tool_calls, usage=usage,
            reasoning_content=reasoning_content,
        )


# ── Internal helpers ───────────────────────────────────────────────────────


def _message_dict(msg: Message) -> dict:
    """Convert a ``Message`` to the OpenAI API JSON dict format."""
    d: dict = {"role": msg.role}
    d["content"] = msg.content or ""
    if msg.tool_calls:
        d["tool_calls"] = msg.tool_calls
    if msg.tool_call_id:
        d["tool_call_id"] = msg.tool_call_id
    if msg.name:
        d["name"] = msg.name
    # DeepSeek thinking mode requires reasoning_content to be passed back
    if msg.reasoning_content:
        d["reasoning_content"] = msg.reasoning_content
    return d


def _parse_json_args(raw: str) -> dict:
    """Safely parse ``arguments`` JSON string from an LLM tool call.

    Strips markdown code fences and handles malformed JSON gracefully.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
