# ADR-0010: Trial groups and pass^k

- Status: Accepted
- Date: 2026-10-17
- Implementation: Phase 2 H3. Not in code at `8bd2ef0`.
- Amended: 2026-11-03 for H3-1 — a `trial_groups` row holds `n`, `k` and
  the seed; aggregates are computed on read.
- Amended: 2026-11-03 for H3-2 — publication, group replay and the
  validation lock.
- Amended: 2026-11-04 (operator decision) — group validation requires a
  strict pass^k increase.
- Depends on: [ADR-0007](0007-environment-agent-split.md)

## Context

One green Replay does not measure an agent that samples. τ-bench defines
pass^k as the probability that all k independent trials of a task succeed,
with unbiased estimator `C(c, k) / C(n, k)` averaged over tasks (Yao et al.,
arXiv:2406.12045). At k = 1 this is the ordinary success rate.

Phase 1 invariant 7 validates a Policy when one Replay succeeds with a
positive score delta. That rule is right for the deterministic fixture agent
and misleading for a local model.

## Decision

A **Trial Group** is `n` Runs created together. They share the EvaluationSpec
except `run_id` and `trial_index`. The Environment seed is fixed for the
group, so tool observations match across trials. Model sampling is not
forced to be deterministic. `n` is chosen by the caller; published numbers
always state `k` and `n`.

API: `POST /api/experiments/{id}/trials` with body fields `k` (the pass^k
parameter to report; `k <= n`) and `n` (trials to run). Default for the
documented local-model demo is `n = 5`, `k = 1` and `k = 5` both reported.
Each trial is a normal Run and RunnerJob. Lease, fencing, and cancellation
are unchanged.

Aggregation, stored on the group after every member is terminal:

- `pass_at_1` = `c / n` over completed trials.
- `pass_at_k` = `C(c, k) / C(n, k)` when `n >= k` and `c >= k`, else 0 when
  `c < k`. Incomplete groups do not publish a pass^k.
- Mean score, mean steps. Failures count as score 0 in the mean.

`c` is the number of trials whose terminal status is `succeeded` and whose
assertion score is above the Environment pass threshold.

Policy validation:

- Group size 1: invariant 7 stands (success and positive score delta).
- Group size greater than 1: the candidate group must show pass^k greater
  than or equal to the baseline group at the same k, and a positive mean
  score delta. A single lucky trial cannot validate a policy. (Tightened to
  a strict pass^k increase by the H3-2 amendment below.)

Before/After for groups compares those aggregates plus the existing
per-run comparison for a chosen pair. The Experiment page shows pass^1,
pass^k, and per-trial status. The Trace page does not gain new views
(trace UI is frozen).

Schema: new columns on `runs` for `trial_group_id` and `trial_index`, one
Alembic revision, `schema_version` stays 1. Absent columns mean "not in a
group" for old rows.

## H3-1 amendment: group row and computed aggregates

Two columns on `runs` cannot hold `k`, and H3-2 compares groups at the `k`
they were created with. The single H3 Alembic revision therefore also adds a
`trial_groups` table: `id`, `experiment_id`, `policy_id` (null for a baseline
group; H3-2 uses it for candidate groups), `n`, `k`, `seed`, `created_at`.
`runs.trial_group_id` references it and `runs.trial_index` runs from 1 to `n`,
unique within a group. Deleting an Experiment deletes its groups.

The EvaluationSpec and protocol v1 do not change. Members' specs are equal
except `run_id`, so an Agent cannot tell a trial from a single Run. Each member
is an ordinary Run of kind `baseline` (or `replay` in a candidate group) with
its own RunnerJob. `n` is 1..20 and `k` is 1..`n`.

Aggregates are not stored. H3-2 computes them from the member Runs on read,
so no second copy can drift from the Runs, and an incomplete group has no
pass^k to publish.

## H3-2 amendment: publication, group replay and validation

`app.trials` holds the estimator and the aggregate; the Control Plane calls it
when rendering an Experiment and when a candidate member terminates.

- A group publishes only when all `n` members are terminal and none was
  cancelled. Cancellation is an operator action, not a failed attempt by the
  Agent, so a group with a cancelled member reports `state = cancelled` and no
  pass^k. `c` counts members whose status is `succeeded`, which already
  requires the registered aggregate >= 1.0 (ADR-0012).
- A Policy whose source Run belongs to a group is replayed as a candidate group
  with the same `n`, `k` and seed. Member `i` replays baseline member `i`. The
  request fails with `409 BASELINE_GROUP_NOT_PUBLISHED` until the baseline group
  has published. `Policy.replay_run_id` stores the first member's id, so the
  existing atomic claim (CONTEXT invariant 19) still yields exactly one group.
- A baseline group yields at most one candidate Policy, not one per failed
  member.
- The Policy is decided when the last candidate member terminates. `n = 1`
  keeps invariant 7. For `n > 1`, `score_delta` is the mean score delta, and
  the Policy is validated if pass^k does not decrease and that delta is
  positive. A group with a cancelled member returns the Policy to `candidate`.
- Each member's terminal transaction locks the group row after its Job and
  Run (lock order Job -> Run -> TrialGroup) and reads siblings only after that
  lock. Without the lock, two members finishing together would each see the
  other as unfinished, and the Policy would stay `replaying`. A PostgreSQL
  test covers this and fails without the lock.

Tightened rule (operator decision, 2026-11-04). "pass^k does not decrease"
always holds when the baseline group's pass^k is 0. One success in `n`
candidate trials would then give a positive mean delta and validate, which
contradicts the intent that a single lucky trial cannot validate. For `n > 1`,
the candidate group now needs a positive mean score delta and either:

- a pass^k strictly above the baseline group's at the same `k`, or
- pass^k = 1 when the baseline group is already at pass^k = 1, which cannot
  increase.

A strict increase needs at least `k` candidate successes and more successes
than the baseline. With `n = k = 5` against a 0/5 baseline, only 5/5
validates. `n = 1` still follows invariant 7. Human activation still gates
every Policy.

## Consequences

- Fixture Golden E2E stays a single trial. pass^k is exercised in tests with
  scripted success/failure mixes, not with a live model.
- A README number from a local model must name the model, Environment
  version, `n`, and `k`.
- Correlated trials (same prompt, same seed, temperature 0) can inflate
  pass^k. The write-up says so when temperature is pinned.
