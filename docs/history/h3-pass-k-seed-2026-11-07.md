# H3 pass^k per-Run seed run — 2026-11-07

This is a dated local record, not a task tracker, a pass-rate claim or an
accuracy study. It follows [h3-pass-k-temp1-2026-11-05.md](h3-pass-k-temp1-2026-11-05.md),
which found that the endpoint pins a default sampler seed. Earlier record:
[h3-pass-k-live-2026-11-04.md](h3-pass-k-live-2026-11-04.md). Current task
status: [phase2-tasks.md](../agents/phase2-tasks.md). Decision:
[ADR-0003](../adr/0003-openai-compatible-provider-boundary.md) (H3 amendment) and
[ADR-0010](../adr/0010-trial-groups-and-pass-k.md). Reading guide:
[06-pass-k.md](../learn/06-pass-k.md).

## Exercised boundary

- Feature: `AGENTOPS_PROVIDER_SEED=per-run` (commit `f00ef7d`), which gives each
  Run its own sampler seed.
- Setup as in the temperature-1 record: the installed Windows llama.cpp
  service, build `b12018`, alias `swift-1.5-27b-iq3xxs`, context 8192;
  `temperature=1`, `max_tokens=256`, `reasoning_effort=none`,
  `parallel_tool_calls=false`; host control plane and Rust Runner; fresh
  PostgreSQL; each cell is one trial group with `n = 5`, `k = 5` and
  Environment seed 42.
- Difference: the per-Run seed.

## Outcomes (n = 5, k = 5, temperature 1, per-Run seed)

2026-11-07, `reference-openai-compatible.v1`:

| Agent | Environment | successes | pass^1 | pass^5 | distinct trajectories | group id prefix |
|---|---|---|---|---|---|---|
| reference | research | 5/5 | 1.0 | 1.0 | 1 | `4593e3d6` |
| reference | fault | 2/5 | 0.4 | 0.0 | 3 | `b23faa14` |
| reference | checkout | 0/5 | 0.0 | 0.0 | 1 | `df27814c` |
| reference | order | 5/5 | 1.0 | 1.0 | 1 | `941cf476` |

2026-11-05, same settings, `pi-coding-agent.v1` (the batch was interrupted
after two groups; pi checkout and order were not run with a per-Run seed):

| Agent | Environment | successes | pass^1 | pass^5 | distinct trajectories |
|---|---|---|---|---|---|
| pi | research | 5/5 | 1.0 | 1.0 | 1 |
| pi | fault | 1/5 | 0.2 | 0.0 | 2 |

## What this shows

- **Independent sampling changed the reference fault group.** With a per-Run
  seed the five trials produced 3 trajectories. Before, at temperature 0 and
  at temperature 1 with the pinned seed, the group was 0/5 with 1 trajectory.
- **Stable choices elsewhere.** Research, checkout and order still produced
  one trajectory per group under independent sampling. On those tasks the
  model's choices are stable; the single trajectory is not a copy caused by
  the seed.
- **Occasional success is not reliable success.** Reference fault shows
  pass^1 0.4 against pass^5 0.0.

## Limits

One model, one endpoint, one Environment seed, `n = 5`. This is not a pass
rate, not evidence about other models, and not analyzer accuracy (H5). Earlier
records: [temperature 0](h3-pass-k-live-2026-11-04.md) and
[temperature 1](h3-pass-k-temp1-2026-11-05.md).
