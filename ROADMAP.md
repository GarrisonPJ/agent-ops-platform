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
- CI verifies contracts, migrations, backend behavior, frontend adapters, Rust supervision, Compose, and the real Golden loop.
- Durable Run diagnostics now correlate queue, lease, attempt, Runner, provider, retry, timing, and terminal signals.
- API/database readiness and durable Runner availability checks are implemented and verified in the real Compose stack.
- Policy activation remains an explicit human action.

## Milestones

| Priority | Milestone | Status | Outcome |
|---|---|---|---|
| P0 | Phase 1.1 — Runner recovery | Complete | A crashed or disconnected Runner cannot strand a Run indefinitely. |
| P1 | Phase 1.2 — OpenAI-compatible provider | Complete | Real model execution uses the same supervised, typed, persisted workflow without making CI depend on an external API. |
| P2 | Phase 1.3 — Observability and operational hardening | In progress | Operators can diagnose queue, lease, Runner, provider, and migration failures from durable signals. |
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

Implementation progress as of 2026-07-31:

- **1.3A — Durable diagnostics: implemented.** Per-Run diagnostics expose Run, Job, current Lease/Runner, Attempt, provider, timing, retries, recovery, and terminal projections. The operations overview reports queue depth, status distribution, expired leases, recoveries, and event retries.
- **1.3B — Health and availability: implemented.** Separate API liveness, database readiness, and durable Runner availability endpoints are backed by authenticated Runner presence. Alembic revisions `0004` and `0005` and the Compose readiness probe are covered by CI and real-stack verification.
- **1.3C — Diagnostic correctness and safe correlation: implemented.** Immutable expired-Attempt history preserves final Lease/Runner correlation, and recovery totals include exhaustion. Provider telemetry and errors are allowlisted at ingestion, Request IDs become SHA-256 fingerprints, and revision `0006` sanitizes legacy records.
- CI run `30549228509` passed backend, migration round-trip, frontend, Rust formatting/lint/tests, Compose validation, Golden E2E, and Runner recovery for commit `f7e4156`.

Remaining Phase 1.3 work:

- Database readiness currently proves connectivity with `SELECT 1` but does not prove that the schema is at the expected Alembic head.
- Backup/restore rehearsal, retention enforcement, and an explicit event/provider metadata redaction policy are not implemented.
- Global operations aggregation currently scans all Runs and Jobs; retention and bounded queries must land before this becomes a production-scale endpoint.

Execution plan:

1. **1.3C — Diagnostic correctness and safe correlation: Complete.** Immutable Attempt history, exhaustion-aware recovery counts, typed Provider errors, Request fingerprints, legacy-data sanitization, and malicious-metadata/cross-version retry tests are implemented.
2. **1.3D — Migration and data recovery (P0).** Make readiness compare the database's `alembic_version` with the application head; add executable `pg_dump` backup and disposable-database restore rehearsal commands; verify schema revision, row counts, foreign keys, and a restored Run trace; run the rehearsal in CI without reusing the primary test database.
3. **1.3E — Retention and redaction (P1).** Record a data-lifecycle decision covering RunEvents, provider telemetry, analyses, and policy dependencies; enforce allowlisted provider metadata at ingestion; add dry-run and explicit-execute retention commands with terminal-age and dependency guards; test that credentials, endpoint values, raw headers, raw provider content, and hidden reasoning cannot be persisted.
4. **1.3F — Alert classification and closeout (P1).** Expose machine-readable states that distinguish API failure, database connectivity, schema drift, missing Runner, expired Lease, and provider outage; add a fault matrix with repeatable commands; bound operations queries; add operator UI only if real usage shows the API/runbook is insufficient.
5. **Phase 1.3 closeout.** Run all Python, TypeScript, Rust, migration, Compose, Golden, recovery, backup/restore, and redaction checks; update both roadmaps and `CONTEXT.md`; mark the milestone Complete only when every acceptance item below is evidenced.

Acceptance:

- A failed Run can be traced across API, database job, Runner attempt, and provider call without log guessing.
- Alerts can distinguish a dead Runner, expired lease, provider outage, and database/migration failure.
- Backup restoration and migration rehearsal have repeatable verification commands.

Acceptance status:

- Failed-Run correlation: **Complete** — current and expired Attempts, including exhaustion, retain Lease/Runner identity and safe Provider fingerprints.
- Failure classification: **Partial** — API/database/Runner checks and Run diagnostics exist; schema drift and provider-outage alert states remain.
- Backup and migration rehearsal: **Partial** — migration round-trip is in CI; backup restoration rehearsal remains.
- Retention and redaction: **Partial** — Provider metadata is allowlisted and legacy records are sanitized; a complete lifecycle policy and retention enforcement remain.

No Phase 1.4 milestone is defined yet. After Phase 1.3 closes, the next milestone must be chosen from measured operational needs; the conditional safety gate takes priority if shared/public use, untrusted endpoints, untrusted accounts, or side-effecting tools enter scope.

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
