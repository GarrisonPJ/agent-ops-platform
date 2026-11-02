# ADR-0009: Agent registry trust boundary

- Status: Accepted
- Date: 2026-10-17
- Implementation: H1 registry integrated; H2 external adapters remain future.
  This was absent from historical `8bd2ef0`.
- Amended: 2026-10-24 by [ADR-0012](0012-h1-canonical-cutover-and-control-plane-success.md)
  (canonical defaults and Control Plane-only task success).
- Amended: 2026-11-02 for H2 — the required external adapter is
  `pi-coding-agent.v1`; `claude-code.v1` becomes optional.
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
| `pi-coding-agent.v1` | external | adapter script; host must have `pi` (required, H2) |
| `openai-agents-sdk.v1` | external | adapter script (optional, H2) |
| `codex-cli.v1` | external | adapter script (optional, H2) |
| `claude-code.v1` | external | adapter script; host must have `claude` (optional, H2) |

Only the first two ship in H1. H2 registers the others. CI registers the
fixture agent and does not require pi, Claude, Codex, or a GPU.

Provider URL, API key, and model for the reference agent stay in
`AGENTOPS_PROVIDER_*` on the Runner (ADR-0003). The reference agent's README
quickstart points those variables at Ollama (`http://127.0.0.1:11434/v1`,
model `qwen3:8b`). `qwen3:14b` is documented as optional for machines that
can spare the weights plus KV cache; it is not the default. The local
closeout host has about 12 GB VRAM, not the originally assumed 16 GB.
The dated H1-5 record covers one already installed Swift/llama.cpp endpoint;
configuration guidance and that finite run set are not a hardware-fit,
accuracy, stable-pass-rate or universal tool-call compatibility result.
Environment defaults and quickstart examples use ADR-0012's canonical v2 IDs,
not the retired three v1 IDs. Missing `agent_id` still selects the fixture or
reference agent by `execution_mode` (ADR-0007); this does not preserve old-v1
world execution.

Adapters are thin: they translate environment variables into the upstream
CLI or SDK call, attach the MCP URL, and may forward upstream stdout as
`process_output`. They do not score, and they do not emit `step_completed`.
Agent-reported success and exit zero cannot determine Run success; Control Plane
alone applies ADR-0012's trusted-evidence and aggregate gate.

Replay passes the same `agent_id` as the baseline. A PolicyPatch does not
switch agents.

## H2 amendment: pi as the required external adapter

The original required adapter was Claude Code. It is a deeply customized
harness whose default model path is Anthropic's API, and its system prompt
may not fit the 8192-token local context. pi
(`@earendil-works/pi-coding-agent`) is a minimal harness. It speaks the
OpenAI-compatible API that the reference agent already uses. So H2-1 can
compare two scaffolds on the same local model.

The `pi-coding-agent.v1` adapter generates a private per-Run
`PI_CODING_AGENT_DIR`. It holds the Environment MCP server (streamable HTTP,
`direct` exposure, Bearer header interpolated from `AGENTOPS_ENV_TOKEN`) and a
`models.json` derived from `AGENTOPS_PROVIDER_*`. The capability token never
appears in argv or on disk. pi runs with extensions, skills, prompt templates,
context files and project trust disabled, keeping only built-in MCP. It runs in
an empty working directory, and only the Environment's tools are declared to the
model. The operator's personal pi configuration therefore does not reach the
Agent-under-test. The generated files are adapter output, never request data.

This amendment does not change Environment discovery. pi can call only tools
that `tools/list` returns, so H2-1 first needs an ADR-0008 amendment for
`submit_result` discovery. The adapter must not submit on the Agent's behalf.
`claude-code.v1` stays possible as optional H2-5.

## Consequences

- Swapping the model or the scaffold is an operator edit plus an Experiment
  that selects an id, not a new backend.
- A hostile API client still cannot start a shell.
- The Runner host is trusted. Running untrusted agent code, or exposing the
  API beyond a trusted network, still requires the safety gate in
  `ROADMAP.md`.
- `AGENTOPS_AGENT_PROGRAM` remains a break-glass override for local
  debugging and is not the product extension point.
