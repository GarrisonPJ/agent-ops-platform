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
CLI or SDK call and attach the MCP URL. They do not forward upstream stdout
(see the H5-era amendment: the Runner discards it). They do not score, and they do not emit `step_completed`.
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
that `tools/list` returns, so H2-1 needed the ADR-0008 H2 amendment for
`submit_result` discovery. The adapter must not submit on the Agent's behalf.
`claude-code.v1` stays possible as optional H2-5.

The registry kind vocabulary is now `fixture`, `reference` and `external` in
the Python and Rust parsers and the public metadata. `pi-coding-agent.v1` lives
in the opt-in `config/agents.external.toml`. The default registry and Runner
image stay fixture/reference only, because the image has no pi.

Spike findings fix four adapter rules. The task reaches pi on stdin, because pi
reads a message argument starting with `@` as a local file. Shared instructions
come from the same function the reference agent uses and are passed as a file.
pi exits 0 after failed Provider retries, so the adapter derives a fixed code
from pi's JSONL stop reason instead of trusting the exit code. The adapter
parses and discards that JSONL, which would otherwise count against
`max_output_bytes`, and it stops pi at an accepted submission, as the reference
loop returns.

## H5-era amendment (2026-11-09): agent-reported error code

The Runner never implemented forwarding an adapter's stdout as `process_output`;
the sentence above is corrected. In the split Environment/Agent path the Agent's
stdout and stderr are untrusted and are only counted against `max_output_bytes`.
Their content is discarded, never persisted and never uploaded.

One narrow channel is added so the Control Plane can tell why an Agent died. An
adapter prints exactly one failure code as the last line of its stderr before
exiting. For the Agent's stderr only (not its stdout, not the Environment's
streams) the Runner keeps the last complete newline-terminated line, at most 64
bytes; a longer line is discarded without being buffered. After the Agent exits,
if that line, minus a trailing `\r`, equals a code in one closed allowlist, the
Runner reports it as `agent_error_code` in the completion's execution facts.
Anything else (other text, an earlier line, an unterminated line, a longer line)
reports nothing. The allowlist is defined once in Python
(`app.agent_error_codes`) and once in Rust (`AGENT_ERROR_CODES` in the protocol
crate); a contract test keeps them equal, and an unknown value is rejected with
422. An older Runner omits the field.

The code is an Agent claim, labelled agent-reported, and is diagnostics only. It
is stored in Run `metrics` as `agent_error_code` and `agent_error_category`. It
cannot change Run status, score, success, the terminal failure kind, or the
ADR-0012 trusted-evidence gate.

The category is an explicit Python-only table, used as attribution for analysis.
The model is part of the Agent-under-test, so `provider` means infrastructure
only:

- `provider`: `PROVIDER_NOT_CONFIGURED`, `PROVIDER_CONFIGURATION_ERROR`,
  `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `PROVIDER_RATE_LIMITED`,
  `PROVIDER_HTTP_ERROR`, `PROVIDER_INVALID_RESPONSE`, `PROVIDER_UNKNOWN`,
  `PI_PROVIDER_ERROR`.
- `environment_connection`: every `MCP_*` code.
- `agent_startup` (the Agent never got to act): `INVALID_SPEC`, `PI_NOT_FOUND`.
- `agent_behavior` (the Agent's or model's own outcome): `PROVIDER_STEP_LIMIT`,
  `PROVIDER_UNSUPPORTED_TOOL`, `PROVIDER_SUBMIT_REQUIRED`,
  `PROVIDER_SUBMIT_INVALID`, `PI_STOP_LENGTH`, `PI_SUBMIT_REQUIRED`,
  `PI_EXIT_NONZERO`, `PI_ABORTED`, `AGENT_FAILED`, `AGENT_TIMEOUT`,
  `AGENT_CANCELLED`.

This category is distinct from the legacy `ProviderFailureKind` and
`TerminalFailureKind` taxonomy, which counted `STEP_LIMIT` and `UNSUPPORTED_TOOL`
as provider failures for the legacy in-process provider path; that path is
unchanged.

The failure analyzer reads the code for a failed Run only. `provider` becomes
`provider_error`; `environment_connection` and `agent_startup` become execution
evidence; `agent_behavior` adds no evidence, so assertion and budget evidence
stand. Only the first three categories stop a zero-step Run from being blamed on
planning, so a model that picks a disallowed tool at step 0 still gets
`tool_selection_error`. `failure-analyzer.v3` output is unchanged for
trajectories without `agent_error`; the new key only adds input, so the revision
stays v3.

## Consequences

- Swapping the model or the scaffold is an operator edit plus an Experiment
  that selects an id, not a new backend.
- A hostile API client still cannot start a shell.
- The Runner host is trusted. Running untrusted agent code, or exposing the
  API beyond a trusted network, still requires the safety gate in
  `ROADMAP.md`.
- `AGENTOPS_AGENT_PROGRAM` remains a break-glass override for local
  debugging and is not the product extension point.
