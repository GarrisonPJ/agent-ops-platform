# Evaluation Calibration

Status: analysis-blind export, bound reporting, and controlled capture are ready
(2026-10-15). The six-case controlled packet uses `failure-analyzer.v2`.
Labels distinguish positive, negative, and unknown dimensions; reports measure
assessed Run/dimension pairs and expose their coverage. Independent human labels
and adjudication remain pending; they do not block engineering work.

## Controlled capture (2026-10-15)

`backend/scripts/controlled_calibration_capture.py` fixes input contracts before
execution, persists Runs through authenticated ASGI Runner API routes, and
compares separate contract labels with predictions. A mismatch retains the
artifacts and fails the command. Context measurements are injected; Provider
cases call a loopback Fake Provider. This does not exercise Rust supervision,
the standard container stack, or an external model.

| Case | Positive dimensions | Unknown dimensions | Evidence boundary |
| --- | --- | --- | --- |
| Context pressure | `context` | Empty | Injected use of 8191/8192, above 95%. |
| Low context pressure | Empty | Empty | Measured use of 1200/8192 with no truncation signal. |
| Context unknown | Empty | `context` | Measurement absent; this is not a confirmed context negative. |
| Provider success | Empty | `context` | Three valid checkout tool calls followed by a final response; context pressure is unmeasured. |
| Provider timeout | `execution` | `context` | Timeout before any completed tool step; context pressure is unmeasured. |
| Invalid Provider response | `execution` | `context` | Empty choices before any completed tool step; context pressure is unmeasured. |

Dimensions outside both sets are assessed negatives. These contract definitions
give expected context coverage of two known and four unknown pairs out of six,
and micro coverage of 20 known and four unknown pairs out of 24. The fresh
unknown-aware capture below verifies these contract counts.

The last two cases exposed a real analyzer defect: the zero-step branch kept
execution evidence but omitted the dimension and dominant projection. The fix
bumps the semantic revision to `failure-analyzer.v2`. Historical v1 packets stay
unchanged and cannot satisfy current strict revision binding. In particular,
the earlier controlled capture under
`.scratch/roadmap-closeout-20261013/controlled-capture-20261013/` incorrectly
gave those Provider failures empty labels; its agreement result is defective
and must not be used as the current gate.

Use a new, empty disposable PostgreSQL database named
`agentops_calibration_<suffix>` (or exactly `agentops_controlled_calibration`),
migrated to the application head. Supply `DATABASE_URL` and `RUNNER_TOKEN`
through a protected environment; do not use a working/demo database. From
`backend/`, choose an unused output directory and run:

```sh
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  UV_CACHE_DIR=/tmp/agentops-uv-cache \
  uv run python -m scripts.controlled_calibration_capture \
  --output ../.scratch/evaluation-calibration/controlled-capture-NEW
```

The tool writes `case-contracts.json`, a real `snapshot.dump`, `packet.json`,
`provisional-labels.json`, `review-record.json`, `strict-report.json`, and
`capture-manifest.json` with file hashes, source-snapshot identity, and per-case
provenance. Existing directories and nonempty or outdated databases are
rejected. Use a fresh database and directory for each rerun.

The earlier binary-label capture on 2026-10-15 is retained locally at
`.scratch/roadmap-closeout-20261015/controlled-capture-20261015/`:
six terminal Runs, 30 RunEvents, schema `0008_regression_cases`, strict binding
`bound`, no contract disagreements, `accuracy_status=provisional_mechanics`,
and `publishable_accuracy=false`. The actual backup digest is
`sha256:f081ab0c53d9df09925f7b449b9b024dbc1a11b33d40439d6c959920c90b053b`.
The reviewer is agent-generated provisional and adjudication is pending.
That report treated every unlisted dimension as an assessed negative, including
unmeasured context. It is historical binary-label mechanics evidence, not a
result for the current unknown-aware evaluation scope or general accuracy.
Retain its artifacts unchanged. Backend verification at that closeout passed
all 225 tests, including PostgreSQL checks, with no skips.

The fresh unknown-aware batch is retained at
`.scratch/roadmap-closeout-20261015/controlled-capture-unknown-20261015/`.
Its six Runs and 30 events pass strict binding with no contract disagreements:
context has two known and four unknown decisions (`tp=1`, `tn=1`); micro has
20 known and four unknown decisions (`tp=3`, `tn=17`). Its scope is
`assessed_run_dimension_pairs`. The real backup digest is
`sha256:22199fe4fc3a4ba5db99e700193df2612e7f50696abfbccdbd99d9068fb839c5`.
The report remains `provisional_mechanics`, `publishable_accuracy=false`, with
agent-generated review and pending adjudication. This is the current controlled
mechanics gate; preserve both earlier batches unchanged. The analyzer remains
`failure-analyzer.v2`, and the packet and v1 review-sidecar schemas are unchanged.
The report does not independently authenticate an external backup. Full backend
verification after this change passes 228 tests including isolated PostgreSQL
checks, with no skips; logs are retained under
`.scratch/roadmap-closeout-20261015/unknown-dimensions-backend/`.

## Historical verification

Verification update (2026-10-13): local evidence at that date is indexed at ignored
`.scratch/evaluation-calibration/20261013-compose-refresh-110352Z/README.md`.
All three Golden Scenarios and Runner kill/replacement recovery passed in an
isolated stack. The fault Scenario was rerun with checks requiring six baseline
steps, three replay steps, and assertion aggregate `1.0` before activation.
The resulting frozen PostgreSQL database at `0008_regression_cases` contains
nine terminal Runs and 67 RunEvents. Backend verification passes
`208 passed, 2 skipped` with loopback proxies removed.

The standard API and Web Dockerfiles built using cached layers. The standard
Runner build stalled during dependency installation and did not complete;
the functional run used a cached Runner image with current Python source
mounted read-only. Its Rust binary matched the current build-stage hash.
No fresh registry pull, standard Runner build, or hosted CI success is claimed.

The retained actual logical backup is `snapshot-final.dump`, with digest
`sha256:b743bf85cd542e432bbe063afe25ff038cf9aa6a558a44ec3d704b33b0dfe7ea`.
In the same evidence directory, `packet-final.json`,
`provisional-labels-final.json`, `review-record-v1-final.json`, and
`strict-report.stdout` form the matching artifact set. The packet includes
`failure-analyzer.v1` and all trajectory/event-stream digests. File hashes,
source consistency, and source-snapshot equality were checked. The strict
report is `bound` with `accuracy_status=provisional_mechanics` and
`publishable_accuracy=false`. The sidecar identifies the agent reviewer,
per-case evidence/uncertainty, and pending adjudication; the author had read
analyzer code and no independent review is claimed. These correlated fixture
pairs include a repeated fault pair, no positive context case, and no Provider
sample. They support engineering verification only. The isolated services
were removed after verification; the dump and all data volumes were retained.

Verification update (2026-10-05): the corrected recovery packet
`.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`
was the demo sample at that date. Its matching temporary labels remain
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
exported trajectory reconstructed from `RunEvent`. Each packet case also carries
an `event_stream_sha256` digest over the complete persisted event stream
(`sequence`, `event_type`, normalized UTC `occurred_at`, and `payload`); the
digest exposes no event payload values. Strict binding requires the
packet and sidecar revisions to match the source-controlled analyzer revision
(`failure-analyzer.v2` in the current implementation); it does not prove that
an external snapshot identifier is authentic. In strict mode, `source_snapshot`
must use the canonical `sha256:<64hex>` form; this catches malformed or
ambiguous identifiers but does not verify that an external backup or database
snapshot actually produced the digest. Historical packets remain parseable and
non-strict mechanics calls retain the legacy string form. The packet is also
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

The offline report compares supplied labels with the current analyzer's
predictions for terminal Runs. It reads the Run's persisted `RunEvent` rows and
uses the same latest-attempt trajectory conversion and Scenario assertions as
terminal analysis. It does not change a Run, its analysis, or its score.

## Prepare labels

1. Export a fixed set of terminal Runs before looking at their analysis
   results. The exporter selects round-robin across registered Scenario,
   execution mode, Run kind, and terminal status; within each group it takes
   the oldest Runs first. Retain the database snapshot and record its digest.
   Check the resulting mix: the tool cannot create missing Provider or failure
   examples.
2. For the internal development gate, agent-generated provisional labels may
   be used with `publishable_accuracy=false`. For publishable calibration,
   have independent reviewers label the task and sanitized persisted events
   without viewing `run_analyses`, score breakdowns, or this command's predictions.
   Mark dimensions that cannot be assessed as unknown and adjudicate reviewer
   disagreements before publishing metrics. Keep the review method, reviewers,
   and adjudication record in `provenance`.
3. Store the label file outside tracked source, such as under ignored
   `.scratch/`. It contains Run IDs and labels, not raw Provider responses or
   credentials.

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
mkdir -p ../.scratch/evaluation-calibration
uv run python -m app.database_recovery backup \
  --output ../.scratch/evaluation-calibration/sample-20261013-v1.dump
calibration_snapshot_sha256=$(sha256sum ../.scratch/evaluation-calibration/sample-20261013-v1.dump | cut -d ' ' -f 1)
uv run python -m app.calibration_review \
  --output ../.scratch/evaluation-calibration/review-packet-20261013-v1.json \
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
`../.scratch/evaluation-calibration/labels-20261013-v1-provisional.json`.
Independent reviews are required before publishing accuracy. Use consistent
interpretations: `planning` covers wrong or
repeated tool choices, `execution` covers tool/Provider errors and timeouts,
`context` covers explicit context loss/truncation or pressure above 95%, and
`budget` covers an exhausted step or token allowance. Missing context
measurements alone make `context` unknown. Record uncertain cases and
adjudication.

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
Each packet case records a canonical `trajectory_sha256` derived from its
sanitized trajectory and an `event_stream_sha256` fidelity digest derived from
all persisted `RunEvent` rows. The historical provisional sidecar is
`.scratch/evaluation-calibration/review-record-v1-20261007-provisional.json`;
the strict-binding derivative is
`.scratch/evaluation-calibration/review-record-v1-20261007-provisional-bound.json`,
paired with
`.scratch/evaluation-calibration/review-packet-20261007-recovery-fixed-bound.json`.
Those dated artifacts were created before the analyzer-revision field was
introduced and before `event_stream_sha256` became mandatory for strict
binding; export a new packet with the current exporter and separately prepare
matching labels and a v1 sidecar before running a new strict report. The
exporter creates only the packet; it does not create labels or a sidecar.
Do not treat a historical file digest as evidence that it satisfies the current
binding contract.
Agent-generated sidecars must keep `publishable_accuracy` set to `false`.
To set `publishable_accuracy=true`, the sidecar must name at least two unique
human reviewer IDs and a completed adjudication with both an adjudicator and a
decision. Every case must also include non-empty evidence and uncertainty.
Empty or duplicate reviewer IDs, incomplete adjudication, missing case evidence
or uncertainty, and agent-generated review metadata are rejected. A publishable
record also requires `--require-binding`; otherwise the report refuses the
publishable claim.
Valid unknown dimensions do not by themselves force `publishable_accuracy` to
`false`; the human-review and binding gates still apply. A publishable record
does not establish complete coverage or a representative sample. Report the
coverage and selection limitations alongside any metrics.

For the new provisional artifact set, prepare
`../.scratch/evaluation-calibration/review-record-v1-20261013-provisional.json`
separately. Compute the packet and finalized labels file hashes from their
exact bytes, including whitespace:

```sh
sha256sum ../.scratch/evaluation-calibration/review-packet-20261013-v1.json \
  ../.scratch/evaluation-calibration/labels-20261013-v1-provisional.json
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
schema change is needed. Human annotation can remain pending while this
provisional artifact set supports engineering verification.

## Generate the report

After the new packet, matching labels, and sidecar are ready, set
`DATABASE_URL` to the same fixed sample database (or its verified isolated
restore), then run from the repository root:

```sh
cd backend
uv run python -m app.calibration_report \
  --labels ../.scratch/evaluation-calibration/labels-20261013-v1-provisional.json \
  --packet ../.scratch/evaluation-calibration/review-packet-20261013-v1.json \
  --review-record ../.scratch/evaluation-calibration/review-record-v1-20261013-provisional.json \
  --require-binding
```

`--require-binding` rejects a report without the packet, structured review
sidecar, packet-file digest, labels-file digest, and explicit analyzer revision.
Binding checks compare the complete Run ID set, packet source snapshot and file
digests, packet/sidecar analyzer revision, sidecar identity rows, packet
terminal status, current database Experiment/Run identity, and each packet
trajectory digest against both the packet content and the current `RunEvent`
reconstruction, and each declared `event_stream_sha256` against a fresh digest
of the current persisted event stream. Historical packets without that field
remain parseable for inspection, but strict binding rejects them. Calls without
these options remain available for old mechanics tests and are reported as
`unbound`. Strict binding also requires the packet and sidecar
`source_snapshot` values to use canonical `sha256:<64hex>` syntax; external
snapshot provenance and authenticity still require an independently retained
snapshot record or verification step.

The JSON output includes `accuracy_status`: `provisional_mechanics` for a
bound non-publishable review, `publishable` only after the human reviewer and
adjudication gate, and `unbound` for legacy calls. It also reports TP, FP, FN,
and TN for each dimension and across assessed Run/dimension pairs (`micro`),
plus precision, recall, F1, and the Run IDs with disagreements. Predictions for
unknown dimensions contribute no confusion counts or disagreements. A metric
is `null` when its denominator is zero. The existing metrics dictionaries keep
their structure; the report adds the stable top-level
`evaluation_scope="assessed_run_dimension_pairs"` and `coverage` fields.

Coverage reports `known_count`, `unknown_count`, `total_count`, and `ratio`
(`known_count / total_count`) for each dimension and for micro. For example,
the six contract definitions above imply this coverage portion of a report:

```json
{
  "evaluation_scope": "assessed_run_dimension_pairs",
  "coverage": {
    "by_dimension": {
      "planning": { "known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0 },
      "execution": { "known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0 },
      "context": { "known_count": 2, "unknown_count": 4, "total_count": 6, "ratio": 0.3333333333333333 },
      "budget": { "known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0 }
    },
    "micro": { "known_count": 20, "unknown_count": 4, "total_count": 24, "ratio": 0.8333333333333334 }
  }
}
```

Unknown coverage must remain visible even when assessed-pair metrics show
perfect agreement. No event payloads are printed; `event_stream_sha256` binds
the persisted stream without placing its raw payload in the packet. Review the
Run IDs and provenance before sharing a report.

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

For the historical nine-Run demo-only packet, the agent-generated draft is
`.scratch/evaluation-calibration/20261013-compose-refresh-110352Z/provisional-labels-final.json`.
The five-Run draft at
`.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`
remains historical evidence.
The earlier `.scratch/evaluation-calibration/demo-temp-labels-20261005.json`
belongs to the historical pre-fix packet. These drafts remain useful
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

Multiple positive dimensions are allowed when independent evidence supports
each one. Mark an unassessable dimension in `unknown_dimensions` instead of
counting it as negative; this includes context pressure when measurements and
explicit context evidence are absent. An empty positive set alone does not
mean every dimension is known. Reviewers should cite the step index or
trajectory field in their working notes, record uncertainty, and avoid
inferring a cause from terminal status alone. A successful replay can still
have a positive dimension if the trajectory shows one, and a failed Run can
have no positive dimensions while some dimensions remain unknown.

Check that each packet task matches its Scenario contract before applying
Scenario assertions. The recovery harness intentionally exercises the
checkout Scenario while injecting a Runner fault, so it uses the checkout
Scenario's default task and emits the same successful tool sequence after
replacement. Runner recovery remains an operational fault test, not evidence
of a planning failure by itself; do not infer a dimension from terminal status.

This contract correction was made after the first 2026-10-05 packet was
captured. That packet and its temporary labels remain historical demo artifacts
with the old one-step recovery trace; do not edit them in place. The corrected
packet named above contains the three-step recovery trace. New packets must
come from newly captured persisted events; record whether collection used a
standard stack or an explicit runtime override, as in the 2026-10-13 sample.

For the 2026-10-05 demo draft, the two failed six-step baselines are marked
`planning` plus `budget` because they repeat the same action/arguments and hit
`max_steps=6` without success. The recovery and successful replay trajectories
have empty positive sets in that historical binary-label draft because they
show no supported planning, execution, context, or budget evidence. A new
review must distinguish unmeasured context from an assessed negative. These
are examples for manual correction, not adjudicated labels. After editing,
replace `name` and `provenance` with the real reviewer
method, roles, snapshot identifier, and adjudication record; do not overwrite
the analysis-blind packet. Current strict reports require a newly exported
packet and matching labels/sidecar bound to `failure-analyzer.v2`.
