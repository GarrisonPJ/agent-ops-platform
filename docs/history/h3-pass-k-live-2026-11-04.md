# H3 pass^k live run — 2026-11-04

This is a dated local record, not a task tracker, a pass-rate claim or an
accuracy study. Current task status: [phase2-tasks.md](../agents/phase2-tasks.md).
Decision: [ADR-0010](../adr/0010-trial-groups-and-pass-k.md) and its H3
amendments. Reading guide: [06-pass-k.md](../learn/06-pass-k.md).

## Exercised boundary

- Source: `264fa42`, plus the `--trials/--k` capture option in
  `scripts/local_model_acceptance.py` that this record commits. The tightened
  group validation rule changes nothing below, because no candidate group was
  replayed.
- Model: the already installed Windows-native llama.cpp service,
  build `b12018-8956c769e`, alias `swift-1.5-27b-iq3xxs`, context 8192. It was
  started and later stopped with the operator's `Manage-Swift.ps1`.
- Runner settings: `temperature=0`, `max_tokens=256`, `reasoning_effort=none`,
  `parallel_tool_calls=false`, timeout 60000 ms, one retry, which matches H1-5
  and H2-1.
- Path: fresh invocation-owned PostgreSQL 16.15, host API, host Rust Runner,
  `config/agents.external.toml`. Each cell is one
  `POST /api/experiments/{id}/trials` group with `n = 5`, `k = 5` and seed 42.
  The five members ran one after another on one Runner.
- The temperature-1 comparison ran on 2026-11-05:
  [h3-pass-k-temp1-2026-11-05.md](h3-pass-k-temp1-2026-11-05.md).

## Outcomes (n = 5, k = 5, temperature 0)

| Agent | Environment | successes | pass^1 | pass^5 | mean score | mean steps | distinct trajectories |
|---|---|---|---|---|---|---|---|
| reference | `multi-step-research.v2` | 5/5 | 1.0 | 1.0 | 0.96202 | 3.0 | 1 |
| reference | `api-fault-orchestration.v2` | 0/5 | 0.0 | 0.0 | 0.0 | 4.0 | 1 |
| pi | `multi-step-research.v2` | 5/5 | 1.0 | 1.0 | 1.0 | 3.0 | 1 |
| pi | `api-fault-orchestration.v2` | 5/5 | 1.0 | 1.0 | 1.0 | 3.0 | 1 |

Trial group IDs: reference research `53f46ed8-89f3-48e0-9815-f51c67a21d32`,
reference fault `6af250bf-b5dd-4d29-82eb-31ea10eed908`, pi research
`082d5a7b-0c77-49bd-b5b7-30c40c58680d`, pi fault
`9a67c0e1-c010-4a71-a540-38da04bd8b79`. Member Runs took 9.8–19.2 s each.

Within every group, all five members produced the same world-call sequence
and the same quality score. The reference fault group repeated
`invoke_service → retry_call → degrade_route → invoke_service` five times,
failing the existing sequence assertion each time (analyzer:
`loop_no_progress`). pi repeated `retry_call → degrade_route → invoke_service`
and succeeded each time. Each group agrees with the single-Run H2-1 outcome
for the same cell.

## What this shows

- **At temperature 0 the trials are copies, so pass^5 equals pass^1.** Same
  prompt, same seed and greedy decoding gave one trajectory per group. The
  estimator then measures whether that one trajectory succeeds, not the
  chance that five independent attempts all succeed. These numbers overstate
  reliability and must not be read as five independent samples.
- **Grouping still exposed nothing new here.** Every cell matched its single
  H2-1 Run. A spread needs sampling (temperature above 0) or varied seeds.
- **Publication and checks.** All four groups reached `state = complete`.
  The capture script checked that each group's `successes` matches its
  captured member statuses, and that every member trace is contiguous with
  terminal agreement. The reference fault group's failures created one
  candidate Policy for that group, not one per failed trial. No candidate was
  replayed.

## Deterministic checks for this increment

Parent-local, not independent review: backend `phase1_tests` on Python 3.12
with PostgreSQL (all PostgreSQL-only modules), frontend unit, build and
Recorded Chromium E2E. Hosted CI passed for `489f1c4` (H3-1), `7e89804` (H3-2)
and `264fa42` (H3-3 page). The commit carrying this record runs its own CI.

## Limits

Four groups of five at temperature 0 on one model. This is not a pass rate,
not evidence about other models or temperatures, and not analyzer accuracy
(H5). The temperature-1 comparison is in
[h3-pass-k-temp1-2026-11-05.md](h3-pass-k-temp1-2026-11-05.md). Capture directories
stay outside the repository; no API key, capability token or Provider body is
published.
