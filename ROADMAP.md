# AgentOps Roadmap

This roadmap describes planned engineering work. It is the source of truth for future milestones; `CONTEXT.md` owns current domain language and invariants, ADRs own accepted architecture decisions, and the git-ignored `.scratch/` workspace holds local implementation records.

## Direction

AgentOps is being developed as a reliable closed-loop evaluation system for tool-using agents. Work is prioritized by correctness, recoverability, observability, and safe operation.

The recorded preview remains useful for offline UI development and deterministic regression testing. It is not a separate product milestone or hosted deployment target.

The intended deployment scope is a single machine or a trusted team network. This is an engineering project, not a public SaaS product. Provider execution therefore remains read-only, Scenario-scoped, and explicitly opt-in. Side-effecting tools, untrusted endpoints, and multi-tenant access are outside the next milestone.

## Current state

Phase 1 is complete and verified:

- Experiments create deterministic baseline and replay runs.
- FastAPI owns domain state, persistence, leases, scoring, analysis, and policy decisions.
- PostgreSQL stores runs, jobs, ordered events, analyses, policies, and regression cases.
- The Rust Runner claims leased jobs, supervises an allowlisted Python process, retries event delivery, and enforces cancellation and timeout.
- Expired Runner leases are reclaimed with attempt fencing, sequence-aware restart, cancellation preservation, and bounded exhaustion.
- React provides the Experiment, Trace, Analysis, Improve, replay, and human activation/rejection workflow.
- Python, TypeScript, and Rust validate shared protocol v1 fixtures.
- CI verifies contracts, migrations, database recovery, backend behavior, frontend adapters, Rust supervision, Compose, and the real Golden loop.
- Durable Run diagnostics now correlate queue, lease, attempt, Runner, provider, retry, timing, and terminal signals.
- Database readiness verifies the Alembic Head, disposable backup/restore rehearsal is executable, and durable Runner availability remains independently observable.
- Machine-readable operational states (`GET /api/operations/state`), bounded operations aggregates, retention/redaction enforcement, and out-of-process health probes are implemented and verified in CI.
- Three versioned built-in Scenarios support deterministic fixture execution and narrow, scenario-specific provider execution.
- Deterministic failure classification, persisted Before/After comparisons, and regression-case replay now inform the human policy gate.
- Policy activation remains an explicit human action.

## Milestones

| Priority | Milestone | Status | Outcome |
|---|---|---|---|
| P0 | Phase 1.1 — Runner recovery | Complete | A crashed or disconnected Runner cannot strand a Run indefinitely. |
| P1 | Phase 1.2 — OpenAI-compatible provider | Complete | Real model execution uses the same supervised, typed, persisted workflow without making CI depend on an external API. |
| P2 | Phase 1.3 — Observability and operational hardening | Complete | Operators can diagnose queue, lease, Runner, provider, and migration failures from durable signals. |
| P1 | Phase 1.4 — Scenario onboarding | Complete | Operators choose from registered built-in Scenarios; the platform is no longer limited to one hard-coded demo. |
| P1 | Closed-loop depth follow-up | Complete | Failed Runs become classified, comparison-ready regression assets that gate policy activation. |
| Gate | Safety and access control | Trigger-based | Required before side-effecting tools, untrusted users, or shared/public operation enter scope. |

## Phase 1.1 — Runner recovery

ADR-0002 records deterministic restart of the same logical Run. Accepted events remain immutable; each retry appends a new attempt marker and continues the global sequence, while analysis scores only the latest attempt segment.

Scope:

- Detect expired leases for `claimed`, `running`, and `cancelling` jobs.
- Make abandoned work reclaimable without allowing the previous lease to mutate the Run.
- Increment and persist the attempt number and recovery reason.
- Define a maximum-attempt policy and an explicit terminal outcome after exhaustion.
- Preserve already accepted events and maintain sequence/idempotency invariants.
- Preserve cancellation intent while a job is being recovered.
- Add a real-stack fault test that terminates the Runner mid-run, starts a replacement, and verifies the final state.

Implementation result:

- Expired claimed, running, and cancelling leases are reclaimed on the next authenticated claim.
- A replacement claim increments Attempt, returns the next event sequence, and fences the old lease.
- Cancellation intent survives recovery; three total attempts are allowed before a documented failed or cancelled terminal state.
- Backend tests cover recovery, stale-lease fencing, cancellation, and exhaustion.
- The Compose fault test terminates a Runner mid-run, waits for lease expiry, starts the replacement, and verifies Attempt 2 completion.

Acceptance:

- No Run remains permanently `claimed`, `running`, or `cancelling` after its Runner disappears.
- A stale Runner cannot append events or complete the recovered Run.
- Recovery does not duplicate or rewrite accepted events.
- Retry exhaustion produces a documented terminal error.
- The recovery path passes backend, Rust, and Docker integration tests.

## Phase 1.2 — OpenAI-compatible provider

Scope:

- Introduce one narrow provider boundary for the Python agent.
- Configure `base_url`, model, and credentials only on the server side.
- Support timeout, bounded retry, cancellation propagation, and structured provider errors.
- Persist model identity, latency, and token usage without leaking credentials or hidden reasoning.
- Keep the deterministic checkout scenario as the default CI and Golden E2E path.
- Test provider behavior against a local fake OpenAI-compatible server; keep live-provider checks opt-in.

Acceptance:

- The same Experiment workflow can select deterministic fixture execution or an explicitly configured provider-backed agent.
- Provider failure, timeout, and cancellation produce valid terminal Run states.
- CI remains deterministic and requires no external API key.
- Recorded preview continues to replay persisted facts rather than provider logic.

## Phase 1.3 — Observability and operational hardening

Scope:

- Add structured correlation fields for experiment, run, job, lease, attempt, Runner, and provider request.
- Measure queue depth, claim latency, lease expiry/recovery, Run duration, event retries, provider latency/tokens, and terminal outcomes.
- Separate liveness and readiness checks for API, database, and Runner availability.
- Make migration, backup, and restore procedures executable and testable.
- Define retention and redaction rules for events and provider metadata.
- Add operator-facing diagnostics only where the collected signals prove they are needed.

Implementation progress as of 2026-08-15:

- **1.3A — Durable diagnostics: implemented.** Per-Run diagnostics expose Run, Job, current Lease/Runner, Attempt, provider, timing, retries, recovery, and terminal projections. The operations overview reports queue depth, status distribution, expired leases, recoveries, and event retries.
- **1.3B — Health and availability: implemented.** Separate API liveness, database readiness, and durable Runner availability endpoints are backed by authenticated Runner presence. Alembic revisions `0004` and `0005` and the Compose readiness probe are covered by CI and real-stack verification.
- **1.3C — Diagnostic correctness and safe correlation: implemented.** Immutable expired-Attempt history preserves final Lease/Runner correlation, and recovery totals include exhaustion. Provider telemetry and errors are allowlisted at ingestion, Request IDs become SHA-256 fingerprints, and revision `0006` sanitizes legacy records.
- **1.3D — Migration and data recovery: implemented.** Readiness compares the live `alembic_version` with the application Head. Environment-only backup and disposable restore commands use an exported snapshot and verify revision, every public-table row count, validated foreign keys, and an ordered Run trace. An isolated PostgreSQL 16 CI job runs the seeded rehearsal.
- **1.3E — Retention and redaction: implemented.** Experiment aggregates are the atomic retention unit; Plan emits a SHA-256 digest and Execute binds a reviewed plan file with PostgreSQL post-lock revalidation; `durable_events.py` owns Provider/Completion ingestion boundaries and `TerminalFailureKind` replaces free-text completion errors; the `database-recovery` CI job covers real PostgreSQL 16 retention locking, stale-plan, rollback, and backup/restore rehearsal. Commit `80e03e0` is on `main`.
- **1.3F — Alert classification and closeout: implemented.** `GET /api/operations/state` exposes machine-readable operator states with precedence across database, schema, Runner, lease, and provider faults; provider outage and rate limiting are separate states; operations overview and state evaluation use bounded SQL aggregates; `scripts/health_probe.py` and `docs/operations/fault-matrix.md` provide repeatable out-of-process probes. Commits `98c903d` and `b8f4b8c` are on `main`.

Phase 1.3 closeout (2026-08-15):

- **Status: Complete.** CI on `main` exercises backend `phase1_tests` (including operational state), migration round-trip, PostgreSQL backup/restore and retention integration, frontend typecheck/tests/build/recorded E2E, Rust fmt/clippy/test, Compose config validation, Golden closed loop, and Runner recovery rehearsal.
- **Evidence:** `.github/workflows/ci.yml` jobs `backend`, `database-recovery`, `frontend`, `rust`, `compose`, and `golden-e2e`; runbooks under `docs/operations/`; ADR-0005.

Execution plan:

1. **1.3C — Diagnostic correctness and safe correlation: Complete.** Immutable Attempt history, exhaustion-aware recovery counts, typed Provider errors, Request fingerprints, legacy-data sanitization, and malicious-metadata/cross-version retry tests are implemented.
2. **1.3D — Migration and data recovery (P0): Complete.** Readiness now compares the live `alembic_version` with the application Head. Executable `pg_dump` backup and disposable restore rehearsal commands verify schema revision, public-table row counts, validated foreign keys, and a restored Run trace in an isolated PostgreSQL 16 CI job.
3. **1.3E — Retention and redaction (P1): Complete.** Experiment-aggregate retention, plan-file execute, PostgreSQL post-lock revalidation, Provider/Completion redaction, and real PostgreSQL 16 integration tests are implemented; the control-plane maintenance boundary is recorded in ADR-0004.
4. **1.3F — Alert classification and closeout (P1): Complete.** Machine-readable operational states, bounded operations queries, provider outage vs rate-limit classification, fault matrix, and out-of-process API probe are implemented.
5. **Phase 1.3 closeout: Complete.** Full CI matrix and operations runbooks are in place; acceptance items below are evidenced.

Acceptance:

- A failed Run can be traced across API, database job, Runner attempt, and provider call without log guessing.
- Alerts can distinguish a dead Runner, expired lease, provider outage, and database/migration failure.
- Backup restoration and migration rehearsal have repeatable verification commands.

Acceptance status:

- Failed-Run correlation: **Complete** — current and expired Attempts, including exhaustion, retain Lease/Runner identity and safe Provider fingerprints.
- Failure classification: **Complete** — `/api/operations/state`, health probes, schema-drift readiness, Runner availability, lease expiry, and separate provider outage vs rate-limit states are implemented with a repeatable fault matrix.
- Backup and migration rehearsal: **Complete** — migration round-trip and an isolated PostgreSQL 16 backup restoration rehearsal have repeatable CI and local commands.
- Retention and redaction: **Complete** — Experiment-aggregate retention, plan-file execute, PostgreSQL post-lock revalidation, Provider/Completion redaction, and real PostgreSQL 16 integration tests are implemented.

No Phase 1.5 milestone is defined yet. With Phase 1.4 and its closed-loop depth follow-up complete, the next milestone must be chosen from measured operational needs rather than speculative features. Promote work from **Deferred until justified** only when a concrete requirement appears. The conditional safety gate takes priority if shared/public use, untrusted endpoints, untrusted accounts, or side-effecting tools enter scope.

## Phase 1.4 — Scenario onboarding

Historical tracker: `.scratch/archive/completed-trackers-20261005/phase1.4-scenario-onboarding/PRD.md`. Architecture boundary: [ADR-0006](docs/adr/0006-scenario-registry-boundary.md).

CI closeout (2026-08-23):

- **Status: Complete.** GitHub Actions run `31922310312` passed all six jobs: `backend`, `database-recovery`, `frontend`, `rust`, `compose`, and `golden-e2e`.
- **Provider follow-up: Complete.** `multi-step-research.v1` and `api-fault-orchestration.v1` now use the shared bounded Provider client with only their scenario-specific tools exposed (`340ca9a`, `bddbbdd`). Fixture execution remains the deterministic CI default.

Scope:

- Decide the public name (rename or qualifier) so the workbench is not confused with AgentOps-AI.
- Version `scenario_id` as `<name>.v<N>` in protocol v1; same id is comparable across Runs; semantic changes bump the version. Keep `schema_version` at 1.
- Keep optional bounded `scenario_params` on EvaluationSpec.
- Score registered Scenarios with a controlled assertion vocabulary (`tool-used`, `tool-args-match`, `tool-sequence`, `step-count`, plus result matchers) with weight, threshold, and defined combination semantics.
- Validate Scenario selection at Run creation; reject unregistered ids with structured errors.
- Expose `GET /api/scenarios` and a workbench Scenario picker on New Experiment.
- Ship two additional built-in Scenarios with fixture (CI) and provider (opt-in) paths, including a Golden closed loop for at least one of them.
- Document Scenario vocabulary, versioning, assertions, and a contributor guide for adding a Scenario.

Acceptance:

- Golden checkout loop still passes after registry and assertion refactor.
- At least one additional Scenario completes the full closed loop in CI without external APIs (`golden_e2e.py` now runs checkout, multi-step-research, and API fault orchestration by default).
- README, CONTEXT, and ADR-0006 describe registry, versioned identity, and assertion semantics; ROADMAP marks this milestone **Complete** when all acceptance items are evidenced in CI.

## Closed-loop depth follow-up

Historical tracker: `.scratch/archive/completed-trackers-20261005/closed-loop-depth/PRD.md`.

Closeout (2026-08-23):

- **Status: Complete.** Deterministic failure taxonomy and metric directions (`ca33d93`), persisted Before/After comparison with regression-first highlighting (`4847e6e`), and failed-Run regression cases with activation-time replay gating (`98d387e`) are on `main`.
- Fixture failures can be saved with scenario, seed, and parameters as deterministic regression assets. Provider failures retain an explicit non-reproducible boundary and do not enter automatic replay gating.
- Regression replays reuse the normal Run pipeline, appear in the Improve report, and never auto-activate a Policy. A person reviews the result and explicitly activates again.
- Local closeout passed backend `141 passed, 1 skipped` (the PostgreSQL retention integration requires `RETENTION_POSTGRES_URL`), frontend `27 passed`, TypeScript, production build, protocol checks, Rust formatting, and Windows Chromium recorded E2E `2 passed`.

Concurrency hardening follow-up (2026-08-31):

- **Status: Complete.** The full backend suite now passes `149 passed, 1 skipped`; focused concurrency/idempotency coverage passes `18` tests.
- The change is on `main` in `2cd081f`: RegressionCase creation, Policy replay claiming, and terminal completion side effects are database-safe and idempotent under contention.

## Next milestone — reliability and evaluation quality

Target: single-machine or trusted-intranet use. Priority is execution reliability first, then evaluation accuracy. Throughput and multi-user collaboration are deliberately deferred.

### P0 — close state-machine correctness gaps

- Make every Run status writer use compare-and-set or a row lock, including cancellation, heartbeat, and event persistence.
- Make duplicate event uploads truly idempotent under concurrent database connections; return a conflict when the same sequence carries a different payload.
- Make Policy activation, rejection, and RegressionCase replay claims atomic; prove that concurrent requests create no orphan Run or RunnerJob.
- Terminate the Agent process group when Runner stdin write or shutdown fails; add a broken-pipe regression test.
- Reject missing `RUNNER_TOKEN` outside an explicit development mode.

Acceptance: PostgreSQL multi-connection contention tests pass; terminal Runs cannot be overwritten; duplicate uploads converge; no orphan replay records remain; all Runner failure paths clean up child processes.

Verification update (2026-09-18):

- Implemented Run status CAS/re-read handling for cancellation, heartbeat, and event persistence; concurrent normalized duplicate events converge, while sequence conflicts remain explicit.
- Implemented conditional RegressionCase replay claims and Policy activation/rejection CAS. Losing activation transactions roll back their Run and RunnerJob before re-reading the winning claim.
- Runner stdin write/shutdown failures now terminate the Agent process group; the broken-pipe regression passes.
- Missing `RUNNER_TOKEN` fails startup unless `AGENTOPS_ENV=development`; empty or structurally invalid Provider messages emit `PROVIDER_INVALID_RESPONSE`.
- Backend verification passes `161 passed, 1 skipped`; Rust workspace verification passes, including the 15 Runner tests. PostgreSQL multi-connection evidence and frontend formatting remain environment-gated follow-ups.

Verification update (2026-09-19):

- Backend verification now passes `177 passed, 2 skipped`; the focused evaluation calibration and labeled-fixture checks pass `27` tests. Activation-claim retry idempotency and per-Policy RegressionCase replay coverage are included.
- A temporary PostgreSQL 16 container passes four state-transition contention tests. The PostgreSQL-focused run including retention reports `10 passed`; a separate local invocation without `RETENTION_POSTGRES_URL` is an explicit skip, not a passing local retention run.
- The Rust workspace passes, including all 15 Runner tests. Rust formatting (`cargo fmt --all -- --check`) and `git diff --check` pass.
- Added [the reliability and local validation runbook](docs/operations/reliability-and-local-validation.md) for token rotation, contention diagnosis, Provider failure interpretation, and WSL/proxy boundaries.
- Lease-bearing heartbeat, event persistence, and completion now fence the RunnerJob row with a database lock; event uploads keep a non-locking read for duplicate convergence, then lock before insert. Activation serializes competing Policies on the Experiment row, and a terminal replay from one Policy is never reused as another Policy's regression result.
- Compose now requires an explicit `RUNNER_TOKEN`, and API startup/authentication use one configuration snapshot. The predictable `local-runner-token` fallback was removed.
- Frontend validation remains blocked in this WSL environment because a Linux Node/npm toolchain is unavailable; no local frontend or frontend-formatting success is claimed. CI remains the frontend verification baseline.
- The reliability acceptance has PostgreSQL evidence for the covered state transitions. The synthetic calibration fixtures are regression guards, not an accuracy claim; independent labels and precision/recall reporting remain open. Local frontend reproducibility also remains open.

Verification update (2026-09-24):

- Persisted `RunEvent` calibration round trips now cover baseline and replay for all three built-in fixture Scenarios through the Runner API. The tests check ordered stored events, reconstructed observations, persisted analysis dimensions, and scores.
- Backend verification passes `180 passed, 2 skipped`; focused calibration verification passes `16` tests. These new round trips use the test SQLite database; the PostgreSQL contention evidence above remains dated 2026-09-19.
- The fixture labels remain synthetic. Independent labels and precision/recall/F1 with a confusion matrix are still required before making an evaluation-accuracy claim.

### P1 — improve evaluation quality and operator confidence

- Reject empty or structurally invalid Provider responses instead of treating them as success.
- Persisted `RunEvent` round trip: complete for the three built-in fixture Scenarios' baseline and replay runs.
- Replace the synthetic calibration smoke set with an independently labeled sample and report precision/recall/F1 plus a confusion matrix.
- Stabilize local verification across WSL and proxy environments; document a supported Node toolchain.
- Add runbooks for token rotation, contention diagnosis, and Provider failure interpretation.

Frontend verification update (2026-09-24):

- A SHA256-verified Linux Node `v22.23.3` toolchain runs `npm ci`, typecheck, 27 unit tests, and the production build successfully in WSL. The [local validation runbook](docs/operations/reliability-and-local-validation.md) records the reproducible setup.
- `npm run format:check` fails on 24 previously unchanged frontend files; the frontend CI job currently omits this check. Local frontend execution is reproducible, but formatting and the full local Golden/Compose matrix are not yet closed.

Calibration reporting update (2026-09-24):

- Added a read-only [evaluation calibration report](docs/evaluation-calibration.md) for separately labeled terminal Runs. It reconstructs persisted `RunEvent` trajectories and reports per-dimension and micro confusion counts, precision, recall, F1, and disagreements.
- The backend suite passes `184 passed, 2 skipped`; four focused reporting tests cover counting, label validation, persisted events, and invalid Run state. No independent label set has been collected, so there is still no evaluation-accuracy result to publish.

Local Compose verification update (2026-09-24):

- An isolated PostgreSQL 16 + API + current-source Rust Runner stack passed both registered `scripts/golden_e2e.py` scenarios: each persisted a failed baseline trace, completed a successful Replay, and activated its validated Policy. `scripts/recovery_e2e.py` killed the running Runner and verified Attempt 2 succeeded after lease recovery.
- The standard four-service Compose build could not fetch the Web `node:22-alpine` image metadata because Docker Hub timed out. The three-service verification used an offline Rust build in `rust:1.96-slim` and a Runner image containing that binary and the current Python Agent source. The isolated project and its volume were removed afterward; the Web container and recorded browser E2E remain unverified locally.

P1 follow-up verification (2026-09-24):

- CI now supplies an ephemeral `RUNNER_TOKEN` to Compose validation and Golden/Recovery Jobs; local Compose interpolation passes with the token and still rejects a missing token. The frontend CI Job now checks formatting after the 24-file Prettier baseline was normalized with `make fmt`.
- Linux Node 22 passes frontend formatting, typecheck, 27 unit tests, build, and both Recorded browser E2E tests. An isolated four-service Compose stack with a temporary local-Node Web override served Web and its API proxy, then passed both Golden scenarios and Runner recovery. The standard Web Dockerfile build remains blocked locally by Docker daemon timeouts fetching `node:22-alpine`; the override is verification-only.
- Added an analysis-blind [calibration review exporter](docs/evaluation-calibration.md) that samples current registered terminal Runs across scenario, mode, kind, and outcome without exposing scores or stored analysis. Its four-case Golden fixture smoke output verifies the workflow, not accuracy. A read-only clone of the existing Phase 1 volume contained only four legacy fixture Runs with an unregistered Scenario, so no independent label set or publishable precision/recall/F1 result exists yet. Backend verification passes `186 passed, 2 skipped`.

Review update (2026-09-25):

- Independent verification now passes the backend suite (`186 passed, 2 skipped`), PostgreSQL 16 contention and retention coverage (`10 passed`), the Rust workspace (15 Runner tests), frontend formatting/typecheck/27 unit tests/build, and two Recorded browser E2E tests. Compose rejects a missing `RUNNER_TOKEN` and accepts an explicit temporary token. The frontend checks ran in an isolated copy because the worktree's existing `node_modules/.vite` files are owned by `root`; the source tree was not changed by dependency installation.
- The calibration exporter is analysis-blind rather than outcome-blind: its packet still exposes Scenario and execution metadata plus terminal status, provider-error presence, limits, timing, and token counts. Keep the current wording and do not publish an accuracy result until independent labels, adjudication, selection provenance, and reviewer-agreement limits are recorded. Baseline/replay correlation and the absence of confidence intervals remain methodology limitations.
- `npm audit --omit=dev` reports two high production vulnerabilities through `react-router-dom`/`react-router` 7.18.0. Upgrade to a patched compatible release, regenerate the lockfile, and rerun frontend checks before any trusted-intranet deployment. This is a P1 fix even though the current target is a trusted network.
- The standard Web Dockerfile image still has no local build evidence because the Docker daemon cannot fetch `node:22-alpine`; the temporary Web override remains smoke-test evidence only.

Dependency and CI closeout (2026-09-29):

- Updated `react-router-dom` from `^7.0.0` to the minimum patched compatible `^7.18.2`; the lockfile now pins both `react-router-dom` and its `react-router` dependency to `7.18.2`. OSV identifies `7.18.2` as the fix for the reported high RSC CSRF advisory affecting `7.12.0` through `7.18.1`.
- Added `npm audit --omit=dev` to the frontend CI job immediately after `npm ci`, before formatting, typecheck, tests, build, and Recorded E2E. The existing frontend CI checks were otherwise already wired and unchanged.
- Current WSL still resolves `npm` to a Windows shim and has no Linux Node runtime, so local `npm ci`, audit, and frontend reruns were not claimed. The lock/package metadata and CI wiring were checked directly; rerun the complete frontend matrix in CI or a Linux Node 22 environment.
- A standard `docker build --pull -f frontend/Dockerfile -t agentops-web-standard-verify frontend` still fails before build steps while resolving `docker.io/library/node:22-alpine` (`dial tcp 173.236.212.42:443: i/o timeout`). The standard Dockerfile gate was later exercised successfully during an intermittent Docker Hub recovery (see 2026-10-05 evidence), but the latest rerun again failed with a registry timeout; treat the build as evidenced but not reproducibly available on this host.

Calibration sample and Docker follow-up (2026-09-29):

- A read-only copy of `agentops_phase1-pgdata` was inspected at Alembic revision `0005_runner_presence`. It contains 33 `RunEvent` rows and only four terminal Runs under the legacy, unregistered `checkout-api-latency` Scenario (three failed baselines and one succeeded replay); the current registered versioned Scenario sample count is zero.
- No review packet, labels, calibration report, or accuracy metric was generated because the source has no eligible current sample. This preserves the independent-label gate; the calibration-focused suite passes `22 passed` (the exporter/report subset passes `6 passed`).
- The current-source API image builds from cached dependencies. A current-source Runner build was attempted but stopped while the crates.io index was unavailable. Temporary snapshot containers, volumes, network, and verification image were removed; the original data volumes and local tooling were preserved.

Proxy-enabled verification and sample capture (2026-10-05):

- Docker Hub access recovered intermittently. The standard Web image built successfully from `frontend/Dockerfile` with base-image digest `sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402`; the image started and served the app. Local Compose needed `DOCKER_BUILDKIT=0` because its buildx reference directory is read-only; this is a host-builder workaround, not a source or Dockerfile change.
- Against the standard Web image, an isolated Node 22 validation ran `npm ci`, `npm audit --omit=dev` (`found 0 vulnerabilities`), formatting, typecheck, 27 unit tests, production build, and 2 Recorded browser E2E tests successfully. The complete dependency install still reports dev-only audit findings; the production CI gate is clean.
- The isolated standard four-service Compose stack built API, Web, and the Rust Runner, then passed both `scripts/golden_e2e.py` scenarios (failed baseline, successful replay, activated policy) and `scripts/recovery_e2e.py` (`running` before kill, `succeeded` after replacement). Temporary project, volume, network, and containers were removed.
- A fresh database snapshot from that stack was recorded as `sha256:dbd3734309ff439e6bcee38b133774e58470bf23b3abcdf4d9db2ad391f72427`. The analysis-blind packet is retained at ignored `.scratch/evaluation-calibration/review-packet-20261005.json` with 5 eligible terminal Runs: checkout (2 baseline, 1 replay) and multi-step research (1 baseline, 1 replay). It contains no scores or stored analysis. Independent labels, adjudication, and accuracy reporting remain pending.
- A demo-only provisional label draft now exists at ignored `.scratch/evaluation-calibration/demo-temp-labels-20261005.json`. It marks the two repeated six-step failed baselines as `planning` plus `budget` and leaves the three successful/recovery Runs empty. The recovery harness now uses the checkout Scenario's default task and emits its successful tool sequence after the replacement Runner, so no assertion-based planning prediction is copied into the draft. The draft is explicitly agent-generated, analysis-blind but outcome-exposed, and not independent or publishable; a human must edit or replace it and record reviewer/provenance/adjudication before metrics are used.
- The 2026-10-05 packet predates that recovery-contract correction and remains a historical demo artifact. The corrected packet below is the current demo sample; export another packet only when a new standard Compose sample is collected. The old temporary labels must not be silently reused as corrected accuracy evidence.
- The corrected isolated standard stack produced logical snapshot digest `sha256:2ad97aa9c22112dd55b2d55e669d605478facf260b3e138f6ea3853b0131f30c` and packet `.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`. It contains five terminal Runs; the recovery baseline now has the complete three-step checkout trace and analyzer dimensions are empty. The corresponding demo draft is `.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`; it is still agent-generated and not accuracy evidence.
- Hosted GitHub Actions run `36029244922` for `fbe51c5` completed successfully across `backend`, `database-recovery`, `frontend`, `rust`, `compose`, and `golden-e2e`. This closes the hosted CI confirmation for the dependency/CI batch; the local classic-builder workaround remains environment-specific.

Calibration mechanics follow-up (2026-10-06):

- The corrected packet and matching draft labels were checked: all five Run IDs match. A fresh temporary PostgreSQL database at Alembic head `0008_regression_cases` reconstructed the packet's `Experiment`, `Run`, and `RunEvent` facts, and `app.calibration_report` completed successfully with five cases and zero computed disagreements.
- The mechanics output is retained at ignored `.scratch/evaluation-calibration/demo-calibration-report-20261006.json`; it reports apparent `1.0` planning/budget and micro metrics, with `null` execution/context metrics because there are no positive labels. These are circular agent-generated-label mechanics results, not publishable accuracy.
- Reviewer/provenance/adjudication details are recorded at ignored `.scratch/evaluation-calibration/review-record-20261006-demo.json`: reviewer is explicitly agent-generated provisional, independent human review is pending, adjudication is absent, and `publishable_accuracy=false`. The temporary report database was removed after execution.
- The P1 calibration binding is implemented in `backend/app/calibration_report.py`: bound reports require the packet file digest, labels file digest, source snapshot, complete Run IDs, scenario/mode/kind/task rows, terminal status, database Experiment/Run identity, canonical trajectory digests exported from `calibration_review.py`, the complete event-stream digest, and the explicit source-controlled analyzer revision `failure-analyzer.v1`. External snapshot authenticity remains outside the packet contract.
- A provisional internal mechanics gate now passes for the fixed five-case fixture packet; the agent reviewed every label against the documented rules and found no correction necessary. This gate permits analyzer, trajectory, Scenario, recovery, exporter, schema, and report-mechanics development while the independent human-label accuracy gate remains pending. Its sidecar record is ignored `.scratch/evaluation-calibration/provisional-internal-gate-20261006.json`.
- Focused calibration verification (`-k calibration`) passes `31 passed, 2 skipped`; the backend suite passes `199 passed, 2 skipped`. The historical v1 sidecar remains preserved, and the pre-revision strict-bound derivative still parses as historical data; regenerate it with the current exporter before a new strict report. Agent-generated review remains non-publishable.

Provider and binding hardening follow-up (2026-10-07):

- Provider execution now rejects model tool arguments that do not exactly match the registered Scenario contract and persists the model-selected arguments in `step_completed`; the checkout tool schemas now declare their required arguments instead of silently accepting `{}`.
- Durable event normalization preserves the bounded integer `attempts` argument used by the fault-orchestration Scenario. Provider and durable-event focused tests pass `28 passed`; the complete backend suite remains `198 passed, 2 skipped`.
- The historical packet remains at `.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`. The dated strict-binding derivative `.scratch/evaluation-calibration/review-packet-20261007-recovery-fixed-bound.json` and sidecar `.scratch/evaluation-calibration/review-record-v1-20261007-provisional-bound.json` predate the analyzer-revision field; retain their recorded digests as historical evidence, but export a new packet and separately prepare matching labels and a sidecar before a new strict report. They remain non-publishable and do not claim accuracy.
- The recovery E2E harness now waits for `running` before fault injection and validates contiguous SSE sequences, attempts 1 and 2, the complete three-step Attempt 2 trace, and Attempt 2 completion. This change is unit-tested; a fresh real Compose kill/recreate run was not performed in this working-tree pass.

Calibration event-stream fidelity follow-up (2026-10-12):

- Each analysis-blind packet case now includes `event_stream_sha256`, a canonical digest of every persisted `RunEvent` row using sequence, event type, normalized UTC timestamp, and payload. The packet stores only the digest, so raw event payload stays out of the review artifact.
- Strict `calibration_report --require-binding` now rejects missing or malformed event-stream digests and rebuilds the digest from the current database before reporting. Event payload, event type, timestamp, and sequence changes are detected even when the sanitized trajectory projection is unchanged.
- Strict binding now requires packet and sidecar `source_snapshot` values to use canonical `sha256:<64hex>` syntax. This catches malformed or ambiguous identifiers while leaving historical packets parseable; it does not prove that an external backup or database snapshot produced the digest.
- Historical packet JSON remains parseable for inspection; strict binding requires the new field and current packet/sidecar identities. The packet remains analysis-blind and outcome exposure is unchanged.

Golden coverage and calibration compatibility follow-up (2026-10-13):

- Canonical snapshot syntax is enforced only with `--require-binding`; matching historical string identifiers remain usable for non-strict mechanics reports. The regression exercises both paths against persisted Runs.
- `golden_e2e.py` now includes `api-fault-orchestration.v1`. Its fixture baseline exhausts six calls returning 503; replay uses `retry_call → degrade_route → invoke_service` and satisfies the Scenario assertions with an improved score. CI's existing default invocation automatically includes all three Scenarios.
- Fault Golden checks now require six baseline steps, three replay steps, and assertion aggregate `1.0` before activation. A positive score delta alone can accept an incomplete replay; regression cases cover that failure and ensure activation is not called. Backend verification with loopback proxies removed passes `208 passed, 2 skipped`.
- The [calibration procedure](docs/evaluation-calibration.md) now starts from a retained backup digest and uses matching new packet/label/sidecar filenames. The exporter creates only the packet; provisional reviewer metadata and labels are prepared separately.
- An isolated four-service stack passed all three Golden Scenarios, Runner kill/replacement recovery, and a repeat fault run with the strengthened checks. API and Web used standard Dockerfiles with cached layers. The standard Runner build stalled at dependency installation; functional verification used a cached Runner image with current Python source mounted read-only. Its Rust binary matched the current build-stage SHA-256. This is explicit override runtime evidence, not a completed standard Runner build or a new hosted CI result.
- The frozen database produced a retained logical dump and a current nine-Run, 67-event packet. The strict report is `bound`, `provisional_mechanics`, and `publishable_accuracy=false`; reviewer metadata records fresh agent-generated labels, per-case evidence/uncertainty, and pending adjudication. The sample includes repeated/correlated fixture pairs and no positive context case. Evidence is indexed at ignored `.scratch/evaluation-calibration/20261013-compose-refresh-110352Z/README.md`. Containers/network were removed; the new and pre-existing data volumes were preserved.

Controlled coverage and local engineering closeout (2026-10-15):

- Fixed missed execution dimensions when a Provider error or timeout occurs before any tool step: existing run-level evidence now also supplies the `execution` dimension and dominant projection. The semantic revision is `failure-analyzer.v2`; retain historical v1 packets, which no longer satisfy current strict revision binding.
- Added `backend/scripts/controlled_calibration_capture.py` with six contracts fixed before prediction: high, low, and unknown context pressure plus Fake Provider success, timeout, and invalid response. Authenticated ASGI Runner API persistence produces a real backup, packet, separate contract labels, review sidecar, strict report, and manifest. Contract mismatches retain artifacts and fail. This does not exercise Rust supervision or external models.
- The earlier binary-label batch captured six Runs and 30 events in a disposable database. Its report is `bound`, `provisional_mechanics`, and `publishable_accuracy=false`, with no contract disagreements. Retain `.scratch/roadmap-closeout-20261015/controlled-capture-20261015/` unchanged as historical mechanics evidence: it counted unmeasured context as negative and does not establish current unknown-aware coverage. The 2026-10-13 controlled capture used semantically incorrect empty labels for Provider failures; preserve it unchanged but stop using it as a gate.
- The standard Runner Dockerfile now pins the Python dependency closure, uses locked Cargo builds, and preserves dependency caches across application changes. A completed standard build plus a final source refresh passed all three Golden Scenarios and Runner recovery in an isolated standard four-service stack, without source mounts. Python source hashes matched the source snapshot recorded for that acceptance run. Base images and API/Web layers used Docker cache; this is local evidence, not a new hosted CI run. Evidence: `.scratch/evaluation-calibration/20261015-standard-runner-closeout/final/`.
- Verification at this earlier closeout was `225 passed` for the backend, including isolated PostgreSQL tests, with no skips; protocol schema export checks passed. Linux Node 22 passed lockfile installation, production audit (zero vulnerabilities), formatting, typecheck, 27 unit tests, build, and two Recorded E2E tests. These dated acceptance records are local under `.scratch/roadmap-closeout-20261015/`.
- Remaining legacy trackers were triaged individually: 24 issues and four planning files are `wontfix`, with original content and replacement paths retained at `.scratch/archive/superseded-trackers-20261013/README.md`. Their old acceptance criteria are not all claimed implemented; removed architecture and public hosting are not current tasks.

Unknown-aware calibration follow-up (2026-10-15):

- Labels accept an optional `unknown_dimensions` set, disjoint from positive `dimensions`; omission preserves historical binary-label behavior. Per-dimension and micro confusion counts, metrics, and disagreements use only assessed Run/dimension pairs. Report dictionaries retain their metrics structure and add top-level `evaluation_scope="assessed_run_dimension_pairs"` plus per-dimension and micro coverage (`known_count`, `unknown_count`, `total_count`, `ratio`).
- A six-Run, 30-event capture passed strict binding with explicit unknown labels and no disagreements: context known 2 / unknown 4 out of 6 (`tp=1`, `tn=1`), micro known 20 / unknown 4 out of 24 (`tp=3`, `tn=17`). The earlier mechanics gate is retained at `.scratch/roadmap-closeout-20261015/controlled-capture-unknown-20261015/`; full backend verification at that checkpoint passed `228 passed`, including isolated PostgreSQL checks, with no skips.
- The analyzer remains `failure-analyzer.v2`; packet and v1 sidecar schemas are unchanged. Existing exact-byte labels digests bind unknown edits. Human review, adjudication, binding, and `accuracy_status` gates remain unchanged; valid unknowns alone do not prohibit a publishable record, which still does not establish complete coverage or representativeness. This batch's agent-generated labels remain `publishable_accuracy=false`.

Controlled calibration integration follow-up (2026-10-15):

- Added `scripts/controlled_calibration_e2e.py` to create isolated PostgreSQL capture/restore databases, run the six fixed contracts, restore the actual dump, verify its digest and matching database manifests, and require all strict-report fields to match after normalizing only the two binding file-digest prefixes. Both original reports remain unchanged. It checks unknown-aware coverage and `publishable_accuracy=false`, retains diagnostics, and only cleans up databases created by that invocation. The reproducible command and exact digest normalization are in the [calibration procedure](docs/evaluation-calibration.md).
- The earlier local mechanics checkpoint passed at `.scratch/roadmap-closeout-20261015/postgres-calibration-e2e/capture-run-verified/`: six Runs, 30 events, schema `0008_regression_cases`, restored backup `sha256:85f81cb1aadd5f7cd3df5c3da2d68783ddccf91c2ede7a6892af885bb835439b`, matching database manifests/reports, context known 2 / unknown 4, micro known 20 / unknown 4, `bound`, `provisional_mechanics`, and `publishable_accuracy=false`. The parent directory's `local-verification.json` records zero remaining harness UUID databases and zero credential matches across 29 artifact files. Historical batches and the initial failed comparison remain preserved.
- Full backend verification at that checkpoint passed `256 passed in 31.89s`, including PostgreSQL checks, with no skips (`backend-full.log` in the same evidence directory). The existing backup/restore rehearsal also passed; `legacy-recovery.log` and `legacy-cleanup.json` record its owned database cleanup and preservation of pre-existing databases. These host checks cover that checkpoint; the earlier standard-image acceptance remains tied to its recorded source snapshot.
- The `database-recovery` CI job now runs that gate after retention checks with a five-minute step timeout and seven-day artifact retention. This closes the manual-only integration gap in the existing reliability/evaluation work; hosted CI for these changes remains pending a later authorized push. Historical snapshots remain preserved; no Phase 1.5 is introduced.

Encoded credentials and CI step follow-up (2026-10-15):

- Fixed Alembic's ConfigParser percent-sign handling for correctly encoded async database URLs, and distinguished post-verification cleanup failures as `cleanup_restore` or `cleanup_source` in the sanitized harness summary. CLI regressions cover encoded URLs and cleanup failures.
- Final full backend verification passed `262 passed in 42.72s`, including PostgreSQL checks with no skips. The current gate also passed migration and actual capture/restore with an encoded-credential `LOGIN CREATEDB` role without superuser access; its dump is `sha256:57feb2fc0d0e7d679b1d9600860bdc56e3f74913cc8e3a549aa62dc84cb1d48a`. Temporary databases/role were removed, the configured database was preserved, and artifacts plus the decompressed dump had zero known credential matches. Evidence is indexed in the [calibration follow-up](docs/evaluation-calibration.md#encoded-credentials-and-ci-step-follow-up-2026-10-15) under `.scratch/roadmap-closeout-20261015/ci-sequence-followup/`.
- Separate local CI command reproductions passed: the earlier backend job ran schema export/diff, `246 passed, 2 skipped`, and migration up/down/up with the two PostgreSQL modules disabled; the earlier recovery job enabled all ten PostgreSQL tests and passed migration, existing backup/restore, and controlled capture/restore in sequence. These local checks do not establish hosted CI success or update the earlier image acceptance. The provisional accuracy gate remains unchanged.

Current-version system acceptance (2026-10-15):

- **Local engineering acceptance passed**, with a non-blocking UI limitation documented in the [acceptance matrix](docs/operations/system-acceptance.md). The code fixes are local commits `3f10485` and `5a65d4a`; nothing was pushed.
- Fixed an actual PostgreSQL cancellation/completion deadlock by aligning cancellation and claim with Job-to-Run lock order. Deterministic PostgreSQL regressions also reject the cancellation-only fix that would move the deadlock to queued claim. Fixed native named-SSE delivery and listener cleanup; the actual browser now renders the previously empty six-step Trace.
- Final verification: **265 backend tests with no skips**, protocol export/diff, **34 frontend unit tests**, typecheck/build/format, **two Recorded E2E tests**, and Rust fmt/Clippy/workspace tests passed. Refreshed standard images passed all three Golden loops, actual checkout UI with RegressionCase activation gating, real request interruption and `after=1` reconnection, cancellation, Runner kill/replacement, nonterminal stale-lease rejection and application backup/restore. The 147-file source manifest matches committed code; application containers have no source mounts. Docker cache was allowed; hosted CI remains unverified for these commits.
- Fresh PostgreSQL migration up/down/up, retention/concurrency and six-contract calibration capture/restore passed. Agent contract labels remain `provisional_mechanics` and `publishable_accuracy=false`; independent labels and adjudication are still pending. Local evidence is under `.scratch/system-acceptance-20261015/`.
- Non-blocking follow-up: RegressionCase card status can require navigation or reload after its replay finishes. The persisted activation gate remains correct. Improve that refresh behavior as a bounded UI maintenance task; no new product milestone is needed.

Next steps:

1. After later push authorization, run hosted CI against the local commits, including the newly wired PostgreSQL calibration capture/restore gate. This batch is local-only; the 2026-10-05 CI result is not evidence for current commits.
2. Maintain the unknown-aware v2 six-contract engineering gate with its automated PostgreSQL capture/restore check. Changes to analyzer, trajectory, Scenario, recovery, exporter, or reporting must maintain relevant regressions and revision/digest binding. No mandatory new product feature remains defined; do not start a speculative Phase 1.5.
3. When practical, obtain at least two independent reviews of a representative sample, record evidence and uncertainty, adjudicate disagreements with a named reviewer, and export/report again. Publish no precision/recall/F1 before that gate; missing reviewers do not block engineering, and the report does not authenticate external snapshot provenance.
4. Address database credentials and access boundaries when trusted-intranet deployment actually starts. Promote throughput, collaboration, and other deferred work only for a concrete need with an ADR.

Track acceptance separately: close deterministic engineering mechanics, local reproducibility, and documentation cleanup independently. Independent labels and the accuracy-publication gate remain pending; agent-authored contracts cannot replace them.

### P2 — explicit cleanup and deferred work

- Keep obsolete planning/analysis documents out of `docs/`; retain them only under `.scratch/archive/` until the next repository cleanup. At that cleanup, delete only documents confirmed superseded by ROADMAP/CONTEXT and leave the dated handoff plus local `.codegraph/` and `.cursor/` tooling untouched.
- Re-triage stale `.scratch/` issues and mark superseded work `wontfix` or `ready-for-human`; delete obsolete issue files only after the label and replacement path are recorded.
- Retain the 2026-10-05 legacy/completed tracker archives and local `.bak` copies, with replacement paths in their README files. On 2026-10-13, all 28 remaining files in `baseline-hardening/`, `closed-loop-architecture/`, and `demo-packaging/` received individual `wontfix` decisions and moved to `.scratch/archive/superseded-trackers-20261013/`; no implementation task remains there. These ignored local archives are not source or rollback inputs. Preserve original content, dated handoffs, `.codegraph/`, and `.cursor/`.
- Before any trusted-intranet deployment, replace the Compose demo PostgreSQL credentials with an explicit secret source; keep demo defaults confined to an explicitly documented local-only profile.
- Do not start MCP, vector memory, side-effecting tools, multi-tenancy, or throughput work until a measured requirement promotes them and a new ADR defines the boundary.

## Conditional safety gate

Before enabling side-effecting tools, user-supplied or untrusted external endpoints, untrusted accounts, or shared/public operation:

- Write a threat-model ADR.
- Add authentication, authorization, audit records, secret redaction, and resource limits.
- Define a PreToolUse decision boundary with explicit allow, block, and escalation semantics.
- Test SSRF, command injection, cross-tenant access, cancellation, and budget enforcement.

These controls are intentionally designed before those capabilities are enabled; they are not approximated by a misleading partial safety layer.

## Deferred until justified

Kubernetes execution, Docker socket access, MCP transport, vector memory, training export, framework adapters, arbitrary code execution, multi-tenancy, billing, and automatic policy activation remain outside the active roadmap until a measured requirement promotes them.

When a deferred item is promoted, update this file, create or update a PRD under `.scratch/`, and record any new architectural boundary in an ADR.
