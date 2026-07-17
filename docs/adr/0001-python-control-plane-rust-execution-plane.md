# ADR-0001: Python control plane and Rust execution plane

- Status: Accepted
- Date: 2026-07-16

## Context

The original application executed agents inside the FastAPI process and also
contained Docker and Kubernetes executor paths. Run events, cancellation, and
tool state were partly process-local, so a visible trace was not a durable
record and horizontal API replicas were not safe.

The portfolio product is now focused on one user journey: run a baseline,
diagnose the failure, review a policy, replay it, and explicitly adopt or
reject the result.

## Decision

FastAPI is the control plane and the only database writer. A Linux/WSL Rust
runner is the execution plane. It claims allowlisted evaluation jobs through
an authenticated HTTP protocol, supervises a Python agent subprocess, and
uploads versioned, sequenced events. Events are persisted before SSE delivery.

Phase 1 supports only the deterministic `checkout-api-latency` scenario. It
does not accept an arbitrary executable, connect to PostgreSQL, or connect to
the Docker daemon. Kubernetes and Docker execution backends are outside the
main product until a real requirement justifies them.

## Consequences

- Baseline and replay use the same immutable EvaluationSpec except for policy.
- The web application can restore a trace after refresh or reconnect.
- Rust has a bounded systems responsibility instead of duplicating AI logic.
- Local development needs an API, a database, and one runner process.
- Native Windows execution, disk spooling, multiple runners, and container
  backends are explicitly deferred.
