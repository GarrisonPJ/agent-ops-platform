"""Deterministic independent MCP Agent for the controlled Runner recovery gate.

The operator-only recovery registry selects it; the first Attempt waits for fault
injection and the replacement calls the real Environment through MCP.
It never emits trusted events, imports CP/world logic, or decides Run success.
Read scripts/recovery_e2e.py for the fencing and terminal-agreement assertions.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.fixture_agent import AgentInputError, AgentSpec, read_spec
from app.mcp_agent_client import MCPAgentClient, MCPAgentError

# Children receive only the Runner's explicit environment allowlist. Keep this
# gate marker fixed instead of relying on an ambient variable being forwarded.
# The gate-only marker must survive force-recreation, just like the Run token.
# Its suffix is not a token record and it never contains a capability.
MARKER = Path("/run/agentops/token-handoff/recovery-fixture.marker")
TOOLS = (
    ("check_service_health", {"service": "checkout-api"}),
    ("query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
    ("fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
)


async def run(spec: AgentSpec) -> int:
    if not MARKER.exists():
        MARKER.write_text("started\n", encoding="utf-8")
        await asyncio.sleep(60)
        return 1
    async with MCPAgentClient(
        os.environ.get("AGENTOPS_ENV_MCP_URL", ""),
        os.environ.get("AGENTOPS_ENV_TOKEN", ""),
        timeout_seconds=spec.timeout_ms / 1000,
    ) as client:
        for name, arguments in TOOLS:
            result = await client.call_tool(name, arguments)
            if result.is_error:
                return 1
        result = await client.call_tool("submit_result", {})
        return 1 if result.is_error else 0


def main() -> int:
    try:
        spec = read_spec()
        return asyncio.run(asyncio.wait_for(run(spec), timeout=spec.timeout_ms / 1000))
    except (AgentInputError, MCPAgentError, asyncio.TimeoutError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
