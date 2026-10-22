# Calibration evidence log

Procedure and publication rules live in
[evaluation-calibration.md](../evaluation-calibration.md). This file only
keeps dated checkpoints and the labeling rubric that the procedure points
here for. Raw dumps, packets, and sidecars stay in a local evidence
directory; they are not in git.

## Rule change (2026-10-17)

`publishable_accuracy` no longer waits on an independent labeler. The CI gate
stays `publishable_accuracy=false`. An agreement study may set it true only
when the author labeled the runs and the write-up says so. Do not add binding
or hashing features. See the procedure's "Scope and status".

## Mechanics checkpoints

| Date | What was shown | Limit |
|---|---|---|
| 2026-10-13 | Three-scenario fixture calibration notes | Not a real-provider sample |
| 2026-10-15 | Six-contract capture, PostgreSQL dump/restore, strict report equal after normalizing the two binding digest prefixes. CI job `database-recovery` green on `8bd2ef0` | Does not exercise the Rust supervisor or an external model. `failure-analyzer.v2` |

The six contracts: `context_pressure`, `context_low_pressure`,
`context_unknown`, `provider_success`, `provider_timeout`,
`provider_invalid_response`.

Provisional fixture review (agent-generated, not human): two failed six-step
baselines accepted as planning + budget; three successful three-step runs
with no positive dimension; execution and context not covered. Details are in
the procedure under "Local development gate".

## Labeling rubric

Use this when labeling. Do not look at `run_analyses` first.

| Dimension | Positive when | Unknown when |
|---|---|---|
| planning | Wrong tool, or the same tool and arguments repeat with no progress | The trace is too thin to judge the choice |
| execution | Tool error, HTTP 4xx/5xx, provider error, or timeout evidence | No observation describes an error and none was expected |
| context | Truncation, or context-window ratio above 0.95 | No context measurement was stored |
| budget | Step or token budget exhausted without success | Limits were not on the spec |

A dimension in neither set is an assessed negative. Empty measurements are
unknown, not a confirmed negative. Disagreement between these rules and the
analyzer is a finding; do not edit the label to match the analyzer.
