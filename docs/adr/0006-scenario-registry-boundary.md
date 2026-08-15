# ADR-0006: Scenario registry boundary

- Status: Accepted
- Date: 2026-08-15

## Context

Phase 1 proved one complete Golden loop, but `scenario_id` was hard-coded to
`checkout-api-latency` in protocol v1. The product narrative requires a
registry of allowlisted Scenarios without opening arbitrary code execution,
user-supplied executables, or untrusted endpoints. `CONTEXT.md` already states
that API clients select an allowlisted `scenario_id`; Phase 1.4 implements that
infrastructure.

ADR-0001 originally limited Phase 1 to a single deterministic scenario. That
constraint is superseded for onboarding additional built-in Scenarios while
preserving the same trust boundary.

## Decision

Scenarios are registered in an explicit allowlist inside the Control Plane.
Only registered Scenarios may appear in an Experiment or Run `EvaluationSpec`.
The registry exposes stable metadata (id, name, description, parameter schema
summary) to the workbench via `GET /api/scenarios`.

Each Scenario implements a narrow protocol stable surface:

- metadata and tool allowlist;
- initial state construction from `EvaluationSpec` (including optional
  `scenario_params` within bounded size);
- single-step execution semantics for fixture and opt-in provider modes;
- terminal scoring inputs consumed by the existing deterministic analyzer.

Registration uses explicit Python registration in Phase 1.4 (not dynamic
import of user code). Runners receive only the Scenario identifier and
validated parameters through the existing EvaluationSpec channel; they do not
load arbitrary modules from client input.

`scenario_params` is optional, size-bounded, and validated at Run creation
with the same allowlist discipline as provider metadata. `schema_version` stays
1; contract changes are backward compatible with defaults.

## Consequences

- Multiple built-in Scenarios can ship without weakening the Control Plane /
  Runner separation or database ownership rules.
- Adding a Scenario requires registry entry, protocol implementation, contract
  fixtures, and tests — not a new execution backend.
- Arbitrary Scenario upload, shell commands, and Docker/Kubernetes executors
  remain out of scope until a future ADR and the conditional safety gate say
  otherwise.
- UI lists Scenarios from the registry; it does not become a second source of
  truth for what may execute.
