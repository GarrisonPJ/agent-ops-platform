"""OpenAI-compatible MCP Agent-under-test for canonical H1 Environments.

The Runner starts this process; it discovers tools through the loopback MCP
Environment and asks the configured provider for one bounded decision at a time.
It must require an explicit submission and never persist scores, tokens, or RunEvents.

Read ``app.mcp_agent_client`` for the transport and ``app.phase1_provider`` for
only the low-level provider chat boundary.
"""

from __future__ import annotations

import asyncio
import os
import sys

from app.fixture_agent import AgentInputError, AgentSpec, read_spec
from app.mcp_agent_client import MCPAgentClient, MCPAgentError
from app.phase1_provider import OpenAICompatibleProvider, ProviderError, ProviderSettings


async def run(spec: AgentSpec) -> int:
    try:
        settings = ProviderSettings.from_env()
        provider = OpenAICompatibleProvider(settings)
    except ProviderError as error:
        _error(error.code)
        return 1
    endpoint = os.environ.get("AGENTOPS_ENV_MCP_URL", "")
    token = os.environ.get("AGENTOPS_ENV_TOKEN", "")
    total_prompt: int | None = 0
    total_completion: int | None = 0
    world_attempts = 0
    try:
        async with MCPAgentClient(endpoint, token, timeout_seconds=spec.timeout_ms / 1000) as environment:
            messages: list[dict[str, object]] = [
                {
                    "role": "system",
                    "content": (
                        "You are an Agent-under-test. Complete the task using only the discovered "
                        "Environment tools. Use at most one tool call per response. Never request "
                        "shell, network, credentials, or arbitrary code. You must explicitly call "
                        "submit_result when the task is complete."
                        + ("\nPolicy instructions:\n" + "\n".join(_policy_instructions(spec)) if spec.policy else "")
                    ),
                },
                {"role": "user", "content": spec.task},
            ]
            # World attempts exclude reserved controls; leave a bounded final
            # provider decision for explicit submission, never auto-submit.
            for _ in range(spec.max_steps + 1):
                response = await provider.chat(messages, environment.tools)
                total_prompt = _accumulate_usage(
                    total_prompt, response.usage.prompt_tokens, response.usage.prompt_tokens_available
                )
                total_completion = _accumulate_usage(
                    total_completion, response.usage.completion_tokens,
                    response.usage.completion_tokens_available,
                )
                usage = await environment.call_tool(
                    "report_usage",
                    {
                        "token_prompt": total_prompt,
                        "token_completion": total_completion,
                    },
                )
                if usage.is_error:
                    _error("MCP_USAGE_REJECTED")
                    return 1
                if len(response.tool_calls) != 1:
                    _error("PROVIDER_SUBMIT_REQUIRED")
                    return 1
                call = response.tool_calls[0]
                if call.name not in environment.world_tool_names and call.name != "submit_result":
                    _error("PROVIDER_TOOL_UNSUPPORTED")
                    return 1
                if call.name == "submit_result":
                    if call.arguments:
                        _error("PROVIDER_SUBMIT_INVALID")
                        return 1
                    submitted = await environment.call_tool("submit_result", {})
                    return 0 if not submitted.is_error else 1
                if world_attempts >= spec.max_steps:
                    _error("PROVIDER_STEP_LIMIT")
                    return 1
                world_attempts += 1
                observation = await environment.call_tool(call.name, call.arguments)
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [call.raw],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": observation.text,
                    }
                )
                if observation.is_error:
                    # The actual bounded MCP error is useful to the model; never
                    # turn it into a process failure or copy raw transport detail.
                    continue
            _error("PROVIDER_STEP_LIMIT")
            return 1
    except (MCPAgentError, ProviderError) as error:
        _error(getattr(error, "code", "AGENT_FAILED"))
        return 1
    except asyncio.TimeoutError:
        _error("AGENT_TIMEOUT")
        return 1


def _accumulate_usage(total: int | None, value: int, available: bool) -> int | None:
    if total is None or not available or type(value) is not int or not 0 <= value <= 1_000_000_000:
        return None
    cumulative = total + value
    return cumulative if cumulative <= 1_000_000_000 else None


def _policy_instructions(spec: AgentSpec) -> list[str]:
    # The input parser keeps policy shape bounded; this is an instruction-only
    # patch and is never treated as a tool contract or expected observation.
    return list(getattr(spec, "instruction_patch", ()))


def _error(code: str) -> None:
    print(str(code)[:64], file=sys.stderr, flush=True)


def main() -> int:
    try:
        spec = read_spec()
        return asyncio.run(asyncio.wait_for(run(spec), timeout=spec.timeout_ms / 1000))
    except (AgentInputError, asyncio.TimeoutError):
        print("INVALID_SPEC", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
