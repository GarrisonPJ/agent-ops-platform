# AgentOps Domain Context

AgentOps is a focused workbench for evaluating and improving a tool-using AI agent:

```text
Experiment → Baseline → Trace → Analysis → Candidate Policy
           → Replay → Comparison → Human Activate or Reject
```

This file defines the vocabulary and invariants of the current Phase 1 + H1 integration. Environment/Agent separation is implemented; terms explicitly marked future belong to H2–H5 and are not current capabilities. ADR acceptance records a decision, not release acceptance. Milestones: [ROADMAP.md](ROADMAP.md); the single executable task list: [phase2-tasks.md](docs/agents/phase2-tasks.md).

## Domain vocabulary

| Term | Definition |
|---|---|
| Control Plane | Python-owned API and explicit operator maintenance commands. It is the only boundary allowed to read or mutate PostgreSQL state. |
| Experiment | Stable container for a task, allowlisted scenario, and selected execution mode. A policy is active only inside its experiment. |
| Retention Unit | One complete Experiment aggregate, including its Runs, RunEvents, Runner records, analyses, policies, and RegressionCases. It is retained or deleted as a whole. |
| EvaluationSpec | Versioned, immutable execution input: run/experiment IDs, scenario, task, seed, execution mode, limits, and optional policy patch. |
| ExecutionMode | `fixture` keeps the deterministic Golden path; `provider` delegates reasoning to the Runner-configured OpenAI-compatible boundary while retaining the same allowlisted tools. |
| Run | One baseline or replay attempt with an explicit lifecycle, score, metrics, and immutable EvaluationSpec. |
| Baseline | A run without a candidate policy. The Golden baseline fails by exhausting its step budget. |
| Replay | A run derived from a baseline. It preserves scenario, task, seed, execution mode, and limits while adding one candidate policy. |
| RunnerJob | The claim and lease record that authorizes one Rust Runner to execute a run. |
| RunEvent | Durable, ordered execution fact. The unique key is `run_id + sequence`. |
| Trace | The ordered projection of persisted RunEvents shown in the UI. It is not a separate source of truth. |
| RunAnalysis | Deterministic failure dimensions, evidence, dominant type, and failure rate for one run. |
| RegressionCase | A failed or timed-out Run preserved as a regression asset with its scenario, seed, parameters, execution mode, and reproducibility boundary. |
| Policy | Experiment-scoped candidate patch derived from one failed baseline and optionally validated by one replay. |
| PolicyPatch | Phase 1 patch with only `instruction_patch`, `tool_priority`, and `max_steps`. |
| Scenario | Phase 1 name of an Environment (ADR-0007). Protocol v1 keeps the field `scenario_id`. A built-in, allowlisted evaluation world with stable metadata, tool surface, fixture semantics, terminal assertions, and optional bounded `scenario_params`. |
| Environment | (H1 implemented) Successor of Scenario: task, tools, deterministic state/observations, seed, fault schedule, limits, terminal `check()` with trajectory + outcome assertions; identity `<name>.v<N>`. |
| Agent-under-test | Registered executable receiving task prompt + Environment endpoint; H1 ships `fixture` and `reference`. H2 adds the `external` kind; `pi-coding-agent.v1` is in the opt-in `config/agents.external.toml`. |
| Agent Registry | (H1 implemented) Operator-side TOML; `GET /api/agents` returns metadata only; the Runner refuses unknown ids. |
| Environment Server | (H1 implemented) `app.env_server`: MCP Streamable HTTP on loopback, per-Run token, env-side `step_completed` capture; reserved tools `submit_result` and `report_usage`. |
| Trial Group | (Future H3) `k` Runs sharing one spec except `run_id` / `trial_index`; Environment seed fixed across trials. |
| pass^k | (Future H3) Unbiased estimator `C(c,k) / C(n,k)` over completed trials in a group; published metrics state `k`. |
| Calibration gate | (Implemented) Six controlled contracts regression-testing the analyzer in CI; mechanics, not accuracy. |
| Agreement study | (Future H5) Author-labeled sample of real Runs vs analyzer output; published confusion matrix and stated biases. |
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
16. A RegressionCase preserves the source Run's execution facts; fixture cases are deterministic and replay-eligible, while provider cases retain an explicit non-reproducibility boundary and do not gate activation.
17. Policy activation remains a human action and is blocked until every replay-eligible RegressionCase succeeds against the candidate Policy.
18. RegressionCase creation is database-unique per source Run; concurrent creators converge on the same persisted case and unrelated integrity errors remain visible.
19. Policy replay uses an atomic database claim for `replay_run_id`; concurrent requests converge on one Replay Run and do not duplicate the associated RunnerJob.
20. Terminal completion uses compare-and-set against the observed Run status; only the winner performs terminal scoring, analysis, and candidate-policy side effects.
21. Heartbeat, event persistence, and completion fence the RunnerJob lease with a row lock; an expired or recovered lease cannot write a later Attempt's state. A terminal RegressionCase replay is never reused as another Policy's replay gate.
22. Activation serializes Policies for one Experiment while preserving the one-active-Policy invariant; startup and authentication resolve one Runner token snapshot, and Compose requires an explicit token.

## Implemented H1 invariants

23. Clients select a registered `agent_id`; commands exist only in the operator registry. The Runner refuses unregistered ids and never receives a command from the API.
24. For any agent, `step_completed` is emitted by the Environment Server from observed tool calls, never accepted from the agent.
25. Agent-internal signals that are not reported are recorded as unknown dimensions, never inferred.
29. H1 makes the one-time incompatible canonical cutover in ADR-0012: the three legacy worlds become v2 defaults, including omitted-ID API/UI/quickstart paths; their v1 execution registrations and coupled loops retire. `order-support.v1` and the generic semantic-version identity rule remain unchanged. No aliases, migration, rescoring, historical replay preservation, cross-version comparison, or automatic data cleanup are included.
30. Control Plane alone determines task success: valid submission, successful check, sufficient valid required trusted evidence, registered suite aggregate >= version-fixed 1.0, and no abnormal execution failure. Missing/invalid required evidence, failed check, no valid submit, Agent abnormal/nonzero exit, or unexpected Environment failure before terminal completion cannot succeed. Runner-requested teardown of a healthy Environment after valid submission, successful check, and necessary evidence capture is planned lifecycle, not abnormal execution failure, including SIGTERM then SIGKILL after the shared two-second grace; H1-3 distinguishes it from unexpected failure. Neither child must exit naturally with zero. This exception cannot override those failure gates, cancellation, timeout, output-limit failure, or lease fencing; terminal events and completion agree. Exit zero, EOF, readiness, and submission/check markers alone never establish success. Preserve ALL=min, ANY=max, WEIGHTED=weighted mean and per-assertion thresholds; neither universal `all(item.passed)` nor penalized quality score is the success gate. Agent/check-reported scores or status are not authoritative; Rust only supervises/relays.
31. Control Plane scores latest-Attempt persisted world attempts including refusals, excluding reserved controls. Rejections project as `rejected_tool`/`failed` and cannot satisfy named used/args/sequence assertions; valid-only Environment history is not the scoring trajectory. Final outcomes use bounded trusted Environment-owned final-check facts, validated against registered expectations, never Agent-supplied expectations or scores. Exactly-once order proof comes from the final ledger; missing usage stays unknown and reports remain Agent-reported. The accepted final-check tuple contains only Environment-owned `scenario_id` and `final_observation`; integration acceptance remains separate.

## Future stage invariants (not implemented)

The original numbering is retained for ADR references. These do not describe current functionality.

26. H3: trials in a group share the EvaluationSpec except `run_id` and `trial_index`, with a fixed Environment seed.
27. H3: group metrics use the unbiased pass^k estimator; published numbers state `k`. Validation for `k > 1` requires non-decreasing pass^k and positive mean score delta (extends invariant 7).
28. H4: OTel export is a lossy projection; RunEvent remains authoritative and export is never read back.

## Built-in scenarios

The integration registry now uses the three canonical v2 defaults from [ADR-0012](docs/adr/0012-h1-canonical-cutover-and-control-plane-success.md), plus `order-support.v1`. Their retired v1 IDs are rejected, not aliases. Production dispatch selects an independent fixture/reference Agent and a separate Environment process. Checkout remains the Golden reference path; order-support uses the stateful world and independent MCP Agents. This describes the current integration payload, not whole-H1 acceptance or a historical data upgrade.

| Scenario ID | Purpose |
|---|---|
| `checkout-api-latency.v2` | Golden checkout latency investigation (health → metrics → logs). |
| `multi-step-research.v2` | Search, fetch, and answer loop with deliberate baseline repetition. |
| `api-fault-orchestration.v2` | Retry and degrade around injected upstream 503 faults. |
| `order-support.v1` | Stateful in-memory order support; refund exactly once after shipping fails. |

Scenario metadata, tool allowlists, and contributor steps live in [docs/agents/scenario-development.md](docs/agents/scenario-development.md).

### Golden scenario (`checkout-api-latency.v2`)

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
| Environment Server (H1) | Tools, deterministic observations, fault schedule, assertions, env-side capture, step budget | Run lifecycle, persistence, agent decisions |
| Agent-under-test (H1) | Decisions about which tool to call | Persistence, scoring, lifecycle, or emitting trusted events |
| PostgreSQL | Durable system facts, including RegressionCases and their replay references | Workflow behavior |

Frontend state follows the project convention:

- server cache → RTK Query;
- cross-route UI state → Redux slice;
- page-local ephemeral state → `useState` or `useRef`.

## Non-goals

Kubernetes, Docker socket execution, containerised agents (deferred to the safety gate), vector memory, training export, user-supplied provider endpoints or credentials via the API, arbitrary command execution, accounts, multi-tenancy, billing, automatic activation, LLM-as-judge, new trace-UI features, annotation queues, and dataset management are outside the current product scope.

MCP is used only as the loopback Environment transport (ADR-0008), not as an open tool bus. Agent adapters are registered operator-side (ADR-0009).

## Current evolution boundary

Phase 1 hosted CI is historical evidence at `8bd2ef0`. H1 now integrates split supervision, registry dispatch, canonical v2 worlds, trusted final checks, CP-owned success and validated authoritative ACKs (ADR-0007/0008/0009/0012). ADR-0013 limits analyzer-v3 diagnostic adaptations; ADR-0014 governs genuine Recorded Preview normalization. The two Runner closeout P2 fixes have scoped independent review, affected regressions and exact-payload parent evidence. H1 local engineering and real-reference-Agent acceptance are complete: direct Windows llama.cpp Runs on fresh PostgreSQL include research/order success and preserved checkout/fault failure. Bounded optional Runner-only generation settings are defined in ADR-0003; no temporary proxy is needed. Standard-image/volume, host, hosted-CI and live-model evidence remain distinct. No model installation or Windows/network/firewall change was needed. No historical rescoring or existing-data reset/deletion is authorized. Human Policy activation and semantic identity remain unchanged. See [acceptance boundaries](docs/operations/system-acceptance.md).

Roadmap changes do not alter domain invariants by themselves. Update this file and add an ADR before a milestone changes state ownership, recovery semantics, trust boundaries, or activation rules.

## ADR index

| ID | Decision | Status |
|---|---|---|
| [0001](docs/adr/0001-python-control-plane-rust-execution-plane.md) | Python control plane and Rust execution plane | Accepted |
| [0002](docs/adr/0002-runner-recovery.md) | Deterministic Runner recovery | Accepted |
| [0003](docs/adr/0003-openai-compatible-provider-boundary.md) | Narrow OpenAI-compatible provider boundary (amended 2026-10-17) | Accepted |
| [0004](docs/adr/0004-data-lifecycle-retention.md) | Experiment aggregate retention in the Python Control Plane | Accepted |
| [0005](docs/adr/0005-operational-alert-classification.md) | Machine-readable operational alert classification | Accepted |
| [0006](docs/adr/0006-scenario-registry-boundary.md) | Scenario registry boundary (amended 2026-10-17) | Accepted |
| [0007](docs/adr/0007-environment-agent-split.md) | Environment / Agent-under-test split | Accepted |
| [0008](docs/adr/0008-environment-mcp-server-and-env-side-capture.md) | Environment MCP server and env-side capture | Accepted |
| [0009](docs/adr/0009-agent-registry-trust-boundary.md) | Agent registry trust boundary | Accepted |
| [0010](docs/adr/0010-trial-groups-and-pass-k.md) | Trial groups and pass^k | Accepted |
| [0011](docs/adr/0011-otel-genai-export.md) | OpenTelemetry GenAI export | Accepted |
| [0012](docs/adr/0012-h1-canonical-cutover-and-control-plane-success.md) | H1 canonical cutover and Control Plane success contract | Accepted |
| [0013](docs/adr/0013-h1-analyzer-v3-canonical-diagnostics.md) | Narrow H1 analyzer-v3 diagnostics | Accepted |
| [0014](docs/adr/0014-h1-recorded-preview-canonical-events.md) | Canonical Recorded Preview event provenance and chronology | Accepted |
