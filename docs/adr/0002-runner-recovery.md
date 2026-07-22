# ADR-0002: deterministic Runner recovery

- Status: Accepted
- Date: 2026-07-22

## Context

A Runner can disappear after the control plane has accepted part of a Run's
trace. Without recovery, the Run remains in claimed, running, or cancelling
indefinitely. Reusing the old lease would allow a late Runner to append events
or complete a Run that a replacement Runner already owns.

The Phase 1 RunnerJob already has an attempt field, but it did not reclaim
expired leases or tell a replacement Runner where the durable event sequence
continues.

## Decision

Recovery uses deterministic restart of the same logical Run:

- The control plane lazily scans for expired claimed, running, and cancelling
  leases whenever an authenticated Runner claims work.
- A recoverable job is returned to queued, its attempt is incremented, and the
  previous lease_id, runner_id, and expiry are cleared. The previous lease
  therefore fails as INVALID_LEASE.
- A claim response includes attempt, next_sequence, and recovery_reason. The
  replacement Runner emits a new run_started marker at next_sequence and
  appends all later events without rewriting accepted events.
- Runner-generated event payloads carry the attempt number. Analysis selects
  the event segment beginning at the latest attempt marker, so a deterministic
  restart does not double-count the abandoned attempt.
- Cancellation intent remains on the RunnerJob. A replacement Runner receives
  cancel through heartbeat, and completion is forced to cancelled.
- Three total attempts are allowed. Lease expiry on the final attempt produces
  failed, or cancelled when cancellation was requested, with a durable terminal
  error and normal analysis effects.

The control plane remains the only database writer. Recovery is intentionally
lazy: a replacement Runner triggers it through the normal claim path, so no
second scheduler or Runner-side database access is introduced.

## Consequences

- A Run keeps one stable identity and one contiguous event sequence across
  recovery.
- The trace preserves the accepted history of abandoned attempts for audit and
  reconnects, while scoring uses only the latest attempt.
- Late heartbeats, events, and completion requests cannot mutate the recovered
  Run.
- A deployment with no replacement Runner still needs an operational liveness
  signal; recovery occurs when a Runner next claims work.
- Backend tests cover lease fencing, cancellation preservation, and exhaustion.
  A Compose fault test kills a Runner mid-run, waits for lease expiry, and
  verifies replacement completion.
