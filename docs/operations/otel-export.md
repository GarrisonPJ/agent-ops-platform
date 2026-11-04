# OTel GenAI export

Optional, off by default. Decision: [ADR-0011](../adr/0011-otel-genai-export.md).
Code: `backend/app/otel_export.py`.

After a Run's terminal transaction commits, the API maps that Run's persisted
RunEvents to spans and sends one OTLP request from a background thread. The
RunEvent store stays authoritative. Export is never read back, and a failed
export only logs `OTel export failed; Run state is unaffected`. Endpoints,
headers and exception text are never logged.

## Enable

Set these on the **API** process. The Runner does not export.

| Variable | Value |
|---|---|
| `AGENTOPS_OTEL_EXPORT` | `1` to enable; anything else (or unset) keeps export off |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector base URL; `/v1/traces` is appended, e.g. `http://127.0.0.1:6006` for Phoenix |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Optional full traces URL instead, e.g. Langfuse `…/api/public/otel/v1/traces` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Optional, e.g. Langfuse `Authorization=Basic …`; keep it in a private secret source |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Unset or `http/protobuf`; other protocols are refused |

The Compose file lists API variables explicitly. To export from the Compose
stack, add these variables to the `api` service in an invocation-owned
override file; do not edit the committed Compose file for one collector.

## What is exported

One trace per terminal Run:

- Root span `invoke_agent {agent name}`: `gen_ai.operation.name=invoke_agent`,
  `gen_ai.agent.name` (registered name), `gen_ai.agent.id`,
  `gen_ai.conversation.id` = `run_id`, plus `agentops.run.status`,
  `agentops.run.kind`, `agentops.run.score`, `agentops.experiment.id`,
  `agentops.scenario.id` and, for trials, `agentops.trial_group.id` /
  `agentops.trial.index`. Status is ERROR unless the Run succeeded.
- One child per `step_completed`: `execute_tool {tool}`,
  `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`,
  `gen_ai.tool.call.id` = event sequence, `agentops.environment.kind`
  (`world`/`control`), `agentops.environment.result`. Status is ERROR unless the
  Environment result is `ok`.

Tool arguments, observations, decision summaries, tokens, Provider URLs and
credentials are not exported. The GenAI semantic conventions are still
Status: Development, so attribute names may move. A rename is a mapper and
docs change, not a RunEvent migration.
