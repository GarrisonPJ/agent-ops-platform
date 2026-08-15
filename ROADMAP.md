# AgentOps Roadmap

This roadmap describes planned engineering work. It is the source of truth for future milestones; `CONTEXT.md` owns current domain language and invariants, ADRs own accepted architecture decisions, and the git-ignored `.scratch/` workspace holds local implementation records.

## Direction

AgentOps is being developed as a reliable closed-loop evaluation system for tool-using agents. Work is prioritized by correctness, recoverability, observability, and safe operation.

The recorded preview remains useful for offline UI development and deterministic regression testing. It is not a separate product milestone or hosted deployment target.

## Current state

Phase 1 is complete and verified:

- Experiments create deterministic baseline and replay runs.
- FastAPI owns domain state, persistence, leases, scoring, analysis, and policy decisions.
- PostgreSQL stores runs, jobs, ordered events, analyses, and policies.
- The Rust Runner claims leased jobs, supervises an allowlisted Python process, retries event delivery, and enforces cancellation and timeout.
- Expired Runner leases are reclaimed with attempt fencing, sequence-aware restart, cancellation preservation, and bounded exhaustion.
- React provides the Experiment, Trace, Analysis, Improve, replay, and human activation/rejection workflow.
- Python, TypeScript, and Rust validate shared protocol v1 fixtures.
- CI verifies contracts, migrations, database recovery, backend behavior, frontend adapters, Rust supervision, Compose, and the real Golden loop.
- Durable Run diagnostics now correlate queue, lease, attempt, Runner, provider, retry, timing, and terminal signals.
- Database readiness verifies the Alembic Head, disposable backup/restore rehearsal is executable, and durable Runner availability remains independently observable.
- Machine-readable operational states (`GET /api/operations/state`), bounded operations aggregates, retention/redaction enforcement, and out-of-process health probes are implemented and verified in CI.
- Policy activation remains an explicit human action.

## Milestones

| Priority | Milestone | Status | Outcome |
|---|---|---|---|
| P0 | Phase 1.1 — Runner recovery | Complete | A crashed or disconnected Runner cannot strand a Run indefinitely. |
| P1 | Phase 1.2 — OpenAI-compatible provider | Complete | Real model execution uses the same supervised, typed, persisted workflow without making CI depend on an external API. |
| P2 | Phase 1.3 — Observability and operational hardening | Complete | Operators can diagnose queue, lease, Runner, provider, and migration failures from durable signals. |
| P1 | Phase 1.4 — Scenario onboarding | Planned | Operators choose from registered built-in Scenarios; the platform is no longer limited to one hard-coded demo. |
| Gate | Safety and access control | Trigger-based | Required before side-effecting tools, untrusted users, or shared/public operation enter scope. |

## Phase 1.1 — Runner recovery

ADR-0002 records deterministic restart of the same logical Run. Accepted events remain immutable; each retry appends a new attempt marker and continues the global sequence, while analysis scores only the latest attempt segment.

Scope:

- Detect expired leases for `claimed`, `running`, and `cancelling` jobs.
- Make abandoned work reclaimable without allowing the previous lease to mutate the Run.
- Increment and persist the attempt number and recovery reason.
- Define a maximum-attempt policy and an explicit terminal outcome after exhaustion.
- Preserve already accepted events and maintain sequence/idempotency invariants.
- Preserve cancellation intent while a job is being recovered.
- Add a real-stack fault test that terminates the Runner mid-run, starts a replacement, and verifies the final state.

Implementation result:

- Expired claimed, running, and cancelling leases are reclaimed on the next authenticated claim.
- A replacement claim increments Attempt, returns the next event sequence, and fences the old lease.
- Cancellation intent survives recovery; three total attempts are allowed before a documented failed or cancelled terminal state.
- Backend tests cover recovery, stale-lease fencing, cancellation, and exhaustion.
- The Compose fault test terminates a Runner mid-run, waits for lease expiry, starts the replacement, and verifies Attempt 2 completion.

Acceptance:

- No Run remains permanently `claimed`, `running`, or `cancelling` after its Runner disappears.
- A stale Runner cannot append events or complete the recovered Run.
- Recovery does not duplicate or rewrite accepted events.
- Retry exhaustion produces a documented terminal error.
- The recovery path passes backend, Rust, and Docker integration tests.

## Phase 1.2 — OpenAI-compatible provider

Scope:

- Introduce one narrow provider boundary for the Python agent.
- Configure `base_url`, model, and credentials only on the server side.
- Support timeout, bounded retry, cancellation propagation, and structured provider errors.
- Persist model identity, latency, and token usage without leaking credentials or hidden reasoning.
- Keep the deterministic checkout scenario as the default CI and Golden E2E path.
- Test provider behavior against a local fake OpenAI-compatible server; keep live-provider checks opt-in.

Acceptance:

- The same Experiment workflow can select deterministic fixture execution or an explicitly configured provider-backed agent.
- Provider failure, timeout, and cancellation produce valid terminal Run states.
- CI remains deterministic and requires no external API key.
- Recorded preview continues to replay persisted facts rather than provider logic.

## Phase 1.3 — Observability and operational hardening

Scope:

- Add structured correlation fields for experiment, run, job, lease, attempt, Runner, and provider request.
- Measure queue depth, claim latency, lease expiry/recovery, Run duration, event retries, provider latency/tokens, and terminal outcomes.
- Separate liveness and readiness checks for API, database, and Runner availability.
- Make migration, backup, and restore procedures executable and testable.
- Define retention and redaction rules for events and provider metadata.
- Add operator-facing diagnostics only where the collected signals prove they are needed.

Implementation progress as of 2026-08-15:

- **1.3A — Durable diagnostics: implemented.** Per-Run diagnostics expose Run, Job, current Lease/Runner, Attempt, provider, timing, retries, recovery, and terminal projections. The operations overview reports queue depth, status distribution, expired leases, recoveries, and event retries.
- **1.3B — Health and availability: implemented.** Separate API liveness, database readiness, and durable Runner availability endpoints are backed by authenticated Runner presence. Alembic revisions `0004` and `0005` and the Compose readiness probe are covered by CI and real-stack verification.
- **1.3C — Diagnostic correctness and safe correlation: implemented.** Immutable expired-Attempt history preserves final Lease/Runner correlation, and recovery totals include exhaustion. Provider telemetry and errors are allowlisted at ingestion, Request IDs become SHA-256 fingerprints, and revision `0006` sanitizes legacy records.
- **1.3D — Migration and data recovery: implemented.** Readiness compares the live `alembic_version` with the application Head. Environment-only backup and disposable restore commands use an exported snapshot and verify revision, every public-table row count, validated foreign keys, and an ordered Run trace. An isolated PostgreSQL 16 CI job runs the seeded rehearsal.
- **1.3E — Retention and redaction: implemented.** Experiment aggregates are the atomic retention unit; Plan emits a SHA-256 digest and Execute binds a reviewed plan file with PostgreSQL post-lock revalidation; `durable_events.py` owns Provider/Completion ingestion boundaries and `TerminalFailureKind` replaces free-text completion errors; the `database-recovery` CI job covers real PostgreSQL 16 retention locking, stale-plan, rollback, and backup/restore rehearsal. Commit `80e03e0` is on `main`.
- **1.3F — Alert classification and closeout: implemented.** `GET /api/operations/state` exposes machine-readable operator states with precedence across database, schema, Runner, lease, and provider faults; provider outage and rate limiting are separate states; operations overview and state evaluation use bounded SQL aggregates; `scripts/health_probe.py` and `docs/operations/fault-matrix.md` provide repeatable out-of-process probes. Commits `98c903d` and `b8f4b8c` are on `main`.

Phase 1.3 closeout (2026-08-15):

- **Status: Complete.** CI on `main` exercises backend `phase1_tests` (including operational state), migration round-trip, PostgreSQL backup/restore and retention integration, frontend typecheck/tests/build/recorded E2E, Rust fmt/clippy/test, Compose config validation, Golden closed loop, and Runner recovery rehearsal.
- **Evidence:** `.github/workflows/ci.yml` jobs `backend`, `database-recovery`, `frontend`, `rust`, `compose`, and `golden-e2e`; runbooks under `docs/operations/`; ADR-0005.

Execution plan:

1. **1.3C — Diagnostic correctness and safe correlation: Complete.** Immutable Attempt history, exhaustion-aware recovery counts, typed Provider errors, Request fingerprints, legacy-data sanitization, and malicious-metadata/cross-version retry tests are implemented.
2. **1.3D — Migration and data recovery (P0): Complete.** Readiness now compares the live `alembic_version` with the application Head. Executable `pg_dump` backup and disposable restore rehearsal commands verify schema revision, public-table row counts, validated foreign keys, and a restored Run trace in an isolated PostgreSQL 16 CI job.
3. **1.3E — Retention and redaction (P1): Complete.** Experiment-aggregate retention, plan-file execute, PostgreSQL post-lock revalidation, Provider/Completion redaction, and real PostgreSQL 16 integration tests are implemented; the control-plane maintenance boundary is recorded in ADR-0004.
4. **1.3F — Alert classification and closeout (P1): Complete.** Machine-readable operational states, bounded operations queries, provider outage vs rate-limit classification, fault matrix, and out-of-process API probe are implemented.
5. **Phase 1.3 closeout: Complete.** Full CI matrix and operations runbooks are in place; acceptance items below are evidenced.

Acceptance:

- A failed Run can be traced across API, database job, Runner attempt, and provider call without log guessing.
- Alerts can distinguish a dead Runner, expired lease, provider outage, and database/migration failure.
- Backup restoration and migration rehearsal have repeatable verification commands.

Acceptance status:

- Failed-Run correlation: **Complete** — current and expired Attempts, including exhaustion, retain Lease/Runner identity and safe Provider fingerprints.
- Failure classification: **Complete** — `/api/operations/state`, health probes, schema-drift readiness, Runner availability, lease expiry, and separate provider outage vs rate-limit states are implemented with a repeatable fault matrix.
- Backup and migration rehearsal: **Complete** — migration round-trip and an isolated PostgreSQL 16 backup restoration rehearsal have repeatable CI and local commands.
- Retention and redaction: **Complete** — Experiment-aggregate retention, plan-file execute, PostgreSQL post-lock revalidation, Provider/Completion redaction, and real PostgreSQL 16 integration tests are implemented.

No Phase 1.5 milestone is defined yet. After Phase 1.4, the next milestone must be chosen from measured operational needs rather than speculative features. Promote work from **Deferred until justified** only when a concrete requirement appears. The conditional safety gate takes priority if shared/public use, untrusted endpoints, untrusted accounts, or side-effecting tools enter scope.

## Phase 1.4 — Scenario onboarding

PRD: `.scratch/phase1.4-scenario-onboarding/PRD.md` (local tracker). Architecture boundary: [ADR-0006](docs/adr/0006-scenario-registry-boundary.md).

Scope:

- Relax `scenario_id` in protocol v1 while keeping `schema_version` at 1 and preserving backward-compatible defaults.
- Introduce optional bounded `scenario_params` on EvaluationSpec.
- Add a Scenario registry and protocol; refactor `checkout-api-latency` as the first registered Scenario.
- Validate Scenario selection at Run creation; reject unregistered ids with structured errors.
- Expose `GET /api/scenarios` and a workbench Scenario picker on New Experiment.
- Ship two additional built-in Scenarios with fixture (CI) and provider (opt-in) paths.
- Document Scenario vocabulary and a contributor guide for adding a Scenario.

Acceptance:

- Golden checkout loop still passes after registry refactor.
- At least one additional Scenario completes the full closed loop in CI without external APIs.
- README and CONTEXT describe registry semantics; ROADMAP marks this milestone Complete only when acceptance is evidenced in CI.

## Conditional safety gate

Before enabling side-effecting tools, user-supplied or untrusted external endpoints, untrusted accounts, or shared/public operation:

- Write a threat-model ADR.
- Add authentication, authorization, audit records, secret redaction, and resource limits.
- Define a PreToolUse decision boundary with explicit allow, block, and escalation semantics.
- Test SSRF, command injection, cross-tenant access, cancellation, and budget enforcement.

These controls are intentionally designed before those capabilities are enabled; they are not approximated by a misleading partial safety layer.

## Deferred until justified

Kubernetes execution, Docker socket access, MCP transport, vector memory, training export, framework adapters, arbitrary code execution, multi-tenancy, billing, and automatic policy activation remain outside the active roadmap until a measured requirement promotes them.

When a deferred item is promoted, update this file, create or update a PRD under `.scratch/`, and record any new architectural boundary in an ADR.
