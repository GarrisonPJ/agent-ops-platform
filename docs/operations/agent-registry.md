# Agent registry

The API reads the operator-owned TOML registry from `config/agents.toml` by
default. Set `AGENTOPS_AGENT_REGISTRY` to select a deployment-specific file.
The API accepts only a registered versioned `agent_id`; when omitted, fixture
mode selects `fixture-agent.v1` and provider mode selects
`reference-openai-compatible.v1`.

`GET /api/agents` exposes only `id`, `name`, `kind`, and `description`. Commands
remain operator configuration and are never request data or public metadata.
The parser rejects malformed TOML, unknown keys, duplicate or malformed ids,
and entries outside the shared size limits before any request can create a
Run.
