# Evaluation Calibration

Status: analysis-blind review export and reporting tools ready; independent
human-labeled sample pending (2026-10-06). A provisional internal mechanics
gate passed for the fixed fixture packet; it is not an accuracy gate.

Verification update (2026-10-05): the corrected recovery packet
`.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`
is the current demo sample. Its matching temporary labels remain
agent-generated and are not an independent label set. Backend verification
with loopback proxy variables removed is `188 passed, 2 skipped`; the same
proxy boundary applies to fake-Provider tests and is recorded in the local
validation runbook.

Verification update (2026-09-29): a read-only copy of the existing Phase 1
volume is at Alembic revision `0005_runner_presence` and contains 33 persisted
`RunEvent` rows but only four terminal Runs under the legacy, unregistered
`checkout-api-latency` Scenario. There are zero eligible current registered
Scenario Runs, so no packet, labels, report, or accuracy result was generated.
The calibration-focused suite passes `22 passed` (the exporter/report subset
passes `6 passed`); synthetic fixtures remain mechanics-only evidence.

On 2026-10-05, the standard four-service stack produced a fresh snapshot
`sha256:dbd3734309ff439e6bcee38b133774e58470bf23b3abcdf4d9db2ad391f72427` and
an analysis-blind packet at
`.scratch/evaluation-calibration/review-packet-20261005.json`. It contains five
terminal Runs across the registered fixture Scenarios: checkout baseline/replay
and multi-step research baseline/replay, with a second checkout baseline from
the recovery exercise. The packet contains no scores or stored analysis. It is
ready for independent human annotation, but no labels or accuracy report exist
yet.

After the recovery-contract correction on 2026-10-05, an isolated standard
Compose stack passed both Golden scenarios and the Runner recovery test again.
The new PostgreSQL logical snapshot digest is
`sha256:2ad97aa9c22112dd55b2d55e669d605478facf260b3e138f6ea3853b0131f30c`,
and the replacement packet is
`.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`.
It contains five terminal Runs, including a three-step successful recovery
baseline; it remains analysis-blind and has no independent labels.

## Mechanics report and review record (2026-10-06)

The fixed packet and its matching agent-generated draft labels were checked
before running the report: all five `run_id` values match, and the packet
contains the expected current registered Scenarios. To exercise the report
without touching the source database, the five packet Runs and their persisted
`RunEvent` rows were reconstructed in a fresh temporary PostgreSQL database at
Alembic head `0008_regression_cases`. The source snapshot volume was not
modified.

The report was run with
`.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`
and its output is retained at
`.scratch/evaluation-calibration/demo-calibration-report-20261006.json`.
It reports five cases, no computed disagreements, and the following mechanics
result: `planning` and `budget` each have `tp=2, fp=0, fn=0, tn=3` with all
three metrics equal to `1.0`; `execution` and `context` have no positive
labels, so their precision, recall, and F1 are `null`; the micro totals are
`tp=4, fp=0, fn=0, tn=16` with all three metrics equal to `1.0`.

These numbers are deliberately **not** an accuracy result. The labels were
written by the agent from the visible packet and match the analyzer's expected
predictions by construction. The review record is
`.scratch/evaluation-calibration/review-record-20261006-demo.json` and records:

- reviewer role: `agent-generated provisional label author`; no independent
  human reviewer is claimed;
- provenance: packet path, logical snapshot digest, packet/label/report file
  hashes, analysis-blind status, reconstruction database, and report command;
- adjudication: pending independent human review; no agreement or adjudicator
  decision exists;
- publishable accuracy: `false`.

The temporary report database was disposable mechanics infrastructure and was
removed after the run. The label and report artifacts remain under ignored
`.scratch/` for inspection. Before publishing any metric, at least two
independent reviewers must annotate this fixed packet, record per-case evidence
and uncertainty, resolve disagreements with a named adjudicator, and rerun the
report against the adjudicated labels.

The report now supports explicit packet and v1 sidecar binding through
`--require-binding`. Legacy calls without those inputs remain `unbound` for
mechanics tests. Binding checks the packet file digest, source snapshot, the
explicit immutable analyzer revision, Run identity rows, terminal status,
current database Experiment/Run identity, and the canonical digest of each
exported trajectory reconstructed from `RunEvent`. Strict binding requires the
packet and sidecar revisions to match the source-controlled analyzer revision
(`failure-analyzer.v1` in the current implementation); it does not prove that
an external snapshot identifier is authentic. The packet is also
analysis-blind rather than outcome-blind; terminal status, limits, timings,
and observations can reveal outcomes.

## Provisional internal development gate (2026-10-06)

The absence of human reviewers does not block engineering work. The sidecar
record `.scratch/evaluation-calibration/provisional-internal-gate-20261006.json`
is a temporary internal gate for this fixed five-case fixture packet. The agent
reviewed every case against the documented rules and found no label correction
necessary:

- the two failed six-step baselines repeat identical tool calls and hit
  `max_steps=6`, so `planning + budget` is accepted provisionally;
- the three successful three-step Runs show no supported dimension evidence;
- no Run shows an execution or context positive, so those dimensions remain
  uncovered rather than validated;
- packet/label IDs, terminal state, Scenario contracts, event reconstruction,
  and report mechanics all pass.

This gate is `pass` only for failure-analyzer, trajectory-replay, Scenario
assertion, recovery-contract, packet/export, schema, and report-mechanics
regressions on this fixed fixture sample. It may not support human-label
precision/recall/F1, inter-rater agreement, adjudication claims, execution or
context coverage, production readiness, or claims about real Provider failure
distributions. The corresponding human-review gate remains
`pending_human_review`.

When independent labels become available, replace the provisional labels with
the human-reviewed set and keep this mechanics record as historical evidence;
do not rename or promote the agent-generated labels.

The offline report compares human labels with the current analyzer's
predictions for terminal Runs. It reads the Run's persisted `RunEvent` rows and
uses the same latest-attempt trajectory conversion and Scenario assertions as
terminal analysis. It does not change a Run, its analysis, or its score.

## Prepare labels

1. Export a fixed set of terminal Runs before looking at their analysis
   results. The exporter selects round-robin across registered Scenario,
   execution mode, Run kind, and terminal status; within each group it takes
   the oldest Runs first. Record the database snapshot identifier. Check the
   resulting mix: the tool cannot create missing Provider or failure examples.
2. Have reviewers label the task and sanitized persisted events without
   viewing `run_analyses`, score breakdowns, or this command's predictions.
   Resolve uncertain cases before reporting metrics. Keep the review method,
   reviewers, and adjudication record in `provenance`.
3. Store the label file outside tracked source, such as under ignored
   `.scratch/`. It contains Run IDs and labels, not raw Provider responses or
   credentials.

From `backend/`, with `DATABASE_URL` set to a migrated database, create the
local review packet before preparing labels:

```sh
mkdir -p ../.scratch/evaluation-calibration
uv run python -m app.calibration_review \
  --output ../.scratch/evaluation-calibration/review-packet.json \
  --source-snapshot '<backup or database snapshot ID>' --limit 32
```

The file is created with owner-only permissions (mode `0600`, possibly further
restricted by the local `umask`) and cannot overwrite an existing packet.
It contains the task and latest-attempt persisted trajectory but omits Run
scores, stored analysis, and Provider process output. Review the task and
observation text for sensitive content before sharing it with reviewers. The
packet is unlabeled; only independently completed reviews become the label
file below. Use consistent interpretations: `planning` covers wrong or
repeated tool choices, `execution` covers tool/Provider errors and timeouts,
`context` covers missing or overloaded context, and `budget` covers an
exhausted step or token allowance. Record uncertain cases and adjudication.

The local Phase 1 volume inspected on 2026-09-24 held only four legacy fixture
Runs under an unregistered Scenario ID. It cannot supply a current independent
sample. Collect current-version Runs with a deliberate scenario and execution
mode mix before claiming calibration results.

The JSON file has this shape:

```json
{
  "name": "example-only",
  "provenance": "Example only; replace with actual selection and analysis-blind review record",
  "cases": [
    {
      "run_id": "00000000-0000-4000-8000-000000000001",
      "dimensions": ["planning"]
    },
    {
      "run_id": "00000000-0000-4000-8000-000000000002",
      "dimensions": []
    }
  ]
}
```

Only `planning`, `execution`, `context`, and `budget` are valid dimensions.
An empty array means the reviewer found none. Duplicate Run IDs, missing Runs,
and non-terminal Runs are rejected.

For a bound report, keep reviewer metadata in a separate v1 sidecar. The
sidecar records the packet snapshot, explicit analyzer revision, packet-file
SHA-256, labels-file SHA-256,
one identity row per Run (`scenario_id`, `execution_mode`, `kind`, and `task`),
reviewer status and method, evidence/uncertainty, and adjudication status.
Each packet case also records a canonical `trajectory_sha256` derived from its
sanitized trajectory. The historical provisional sidecar is
`.scratch/evaluation-calibration/review-record-v1-20261007-provisional.json`;
the strict-binding derivative is
`.scratch/evaluation-calibration/review-record-v1-20261007-provisional-bound.json`,
paired with
`.scratch/evaluation-calibration/review-packet-20261007-recovery-fixed-bound.json`.
Those dated artifacts were created before the analyzer-revision field was
introduced; regenerate the packet and sidecar with the current exporter before
running a new strict report. Do not treat a historical file digest as evidence
that it satisfies the current binding contract.
Agent-generated sidecars must keep `publishable_accuracy` set to `false`.
To set `publishable_accuracy=true`, the sidecar must name at least two unique
human reviewer IDs and a completed adjudication with both an adjudicator and a
decision. Every case must also include non-empty evidence and uncertainty.
Empty or duplicate reviewer IDs, incomplete adjudication, missing case evidence
or uncertainty, and agent-generated review metadata are rejected. A publishable
record also requires `--require-binding`; otherwise the report refuses the
publishable claim.

## Generate the report

Set `DATABASE_URL` to the database containing those Runs, then run from the
repository root:

```sh
cd backend
uv run python -m app.calibration_report \
  --labels ../.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json \
  --packet ../.scratch/evaluation-calibration/review-packet-20261007-recovery-fixed-bound.json \
  --review-record ../.scratch/evaluation-calibration/review-record-v1-20261007-provisional-bound.json \
  --require-binding
```

`--require-binding` rejects a report without the packet, structured review
sidecar, packet-file digest, labels-file digest, and explicit analyzer revision.
Binding checks compare the complete Run ID set, packet source snapshot and file
digests, packet/sidecar analyzer revision, sidecar identity rows, packet
terminal status, current database Experiment/Run identity, and each packet
trajectory digest against both the packet content and the current `RunEvent`
reconstruction. Calls without these options remain available for old mechanics
tests and are reported as `unbound`.

The JSON output includes `accuracy_status`: `provisional_mechanics` for a
bound non-publishable review, `publishable` only after the human reviewer and
adjudication gate, and `unbound` for legacy calls. It also reports TP, FP, FN,
and TN for each dimension and across all
Run/dimension pairs (`micro`), plus precision, recall, F1, and the Run IDs with
disagreements. A metric is `null` when its denominator is zero. No event
payloads are printed. Review the Run IDs and provenance before sharing a
report.

The exporter is **analysis-blind**, rather than outcome-blind. It omits scores,
stored analysis, and raw Provider output, but the packet still includes the
Scenario and execution metadata plus trajectory fields such as terminal
status, `provider_error`, step limits, timing, and token counts. Reviewers may
therefore infer some outcome information from the packet. Do not describe this
as a double-blind or prediction-free human study.

The command verifies label-file structure, packet identity, sidecar metadata,
canonical trajectory content, and database state when binding is required. It
still does not establish that
labels are independent, representative, or reproducible across reviewers.
Round-robin strata do not prevent correlated baseline/replay pairs, and the
report provides no confidence intervals or inter-rater agreement.
The existing synthetic calibration fixtures and the command's tests verify
mechanics only; they are not an accuracy measurement. Publish metrics only
after an independently reviewed sample, selection record, adjudication record,
and the limitations above are documented.

## Demo temporary labels

For the current demo-only packet, an agent-generated draft is retained at
`.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`.
The earlier `.scratch/evaluation-calibration/demo-temp-labels-20261005.json`
belongs to the historical pre-fix packet. The current draft is useful
for exercising the report command and preparing a human review, but it is not
an independent label set and must not be used to claim precision, recall, F1,
or production readiness. The draft's provenance says this explicitly.

Use the following rules when an agent prepares a draft or a reviewer edits it:

| Dimension | Label positive only when the trajectory itself shows | Do not label merely because |
| --- | --- | --- |
| `planning` | repeated/circular tool use, wrong tool or arguments, or a failed Scenario planning assertion | the Run failed, the answer was incomplete, or the Scenario was difficult |
| `execution` | a Provider/tool error, HTTP 4xx/5xx observation, timeout, or equivalent execution failure | a normal slow call, a failed assertion, or a failed Run without execution evidence |
| `context` | explicit truncation/context evidence or context-window use above 95% of its limit | token counts are nonzero or the Run has several steps |
| `budget` | the trajectory reaches `max_steps` (or an explicit token/step allowance) without success | a Run ends early, succeeds, or has a large but unused allowance |

The label is a set: multiple dimensions are allowed when independent evidence
supports each one, and an empty set means no demonstrated dimension. Reviewers
should cite the step index or trajectory field in their working notes, keep
uncertain cases separate, and avoid inferring a cause from terminal status
alone. A successful replay can still have a dimension if the trajectory shows
one, and a failed Run can have an empty set if no supported dimension is
observable.

Check that each packet task matches its Scenario contract before applying
Scenario assertions. The recovery harness intentionally exercises the
checkout Scenario while injecting a Runner fault, so it uses the checkout
Scenario's default task and emits the same successful tool sequence after
replacement. Runner recovery remains an operational fault test, not evidence
of a planning failure by itself; do not infer a dimension from terminal status.

This contract correction was made after the first 2026-10-05 packet was
captured. That packet and its temporary labels remain historical demo artifacts
with the old one-step recovery trace; do not edit them in place. The corrected
packet named above contains the three-step recovery trace. Export another
packet only after collecting a new standard Compose sample.

For the 2026-10-05 demo draft, the two failed six-step baselines are marked
`planning` plus `budget` because they repeat the same action/arguments and hit
`max_steps=6` without success. The recovery and successful replay trajectories
are empty because they show no supported planning, execution, context, or
budget evidence. These are examples for manual correction, not adjudicated
labels. After editing, replace `name` and `provenance` with the real reviewer
method, roles, snapshot identifier, and adjudication record; do not overwrite
the analysis-blind packet.
