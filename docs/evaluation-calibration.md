# Evaluation Calibration

Status: analysis-blind review export and reporting tools ready; independently
labeled sample pending (2026-09-25).

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

## Generate the report

Set `DATABASE_URL` to the database containing those Runs, then run from the
repository root:

```sh
cd backend
uv run python -m app.calibration_report --labels ../.scratch/evaluation-calibration/labels.json
```

The JSON output reports TP, FP, FN, and TN for each dimension and across all
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

The command verifies label-file structure and database state, not whether the
labels are independent, representative, or reproducible across reviewers.
Round-robin strata do not prevent correlated baseline/replay pairs, and the
report currently provides no confidence intervals or inter-rater agreement.
The existing synthetic calibration fixtures and the command's tests verify
mechanics only; they are not an accuracy measurement. Publish metrics only
after an independently reviewed sample, selection record, adjudication record,
and the limitations above are documented.
