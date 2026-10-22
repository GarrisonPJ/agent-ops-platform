# ADR-0009: Agent registry trust boundary

- Status: Accepted
- Date: 2026-10-17
- Implementation: Phase 2 H1 for the registry; H2 for external adapters.
  Not in code at `8bd2ef0`.
- Depends on: [ADR-0006](0006-scenario-registry-boundary.md),
  [ADR-0007](0007-environment-agent-split.md)

## Context

ADR-0006 forbids API clients from supplying an executable, command, provider
URL, or credential. Phase 2 needs more than one agent. Letting the browser
post a shell command would break that boundary.

External coding agents are useful and optional. They run on the operator's
machine because that is the trusted-intranet deployment this repo already
chose. Container isolation is a later safety-gate problem, not an H2
requirement.

## Decision

Agents are registered by the operator in a TOML file on the API and Runner
hosts (same ids, same commands). The file is deployment configuration, not
request data.

`GET /api/agents` returns id, display name, kind (`fixture`, `reference`,
`external`), and description. It does not return argv, environment, or
secrets.

`EvaluationSpec` gains optional `agent_id`. The control plane rejects unknown
ids the same way it rejects unknown `scenario_id`s. The Runner receives the
id inside the spec and resolves argv from its own copy of the registry. If
the id is missing from that copy, the Runner fails the job. The API never
sends a command string.

Initial entries:

| id | kind | Command (illustrative) |
|---|---|---|
| `fixture-agent.v1` | fixture | `python -m app.fixture_agent` |
| `reference-openai-compatible.v1` | reference | `python -m app.reference_agent` |
| `claude-code.v1` | external | adapter script; host must have `claude` |
| `openai-agents-sdk.v1` | external | adapter script (optional, H2) |
| `codex-cli.v1` | external | adapter script (optional, H2) |

Only the first two ship in H1. H2 registers the others. CI registers the
fixture agent and does not require Claude, Codex, or a GPU.

Provider URL, API key, and model for the reference agent stay in
`AGENTOPS_PROVIDER_*` on the Runner (ADR-0003). The reference agent's README
quickstart points those variables at Ollama (`http://127.0.0.1:11434/v1`,
model `qwen3:8b`). `qwen3:14b` is documented as optional for machines that
can spare the weights plus KV cache; it is not the default on 16 GB GPUs.

Adapters are thin: they translate environment variables into the upstream
CLI or SDK call, attach the MCP URL, and may forward upstream stdout as
`process_output`. They do not score, and they do not emit `step_completed`.

Replay passes the same `agent_id` as the baseline. A PolicyPatch does not
switch agents.

## Consequences

- Swapping the model or the scaffold is an operator edit plus an Experiment
  that selects an id, not a new backend.
- A hostile API client still cannot start a shell.
- The Runner host is trusted. Running untrusted agent code, or exposing the
  API beyond a trusted network, still requires the safety gate in
  `ROADMAP.md`.
- `AGENTOPS_AGENT_PROGRAM` remains a break-glass override for local
  debugging and is not the product extension point.
