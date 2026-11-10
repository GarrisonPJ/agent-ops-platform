# ADR-0011: OpenTelemetry GenAI export

- Status: Accepted
- Date: 2026-10-17
- Implementation: Phase 2 H4, integrated: `backend/app/otel_export.py`, off
  unless `AGENTOPS_OTEL_EXPORT=1` (see `docs/learn/07-otel-mapping.md`). Not in
  code at the historical `8bd2ef0`.
- Depends on: [ADR-0008](0008-environment-mcp-server-and-env-side-capture.md)

## Context

Langfuse and Phoenix already store and render traces. Rebuilding that UI is
out of scope. Both accept OTLP/HTTP (`/api/public/otel/v1/traces` and
`:6006/v1/traces`). The GenAI semantic conventions
(`open-telemetry/semantic-conventions-genai`) are Status: Development. As of
2026-10-17 that repository has no release tag; attribute names can still
move. OpenInference is the schema many tools actually index.

Our `RunEvent` log is the source of truth for scores, leases, and recovery.
An exporter that became a second store would fork the product.

## Decision

After a Run reaches a terminal state, an optional exporter maps persisted
events to spans:

- Root span `invoke_agent {agent name}`, `gen_ai.operation.name=invoke_agent`,
  `gen_ai.agent.name` from the registered agent, `gen_ai.conversation.id` =
  `run_id`.
- One child span per `step_completed`: `execute_tool {tool name}`,
  `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`,
  `gen_ai.tool.call.id` = the event sequence.

Export is off unless `AGENTOPS_OTEL_EXPORT=1`. Destination and protocol come
from the standard `OTEL_EXPORTER_OTLP_ENDPOINT` and
`OTEL_EXPORTER_OTLP_PROTOCOL` variables. The control plane performs the
export; the Runner does not open a second telemetry channel.

The mapping document (`docs/learn/07-otel-mapping.md`, written in H4) pins
the `semantic-conventions-genai` commit it was checked against and states
that the convention is Development. Attribute renames are a docs-and-mapper
change, not a RunEvent migration.

Export is lossy and is never read back. Failure to export does not change
Run status. Spans omit credentials, provider URLs, and hidden reasoning,
same as Run metrics (ADR-0003).

## Consequences

- A local Phoenix or Langfuse can show the same tool trace without this repo
  growing a trace explorer.
- We do not claim conformance to a stable standard that does not exist yet.
- H4 can slip without blocking H1–H3 or the README quickstart.
