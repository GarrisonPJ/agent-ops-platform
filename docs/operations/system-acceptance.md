# Current-version system acceptance

Date: 2026-10-15. Status: local engineering acceptance passed with the limits below.

This acceptance covers the implemented single-machine/trusted-intranet system.
It prioritizes reliable execution and correct evaluation mechanics. Independent
human labels and adjudication remain pending and do not block engineering
acceptance. External Provider execution and hosted CI are separate checks.

## Source and execution boundary

The initial candidate is `6a7ac512df74dd6153f67611e34bf8d4120dc050`.
The accepted code fixes are local commits `3f10485` (PostgreSQL lock order) and
`5a65d4a` (native SSE subscription); final runtime checks use those source bytes.
The current acceptance uses a disposable four-service Compose project with the
standard API, Runner and Web Dockerfiles, temporary credentials, isolated ports
and no source mounts. Docker layer cache is allowed. `DOCKER_BUILDKIT=0` is a
host builder workaround. A ports/credentials override does not replace any
application build or mount application source.
The standard Web Dockerfile starts the Vite development server. Its browser
acceptance and the isolated Linux Node 22 production-build check are separate
evidence; production Web deployment is outside this acceptance.

Local logs, source manifests and machine-readable results are retained under
the ignored `.scratch/system-acceptance-20261015/` directory. These artifacts
are local evidence; a fresh checkout must repeat the checks below. Historical
acceptance records remain unchanged.

The refreshed image IDs start with `sha256:5a1ac2557641` (API),
`sha256:f05db781a723` (Runner) and `sha256:207a8ed99e43` (Web).
`stack/final-runtime-identity.json` records all full image IDs, 147 workspace
file hashes and source hashes read inside the containers. Its SHA-256 is
`5685e6150bb12015eda1b5b60a60d50b9435d3fbcb25f878ba5a7586f2e7670b`.
An independent review checked those hashes and confirmed no application mounts.
`source-commit-binding.json` additionally verifies that all 147 manifest files
match committed code at `5a65d4ae6a3f606d481aff7d6f6b8e74fda37cef`.

Execution owners were `root` (backend/Rust), `calibration_ci_design`
(standard stack/database/faults) and `calibration_ci_wiring_docs` (frontend/live
browser). `calibration_postgres_audit` independently reviewed source changes
and execution evidence. These are agent engineering reviews; independent human
calibration review and adjudication remain pending.

## Acceptance matrix

| Area | Result | Evidence and limits |
| --- | --- | --- |
| Backend, protocol and PostgreSQL | Passed | Final source: **265 passed in 58.62s**, no skips, with PostgreSQL enabled; protocol export matched tracked contracts. `control-plane/final-2/summary.json`. |
| Rust | Passed | Workspace fmt, Clippy with warnings denied and locked tests; 30 tests across protocol, fixtures and Runner. No Rust changes in this acceptance. |
| Frontend and Recorded Preview | Passed | Linux Node 22, clean lockfile installation and production audit (0 vulnerabilities); final format, typecheck, **34 unit tests**, build and **two Recorded E2E tests** passed. `browser/frontend-fixed-checks.json`, `fixed-recorded-results.json`, `make-fmt.result.json`. |
| Standard images and three Golden loops | Passed | Refreshed all three standard images after fixes; checkout, multi-step research and API fault orchestration all passed baseline/replay/comparison/explicit activation. `stack/final-standard-build.result.json`, `final-golden-three-scenarios.result.json`. |
| Live browser and event stream | Passed | Actual checkout workflow: baseline, Trace, Analysis, Improve, replay, comparison and explicit activation. First activation returned `409 REGRESSION_REPLAY_PENDING`; case replay succeeded before a second explicit activation. Interrupting a real stream after sequence 1 caused a native error and automatic application reconnection with `after=1`; received sequences 1–9 were continuous and unique. Three-Scenario UI coverage and whole-device offline/online recovery are not claimed. `browser/live-acceptance.json`, `live-cursor-reconnect.json`. |
| Cancellation, recovery and lease fencing | Passed | Running cancellation reached `cancelled`; terminal retry did not change facts. Original recovery assertions passed after Runner kill/lease expiry/replacement, ending in successful Attempt 2. While another Attempt 2 was still nonterminal, old heartbeat/events/completion all returned `403 INVALID_LEASE` and changed no facts. `stack/real-runner-recovery.json`, `real-cancellation-stale-lease.json`. |
| Migrations and retention | Passed | Fresh PostgreSQL migration up/down-to-base/up reached `0008_regression_cases`; owned database removed. Final backend suite included real PostgreSQL retention and concurrency checks. `stack/migration-cycle.json`. |
| Application backup/restore | Passed | Freeze writes in the disposable stack, retain a real dump, restore into an owned database and compare revision, all public-table counts (22 Runs / 150 events), 15 foreign keys and a selected Run's nine-event ordered trace. Restore database removed; application database/volume preserved. `stack/acceptance-summary.json`. |
| Controlled calibration | Passed, provisional | Six Runs / 30 events captured in real PostgreSQL and restored from the actual dump; reports match, binding is `bound`, unknown coverage is preserved, `publishable_accuracy=false`. Agent-authored contract labels cannot establish independent accuracy. `controlled-calibration/`. |

## Defects found during acceptance

- **Fixed: PostgreSQL deadlock.** Cancellation and completion acquired
  Run/RunnerJob locks in opposite orders. Cancellation and claim now follow
  Job-to-Run order. Independent-connection regressions use `pg_blocking_pids`
  to prove actual contention and verify terminal convergence and one-time
  effects. The original code deadlocks in both cancellation/completion orders;
  a cancellation-only fix deadlocks against queued claim. All three final
  regressions pass. Two overly strict response assertions were corrected to
  allow a post-commit refresh to observe a later legal state; final database
  and side-effect assertions remain strict. Failed attempts are retained.
- **Fixed: missing live Trace events.** The API emits named SSE events, which
  do not invoke `EventSource.onmessage`. The frontend now registers all protocol
  event types and removes listeners on terminal/error/unsubscribe. Before-fix
  browser evidence showed an empty Trace for a six-step Run; the refreshed
  standard Web renders those six steps and process output. Subscription-boundary
  regressions cover named dispatch and cleanup, separately from parser tests.

## Cleanup and retained evidence

The stack executor verified that all ten invocation-owned temporary databases
were removed while the application database remained present. The browser/CLI
processes were closed, and the separate backend-verification PostgreSQL was
stopped with its container and mounts preserved. The disposable Compose project
is shut down after independent review; its application volume, images, dumps
and local evidence remain available. `stack/stack-shutdown.json` records zero
remaining project containers/networks and preservation of the volume/images,
separately from the immutable runtime manifests.

The closeout credential scan found no temporary credential matches in 1,023
shareable files or either decompressed dump. Private connection settings are
excluded from those shareable artifacts and retained with restrictive modes.

## Repeat the checks

Use the [local validation runbook](reliability-and-local-validation.md) for
Linux Node 22, proxy handling, backend/Rust checks and the PostgreSQL calibration
gate. Use the [backup/restore runbook](backup-restore.md) for disposable recovery.
PostgreSQL concurrency tests must use a newly owned test database with
`RETENTION_POSTGRES_URL`; never point those tests at an
application database.

Build the standard Compose stack in a uniquely named project using temporary
credentials. Run `scripts/golden_e2e.py` for all three registered Scenarios.
Run `scripts/recovery_e2e.py` only against that disposable stack: it kills and
replaces the Runner. Verify the real browser workflow separately from
`frontend/tests/phase1-recorded.spec.ts` and preserve event order, replay results
and explicit policy decisions. After source fixes, rebuild affected images and
repeat the affected acceptance checks.

## Remaining gates

- Local engineering acceptance is complete for this source snapshot and scope.
- Non-blocking UI follow-up: a RegressionCase card can retain its earlier
  queued/running projection until navigation or reload. Opening its replay
  shows the actual Run; activation still enforces the persisted server gate.
  Automatic card polling was not verified or added in this acceptance.
- Hosted CI for these local commits requires a later authorized push.
- External Provider checks remain opt-in and were not requested here.
- Independent representative labels, reviewer provenance and adjudication are
  required before publishing accuracy. The controlled report must remain
  `provisional_mechanics` with `publishable_accuracy=false` in their absence.
- Deployment credentials and access controls are addressed when actual
  trusted-intranet deployment begins. No speculative Phase 1.5 is introduced.
