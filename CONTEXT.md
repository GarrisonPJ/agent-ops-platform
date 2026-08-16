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
| Control Plane | Python-owned API and explicit operator maintenance commands. It is the only boundary allowed to read or mutate PostgreSQL state. |
| Experiment | Stable container for a task, allowlisted scenario, and selected execution mode. A policy is active only inside its experiment. |
| Retention Unit | One complete Experiment aggregate, including its Runs, RunEvents, Runner records, analyses, and policies. It is retained or deleted as a whole. |
| EvaluationSpec | Versioned, immutable execution input: run/experiment IDs, scenario, task, seed, execution mode, limits, and optional policy patch. |
| ExecutionMode | `fixture` keeps the deterministic Golden path; `provider` delegates reasoning to the Runner-configured OpenAI-compatible boundary while retaining the same allowlisted tools. |
| Run | One baseline or replay attempt with an explicit lifecycle, score, metrics, and immutable EvaluationSpec. |
| Baseline | A run without a candidate policy. The Golden baseline fails by exhausting its step budget. |
| Replay | A run derived from a baseline. It preserves scenario, task, seed, execution mode, and limits while adding one candidate policy. |
| RunnerJob | The claim and lease record that authorizes one Rust Runner to execute a run. |
| RunEvent | Durable, ordered execution fact. The unique key is `run_id + sequence`. |
| Trace | The ordered projection of persisted RunEvents shown in the UI. It is not a separate source of truth. |
| RunAnalysis | Deterministic failure dimensions, evidence, dominant type, and failure rate for one run. |
| Policy | Experiment-scoped candidate patch derived from one failed baseline and optionally validated by one replay. |
| PolicyPatch | Phase 1 patch with only `instruction_patch`, `tool_priority`, and `max_steps`. |
| Scenario | A built-in, allowlisted evaluation world with stable metadata, tool surface, fixture semantics, terminal assertions, and optional bounded `scenario_params`. |
| Scenario Registry | Control-plane allowlist of Scenarios exposed through `GET /api/scenarios`. Only registered Scenarios may appear on Experiments or Runs. |
| ScenarioAssertion | Controlled terminal scoring primitive (`tool-used`, `tool-args-match`, `tool-sequence`, `step-count`, `equals`, `contains`, `json-match`) declared on a Scenario. Assertions carry `weight`, `threshold`, and combine via `all`, `weighted`, or `any`. |
| Recorded Preview | Offline-development and regression adapter that replays Golden E2E fixtures and never implements backend business rules. |
| OperationalState | Machine-readable rollup from `GET /api/operations/state` classifying database, schema, Runner, lease, and provider faults with fixed precedence. |

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

1. The Control Plane is the only database reader and writer; explicit operator maintenance commands execute inside that boundary.
2. A RunEvent is committed before the API notifies SSE subscribers.
3. Event sequences begin at one, increase contiguously, and are idempotent on retry.
4. A lease binds one `runner_id`, `lease_id`, and `run_id`; an expired lease cannot mutate a non-terminal run.
5. Repeated event upload, replay request, and terminal completion return the existing result.
6. Baseline and replay share scenario, task, seed, execution mode, and limits. Only run identity and candidate policy differ.
7. A policy is validated only when its replay succeeds with a positive score delta.
8. An experiment has at most one active policy; activating a new one atomically supersedes the previous one.
9. Activation is always a human action. Analysis and compilation are deterministic in Phase 1.
10. API clients select an allowlisted `scenario_id` and `execution_mode`; they never provide an executable, command, provider URL, model, credential, or arbitrary code.
11. A Scenario must be registered before it can execute. Unregistered ids are rejected with a structured error.
12. `scenario_id` uses `<name>.v<N>` versioning; only Runs with the same id are score-comparable; semantic changes require a new version.
13. `scenario_params` is optional, size-bounded, and validated against Scenario metadata at Experiment and Run creation.
14. Terminal scores for registered Scenarios derive from declared ScenarioAssertions, not ad-hoc scoring functions.
15. Product events expose a concise `decision_summary`, not hidden chain-of-thought.

## Built-in scenarios

The Scenario registry currently ships three built-in Scenarios. Checkout remains the Golden reference path; the others extend the same closed loop without changing Control Plane / Runner separation.

| Scenario ID | Purpose |
|---|---|
| `checkout-api-latency.v1` | Golden checkout latency investigation (health → metrics → logs). |
| `multi-step-research.v1` | Search, fetch, and answer loop with deliberate baseline repetition. |
| `api-fault-orchestration.v1` | Retry and degrade around injected upstream 503 faults. |

Scenario metadata, tool allowlists, and contributor steps live in [docs/agents/scenario-development.md](docs/agents/scenario-development.md).

### Golden scenario (`checkout-api-latency.v1`)

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
| Control Plane | Domain state, persistence, leases, scoring, analysis, policy decisions, and explicit operator maintenance | Child process supervision |
| Rust Runner | Process group, JSONL transport, heartbeat, retry, cancel, timeout | Database access, scoring, policy logic |
| Python Agent | Deterministic fixture behavior or narrow provider orchestration for the allowlisted scenario | Run lifecycle, persistence, arbitrary tools, or arbitrary execution |
| PostgreSQL | Durable system facts | Workflow behavior |

Frontend state follows the project convention:

- server cache → RTK Query;
- cross-route UI state → Redux slice;
- page-local ephemeral state → `useState` or `useRef`.

## Phase 1 non-goals

Kubernetes, Docker socket execution, MCP, vector memory, training export, framework adapters, user-supplied provider endpoints or credentials, arbitrary command execution, accounts, multi-tenancy, billing, and automatic activation are outside the current product.

## Planned evolution

Runner recovery and the narrow OpenAI-compatible provider boundary are implemented. Phase 1.3 observability and operational hardening is complete: durable diagnostics, migration-aware API/database/Runner health signals, immutable expired-Attempt correlation, safe Provider fingerprints, executable backup/restore rehearsal, retention/redaction enforcement, and machine-readable alert classification via `/api/operations/state`. Recorded Preview remains a testing adapter, not a separate delivery track. Kubernetes, MCP, vector memory, arbitrary execution, multi-tenancy, and automatic policy activation stay deferred until a measured requirement promotes them.

Roadmap changes do not alter these domain invariants by themselves. Update this file and add an ADR before a milestone changes state ownership, recovery semantics, trust boundaries, or activation rules. See [ROADMAP.md](ROADMAP.md).

## ADR index

| ID | Decision | Status |
|---|---|---|
| [0001](docs/adr/0001-python-control-plane-rust-execution-plane.md) | Python control plane and Rust execution plane | Accepted |
| [0002](docs/adr/0002-runner-recovery.md) | Deterministic Runner recovery | Accepted |
| [0003](docs/adr/0003-openai-compatible-provider-boundary.md) | Narrow OpenAI-compatible provider boundary | Accepted |
| [0004](docs/adr/0004-data-lifecycle-retention.md) | Experiment aggregate retention in the Python Control Plane | Accepted |
| [0005](docs/adr/0005-operational-alert-classification.md) | Machine-readable operational alert classification | Accepted |
| [0006](docs/adr/0006-scenario-registry-boundary.md) | Scenario registry boundary | Accepted |
