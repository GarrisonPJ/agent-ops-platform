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
