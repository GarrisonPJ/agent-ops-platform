# ADR-0008: Environment MCP server and env-side capture

- Status: Accepted
- Date: 2026-10-17
- Implementation: H1 integration; absent from historical `8bd2ef0`.
  Decision acceptance is distinct from final delivery acceptance.
- Amended: 2026-10-24 by [ADR-0012](0012-h1-canonical-cutover-and-control-plane-success.md)
  (authoritative success, trusted evidence, and terminal agreement).
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
  - `submit_result` — the agent submits; the Environment runs `check()`. This
    produces facts, not authoritative score or Run status. A valid submission
    and successful check are necessary but not sufficient for success.
  - `report_usage` — optional context/token report. Absence leaves those
    dimensions unknown; reported values remain Agent-reported.
- Control Plane evaluates the latest-Attempt persisted all-world-attempt
  trajectory, including refusals, plus required trusted final-check facts;
  reserved controls are excluded. Valid-only Environment history is not the
  scoring trajectory. ADR-0012 defines rejection projection and the registered
  aggregate >= 1.0 success gate, distinct from quality penalties. Missing or
  invalid required evidence, failed check, no valid submit, Agent abnormal/nonzero
  exit, or unexpected Environment failure before terminal completion cannot
  succeed. Cancellation, timeout, output-limit failure, and lease fencing dominate.
- Rust only supervises/relays; it never scores or trusts Agent/check-reported
  status. Terminal events and Control Plane completion must agree. Submission,
  exit zero, and EOF are not success signals. H1-3 owns supervision and
  distinguishes unexpected failure from Runner-requested teardown of a healthy
  Environment after valid submission, successful check, and necessary evidence
  capture. Such planned teardown, including SIGTERM then SIGKILL after the shared
  two-second grace, is not abnormal execution failure; neither child must exit
  naturally with zero. This exception cannot override the failure gates above.
  H1-5/integration owns evidence and Control Plane completion wiring. The exact
  additive evidence wire was subsequently reviewed and integrated in this H1
  worktree; this ADR does not itself prescribe additional fields or types.
- The Environment enforces `max_steps` and the run timeout. Calls past the
  budget return an error observation and are still recorded.
- Both children share the Runner's process group. Cancel and timeout SIGTERM
  the group, then SIGKILL after the existing two-second grace (ADR-0002).
  Rust tests for dual-child teardown land before the feature is considered
  done.
- `schema_version` stays 1. `env_ready` is a local supervision message. No
  new product event type is required.

## H1 closeout amendment (operator-approved)

Lock files in the capability handoff directory must reject symlinks and
nonregular files at open time, for both the capacity lock and per-Run lock.
Use atomic no-follow opens and validate the opened descriptor's type, owner
and permissions before locking; do not truncate or mutate a refused target.
The protected directory is a trusted-host boundary, not isolation from a
malicious process running as the same OS user.

At Agent exit, retain the cancellation-safe JSONL reader across the final
bounded read opportunity. An incomplete final line must fail closed and must
not be uploaded or hidden by planned teardown, even after an accepted submit.
Observed unexpected Environment EOF/exit remains failure; planned teardown is
only requested after required complete facts have been uploaded and the
Environment remains alive. Do not introduce a new scorer or claim a universal
priority among simultaneously ready failure signals. Deterministic tests must
prove reader-side prefix consumption before observed Agent exit, release the
suffix only afterward, and assert exact accepted events and sequence numbers.

Fixture/fake-Provider tests remain deterministic and CI does not require a live
model. H1-5 separately records finite direct llama.cpp acceptance against an
already installed endpoint; research/order succeeded and checkout/fault failures
remain failures. This is not evidence of Ollama/vLLM compatibility, accuracy,
stable pass rate or public deployment.

## Consequences

- Any agent that can call MCP tools can be evaluated without a custom trace
  exporter.
- Traces lack chain-of-thought and, unless `report_usage` is called, token
  and context figures. That is accepted.
- MCP SDK churn is contained in `app.env_server`.
- CI still drives the fixture agent through this server, or through the same
  Environment interface in-process if a test needs no socket. Either way the
  event log is the contract, not the transport.
