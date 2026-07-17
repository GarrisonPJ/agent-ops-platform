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
- React provides the Experiment, Trace, Analysis, Improve, replay, and human activation/rejection workflow.
- Python, TypeScript, and Rust validate shared protocol v1 fixtures.
- CI verifies contracts, migrations, backend behavior, frontend adapters, Rust supervision, Compose, and the real Golden loop.
- Policy activation remains an explicit human action.

## Milestones

| Priority | Milestone | Status | Outcome |
|---|---|---|---|
| P0 | Phase 1.1 — Runner recovery | Next | A crashed or disconnected Runner cannot strand a Run indefinitely. |
| P1 | Phase 1.2 — OpenAI-compatible provider | Planned | Real model execution uses the same supervised, typed, persisted workflow without making CI depend on an external API. |
| P2 | Phase 1.3 — Observability and operational hardening | Planned | Operators can diagnose queue, lease, Runner, provider, and migration failures from durable signals. |
| Gate | Safety and access control | Trigger-based | Required before side-effecting tools, untrusted users, or shared/public operation enter scope. |

## Phase 1.1 — Runner recovery

Before implementation, record the recovery semantics for a partially persisted trace in an ADR: resume the same attempt, restart deterministically, or create a linked retry attempt. The design must not silently mix incompatible event histories.

Scope:

- Detect expired leases for `claimed`, `running`, and `cancelling` jobs.
- Make abandoned work reclaimable without allowing the previous lease to mutate the Run.
- Increment and persist the attempt number and recovery reason.
- Define a maximum-attempt policy and an explicit terminal outcome after exhaustion.
- Preserve already accepted events and maintain sequence/idempotency invariants.
- Preserve cancellation intent while a job is being recovered.
- Add a real-stack fault test that terminates the Runner mid-run, starts a replacement, and verifies the final state.

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

Acceptance:

- A failed Run can be traced across API, database job, Runner attempt, and provider call without log guessing.
- Alerts can distinguish a dead Runner, expired lease, provider outage, and database/migration failure.
- Backup restoration and migration rehearsal have repeatable verification commands.

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
