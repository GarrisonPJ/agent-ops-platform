# ADR-0003: narrow OpenAI-compatible provider boundary

- Status: Accepted
- Date: 2026-07-26

## Context

Phase 1 previously executed only a deterministic Python checkout scenario. A
real model is useful for evaluating the same workflow, but moving provider
configuration into an API request, letting a model select arbitrary tools, or
persisting raw provider output would broaden the trust boundary and compromise
the deterministic Golden path.

## Decision

- An Experiment selects one immutable `execution_mode`: `fixture` or
  `provider`. The mode is included in each EvaluationSpec, and a Replay
  inherits the baseline mode rather than reading a mutable current setting.
- The OpenAI-compatible base URL, API key, model, timeout, and retry budget are
  Runner-only operator environment variables. The API and frontend accept no
  provider endpoint, model, or credential fields.
- The Python boundary makes bounded `/chat/completions` calls. In provider mode
  the model can choose only `check_service_health`, `query_service_metrics`,
  and `fetch_service_logs`; those calls return deterministic fixture data and
  cannot execute arbitrary code or network actions.
- Timeout and retry are bounded, and `asyncio.CancelledError` propagates to the
  Runner so normal process cancellation semantics still apply. Safe structured
  provider errors are emitted as existing `process_output` events.
- FastAPI projects only latest-attempt model identity, latency, request count,
  prompt/completion usage, and safe provider error fields into Run metrics. A
  generic failed child exit is replaced with `CODE: message` when an emitted
  provider error explains it. Credentials, endpoint values, raw request
  headers, and hidden reasoning are never persisted.
- Fixture remains the default CI and Golden E2E mode. Provider behavior is
  tested against a local fake compatible server; live calls are opt-in.
  Recorded Preview continues to replay persisted fixtures and never invokes
  provider logic.

## Consequences

- The product can compare fixture and provider-backed executions through the
  same typed Run, event, lease, score, and policy workflow.
- Missing configuration, HTTP failure, timeout, and cancellation resolve to
  existing terminal Run states instead of adding a second state machine.
- Operators configure trusted endpoints only at deployment time. Supporting
  user-supplied endpoints, side-effecting tools, broader model APIs, or
  provider-specific hidden-reasoning retention requires a new safety decision.
