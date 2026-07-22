# AgentOps Domain Context

AgentOps is a focused workbench for evaluating and improving a tool-using AI agent:

```text
Experiment → Baseline → Trace → Analysis → Candidate Policy
           → Replay → Comparison → Human Activate or Reject
```

This file defines the shared vocabulary and invariants for the implemented Phase 1 loop. Phase 1 is complete and verified. Product and implementation changes must preserve these invariants or justify a new ADR; future milestones are tracked in [ROADMAP.md](ROADMAP.md).

## Domain vocabulary

| Term | Definition |
|---|---|
| Experiment | Stable container for a task and allowlisted scenario. A policy is active only inside its experiment. |
| EvaluationSpec | Versioned, immutable execution input: run/experiment IDs, scenario, task, seed, limits, and optional policy patch. |
| Run | One baseline or replay attempt with an explicit lifecycle, score, metrics, and immutable EvaluationSpec. |
| Baseline | A run without a candidate policy. The Golden baseline fails by exhausting its step budget. |
| Replay | A run derived from a baseline. It preserves scenario, task, seed, and limits while adding one candidate policy. |
| RunnerJob | The claim and lease record that authorizes one Rust Runner to execute a run. |
| RunEvent | Durable, ordered execution fact. The unique key is `run_id + sequence`. |
| Trace | The ordered projection of persisted RunEvents shown in the UI. It is not a separate source of truth. |
| RunAnalysis | Deterministic failure dimensions, evidence, dominant type, and failure rate for one run. |
| Policy | Experiment-scoped candidate patch derived from one failed baseline and optionally validated by one replay. |
| PolicyPatch | Phase 1 patch with only `instruction_patch`, `tool_priority`, and `max_steps`. |
| Recorded Preview | Offline-development and regression adapter that replays Golden E2E fixtures and never implements backend business rules. |

## State machines

Run:

```text
queued → claimed → running
queued → cancelled
claimed | running → cancelling → cancelled
claimed | running → succeeded | failed | cancelled | timed_out
```

Policy:

```text
candidate → replaying → validated
replaying → candidate (replay fails or does not improve the score)
candidate | validated → rejected
validated → active
active → superseded
```

Terminal run states and rejected/superseded policies never transition again.

Recovery transitions are:

claimed/running/cancelling -- expired lease --> queued with a new Attempt
claimed/running/cancelling -- exhausted attempts --> failed or cancelled

## Core invariants

1. FastAPI is the only database reader and writer.
2. A RunEvent is committed before the API notifies SSE subscribers.
3. Event sequences begin at one, increase contiguously, and are idempotent on retry.
4. A lease binds one `runner_id`, `lease_id`, and `run_id`; an expired lease cannot mutate a non-terminal run.
5. Repeated event upload, replay request, and terminal completion return the existing result.
6. Baseline and replay share scenario, task, seed, and limits. Only run identity and candidate policy differ.
7. A policy is validated only when its replay succeeds with a positive score delta.
8. An experiment has at most one active policy; activating a new one atomically supersedes the previous one.
9. Activation is always a human action. Analysis and compilation are deterministic in Phase 1.
10. API clients select an allowlisted `scenario_id`; they never provide an executable, command, provider, or arbitrary code.
11. Product events expose a concise `decision_summary`, not hidden chain-of-thought.

## Golden scenario

Scenario ID: `checkout-api-latency`

Available tools:

- `check_service_health`
- `query_service_metrics`
- `fetch_service_logs`

The baseline repeats the same log call six times and fails. Analysis reports Planning and Budget evidence. The candidate prohibits identical calls and prioritizes health, metrics, then evidence-backed logs. Replay succeeds in three steps and can then be activated by a person.

## Protocol

Python Pydantic models in `backend/app/phase1_schemas.py` own protocol v1. Generated JSON Schemas and Golden fixtures live under `contracts/v1`. Rust Serde structs validate the same fixtures.

Phase 1 event types:

```text
run_started
step_completed
process_output
run_completed
run_failed
run_cancelled
```

SSE clients reconnect with `after=<lastSequence>`. The API replays later persisted events before subscribing to new notifications.

## Component boundaries

| Component | Owns | Must not own |
|---|---|---|
| React | Workflow UI and client-side presentation state | Scoring, analysis, policy compilation, run state machine |
| FastAPI | Domain state, persistence, leases, scoring, analysis, policy decisions | Child process supervision |
| Rust Runner | Process group, JSONL transport, heartbeat, retry, cancel, timeout | Database access, scoring, policy logic |
| Python Agent | Deterministic scenario behavior | Run lifecycle or persistence |
| PostgreSQL | Durable system facts | Workflow behavior |

Frontend state follows the project convention:

- server cache → RTK Query;
- cross-route UI state → Redux slice;
- page-local ephemeral state → `useState` or `useRef`.

## Phase 1 non-goals

Kubernetes, Docker socket execution, MCP, vector memory, training export, framework adapters, real model providers, arbitrary command execution, accounts, multi-tenancy, billing, and automatic activation are outside the current product.

## Planned evolution

Runner recovery is implemented. The next milestones are an OpenAI-compatible provider and observability/operational hardening. Recorded Preview remains a testing adapter, not a separate delivery track. Kubernetes, MCP, vector memory, arbitrary execution, multi-tenancy, and automatic policy activation stay deferred until a measured requirement promotes them.

Roadmap changes do not alter these domain invariants by themselves. Update this file and add an ADR before a milestone changes state ownership, recovery semantics, trust boundaries, or activation rules. See [ROADMAP.md](ROADMAP.md).

## ADR index

| ID | Decision | Status |
|---|---|---|
| [0001](docs/adr/0001-python-control-plane-rust-execution-plane.md) | Python control plane and Rust execution plane | Accepted |
| [0002](docs/adr/0002-runner-recovery.md) | Deterministic Runner recovery | Accepted |
