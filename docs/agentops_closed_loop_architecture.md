# AgentOps Closed-Loop Architecture

Status: Phase 1 complete; Phase 2 — Harness in progress
Last reviewed: 2026-10-17

## System goal

AgentOps evaluates one agent configuration, explains a failed run, proposes a bounded policy change, replays the same evaluation input, and leaves activation to a person:

```text
Experiment → Baseline → Persisted Trace → Failure Analysis
           → Candidate Policy → Replay → Comparison → Human Activate or Reject
```

The current system is a focused evaluation control plane, not a generic orchestration platform. `backend/app/main.py` exports the Phase 1 application from `phase1_main.py`. Pre-Phase 1 executor, auto-replay, memory, and export modules have been removed from the tree. An earlier open MCP tool bus was removed with them. Phase 2 brings MCP back only as a loopback Environment transport ([ADR-0008](adr/0008-environment-mcp-server-and-env-side-capture.md)), which is a different boundary. Future work is tracked in [ROADMAP.md](../ROADMAP.md).

## Implemented Phase 1

| Capability | Current implementation | Source of truth |
|---|---|---|
| Domain workflow | Experiment, baseline/replay Run, analysis, RegressionCase, candidate/active Policy | `backend/app/phase1_service.py` |
| API and state transitions | Typed FastAPI routes and explicit errors | `backend/app/phase1_main.py` |
| Persistence | PostgreSQL models and Alembic migration | `backend/app/phase1_models.py`, `backend/alembic/` |
| Execution protocol | Versioned EvaluationSpec and EventEnvelope | `backend/app/phase1_schemas.py`, `contracts/v1/` |
| Process supervision | Rust claim/heartbeat loop, process groups, timeout, cancellation, bounded JSONL | `runner/crates/agentops-runner/` |
| Python agent | Registered Scenario handlers with deterministic fixture and narrow Provider paths | `backend/app/scenarios/`, `backend/app/phase1_provider.py` |
| Product workflow | Experiments, Trace, deterministic Analysis, persisted comparisons, regression replay, activate/reject | `frontend/src/Phase1App.tsx` |
| Offline regression adapter | Recorded persisted facts validated through the frontend contract | `frontend/src/services/recorded/` |
| Evaluation calibration | Read-only analysis-blind packets and a six-contract mechanics gate. Not an accuracy claim. Agreement-study rules are in the calibration doc | `backend/app/calibration_review.py`, `backend/app/calibration_report.py`, `docs/evaluation-calibration.md` |
| End-to-end verification | Real Compose stack, Golden loop, and Runner recovery fault test | `infra/docker/docker-compose.phase1.yml`, `scripts/golden_e2e.py`, `scripts/recovery_e2e.py` |

## Component ownership

```mermaid
flowchart LR
    UI["React + RTK Query"] -->|"typed HTTP / SSE"| API["FastAPI control plane"]
    API -->|"only database writer"| DB[("PostgreSQL")]
    Runner["Rust Runner"] -->|"claim / heartbeat / events / complete"| API
    Runner -->|"EvaluationSpec on stdin<br/>EventEnvelope JSONL on stdout"| Agent["Allowlisted Python agent"]
    Agent -->|"opt-in /chat/completions"| Provider["OpenAI-compatible provider"]
```

- React owns workflow presentation and client state. It does not score, compile policies, or advance server state machines.
- FastAPI owns domain state, leases, persistence, scoring, analysis, and policy decisions. It does not supervise child processes.
- The Rust Runner owns process lifecycle, bounded transport, retry, cancellation, and timeout. It does not access PostgreSQL or implement policy logic.
- PostgreSQL stores durable facts; SSE is a projection of committed RunEvents.
- The Python agent consumes an immutable EvaluationSpec and emits protocol events. In provider mode it may call only the operator-configured OpenAI-compatible endpoint and the selected Scenario's explicit tool definitions; it does not own Run lifecycle state.

## Closed-loop behavior

1. A user creates an Experiment with a registered, versioned Scenario and either fixture or provider execution mode.
2. FastAPI creates a queued baseline Run and RunnerJob.
3. A Rust Runner claims the Job under a lease and supervises the fixture or provider-backed Python agent.
4. FastAPI validates and commits ordered events before notifying SSE clients.
5. Terminal processing calculates score and deterministic failure analysis with controlled failure types and metric directions.
6. A failed baseline produces a bounded candidate PolicyPatch.
7. A person may preserve a failed Run as a RegressionCase. Fixture cases are deterministic replay gates; Provider cases record the external-model reproducibility boundary and remain analysis-only.
8. A person starts Replay; the same scenario, task, seed, and limits are retained while the candidate patch is added. Improve compares persisted score, analysis, trajectory, latency, and token facts.
9. A successful positive Replay validates the candidate. The first activation request queues any missing eligible RegressionCase replays without activating the Policy.
10. A person reviews the regression results and explicitly activates again or rejects the Policy. No policy activates automatically.

The registry contains `checkout-api-latency.v1`, `multi-step-research.v1`, and
`api-fault-orchestration.v1`. Each Scenario retains its own allowlisted tools,
fixture semantics, and terminal assertions within this same workflow. The
[Scenario contributor guide](agents/scenario-development.md) describes their
versioning and assertion contract; `scripts/golden_e2e.py` covers all three.

## Reliability properties already enforced

- `run_id + sequence` is unique and duplicate event uploads are idempotent.
- Repeated replay requests and repeated terminal completion return the existing result.
- RegressionCase creation is unique per source Run; concurrent creators converge on one persisted case.
- Policy replay atomically claims `replay_run_id`; concurrent requests converge on one Replay Run and RunnerJob.
- Terminal completion compares and sets the observed Run status; only the winner executes terminal scoring, analysis, and candidate-policy side effects.
- Lease-bearing RunnerJob writes lock the lease row; recovery cannot interleave an old heartbeat, event upload, or completion with a new Attempt.
- Each Policy must pass eligible RegressionCases against its own patch; a terminal replay owned by another Policy is replaced before activation.
- Competing Policy activations lock the Experiment row before superseding and activating, preserving one active Policy without an IntegrityError response.
- Runner endpoints authenticate the Runner and validate lease ownership.
- Expired leases cannot append events or mutate non-terminal Runs.
- Commands and arguments remain separate; API clients cannot submit an executable or shell command.
- JSONL line and combined-output limits are bounded.
- Linux/WSL child processes run in their own process group and receive SIGTERM before SIGKILL.
- Protocol v1 fixtures are validated by Python and Rust; frontend live and recorded paths share Zod schemas.
- Provider metadata is projected from latest-attempt events into Run metrics; credentials, endpoint values, and hidden reasoning are not persisted.
- Regression replays use normal Runs and RunnerJobs but cannot overwrite the Policy's primary replay score delta or validation status.
- Retention treats RegressionCases as part of the Experiment aggregate and blocks cross-aggregate references before deletion.
- Calibration review packets are analysis-blind: they omit scores, stored analysis, and raw Provider output, while retaining enough execution metadata that they must not be presented as outcome-blind or double-blind evidence.
- Recorded Preview replays persisted facts for offline development and regression testing; it does not reproduce backend business logic or provider calls.

## Known boundary

Runner recovery, registered Scenarios, and the narrow Provider boundary are implemented. Provider mode remains deliberately constrained: endpoint, model, and credential are Runner-only operator configuration; the model can choose only the selected Scenario's explicit tool definitions; live calls are opt-in; and CI uses deterministic fixtures plus local fake compatible servers.

Deployment stays a single machine or trusted team network. Phase 2 (below) splits Environment from Agent-under-test; it does not add throughput or multi-user collaboration. Tasks: [phase2-tasks.md](agents/phase2-tasks.md).

The [roadmap](../ROADMAP.md) and [calibration procedure](evaluation-calibration.md)
record dated verification evidence. At the earlier standard-image acceptance on
2026-10-15, the standard Runner build completed and an isolated standard
four-service stack passed all three Golden
Scenarios and Runner kill/replacement recovery. Runner/API Python hashes matched
the recorded source snapshot for that acceptance run, with no source mounts. Base images and API/Web layers used
Docker cache; Runner Rust compilation and pinned Python dependency installation
completed before the final source refresh reused those layers. On commit `8bd2ef0`,
GitHub Actions passed all six jobs: 265 backend tests (including PostgreSQL
checks, no skips), 34 frontend unit tests, 2 recorded E2E tests, and 15 Rust
runner tests. The host-run PostgreSQL calibration gate also passes real dump
restoration and strict-report comparison, normalizing only the two binding
file-digest prefixes. The [local validation runbook](operations/reliability-and-local-validation.md)
records toolchain, cache, proxy, and build boundaries.

The current six-case controlled capture supplements the fixture sample with
injected context pressure/low-use/unknown measurements and loopback Fake
Provider success/timeout/invalid-response cases. It uses authenticated ASGI
Runner API persistence, independently of the standard-stack verification above;
it does not exercise Rust supervision or external models. Zero-step Provider
execution failures now project their execution evidence into dimensions and
dominant; this semantic fix uses `failure-analyzer.v2`, requiring new strict
packets rather than relabeling historical v1 reports.

The bound report remains provisional engineering evidence with
`publishable_accuracy=false`. Agent-authored input contracts and correlated
fixtures cannot establish general accuracy; missing context measurements are
not confirmed negatives. Calibration labels can explicitly mark
`unknown_dimensions`; report counts and disagreements use assessed Run/dimension
pairs, with separate coverage counts. Publication no longer waits on an
independent labeler. The CI gate stays a mechanics check
(`publishable_accuracy=false`). The agreement study is author-labeled and
says so. Rules: [evaluation-calibration.md](evaluation-calibration.md).
Self-labeling is a documented limitation, not a blocker for Harness work.

## Deliberately deferred

Kubernetes, Docker socket execution, containerised agents, vector memory, training export, arbitrary code execution, LLM-as-judge, multi-tenancy, billing, and automatic activation remain outside the active architecture. MCP as loopback Environment transport and operator-side agent adapters left this list on 2026-10-17 (ADR-0008, ADR-0009). Promotion of remaining items requires a measured need, a roadmap update, and an ADR when trust boundaries change.

## Phase 2 target architecture

Phase 2 — Harness (ADR-0007..0011) keeps the Control Plane as the sole database writer and preserves lease fencing, recovery, and the human activation gate. Execution becomes a supervised pair: the Environment Server hosts deterministic tools over MCP; the Agent-under-test connects on loopback and decides tool calls.

```mermaid
flowchart LR
    UI["React workbench"] -->|"typed HTTP / SSE"| API["FastAPI control plane"]
    API -->|"single writer"| DB[("PostgreSQL")]
    Runner["Rust Runner"] -->|"claim / heartbeat / events"| API
    Runner -->|"EvaluationSpec stdin"| EnvSrv["Environment Server"]
    Runner -->|"registered argv"| AgentUAT["Agent-under-test"]
    AgentUAT -->|"MCP Streamable HTTP"| EnvSrv
    EnvSrv -->|"JSONL child events"| Runner
    AgentUAT -.->|"reference agents"| Provider["OpenAI-compatible endpoint"]
```

| Component | Phase 1 | Phase 2 change | ADR |
|---|---|---|---|
| Control Plane | Experiments, Runs, leases, scoring, analysis, policies | Adds `agent_id` validation, trial groups, group metrics projection; still no child supervision | 0009, 0010 |
| Rust Runner | One Python child, JSONL transport | Two-process supervision: `env_server` then registered agent; waits for `env_ready` | 0007, 0008, 0009 |
| Python agent / Environment Server | `demo_agent` + `ScenarioHandler` in one process | Environment logic in `app.env_server` (MCP); fixture/reference/external agents are separate registered processes | 0007, 0008 |
| Agent-under-test | Implicit (`demo_agent` only) | First-class registry entry (`fixture`, `reference`, `external`); receives task + MCP URL via env | 0007, 0009 |
| Frontend | Full Experiment / Trace / Improve workflow | Agent dropdown on Experiment; trial group summary on Experiment page; Trace UI frozen | 0010 |
| Contracts | `scenario_id`, `execution_mode` | Adds `agent_id`, `trial_index`; child protocol adds `env_ready` | 0008, 0009, 0010 |
| Analyzer | Deterministic dimensions from persisted events | Unobserved agent internals → unknown dimensions, not inference | 0007, 0008 |
| Export | None | Optional OTel GenAI projection after terminal state; RunEvent remains canonical | 0011 |

What does not change:

- Human activation gate — policies never auto-apply.
- Lease, fencing, and recovery semantics (ADR-0002).
- Single database writer — only FastAPI mutates PostgreSQL.
- Protocol v1 field compatibility — `scenario_id` and existing event types remain; new fields and `env_ready` are additive.
