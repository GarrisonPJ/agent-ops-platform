# ADR-0013: H1 canonical diagnostic adaptation (analyzer v3)

- Status: Accepted
- Approval: Explicit operator approval of the narrow H1 integration repair.
- Amends: [ADR-0008](0008-environment-mcp-server-and-env-side-capture.md),
  [ADR-0012](0012-h1-canonical-cutover-and-control-plane-success.md).
- Scope: H1 integration only; not H2, H5 research, or historical rescoring.

## Context

The canonical Environment projection excludes reserved controls from world
steps and stores optional cumulative usage separately as `agent_reported`.
The legacy six-contract calibration adapter injects context into world-step
metadata that this projection deliberately does not treat as measured truth.
Also, a Provider failure before any world call leaves tool assertions unsatisfied;
that absence alone is not evidence of a planning decision.

## Decision

Increment the shared diagnostic analyzer to `failure-analyzer.v3` with exactly
these adaptations:

1. Consume an explicitly sourced `trajectory.agent_usage.context_window` as
   Agent-reported, run-level context evidence when valid usage exceeds 95%.
   Keep `step_index=None`, retain `source=agent_reported` in evidence details,
   and mark the context dimension at 1.0 for that run-level signal. Do not
   duplicate cumulative usage across steps or infer missing measurements.
   Existing step-based context detection remains available for its original
   inputs; absent, null, invalid, and low reported usage add no positive signal.
2. Suppress assertion-derived planning attribution only when the trajectory has
   no world steps and has an explicit Provider error. Preserve the execution
   evidence and all actual assertion results, aggregate scores, quality scores,
   and completion gates. Nonempty trajectories, including refused calls, keep
   their planning attribution.

The controlled capture calls the real Environment `report_usage` control before
submission for the high/low context cases. Unknown cases omit context reporting.
The six fixed label sets and known/unknown coverage remain unchanged. No
synthetic world/control facts or compatibility fallback may replace the calls.

## Invariants and verification

Control Plane alone decides success from valid current-attempt evidence and the
registered aggregate gate. Diagnostic classification never supplies success
operands or changes score/terminal decisions. Usage is a claim, not measurement.

Keep existing packet, trajectory/event-stream digest, revision, and strict-binding
mechanics unchanged. Generate fresh v3 packets; do not relabel, overwrite,
migrate, or rescore historical packets or existing application data.

Tests must cover high/low/absent/invalid usage, zero-step run-level evidence,
latest-report replacement, abandoned-attempt isolation, both assertion input
paths, and nonempty planning controls. Retain the six-contract disagreement
gate and both projected-trajectory and ignored-metadata tamper checks. This
ADR approves the repair, not H1 acceptance or diagnostic accuracy claims.
