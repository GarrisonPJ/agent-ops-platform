# ADR-0012: H1 canonical cutover and Control Plane success contract

- Status: Accepted
- Date: 2026-10-24
- Approval: Explicit user decision for the single-user local deployment.
- Implementation: Planned H1 integration; this decision does not ship the cutover.
- Amends: [ADR-0007](0007-environment-agent-split.md),
  [ADR-0008](0008-environment-mcp-server-and-env-side-capture.md),
  [ADR-0009](0009-agent-registry-trust-boundary.md). Supersedes the old-v1
  compatibility requirement for this one-time upgrade, including the additive
  compatibility assumption in [ADR-0006](0006-scenario-registry-boundary.md).

## Context

H1-1 extracted independent worlds but retained the coupled Phase 1 execution
paths. Their fixture/provider semantics differ from the canonical worlds.
Promotion therefore needs new identities, not an in-place semantic change.
The operator explicitly accepts an incompatible H1 upgrade: old local records
are disposable and old-v1 execution compatibility is not required. Separately,
submission, a check report, and process exit cannot establish task success.

## Decision

### One canonical execution path

The canonical H1 identities are `checkout-api-latency.v2`,
`multi-step-research.v2`, and `api-fault-orchestration.v2`. They replace the
three corresponding v1 execution registrations and coupled fixture/provider
loops, rather than adding dual paths. Defaults, including omitted-`scenario_id`
API requests, UI selection, and quickstart examples, use the canonical v2 path;
checkout defaults to `checkout-api-latency.v2`. `order-support.v1` stays unchanged
because it has no legacy counterpart.

The generic `<name>.v<N>` semantic-identity invariant remains: semantic changes
require a new version and only Runs with the same id are score-comparable.
This upgrade includes no migration, aliases, rescoring, historical replay
preservation, or cross-version comparability work. Accurate Phase 1 documentation
remains historical, not a promise of executable v1 compatibility.

Disposable records are **not** authorization to delete anything. There is no
auto-reset, destructive migration, table drop, database/file/volume deletion,
or access to existing application data in this work. Any later reset requires
a separate operator request. Implementation validation uses fresh isolated data.

### Control Plane task success

Python Control Plane alone evaluates the registered assertion suite and owns
authoritative score, Run state, and database writes. A Run can succeed only with:

1. A valid submission and successful Environment check.
2. Sufficient valid required trusted evidence, including every required operand.
3. The registered suite aggregate **>= 1.0**, with the success threshold fixed
   at 1.0 for that Environment version.
4. No abnormal execution failure; abnormal or nonzero process exit cannot succeed.

Missing/invalid required evidence, failed check, or no valid submission cannot
succeed, even if a partial trajectory appears to score fully. Cancellation,
timeout, and lease fencing still dominate completion. Terminal events and the
Control Plane completion result must agree. Exit zero, EOF, readiness, and
submission/check markers alone are not success.

Preserve assertion combination semantics: `ALL` is the minimum, `ANY` the
maximum, and `WEIGHTED` the weighted mean of assertion scores. Per-assertion
thresholds retain their meaning; a universal `all(item.passed)` must not replace
the registered combination. The success gate uses the suite aggregate, **not**
the penalized quality score. Quality penalties and human Policy validation and
activation rules remain unchanged. Never trust Agent- or check-reported scores
or status as authoritative. Rust supervises and relays facts; it is not a second
scorer or a scoring-policy owner.

### Trusted scoring projection

Control Plane trajectory assertions use persisted world-call attempts from the
latest Attempt, including refusals. Reserved controls are excluded. Rejections
project as `rejected_tool` with status `failed`; the requested tool name or
arguments cannot satisfy named used/args/sequence assertions. Valid-only
Environment history must not override this all-attempt trajectory.

Final-state outcomes use bounded, trusted Environment-owned final-check facts,
not expected values or scores supplied by the Agent. Control Plane validates
required operands against registered expectations. Exactly-once order behavior
is proved by the final ledger, not by counting requested calls. Missing usage
remains unknown; reported usage remains Agent-reported, not measured truth.
The exact additive evidence wire contract is a later reviewed seam; this ADR
does not prescribe new fields or types.

## Consequences and delivery boundary

H1-3 owns dual-process supervision, source separation, cancellation/timeout,
lease fencing, and relay of execution facts, not Control Plane scoring-policy
implementation. H1-5/integration wires trusted evidence, canonical defaults,
retirement of legacy execution, and Control Plane completion under this contract.
H1-4 aligns API/UI defaults; H1-6 supplies the order world's final ledger proof.

Component acceptance does not establish integrated H1 acceptance. Fresh-data
fixture Golden E2E, PostgreSQL-specific validation, and local reference-model
acceptance remain implementation dependencies; none is claimed by this decision.
