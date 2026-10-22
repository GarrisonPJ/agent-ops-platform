# ADR-0008: Environment MCP server and env-side capture

- Status: Accepted
- Date: 2026-10-17
- Implementation: Phase 2 H1. Not in code at `8bd2ef0`.
- Depends on: [ADR-0007](0007-environment-agent-split.md)
- Amends: [ADR-0001](0001-python-control-plane-rust-execution-plane.md) (one
  child process becomes two when H1 ships)

## Context

If the agent reports its own tool calls, every adapter must speak our JSONL
dialect, and a misbehaving agent can forge the trace. The Model Context
Protocol (revision 2026-07-28) already has a Streamable HTTP transport and a
Python SDK (`mcp` 2.3.x, class `MCPServer`, formerly `FastMCP`) with
middleware on `tools/call`. Claude Code, Codex CLI, and the OpenAI Agents SDK
can all attach to a Streamable HTTP MCP server.

Phase 1 removed an earlier, unrelated MCP tool-bus. This ADR does not bring
that bus back.

## Decision

`python -m app.env_server` is the Environment. The Runner starts it as a
child, then starts the agent.

- Transport is MCP Streamable HTTP on `127.0.0.1` and an ephemeral port, path
  `/mcp`. Stdio MCP and SSE are not used. Pin `mcp` to the 2.3 minor line.
- The server prints one supervision line and then serves:

  ```json
  {"type":"env_ready","payload":{"mcp_url":"http://127.0.0.1:<port>/mcp"}}
  ```

  `env_ready` is not a RunEvent. It must not contain the raw token.
- The Runner generates one token per Run, passes it to the Environment as
  `AGENTOPS_ENV_TOKEN`, and passes `AGENTOPS_ENV_MCP_URL`,
  `AGENTOPS_ENV_TOKEN`, and `AGENTOPS_TASK_PROMPT` to the agent. The
  Environment rejects MCP requests without that token. The token is not
  written to PostgreSQL or to product events.
- Middleware on `tools/call` appends `step_completed` to the existing JSONL
  stdout stream. The Runner wraps those lines as `EventEnvelope`s and uploads
  them as it does today. The agent is not a source of `step_completed`.
- Reserved tools, hidden from the model's normal tool list where the SDK
  allows it, and rejected if the model calls anything outside
  `tool_definitions()` plus these two:
  - `submit_result` — the agent is done; the Environment runs `check()`.
  - `report_usage` — optional context/token report. Absence leaves those
    dimensions unknown.
- The Environment enforces `max_steps` and the run timeout. Calls past the
  budget return an error observation and are still recorded.
- Both children share the Runner's process group. Cancel and timeout SIGTERM
  the group, then SIGKILL after the existing two-second grace (ADR-0002).
  Rust tests for dual-child teardown land before the feature is considered
  done.
- `schema_version` stays 1. `env_ready` is a local supervision message. No
  new product event type is required.

## Consequences

- Any agent that can call MCP tools can be evaluated without a custom trace
  exporter.
- Traces lack chain-of-thought and, unless `report_usage` is called, token
  and context figures. That is accepted.
- MCP SDK churn is contained in `app.env_server`.
- CI still drives the fixture agent through this server, or through the same
  Environment interface in-process if a test needs no socket. Either way the
  event log is the contract, not the transport.
