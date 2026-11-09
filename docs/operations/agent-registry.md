# Agent registry

The API reads the operator-owned TOML registry from `config/agents.toml` by
default. In the Compose images the path is explicit: `/app/config/agents.toml`
for the API and `/etc/agentops/agents.toml` for the Runner. Set
`AGENTOPS_AGENT_REGISTRY` to select a deployment-specific file; do not rely on a
source-tree-relative default in a deployed image.
The API accepts only a registered versioned `agent_id`; when omitted, fixture
mode selects `fixture-agent.v1` and provider mode selects
`reference-openai-compatible.v1`.

`GET /api/agents` exposes only `id`, `name`, `kind`, and `description`. Commands
remain operator configuration and are never request data or public metadata.
The Runner image's normal command is the registered fixture Agent; the
`AGENTOPS_AGENT_PROGRAM/ARGS` environment override remains operator-only
break-glass debugging for omitted wire agent IDs, never an API request field.
Explicit registered IDs win; unknown explicit IDs fail closed. Since the CP
materializes defaults into explicit IDs, use a dedicated operator registry
for controlled recovery, not an override that cannot win dispatch. The Runner's per-Run
capability token handoff uses a mode-0700 directory and is removed only after
the Control Plane's terminal acknowledgment.
Compose mounts the named `phase1-token-handoff` volume at
`/run/agentops/token-handoff` so a replacement Runner can reuse the record.
The image pre-creates this directory for uid 10001 with mode 0700; unsafe
existing ownership/permissions fail closed. Static Compose validity and host
process recovery do not prove actual image/volume initialization or replacement.

`config/agents.recovery.toml` is gate-only operator configuration. The recovery
overlay selects it on both API and Runner, and the gate explicitly requests
`recovery-fixture.v1`. It is absent from the normal registry, not a new external
adapter or H2 extension. Both services are restored to their default registry
after the controlled gate. Child environment variables are deliberately
allowlisted; the recovery marker uses its fixed local path.

The parser rejects malformed TOML, unknown keys, duplicate or malformed ids,
and entries outside the shared size limits before any request can create a
Run.

## External Agent: pi (H2-1)

`config/agents.external.toml` is an opt-in registry for a host Runner that has
pi installed. It lists the fixture and reference Agents plus
`pi-coding-agent.v1` (kind `external`, command `python -m app.pi_agent`). The
default `config/agents.toml` and the Runner image do not include pi. Set
`AGENTOPS_AGENT_REGISTRY` to this file on both API and Runner, then select the
Agent with provider execution mode.

Prerequisites on the Runner host:

- pi `1.3.0` (`npm install -g @earendil-works/pi-coding-agent@1.3.0`) and its
  Node (>= 22.19) on the Runner's `PATH`. The Runner passes only `PATH`,
  `AGENTOPS_PROVIDER_*` and the per-Run variables to Agents.
- The same `AGENTOPS_PROVIDER_*` settings as the reference Agent. The adapter
  maps base URL, model, API key, `MAX_TOKENS`, `TEMPERATURE`,
  `REASONING_EFFORT` and `PARALLEL_TOOL_CALLS` into pi's `models.json`. pi
  applies its own request timeout and retries (three attempts); the
  reference Agent's `TIMEOUT_MS` and `MAX_RETRIES` are not mapped. The Run
  timeout still bounds the whole Agent.

`app.pi_agent` writes a private per-Run `PI_CODING_AGENT_DIR` holding
`mcp.json`, `models.json` and the shared system instructions, and removes it on
exit. The capability token and API key reach pi only as environment variables
interpolated by pi; they are not in argv or files. pi runs in an empty working
directory with extensions (except built-in MCP), skills, prompt templates,
context files, themes, project trust, sessions and startup network traffic
disabled. `--tools 'mcp__env__*'` declares only the Environment's tools.

The task is written to pi's stdin, never passed as an argument, because pi
treats an argument starting with `@` as a file to read. pi's JSONL is parsed
and discarded rather than forwarded, so it does not count against
`max_output_bytes`. The adapter stops pi after an accepted `submit_result`,
as the reference loop does. On failure it prints exactly one fixed code as the
last stderr line, which the Runner reports only if it is on the closed allowlist
(ADR-0009 H5-era amendment; diagnostics, never a Run outcome):
`PI_NOT_FOUND`, `PI_PROVIDER_ERROR` (pi exits 0 after failed retries),
`PI_SUBMIT_REQUIRED`, `PI_EXIT_NONZERO`, `PI_ABORTED`, `PI_STOP_LENGTH`,
`AGENT_TIMEOUT` or `AGENT_CANCELLED`. A zero exit is not Run success. pi never
calls `report_usage`, so token and context figures stay unknown for pi Runs.
