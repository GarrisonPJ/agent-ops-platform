# AgentOps Platform handoff

> **Superseded on 2026-10-17.** Do not execute the "next development batch"
> below. Phase 1 is complete (`8bd2ef0`). Phase 2 — Harness is the direction:
> `ROADMAP.md`, `docs/agents/phase2-tasks.md`, ADR-0007 through ADR-0011.
> Independent human labels are no longer a publication gate
> (`docs/evaluation-calibration.md`). MCP is in scope only as a loopback
> Environment transport (ADR-0008), not as an open tool bus. The rest of this
> file is a historical session note.

Date: 2026-09-12
Workspace: `/home/garry/Project/agent-ops-platform`

## Resume here (2026-09-28)

The previous session published four commits to `origin/main`:
`e58c9db` (runtime reliability), `77c9fe4` (calibration tools),
`4666751` (frontend formatting), and `fbe51c5` (documentation).
The local checkout still points to `fbe51c5` as of this update, with only
the preserved local tooling and handoff files untracked. Earlier statements
about uncommitted work describe historical review stages.

Use `ROADMAP.md` / `ROADMAP.zh-CN.md` for priorities and acceptance criteria;
use `CONTEXT.md` and accepted ADRs for architecture boundaries. This handoff
records context and dated evidence. Do not repeat the historical P0 fixes.

The next development batch should:

1. Check CI for the published commit and resolve any confirmed failures.
2. Refresh the React Router advisory and check its applicability to this SPA;
   audit entries for a package and its dependent do not necessarily represent
   separate exploitable vulnerabilities. Make the smallest compatible patched
   upgrade and rerun frontend installation, formatting, typecheck, tests,
   build, Recorded E2E, and audit.
3. Prepare current registered-Scenario samples, a fixed snapshot, annotation
   instructions, and analysis-blind packets following
   `docs/evaluation-calibration.md`. Obtain independent human labels before
   reporting human-label accuracy; agent-generated labels must be disclosed
   separately and cannot substitute for that gate.
4. In parallel where practical, verify the standard Web Dockerfile and the
   isolated four-service Golden/Runner recovery flow. If image fetching is
   still blocked, record it and continue independent work; do not equate an
   override-based run with a standard image build.

Keep tracker cleanup and intranet deployment hardening at their roadmap
priority. Avoid feature expansion into MCP, vector memory, multi-tenancy, or
throughput. Delegate detailed reading/implementation to GPT-5.6 Luna with
maximum reasoning as requested; the main agent owns scope and independent
review. Report changes, test evidence, remaining human input, and updated
roadmap status at the end of each bounded batch.

## Dependency and CI closeout (2026-09-29)

- The React Router production dependency is now `^7.18.2`; both `react-router-dom` and its locked `react-router` dependency are `7.18.2`. OSV identifies that version as the fix for the high RSC CSRF advisory affecting `7.12.0` through `7.18.1`.
- Frontend CI now runs `npm audit --omit=dev` immediately after `npm ci`, followed by the existing formatting, typecheck, unit, build, and Recorded E2E checks.
- Local WSL frontend reruns remain unverified because `npm` resolves to a Windows shim and Linux Node is absent. The package metadata and workflow wiring were checked directly; use CI or Linux Node 22 for the complete matrix.
- Standard Web Dockerfile verification remains blocked. `docker build --pull -f frontend/Dockerfile -t agentops-web-standard-verify frontend` timed out resolving `docker.io/library/node:22-alpine` before build steps. Override-based Compose evidence remains separate and is not a standard image-build result.
- The next batch is independent registered-Scenario sampling/labeling and, when Docker Hub access is restored, the standard four-service Golden/Runner recovery verification. Preserve `.codegraph/`, `.cursor/`, this handoff, and its Zone.Identifier.

## Next-batch sample verification (2026-09-29)

- A read-only copy of `agentops_phase1-pgdata` was inspected at Alembic revision `0005_runner_presence`: 33 `RunEvent` rows, four terminal Runs, and zero eligible current registered versioned Scenarios. The four Runs are legacy unregistered `checkout-api-latency` data: three failed baselines and one succeeded replay.
- No review packet, labels, report, or accuracy metric was produced. The calibration-focused suite passed `22 passed in 1.07s`; the exporter/report subset passed `6 passed in 0.24s`.
- The current-source API image built from cached dependencies. The current-source Runner build stopped at the unavailable crates.io index, and the standard Web Dockerfile still timed out fetching `node:22-alpine`. Temporary validation resources were cleaned; original volumes and local tooling remain untouched.
- Human input remains required for independent labels and disagreement adjudication after current registered Scenario Runs are collected.

## Proxy-enabled Docker and packet verification (2026-10-05)

- Docker Hub access recovered intermittently. The standard Web Dockerfile built with base digest `sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402` and served the app. Local Compose required `DOCKER_BUILDKIT=0` because the buildx reference directory is read-only.
- Standard Web validation passed production audit (`npm audit --omit=dev`: 0 vulnerabilities), formatting, typecheck, 27 unit tests, build, and 2 Recorded browser E2E tests. The isolated standard four-service stack passed both Golden scenarios and Runner recovery; temporary resources were removed.
- Snapshot `sha256:dbd3734309ff439e6bcee38b133774e58470bf23b3abcdf4d9db2ad391f72427` and packet `.scratch/evaluation-calibration/review-packet-20261005.json` now provide five current registered terminal Runs. The packet is analysis-blind, not outcome-blind; independent labels and adjudication are still required before metrics.

## Demo temporary-label draft (2026-10-05)

- Because the current project remains a demo rather than a production workload,
  an agent-generated provisional label file was prepared at
  `.scratch/evaluation-calibration/demo-temp-labels-20261005.json`. It is a
  valid `LabelSet` for exercising the report workflow, not an independent
  annotation set.
- The draft marks the two repeated six-step failed baselines as `planning` and
  `budget`; the recovery and successful replay Runs are empty. In the captured
  packet, the recovery harness reused the checkout Scenario ID for a different
  task, so the analyzer's planning assertion prediction is kept as a
  data-quality and adjudication example rather than copied into the draft. The rationale and
  editing rules are in `docs/evaluation-calibration.md` under “Demo temporary
  labels”. A reviewer may edit or replace the file, but must update provenance
  with reviewer roles, method, snapshot, and adjudication before any metric is
  treated as evidence. No accuracy claim is made from this draft.

The recovery contract was corrected after this packet was captured: the
replacement Runner now emits the checkout Scenario's successful tool sequence
and the harness uses its default task. The retained packet and temporary labels
are historical; export a fresh packet before recalibration.

Hosted GitHub Actions run `36029244922` for `fbe51c5` is green across all six
jobs (`backend`, `database-recovery`, `frontend`, `rust`, `compose`, and
`golden-e2e`). The local `DOCKER_BUILDKIT=0` Compose setting remains a host
builder workaround, not a source change.

The corrected isolated standard stack also passed both Golden scenarios and
Runner recovery. Its logical PostgreSQL snapshot digest is
`sha256:2ad97aa9c22112dd55b2d55e669d605478facf260b3e138f6ea3853b0131f30c`;
the fresh analysis-blind packet is
`.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`,
with the matching demo draft at
`.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`.
The recovery baseline now has the complete checkout trace; no labels or
accuracy report have been published.

## Repository cleanup (2026-10-05)

- Fully triaged legacy trackers were moved, without deletion, to
  `.scratch/archive/legacy-trackers-20261005/`: `agentops-platform-v0.1`,
  `agentops-platform-v0.3`, and `frontend-polish`. The archive README records
  each `wontfix`/supersession decision and current replacement path.
- `baseline-hardening`, `closed-loop-architecture`, and `demo-packaging` were
  deliberately retained because they still contain unresolved or
  `ready-for-agent` items. They require a separate triage decision; local
  tools, the dated handoff, and historical calibration artifacts remain
  preserved.

## Final continuation update (2026-10-05)

- The full backend suite was rerun with all HTTP proxy variables removed and
  loopback explicitly allowed:
  `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy NO_PROXY=localhost,127.0.0.1 UV_CACHE_DIR=/tmp/agentops-uv-cache uv run pytest phase1_tests/ -q --tb=short`.
  Result: `188 passed, 2 skipped in 11.12s`. The earlier `12 failed` run was
  fake-Provider loopback traffic sent through the inherited proxy, not a
  recovery regression. Keep this proxy boundary in any future local report.
- The latest standard Web Dockerfile retry was:
  `timeout 120s docker build --pull -f frontend/Dockerfile -t agentops-web-standard-verify:20261005-rerun frontend`.
  It failed before build steps while resolving `node:22-alpine` with
  `registry-1.docker.io ... i/o timeout`. A standard image and four-service
  Compose stack did succeed during the same day's intermittent Docker Hub
  recovery; therefore the Dockerfile has success evidence but no reproducible
  local network guarantee. Do not count a Web override as a standard image
  build.
- The corrected packet remains
  `.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`
  with matching demo labels at
  `.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`.
  Both are ignored, owner-only demo materials; labels are agent-generated and
  must be independently reviewed before any accuracy metric.
- Cleanup moved completed milestone trackers to
  `.scratch/archive/completed-trackers-20261005/` and stale local `.bak`
  editor copies to `.scratch/archive/local-backups-20261005/`. Explicitly
  unresolved `baseline-hardening`, `closed-loop-architecture`, and
  `demo-packaging` remain active. `.codex-helper-probe`, `.codegraph/`,
  `.cursor/`, this handoff, its Zone.Identifier, and current calibration files
  were preserved.
- The three old stopped default-Compose containers (`agentops-runner-1`,
  `agentops-api-1`, `agentops-db-1`) and their unused `agentops_default`
  network were removed after checking that the containers were stopped and
  that the database container's named `agentops_phase1-pgdata` volume was not
  removed. The named `agentops_pgdata` and `agentops_phase1-pgdata` volumes
  remain.

## Calibration mechanics follow-up (2026-10-06)

- The corrected packet `.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json` and matching draft labels contain the same five Run IDs. A fresh temporary PostgreSQL database was migrated to Alembic head `0008_regression_cases`, populated from the packet's `Experiment`, `Run`, and `RunEvent` facts, and used to run `python -m app.calibration_report`.
- The mechanics report is retained at `.scratch/evaluation-calibration/demo-calibration-report-20261006.json`: five cases, zero computed disagreements, apparent planning/budget and micro metrics of `1.0`, and null execution/context metrics because no positive labels are present. This is not an accuracy result; the labels are agent-generated and match the analyzer by construction.
- Reviewer/provenance/adjudication information is recorded at `.scratch/evaluation-calibration/review-record-20261006-demo.json`. The reviewer role is explicitly agent-generated provisional, no independent human reviewer or adjudicator is claimed, and `publishable_accuracy` is false. Packet/source snapshot, artifact hashes, reconstruction database, and method are recorded. The temporary report database was removed after the run; source volumes were not changed.
- P1 calibration binding is now implemented: `--require-binding` checks packet and labels file digests, source snapshot, complete Run IDs, scenario/mode/kind/task metadata, terminal status, database Experiment/Run identity, and canonical trajectory digests emitted by the packet exporter. External snapshot authenticity and analyzer revision binding remain open limitations. Preserve the analysis-blind versus outcome-blind distinction.

## Provider and strict-binding follow-up (2026-10-07)

- Provider tool arguments are now checked against the registered Scenario contract before a `step_completed` event is emitted; the event retains the actual model arguments instead of replacing them with callback-generated canonical values. Checkout tool schemas declare their required arguments, and durable normalization preserves the bounded integer `attempts` argument.
- Focused Provider/durable-event tests pass `28`; the complete backend suite passes `198 passed, 2 skipped`. `compileall` and `git diff --check` pass.
- The old corrected packet remains historical. The strict-binding derivative is `.scratch/evaluation-calibration/review-packet-20261007-recovery-fixed-bound.json` with packet SHA-256 `618b2ea20c769f72d54bc8fddcfd22c2ea114219edfd3c41f8d6d740b2b8e706`; the provisional sidecar is `.scratch/evaluation-calibration/review-record-v1-20261007-provisional-bound.json`, and the labels SHA-256 is `852a0e94d5de8b20dfb7473a6170000689ad69c935a84820c15aff884b8e2a2b`. Its strict identity binding returned `status=bound`; no accuracy claim was made.
- The recovery E2E harness now waits for an observed `running` state before killing the Runner and checks contiguous SSE sequences, both attempts, the complete three-step Attempt 2 trace, and Attempt 2 completion. This pass covered unit checks only; no new real Compose kill/recreate result is claimed.

## Provisional internal development gate (2026-10-06)

- The lack of human reviewers does not block engineering work. The sidecar `.scratch/evaluation-calibration/provisional-internal-gate-20261006.json` records a temporary `provisional_internal_mechanics` gate with status `pass` for this fixed five-case fixture packet.
- The agent checked every case against the documented rules and found no label correction necessary: the two repeated failed baselines are `planning + budget`; the three successful three-step Runs are empty; execution/context have no positive examples and remain uncovered.
- The gate allows analyzer, trajectory, Scenario assertion, recovery contract, exporter, schema, and report-mechanics regression work. It cannot support human-label accuracy, inter-rater agreement, adjudication, execution/context coverage, Provider-distribution claims, or production readiness. `human_review_gate` remains `pending_human_review`.
- When human labels become available, replace the provisional labels and keep this sidecar as mechanics-only history; do not promote or rename agent-generated labels.

## Handoff next actions

1. Use the provisional internal gate to continue analyzer, trajectory,
   Scenario, recovery, exporter, schema, and report-mechanics work without
   publishing accuracy claims.
2. Decide whether an immutable analyzer revision identifier should become part
   of the packet contract; canonical reconstructed trajectory binding is covered,
   while raw event payload fidelity remains outside the packet contract.
3. When practical, give the corrected packet to at least two independent
   reviewers; record per-case evidence, reviewer roles, provenance,
   disagreements, and named adjudication, then rerun the calibration report.
   Do not publish precision/recall/F1 from either temporary label file or the
   2026-10-06 mechanics report.
4. Treat hosted CI run `36029244922` and the corrected standard Compose run as
   the current engineering confirmation. Retry the standard Docker build only
   after Docker Hub connectivity changes or image/dependency/Runner inputs
   change.
5. Triage the remaining active `.scratch` directories one at a time. Archive
   or delete only files with explicit supersession evidence, and update the
   archive README plus both roadmaps when doing so.

## Purpose

Continue the engineering review and execute the newly agreed follow-up plan for AgentOps Workbench. The user confirmed the intended deployment scope is a single machine or trusted team intranet, with resume-project value but no public SaaS target. The next priorities are execution reliability and evaluation accuracy; throughput and multi-user collaboration are deferred.

For current domain language and roadmap, read:

- `/home/garry/Project/agent-ops-platform/CONTEXT.md`
- `/home/garry/Project/agent-ops-platform/ROADMAP.md`
- `/home/garry/Project/agent-ops-platform/ROADMAP.zh-CN.md`
- `/home/garry/Project/agent-ops-platform/docs/agentops_closed_loop_architecture.md`
- Relevant ADRs under `/home/garry/Project/agent-ops-platform/docs/adr/`

## Work completed in the previous session

A full review was performed with two delegated read-only agents: one for implementation/tests and one for documentation/architecture. The main findings were incorporated into the new P0/P1/P2 section of both roadmap files.

Tracked documentation changed:

- `CONTEXT.md`: records concurrency invariants and the single-machine/trusted-intranet, read-only Provider boundary.
- `ROADMAP.md` and `ROADMAP.zh-CN.md`: add the next milestone, explicit fixes, cleanup items, acceptance criteria, and deferred work.
- `docs/agentops_closed_loop_architecture.md`: updates the next-milestone boundary and deferred side-effecting tools.
- `README.md` and `README.zh-CN.md`: clarify deployment scope and use engineering-focused wording.

Three stale, untracked planning/analysis documents were moved (preserved, not deleted) to `.scratch/archive/`:

- `improvement-analysis-2026-09-12.md`
- `floofy-watching-stonebraker-2026-09-12.md`
- `agentops-后续开发规划-d3077303-2026-09-12.md`

`.codegraph/` and `.cursor/` remain untracked local tooling directories and were intentionally left untouched.

No code was changed, committed, or pushed.

## Status update (2026-09-19)

The reliability and evaluation follow-up has been implemented in the current
working tree. The intended scope remains a single machine or trusted team
network; Provider execution is read-only, Scenario-scoped, and explicitly
opt-in.

Completed since the original handoff:

- Run cancellation, heartbeat, event persistence, terminal completion, and
  Policy state transitions use conditional updates or row-lock-based claims
  with rereads after contention.
- Duplicate normalized events converge under concurrent database connections;
  conflicting payloads return `EVENT_CONFLICT`. Policy Replay and RegressionCase
  activation claims create at most one Run/RunnerJob, with losing transactions
  rolled back. Activation retry is idempotent when the winner is already active.
- Runner stdin write/shutdown failures terminate the Agent process group, with
  a broken-pipe regression test. Missing `RUNNER_TOKEN` fails startup outside
  explicit development mode.
- Empty or structurally invalid Provider responses emit
  `PROVIDER_INVALID_RESPONSE`. Failure analysis and scoring now both recognize
  HTTP 4xx/5xx observations, with labeled calibration fixtures and Scenario
  baseline/replay checks.
- Added [reliability and local validation runbook](docs/operations/reliability-and-local-validation.md)
  and PostgreSQL contention tests wired into the `database-recovery` CI job.

Verification evidence as of this update:

- Backend: `175 passed, 2 skipped`; evaluation calibration focused tests:
  `27 passed`.
- PostgreSQL 16 temporary-container verification: the three new contention
  tests passed; the PostgreSQL-focused run including retention reported
  `9 passed`. Without `RETENTION_POSTGRES_URL`, the local PostgreSQL test path
  explicitly skips.
- Rust workspace passed, including 15 Runner tests. `cargo fmt --all -- --check`
  and `git diff --check` passed.
- `make fmt` completed the Rust formatter but frontend formatting remains
  blocked because this WSL environment resolves `npm` to a Windows shim and
  lacks Linux Node/npm. CI remains the frontend verification baseline.

Open follow-up:

- Install or provide a Linux Node 22/npm toolchain in WSL, then rerun frontend
  typecheck, tests, build, and formatting. Do not claim local frontend success
  before that verification.
- Keep PostgreSQL contention evidence in CI or a PostgreSQL 16 environment;
  SQLite and the local skip are not substitutes for multi-connection evidence.

The working tree remains uncommitted and unpushed. Preserve unrelated local
files, including `.codegraph/`, `.cursor/`, and the handoff's Zone.Identifier.

## Review follow-up (2026-09-19)

The first status update overstated a few guarantees. The current working tree
now includes the following corrections:

- Heartbeat, event persistence, and terminal completion acquire the
  `RunnerJob` row lock after lease validation. Event uploads keep an initial
  non-locking read so concurrent duplicate requests can converge, then lock
  before insert; recovery cannot interleave an old lease with a new Attempt.
- Activation serializes competing Policies on the Experiment row. A terminal
  RegressionCase replay owned by another Policy is replaced with a replay for
  the current Policy; in-flight replays remain exclusive and pending.
- API startup and authentication use one Runner-token snapshot. Compose now
  requires an explicit `RUNNER_TOKEN`; the predictable local fallback was
  removed.
- Added regression tests for the startup snapshot, per-Policy RegressionCase
  replay, and concurrent activation of different Policies.

Verification after these corrections:

- Backend: `177 passed, 2 skipped`.
- PostgreSQL 16 temporary container: four state-transition contention tests
  passed; the combined PostgreSQL retention/state run reported `10 passed`.
- Rust verification previously passed all 15 workspace tests and formatting;
  `git diff --check` passes after the follow-up edits.

Remaining work is deliberately narrower than the original follow-up:

- Frontend local verification is still blocked by the missing Linux Node/npm
  toolchain in WSL; CI remains the frontend evidence.
- The calibration fixtures are synthetic regression guards. Add an
  independently labeled set, precision/recall/F1 and confusion-matrix
  reporting, and a persisted RunEvent round-trip before claiming evaluation
  accuracy.
- At the next cleanup, delete only `.scratch` planning/issue files proven
  superseded by the roadmap and record their replacement path first. Preserve
  the dated handoff, `.codegraph/`, and `.cursor/` local tooling.
- Before trusted-intranet deployment, move the Compose demo PostgreSQL
  credentials to an explicit secret source; this remains a P2 deployment
  hardening item.

## Calibration follow-up (2026-09-24)

- The three built-in fixture Scenarios now have baseline/replay tests that upload
  events through the Runner API, persist RunEvents, and verify reconstructed
  observations, stored analysis dimensions, and scoring.
- Backend verification: `180 passed, 2 skipped`; focused calibration tests:
  `16 passed`. The new round trips use SQLite; PostgreSQL contention results
  above remain dated evidence.
- Synthetic fixture labels still do not establish evaluation accuracy. An
  independently labeled sample and precision/recall/F1 confusion-matrix report
  remain open. Local frontend validation had not yet been rerun at this point.

## Local frontend verification (2026-09-24)

- Downloaded the official Linux x64 Node `v22.23.3` archive to `/tmp`,
  checked its published SHA256, and used it without changing the Windows Node
  installation. `npm ci`, typecheck, 27 unit tests, and production build pass.
- `npm run format:check` fails on 24 frontend files that were already unchanged
  in this worktree; the frontend CI job does not run that check. The
  runbook now provides the temporary WSL toolchain setup. Recorded browser E2E
  and the local Golden/Compose checks were not rerun in this update.

## Calibration reporting follow-up (2026-09-24)

- Added the read-only `python -m app.calibration_report --labels <path>`
  command. It validates separate labels, reconstructs terminal Runs from
  persisted RunEvents, and reports per-dimension and micro TP/FP/FN/TN,
  precision, recall, F1, and Run-level disagreements.
- Four focused reporting tests pass; the full backend suite passes
  `184 passed, 2 skipped`. The two skips require `RETENTION_POSTGRES_URL`.
- [The annotation procedure](docs/evaluation-calibration.md) records blind
  review and sample-selection requirements. No independent label set or
  accuracy result was produced; synthetic test labels remain mechanics-only.

## Local Compose verification (2026-09-24)

- Used an isolated `agentops-codex-20260924` Compose project with separate
  ports and a temporary PostgreSQL volume. The existing `agentops` project
  and its volumes were left untouched.
- The standard four-service build stopped while fetching Web's
  `node:22-alpine` image metadata from Docker Hub. For the three-service
  verification, compiled the current Rust Runner source offline inside
  `rust:1.96-slim` and packaged that binary with the current Python Agent
  source. PostgreSQL 16, API, and Runner reached healthy state.
- `scripts/golden_e2e.py` passed both registered scenarios, including
  persisted failed baseline traces, successful Replays, and activated
  Policies. `scripts/recovery_e2e.py` killed the running Runner and passed
  after Attempt 2 succeeded. The isolated Compose project and volume were
  removed after verification.
- Web container startup and recorded browser E2E were not verified locally.
  Independent labels and published evaluation-accuracy metrics also remain
  open.

## P1 follow-up verification (2026-09-24)

- Fixed CI Compose validation and Golden/Recovery Jobs to supply ephemeral
  random Runner tokens. Added frontend formatting to CI after `make fmt`
  normalized the 24-file baseline. Local Compose interpolation, frontend
  formatting, typecheck, 27 unit tests, build, and both Recorded browser E2E
  tests pass.
- Added an analysis-blind calibration review exporter and two focused tests. It writes
  private packets for current registered terminal Runs without stored scores
  or analysis. The full backend suite passes `186 passed, 2 skipped`. A
  read-only clone of the existing Phase 1 data volume had only four legacy
  fixture Runs under an unregistered Scenario ID; it yielded no eligible
  calibration sample. Four current Golden fixture Runs exercised the export
  command but are not independently labeled accuracy evidence.
- A temporary offline Web override using the verified Linux Node toolchain
  brought up all four Compose services. Web and its API proxy responded, and
  both Golden scenarios plus Runner Recovery passed again. Standard Web image
  construction still cannot resolve `node:22-alpine` through this Docker
  daemon, so the local Dockerfile build remains unverified. Independent human
  labels and publishable precision/recall/F1 remain open.

## Documentation sync (2026-09-24)

- Updated `README.md` and `README.zh-CN.md` with the local-demo
  `RUNNER_TOKEN` boundary, formatting and Recorded browser E2E checks, and the
  Golden/Runner recovery commands. The recovery script kills and recreates a
  Runner, so the READMEs limit it to an isolated, disposable Compose stack.
  Both READMEs link the [local validation runbook](docs/operations/reliability-and-local-validation.md)
  and [calibration procedure](docs/evaluation-calibration.md).
- Updated `docs/agentops_closed_loop_architecture.md` to reflect the read-only
  calibration export/report commands and the 2026-09-24 verification boundary.
  Both roadmap versions and this handoff already hold the dated test evidence;
  `CONTEXT.md` domain invariants did not need another change.
- This documentation-only pass checked referenced file paths and
  `git diff --check`. It did not rerun backend, frontend, Rust, or Compose
  tests. Existing worktree changes remain uncommitted and unpushed.

Next handoff actions: collect current registered terminal Runs with a useful
scenario/mode/outcome mix, obtain independent blind labels, then generate and
review the calibration report before publishing any accuracy metric. When the
Docker daemon can fetch `node:22-alpine`, verify the standard Web Dockerfile
build and repeat the isolated four-service Golden and Runner recovery checks
without the temporary Web override. Do not interpret the earlier fixture
smoke sample or override-based Compose run as completion of those gates.

## Review closure (2026-09-25)

Independent verification of the current working tree completed:

- Backend: `186 passed, 2 skipped`.
- PostgreSQL 16 contention plus retention: `10 passed` in an isolated temporary container; the container was stopped and removed afterward.
- Rust: workspace tests passed, including 15 Runner tests; `cargo fmt --all -- --check` passed.
- Frontend: in an isolated copy with Linux Node `v22.23.3`, formatting, typecheck, 27 unit tests, production build, and 2 Recorded browser E2E tests passed. The repository's existing `node_modules/.vite` cache contains root-owned files, so `npm ci` was not run in place.
- Compose interpolation rejects a missing `RUNNER_TOKEN` and passes with an explicit temporary token. The standard Web Dockerfile build remains unverified because Docker Hub access to `node:22-alpine` timed out; the temporary Web override is not build evidence.

Review findings and decisions:

- The calibration packet is **analysis-blind**, not outcome-blind. It omits scores, stored analysis, and raw Provider output, but exposes Scenario/execution metadata and trajectory fields including terminal status, Provider-error presence, limits, timing, and token counts. The calibration document and roadmap now state this limitation. No accuracy result is publishable without independent labels, adjudication, sample provenance, and reviewer-agreement limits.
- `npm audit --omit=dev` reports two high production vulnerabilities through `react-router-dom`/`react-router` 7.18.0. Upgrade to a patched compatible release and rerun all frontend checks before trusted-intranet deployment.
- The next cleanup may delete only `.scratch/` planning or issue files whose replacement path and triage label are recorded. Preserve this dated handoff, `.codegraph/`, and `.cursor/`.

No code, dependency lockfile, commit, or push was made by this review pass.

## Historical high-priority implementation findings

The following findings drove the implementation above. They are retained as
dated history; verify current source and the status update above before opening
new work.

These are already written into `ROADMAP.md`; verify current source before editing:

1. `backend/app/phase1_service.py`: `cancel_run`, `heartbeat`, and `persist_events` can write stale state after `complete_job` terminal CAS. Unify all Run state writes behind CAS or row locks and reread on CAS loss.
2. `persist_events` first reads sequence state and then inserts, so concurrent duplicate uploads can raise `uq_run_events_run_sequence` and return 500. Implement database-safe idempotency; identical normalized payloads converge, differing payloads return an explicit conflict.
3. `activate_policy` creates RegressionCase replay Runs without an atomic per-case claim. Concurrent activation can leave orphan Runs/RunnerJobs and overwrite replay pointers. Add conditional claims and Policy activate/reject CAS.
4. `runner/crates/agentops-runner/src/lib.rs`: stdin write/shutdown failure after child spawn can return without terminating the Agent process group. Add explicit cleanup and a broken-pipe regression test.
5. `backend/app/phase1_main.py` and Compose use predictable default Runner tokens. Fail fast outside explicit development mode when `RUNNER_TOKEN` is absent.
6. `backend/app/phase1_provider.py`: an empty Provider message with no tool call can be accepted as success. Return `PROVIDER_INVALID_RESPONSE`.

## Historical verification evidence

- Rust: `cargo test --workspace --locked` passed.
- Backend with proxy variables unset: `149 passed, 1 skipped`.
- Backend in the inherited environment: 9 Provider fake-server tests failed because HTTPX used the configured proxy; these passed when `NO_PROXY/no_proxy` included localhost. Treat as local test-environment reproducibility work, not a confirmed business regression.
- Frontend validation was blocked locally because `npm` resolves to a Windows shim and Linux `node` is unavailable in WSL. CI remains the known frontend verification path.
- `git diff --check` passed.

## Historical recommended next-session sequence

The sequence below is retained for provenance and is superseded by the
2026-09-19 status update above.

1. Re-read the P0 section in both roadmap files and inspect the current service/Rust code.
2. Add focused PostgreSQL multi-connection tests before changing concurrency code.
3. Implement the smallest CAS/upsert/cleanup changes needed to make those tests pass.
4. Run backend focused tests, full backend tests, Rust tests, and whitespace checks.
5. Update the roadmap/ADR only when behavior and evidence are actually changed.
6. Do not start MCP, vector memory, side-effecting tools, multi-tenancy, or throughput work until a measured requirement promotes them.

## Suggested skills

- `diagnosing-bugs`: for the concurrency races, event idempotency failures, Provider response bug, and Runner cleanup bug.
- `tdd`: for writing reproducing tests before implementing P0 fixes.
- `code-review`: after implementation, review the resulting diff against the roadmap and repository standards.
- `resolving-merge-conflicts`: only if concurrent work introduces an actual merge/rebase conflict.
- `frontend-workbench`: only when the local Node toolchain is available and frontend reliability/accuracy work begins.
