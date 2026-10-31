# ADR-0014: H1 Recorded Preview canonical event fidelity

- Status: Accepted
- Scope: H1 Recorded Preview fixture and its recorder only; no H2 work.
- Amends: ADR-0008 and ADR-0012.

## Context

The Recorded Preview fixture is a checked-in projection of a real canonical v2
Golden execution. A prior ID-only cutover left the fixture with legacy shapes.
The fresh recording contains Environment-owned `submit_result` control events,
including a final-check tuple, and must preserve that durable event shape.

## Decision

1. The recorder may normalize only stable IDs and deterministic timestamps. It
   must deep-copy and preserve observations, scores, assertion aggregates,
   usage claims, Environment provenance, final-check operands, and tool calls.
2. Durable event normalization preserves the reserved `submit_result` tool-call
   summary with empty arguments. The control remains excluded from the world
   trajectory and scoring operands; retaining its event payload is provenance,
   not a scoring change.
3. Normalized event timestamps are linearly placed between the normalized
   `started_at` and `completed_at` values. They are deterministic, ordered, and
   never imply that the event occurred outside the Run lifecycle interval.
4. Frontend contract tests require exactly one accepted canonical finalization
   control per recorded run, its `submit_result` tool call, the v2 final-check
   tuple, and event timestamps inside the persisted lifecycle interval.

## Verification and boundaries

The fixture is regenerated with `scripts/record_golden_fixture.py` against a
fresh isolated canonical Rust/API/PostgreSQL execution. Parent validation
covers frontend format/typecheck/unit/build/Chromium tests, the targeted and
full backend tests, and container evidence separately. The derived offline
Runner image is not a standard network-build claim, and this ADR does not
accept H1, a live model, historical data migration, or H2.
