# ADR-0005: Machine-readable operational alert classification

- Status: Accepted
- Date: 2026-08-15

## Context

Phase 1.3 required operators to distinguish API failure, database connectivity,
schema drift, missing Runner, expired lease, and provider faults without log
guessing. Health endpoints already covered liveness, readiness, and Runner
availability, but there was no single machine-readable rollup with precedence,
bounded incident lists, or separate provider outage vs rate-limit states.
`GET /api/operations/overview` also loaded every Run and Job into memory.

## Decision

Add `app/operational_state.py` as the bounded evaluation module and expose
`GET /api/operations/state` with schema version `1`. The endpoint always
returns HTTP `200`; consumers inspect `status`, `primary_state`, and the
`states[]` list. Precedence is fixed:

`database_unavailable` → `schema_drift` → `runner_unavailable` →
`lease_expired` → `provider_rate_limited` → `provider_unavailable` → `ok`.

Provider outage and rate limiting remain separate active states so paging rules
can throttle retries without masking hard outages. Operations overview and state
evaluation use database-side aggregates with bounded incident windows and
cursors. Out-of-process probing uses `scripts/health_probe.py` against
`/api/health/live` and optionally `/api/operations/state`. Operator UI is
deferred until real usage proves the API and runbooks are insufficient.

## Consequences

- Alerting and on-call runbooks can key off one stable JSON contract.
- Large deployments no longer require loading all Runs for queue summaries.
- Adding new operator states requires updating precedence, tests, and the fault
  matrix; contract changes should bump `schema_version`.
- UI work remains optional and must not become a second source of truth.
