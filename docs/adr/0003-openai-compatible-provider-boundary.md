# ADR-0003: narrow OpenAI-compatible provider boundary

- Status: Accepted
- Date: 2026-07-26
- Amended: 2026-08-23 for registered Scenario-specific tool surfaces
- Amended: 2026-10-17 by ADR-0007 — the provider loop moves out of the
  Scenario into the registered `reference-openai-compatible.v1` agent
- Amended: 2026-11-01 for H1-5 — bounded optional Runner-only generation
  settings replace a temporary local-model request-options proxy
- Amended: 2026-11-05 for H3 — optional Runner-only sampler `SEED`, including
  a per-Run mode so trial groups can sample independently

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
  the model can choose only the explicit tool definitions supplied by the
  selected registered Scenario. Checkout, multi-step research, and API fault
  orchestration each provide a separate allowlist and deterministic tool
  executor; those tools cannot execute arbitrary code or additional network
  actions.
- Timeout and retry are bounded, and `asyncio.CancelledError` propagates to the
  Runner so normal process cancellation semantics still apply. Safe structured
  provider errors are emitted as existing `process_output` events. (Phase 1
  in-process path. 2026-11-10 note: in the split Environment/Agent path the
  Runner discards Agent stdout; only an allowlisted `agent_error_code` is
  reported, see ADR-0009 2026-11-09 amendment.)
- FastAPI projects only latest-attempt model identity, latency, request count,
  prompt/completion usage, and safe provider error fields into Run metrics. A
  generic failed child exit is replaced with `CODE: message` when an emitted
  provider error explains it. Credentials, endpoint values, raw request
  headers, and hidden reasoning are never persisted.
- Provider telemetry may persist only model identity, bounded latency,
  request/token counters, and up to 20 SHA-256 request fingerprints. A
  provider request ID is accepted only transiently at the boundary and is
  never stored in raw form.
- Provider errors may persist only code, a fixed safe message selected from an
  explicit error-code allowlist (or a fixed generic message for unknown
  codes), retryable, bounded attempts, and an optional SHA-256 request
  fingerprint. Unknown Provider fields and arbitrary completion metrics are
  discarded before RunEvent or Run metrics persistence. For a process_output
  event with valid Provider metadata, the outer content is replaced by a fixed
  safe summary; ordinary non-Provider process output remains unchanged.
- Alembic revision `0006_runner_attempts` rewrites legacy RunEvent and Run
  metrics Provider metadata to the same allowlists and fingerprints. This
  redaction is intentionally not reversed on downgrade.
- Fixture remains the default CI and Golden E2E mode. Provider behavior is
  tested against a local fake compatible server; live calls are opt-in.
  Recorded Preview continues to replay persisted fixtures and never invokes
  provider logic.

## H1-5 amendment: explicit generation options

The installed llama.cpp endpoint needs bounded output and single-call generation
for a reproducible reference-Agent run. A temporary HTTP proxy demonstrated these
options, but is not a supported deployment dependency. Add four optional
`AGENTOPS_PROVIDER_*` settings directly to the existing low-level client:

- `MAX_TOKENS`: integer 1..32768, sent as `max_tokens`.
- `TEMPERATURE`: finite number 0..2, sent as `temperature`.
- `REASONING_EFFORT`: `none`, `minimal`, `low`, `medium`, `high`, or `xhigh`, sent
  unchanged as `reasoning_effort`; individual endpoints may support fewer values.
- `PARALLEL_TOOL_CALLS`: exact `true` or `false`, sent as a JSON boolean.

Unset/blank settings omit the corresponding request fields, preserving the
existing request shape. Invalid settings fail before a Provider request with the
existing safe configuration-error category. No arbitrary JSON override, prompt,
tool schema, response rewriting, automatic argument repair, or forced submission
is admitted. These settings remain operator-only, forwarded by the Runner to the
Agent, never accepted through Experiment/API/frontend inputs. They do not change
world rules, evaluation budgets, scoring, or success gates. Providers can reject
unsupported fields normally; do not silently retry with different semantics.

Local-model acceptance records the exact settings and real success/failure.
CI tests this boundary with fake Providers, without a GPU or external service.
One successful real-model Run plus faithfully captured failures validates H1's
integration, not model accuracy, universal compatibility, or a pass-rate claim.

## H3 amendment: sampler seed

Trial groups (ADR-0010) assume independent trials. The installed llama.cpp
endpoint pins a default sampler seed, so identical requests return identical
output even at temperature 1. Every reference-Agent trial was then a copy of
one trajectory (`docs/history/h3-pass-k-temp1-2026-11-05.md`). Operator decision
(2026-11-05): add one more optional Runner-only setting, `AGENTOPS_PROVIDER_SEED`:

- unset or blank: no `seed` field, the existing request shape;
- an integer 0..2147483647: sent unchanged as `seed` on every request;
- `per-run`: each Agent sends `seed` derived from its own `run_id`, the first
  four bytes of SHA-256(`run_id`) masked to 31 bits.

The derived seed is stable for a Run, so a recovered Attempt repeats it. It
differs between trials, because every trial is a separate Run. It is a sampling
input only. It is not the Environment seed, which stays fixed for a trial group,
and it never enters scoring, assertions or success. The same rules as the H1-5
options apply: operator-only, validated before the first request, never accepted
through Experiment/API/frontend inputs, and rejected fields surface normally.
External adapters map it where their client allows; the pi adapter passes it
through `samplingParams`.

A pinned or per-Run seed makes sampling reproducible per request only on
endpoints that honour `seed`. It does not guarantee bitwise-identical output
across hardware or runtime builds, and it does not make trials independent of
the shared prompt and Environment.

## Consequences

- The product can compare fixture and provider-backed executions through the
  same typed Run, event, lease, score, and policy workflow.
- Missing configuration, HTTP failure, timeout, and cancellation resolve to
  existing terminal Run states instead of adding a second state machine.
- Operators configure trusted endpoints only at deployment time. Supporting
  user-supplied endpoints, side-effecting tools, broader model APIs, or
  provider-specific hidden-reasoning retention requires a new safety decision.
- Adding a Scenario-specific Provider path does not expand this trust boundary
  when it reuses the bounded client, exposes only registered tools, returns
  deterministic observations, and preserves the same telemetry redaction.
