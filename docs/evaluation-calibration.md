# Evaluation calibration

## Scope and status (2026-10-17)

The failure analyzer is rule-based (`failure-analyzer.v2`). It classifies terminal
Runs along four dimensions: `planning`, `execution`, `context`, and `budget`.
Labels distinguish positive dimensions, assessed negatives, and `unknown_dimensions`
when evidence is insufficient. Reports use evaluation scope
`assessed_run_dimension_pairs`, expose per-dimension and micro coverage, and
compare supplied labels with analyzer predictions reconstructed from persisted
`RunEvent` rows.

Calibration serves two purposes:

**(a) Analyzer regression gate** — Six controlled contracts
(`context_pressure`, `context_low_pressure`, `context_unknown`, `provider_success`,
`provider_timeout`, `provider_invalid_response`) are captured through the
authenticated Runner API with a loopback Fake Provider, real PostgreSQL
dump/restore, and a strictly bound report. This gate runs in CI
(`scripts/controlled_calibration_e2e.py`). It guards analyzer, trajectory, and
exporter changes. It proves capture, binding, backup/restore, and report
mechanics; it does **not** prove accuracy on real agent behavior.

**(b) Agreement study (Phase 2 H5)** — The author labels roughly forty real
terminal Runs from local models and external agents across three to four
Environments, using the packet and report CLIs documented below. Publication
includes a confusion matrix per dimension, precision/recall/F1, coverage with
unknowns, a disagreement list with the labeling rule that failed, known biases,
and a methodology section (sample selection, single self-labeler, correlated
baseline/replay pairs, no confidence intervals).

**`publishable_accuracy`:** Provisional and regression artifacts keep
`publishable_accuracy=false`. For the agreement study, `publishable_accuracy` may
be `true` when the sidecar and report use `--require-binding`, labels are
author-generated (`reviewer=author` or equivalent), and the published
methodology states that labels are author-generated. Independent human labels
and adjudication are a documented limitation, not a publication gate.

**Frozen mechanics:** Do not add new binding, digest, snapshot-identity, or
strict-mode behavior; maintain existing contracts only.

| Artifact | Produced by | Purpose |
| --- | --- | --- |
| Review packet | `python -m app.calibration_review` | Analysis-blind export of terminal Runs for labeling |
| Labels JSON | Author or provisional reviewer | Positive and unknown dimension sets per Run |
| Review sidecar (v1) | Prepared alongside labels | Binding metadata, reviewer identity, per-case evidence |
| Bound report | `python -m app.calibration_report --require-binding` | Metrics, coverage, disagreements, `accuracy_status` |
| Controlled capture manifest | `controlled_calibration_capture` / e2e harness | Six-contract regression gate evidence |

## Analyzer regression gate

`scripts/controlled_calibration_e2e.py` makes the six-case controlled gate
repeatable without manually provisioning the capture database. Supply
`DATABASE_URL` in the project's SQLAlchemy async format, `postgresql+asyncpg://`,
through a protected environment for a dedicated PostgreSQL test service whose
role has `CREATEDB`; install matching `pg_dump` and `pg_restore`
clients on `PATH` (CI uses PostgreSQL 16). The script generates a temporary
Runner token in memory when none is supplied. From `backend/`, choose an output
directory that does not exist. Store output under a **local evidence directory
(not in the repository)**:

```sh
PYTHONPATH=. VIRTUAL_ENV= uv run python ../scripts/controlled_calibration_e2e.py \
  --output <local-evidence>/calibration-postgres-NEW
```

The script creates uniquely named capture and restore databases, migrates the
capture database, runs the existing six contracts, and restores their actual
`snapshot.dump` into the second database. It checks the backup digest, compares
source and restored database manifests, and requires the restored strict report
to equal the source strict report in all fields after normalizing only
`binding.packet_sha256` and `binding.labels_sha256`: both must be valid
64-hex digests, with an optional `sha256:` prefix. The original report files are
retained unchanged. The checks include
`assessed_run_dimension_pairs` scope, context known 2 / unknown 4,
micro known 20 / unknown 4, `provisional_mechanics`, and
`publishable_accuracy=false`. This verifies backup/report mechanics for the
controlled sample only; see **Scope and status** for publication rules.

The output retains the original capture artifacts under `capture/`, plus
`restored-strict-report.json` and `e2e-summary.json`. Failures retain available
artifacts and a summary with the failed stage and error type, without logging
credentials or environment settings. Cleanup runs on success and failure and
only drops databases created by this invocation; each rerun needs a new output
path.

The `database-recovery` CI job runs this command after its retention tests,
using a local evidence directory (not in the repository) for artifacts and
separate temporary databases on the PostgreSQL service. As of commit `8bd2ef0`,
GitHub Actions on that revision is green, including this job. Contract
definitions, capture CLI usage, encoded-credential follow-up, and dated
verification checkpoints are in the
[calibration evidence log](history/calibration-evidence-log.md).

### Local development gate

The absence of human reviewers does not block engineering work. A provisional
sidecar record in a local evidence directory (not in the repository) is a
temporary internal gate for a fixed five-case fixture packet. The agent
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
regressions on this fixed fixture sample. It may not support precision/recall/F1
on a representative sample, execution or context coverage, production readiness,
or claims about real Provider failure distributions.

When agreement-study labels replace provisional labels for publication, keep
this mechanics record as historical evidence; do not rename or promote
agent-generated provisional labels as independent review.

## Agreement study (Phase 2 H5)

**Goal:** Measure agreement between author labels and `failure-analyzer.v2` on
real Runs (successes and failures), and publish bounded metrics with explicit
limitations.

**Sample:** About forty terminal Runs across three to four Environments, including
the reference agent on a local model and at least one external adapter. Include
both successes and failures. Expect correlated baseline/replay pairs from
round-robin export strata.

**Procedure:** Export an analysis-blind packet (`python -m app.calibration_review`).
Label using the rules in **Prepare labels**. Prepare a v1 sidecar with
`reviewer=author` (or equivalent), per-case evidence and uncertainty, and
hashes for binding. Run
`python -m app.calibration_report --require-binding`.

**Publication (in `docs/`):** Confusion matrix per dimension; precision, recall,
and F1; coverage including unknowns; disagreement list with the labeling rule
that failed; known biases (single self-labeler, correlated pairs, analysis-blind
but not outcome-blind packet). Methodology must state author-generated labels.

**Feedback:** Poor agreement should drive analyzer rule changes, a bump to
`failure-analyzer.v3`, and a rerun of the six-contract regression gate. Never tune
labels to match the analyzer.

## Prepare labels

Store artifacts under a **local evidence directory (not in the repository)**. The
examples below use `$CALIBRATION_EVIDENCE` as a placeholder path outside tracked
source.

1. Export a fixed set of terminal Runs before looking at their analysis
   results. The exporter selects round-robin across registered Scenario,
   execution mode, Run kind, and terminal status; within each group it takes
   the oldest Runs first. Retain the database snapshot and record its digest.
   Check the resulting mix: the tool cannot create missing Provider or failure
   examples.
2. For the analyzer regression gate and local mechanics checks, use
   agent-generated provisional labels with `publishable_accuracy=false`. For the
   agreement study, the author labels the task and sanitized persisted events
   without viewing `run_analyses`, score breakdowns, or this command's
   predictions. Mark dimensions that cannot be assessed as unknown. Record
   reviewer identity and method in `provenance`; see **Scope and status** for
   when `publishable_accuracy` may be `true`.
3. Store the label file outside tracked source in the local evidence directory.
   It contains Run IDs and labels, not raw Provider responses or credentials.

From `backend/`, with `DATABASE_URL` set to a migrated, quiescent isolated
sample database, capture a backup and create the local review packet before
preparing labels. PostgreSQL 16 client prerequisites are documented in the
[backup runbook](operations/backup-restore.md). Keep the database unchanged
through backup, export, and reporting, or export and report from an isolated
database restored from the retained backup. Choose unused filenames and stop
if any command fails. The `20261013-v1` names below illustrate a new artifact
set, not a completed export or verification:

```sh
umask 077
mkdir -p "$CALIBRATION_EVIDENCE/evaluation-calibration"
uv run python -m app.database_recovery backup \
  --output "$CALIBRATION_EVIDENCE/evaluation-calibration/sample-20261013-v1.dump"
calibration_snapshot_sha256=$(sha256sum "$CALIBRATION_EVIDENCE/evaluation-calibration/sample-20261013-v1.dump" | cut -d ' ' -f 1)
uv run python -m app.calibration_review \
  --output "$CALIBRATION_EVIDENCE/evaluation-calibration/review-packet-20261013-v1.json" \
  --source-snapshot "sha256:$calibration_snapshot_sha256" --limit 32
```

Retain the actual backup file and a sanitized capture record identifying the
source database, capture time, application/schema revision, and digest. Pass
that same `source_snapshot` value into the sidecar. Neither CLI reads the
backup to verify that it produced the packet: canonical `sha256:<64hex>` syntax
is a format check, not proof of authenticity or database correspondence.

The file is created with owner-only permissions (mode `0600`, possibly further
restricted by the local `umask`) and cannot overwrite an existing packet.
It contains the task and latest-attempt persisted trajectory but omits Run
scores, stored analysis, and Provider process output. Review the task and
observation text for sensitive content before sharing it with reviewers. The
packet is unlabeled; prepare its matching label file separately, for example
`$CALIBRATION_EVIDENCE/evaluation-calibration/labels-20261013-v1-provisional.json`.
See **Scope and status** before publishing accuracy metrics. Use consistent
interpretations: `planning` covers wrong or
repeated tool choices, `execution` covers tool/Provider errors and timeouts,
`context` covers explicit context loss/truncation or pressure above 95%, and
`budget` covers an exhausted step or token allowance. Missing context
measurements alone make `context` unknown. Record uncertain cases in the
sidecar.

The JSON file has this shape:

```json
{
  "name": "example-only",
  "provenance": "Example only; replace with actual selection and analysis-blind review record",
  "cases": [
    {
      "run_id": "00000000-0000-4000-8000-000000000001",
      "dimensions": ["planning"],
      "unknown_dimensions": ["context"]
    },
    {
      "run_id": "00000000-0000-4000-8000-000000000002",
      "dimensions": []
    }
  ]
}
```

Only `planning`, `execution`, `context`, and `budget` are valid dimensions.
`dimensions` is the positive set; optional `unknown_dimensions` is the set the
reviewer could not assess. Both sets use the same allowed names and must be
disjoint. A dimension in neither set is an assessed negative. An empty
`dimensions` array means no positives, so use `unknown_dimensions` whenever an
absence of evidence cannot support a negative. Omitting `unknown_dimensions`
defaults to an empty set and preserves historical binary-label behavior.
Duplicate Run IDs, missing Runs, non-terminal Runs, and overlapping positive
and unknown dimensions are rejected.

For a bound report, keep reviewer metadata in a separate v1 sidecar. The
sidecar records the packet snapshot, explicit analyzer revision, packet-file
SHA-256, labels-file SHA-256,
one identity row per Run (`scenario_id`, `execution_mode`, `kind`, and `task`),
reviewer status and method, evidence/uncertainty, and adjudication status.
A single-author agreement study sets adjudication to `not_applicable`: there
is no second reviewer. Provisional mechanics sidecars stay `pending`.
Each packet case records a canonical `trajectory_sha256` derived from its
sanitized trajectory and an `event_stream_sha256` fidelity digest derived from
all persisted `RunEvent` rows. Historical provisional sidecars from a local
evidence directory (not in the repository) may predate mandatory
`event_stream_sha256` or analyzer-revision fields; export a new packet with the
current exporter and separately prepare matching labels and a v1 sidecar before
running a new strict report. The exporter creates only the packet; it does not
create labels or a sidecar. Do not treat a historical file digest as evidence
that it satisfies the current binding contract.

Provisional and regression-gate sidecars must keep `publishable_accuracy` set to
`false`. For an agreement-study publication, the sidecar may set
`publishable_accuracy=true` when reviewer metadata identifies the author (for
example `reviewer_ids` including `author`), every case includes non-empty
evidence and uncertainty, and the published methodology states that labels are
author-generated. The report requires `--require-binding` for a publishable
claim; otherwise it refuses that claim. Missing case evidence or uncertainty is
rejected regardless of publishable status. Valid unknown dimensions do not by
themselves force `publishable_accuracy` to `false`; binding and coverage
limitations still apply. A publishable record does not establish complete
coverage or a representative sample. Report the coverage and selection
limitations alongside any metrics.

For a new provisional artifact set, prepare
`$CALIBRATION_EVIDENCE/evaluation-calibration/review-record-v1-20261013-provisional.json`
separately. Compute the packet and finalized labels file hashes from their
exact bytes, including whitespace:

```sh
sha256sum "$CALIBRATION_EVIDENCE/evaluation-calibration/review-packet-20261013-v1.json" \
  "$CALIBRATION_EVIDENCE/evaluation-calibration/labels-20261013-v1-provisional.json"
```

Use this v1 sidecar shape, replacing every placeholder with the actual value.
Copy `analyzer_revision` and `source_snapshot` from the new packet, set
`case_count` to its case count, and include one identity row per packet Run
with its exact `run_id`, `scenario_id`, `execution_mode`, `kind`, and `task`.
The single case below is illustrative; the digest placeholders are not valid
strict-binding inputs:

```json
{
  "version": "v1",
  "packet": {
    "analyzer_revision": "<copy from new packet>",
    "source_snapshot": "sha256:<digest of retained backup>",
    "case_count": 1,
    "packet_sha256": "<packet file SHA-256>",
    "labels_sha256": "<finalized labels file SHA-256>"
  },
  "reviewer": {
    "status": "agent_generated_provisional",
    "reviewer_ids": ["<actual agent reviewer ID>"],
    "method": "<actual provisional review method>"
  },
  "adjudication": { "status": "pending" },
  "cases": [
    {
      "run_id": "00000000-0000-4000-8000-000000000001",
      "scenario_id": "<copy from packet case>",
      "execution_mode": "<copy from packet case>",
      "kind": "<copy from packet case>",
      "task": "<copy from packet case>",
      "evidence": ["<actual step or trajectory evidence>"],
      "uncertainty": "<actual uncertainty or explicit none>"
    }
  ],
  "publishable_accuracy": false
}
```

Recompute the corresponding file hash if the packet or labels change, including
any `unknown_dimensions` edit, and review the sidecar's case metadata again.
The existing labels-file digest covers the exact bytes; no packet or sidecar
schema change is needed. Provisional artifact sets support engineering
verification while agreement-study labeling proceeds.

The dimension labeling table and demo examples are in the
[calibration evidence log](history/calibration-evidence-log.md).

## Generate the report

After the new packet, matching labels, and sidecar are ready, set
`DATABASE_URL` to the same fixed sample database (or its verified isolated
restore), then run from the repository root:

```sh
cd backend
uv run python -m app.calibration_report \
  --labels "$CALIBRATION_EVIDENCE/evaluation-calibration/labels-20261013-v1-provisional.json" \
  --packet "$CALIBRATION_EVIDENCE/evaluation-calibration/review-packet-20261013-v1.json" \
  --review-record "$CALIBRATION_EVIDENCE/evaluation-calibration/review-record-v1-20261013-provisional.json" \
  --require-binding
```

`--require-binding` requires the packet, v1 sidecar, file digests, and explicit
analyzer revision, then checks Run identity, terminal status, trajectory and
event-stream digests against the database, and canonical `sha256:<64hex>`
`source_snapshot` syntax. Legacy calls without binding stay `unbound`. Binding
does not prove external snapshot authenticity.

The JSON output includes `accuracy_status`: `provisional_mechanics` for a
bound non-publishable review, `publishable` when the sidecar's publishable gate
passes (see **Scope and status**), and `unbound` for legacy calls. It also
reports TP, FP, FN, and TN for each dimension and across assessed Run/dimension
pairs (`micro`), plus precision, recall, F1, and the Run IDs with disagreements.
Predictions for unknown dimensions contribute no confusion counts or
disagreements. A metric is `null` when its denominator is zero. The existing
metrics dictionaries keep their structure; the report adds the stable top-level
`evaluation_scope="assessed_run_dimension_pairs"` and `coverage` fields.

Coverage reports `known_count`, `unknown_count`, `total_count`, and `ratio`
(`known_count / total_count`) for each dimension and for micro (the six
controlled contracts expect context 2 known / 4 unknown and micro 20 / 4; see
the evidence log). Unknown coverage must remain visible even when assessed-pair metrics show
perfect agreement. No event payloads are printed; `event_stream_sha256` binds
the persisted stream without placing its raw payload in the packet. Review the
Run IDs and provenance before sharing a report.

The packet is analysis-blind (not outcome-blind): trajectory metadata can reveal
outcomes. The command verifies structure and binding; synthetic fixtures test
mechanics only. Publish agreement-study metrics only with methodology, sample
selection, author-label limitation, and coverage or correlation limits documented
in `docs/`.

## History

Dated captures, verification checkpoints, mechanics demos, and label examples
are preserved in [calibration evidence log](history/calibration-evidence-log.md).
