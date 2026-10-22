# ADR-0010: Trial groups and pass^k

- Status: Accepted
- Date: 2026-10-17
- Implementation: Phase 2 H3. Not in code at `8bd2ef0`.
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
  score delta. A single lucky trial cannot validate a policy.

Before/After for groups compares those aggregates plus the existing
per-run comparison for a chosen pair. The Experiment page shows pass^1,
pass^k, and per-trial status. The Trace page does not gain new views
(trace UI is frozen).

Schema: new columns on `runs` for `trial_group_id` and `trial_index`, one
Alembic revision, `schema_version` stays 1. Absent columns mean "not in a
group" for old rows.

## Consequences

- Fixture Golden E2E stays a single trial. pass^k is exercised in tests with
  scripted success/failure mixes, not with a live model.
- A README number from a local model must name the model, Environment
  version, `n`, and `k`.
- Correlated trials (same prompt, same seed, temperature 0) can inflate
  pass^k. The write-up says so when temperature is pinned.
