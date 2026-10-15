# Reliability and Local Validation

This runbook covers the Phase 1 reliability checks that are useful during a
local or trusted-intranet deployment. It describes the behavior currently
implemented by the API, Runner, and Compose stack. It does not turn the
single-machine deployment into a multi-tenant or public service.

## Before changing anything

Set the API URL without embedding credentials in it:

    export API_URL="${API_URL:-http://127.0.0.1:8000}"

Check liveness, database/schema readiness, Runner presence, and the classified
state before investigating a Run:

    curl -sS "$API_URL/api/health/live" | jq .
    curl -sS "$API_URL/api/health/ready" | jq .
    curl -sS "$API_URL/api/health/runner" | jq .
    curl -sS "$API_URL/api/operations/state?window_seconds=900" | jq .

`/api/operations/state` returns HTTP 200 even when `status` is `degraded`.
Use `primary_state`, `states`, `incidents`, and `summary`; do not use the HTTP
status alone as the operational verdict.

## Rotate `RUNNER_TOKEN`

The API and Runner use the same bearer token. The API copies the value into its
application state at startup, and the Runner reads its environment when it
starts. Rotation therefore requires updating both services and restarting
them; there is no hot-reload endpoint.

1. Generate a replacement in a protected shell. Do not paste it into a ticket
   or print it in CI logs:

       umask 077
       new_token="$(openssl rand -hex 32)"

2. Update the ignored Compose `.env` file (or the deployment secret source)
   so `RUNNER_TOKEN` contains the new value. Use a protected editor or secret
   manager input; do not echo `$new_token` into a CI log. Keep the file mode
   restricted and do not commit it. The API and Runner must receive the exact
   same value.

3. Restart the stack together so no Runner continues using the old token:

       docker compose -f infra/docker/docker-compose.phase1.yml up -d --build --force-recreate api runner

   A restart may leave queued work waiting briefly; check Runner availability
   before treating that as a failure.

4. Verify readiness and Runner heartbeat, without revealing the token:

       curl -sS "$API_URL/api/health/ready" | jq .
       curl -sS "$API_URL/api/health/runner" | jq .
       docker compose -f infra/docker/docker-compose.phase1.yml ps api runner

The Compose file requires `RUNNER_TOKEN` during interpolation and refuses to
start when it is unset. A direct API startup also fails fast outside explicit
development mode. Set and rotate an explicit secret for every Compose or
trusted-intranet deployment; do not restore a predictable fallback.

If the API is ready but `/api/health/runner` remains unavailable, inspect the
Runner logs and confirm that its `AGENTOPS_SERVER_URL`, `RUNNER_TOKEN`, and
Runner identity match the API endpoint. Do not solve a token mismatch by
disabling authentication.

## Diagnose contention and stale leases

For a suspected Run, save only sanitized JSON (no environment dumps or
credentials):

    export RUN_ID="replace-with-run-id"
    curl -sS "$API_URL/api/operations/runs/$RUN_ID" | jq .
    curl -sS "$API_URL/api/operations/overview" | jq .

The Run diagnostics expose the current Run status, job lease and Runner,
attempt/recovery history, queue/run timing, event retry count, and safe Provider
correlation data. An expired lease is recoverable by the next authenticated
claim; repeated recoveries are visible in `attempts` and `summary`.

Interpret API conflicts as follows:

| Code | Meaning | Operator action |
|---|---|---|
| `EVENT_CONFLICT` | A sequence already exists with a different event type or normalized payload. | Stop retrying that payload; compare the Run and Runner logs and preserve the conflicting sequence as evidence. |
| `EVENT_SEQUENCE_GAP` | The upload skipped the next contiguous sequence. | Re-read `accepted_through`/diagnostics and resume from the next sequence; do not invent a sequence. |
| `LEASE_EXPIRED` | The lease is no longer valid for heartbeat, event upload, or completion. | Let the next authenticated claim recover the Run; do not reuse the expired lease. |
| `INVALID_LEASE` | The lease does not belong to the supplied Runner or Run. | Check Runner identity and token configuration; never copy a lease between Runners. |
| `RUN_TERMINAL` | A terminal Run cannot accept more events or a heartbeat. | Re-read the terminal outcome and diagnostics. A duplicate completion is idempotent; do not append after terminal state. |
| `REGRESSION_REPLAY_PENDING` | Activation is waiting for one or more RegressionCase replays. | Use the response `case_ids` and `run_ids`, then inspect each Run; activation must wait for successful replays. |

Concurrent state writers use conditional updates and re-read the winner. A
losing terminal completion does not run terminal scoring or policy side
effects. Concurrent duplicate event uploads with the same normalized payload
converge; a different payload at the same sequence remains an explicit
`EVENT_CONFLICT`. Do not treat a 409 as evidence that a second replay or job
was created; inspect the returned IDs and diagnostics first.

## Interpret Provider failures

Provider settings are supplied to the Runner only. The API does not accept a
Provider URL, model, or credential from an Experiment request. Run diagnostics
and terminal Run errors contain safe codes, model identity, request counts,
latency, token totals, and optional request fingerprints; endpoint values,
credentials, and hidden reasoning are not persisted.

For a terminal Provider Run, inspect both `terminal.error` and
`provider.error`:

| Code | Classification | Meaning and next check |
|---|---|---|
| `PROVIDER_NOT_CONFIGURED` | Configuration, not retryable | One or more required Runner settings (`AGENTOPS_PROVIDER_BASE_URL`, `AGENTOPS_PROVIDER_API_KEY`, or `AGENTOPS_PROVIDER_MODEL`) is missing. |
| `PROVIDER_CONFIGURATION_ERROR` | Configuration, not retryable | The URL or numeric timeout/retry setting is invalid. Fix Runner environment and restart it. |
| `PROVIDER_TIMEOUT` | Transport, retryable | The bounded request timed out. Check Provider latency and the configured timeout before retrying. |
| `PROVIDER_UNAVAILABLE` | Transport, retryable | HTTPX could not complete the request. Check DNS, network route, proxy, and Provider health. |
| `PROVIDER_RATE_LIMITED` | Remote rate limit, retryable | The Provider returned HTTP 429. Respect the Provider's limit/backoff policy. |
| `PROVIDER_HTTP_ERROR` | Remote HTTP error | The stored `retryable` flag is authoritative (408, 429, and 5xx are retryable in the current client). The persisted message is redacted; correlate the request fingerprint or request ID with Provider-side logs when the remote service exposes them. |
| `PROVIDER_INVALID_RESPONSE` | Protocol, not retryable | The completion was empty or structurally invalid, or returned invalid/multiple tool calls or arguments. Fix the compatible server response. |
| `PROVIDER_STEP_LIMIT` | Evaluation limit, not retryable | The Provider path exhausted its configured step budget. Review the Scenario or policy rather than blindly retrying. |
| `PROVIDER_UNSUPPORTED_TOOL` | Allowlist violation, not retryable | The model selected a tool outside the Scenario's registered tool set. Do not expand the allowlist as an incident workaround. |

`/api/operations/state` classifies recent `PROVIDER_RATE_LIMITED` separately
from `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, and `PROVIDER_HTTP_ERROR`.
Configuration, protocol, step-limit, and unsupported-tool failures remain
visible on the Run but do not become the `provider_unavailable` aggregate
state. Use the Run diagnostics when the aggregate is `ok` but an individual
Provider Run failed.

## WSL, proxy, and local verification boundary

The reproducible baseline is the CI toolchain: Ubuntu, Python 3.12 via `uv`,
Node 22 with `npm ci`, Rust 1.96, PostgreSQL 16, and Docker Compose. Local WSL
checks are valid only when their tools resolve to Linux binaries:

    command -v node
    node --version
    command -v npm
    npm --version
    command -v uv
    command -v cargo

On Linux x86_64 WSL, a temporary, verified Node 22 toolchain can be installed
without changing the Windows Node installation:

    node_version=v22.23.3
    node_bundle="node-$node_version-linux-x64"
    node_dir="$(mktemp -d /tmp/agentops-node22.XXXXXX)"
    curl -fsSLo "$node_dir/$node_bundle.tar.xz" "https://nodejs.org/dist/$node_version/$node_bundle.tar.xz"
    curl -fsSLo "$node_dir/SHASUMS256.txt" "https://nodejs.org/dist/$node_version/SHASUMS256.txt"
    (cd "$node_dir" && rg -F "  $node_bundle.tar.xz" SHASUMS256.txt | sha256sum -c -)
    tar -xJf "$node_dir/$node_bundle.tar.xz" -C "$node_dir"
    export PATH="$node_dir/$node_bundle/bin:$PATH"
    node --version
    npm --version

Use the Linux package for the actual WSL architecture. The `PATH` change
applies only to the current shell; a new shell needs its own setup.

For frontend validation, use Node 22 and the repository lockfile:

    cd frontend
    npm ci
    npm run typecheck
    npm test
    npm run build
    npm run format:check
    npm run e2e:recorded

If `npm` resolves to a Windows `.cmd` shim (for example under `/mnt/c/...`)
or Linux `node` is unavailable, stop the frontend check and report it as an
environment blocker. Do not claim `make test` or `make fmt` from a partial
WSL run; use CI or a Linux Node 22 installation.

Verification on 2026-09-25 with verified Linux Node `v22.23.3` in an isolated frontend copy: `npm ci`,
format checking, typecheck, 27 unit tests, production build, and two Recorded
browser E2E tests passed after `make fmt` normalized the prior 24-file format
baseline. The repository worktree had root-owned Vite cache files, so dependency installation was kept out of the worktree. The frontend CI job now runs `format:check`. The Recorded E2E uses
Playwright Chromium, which must be installed locally before running it.

An isolated four-service local Compose run first passed Web/API readiness,
both Golden scenarios, and Runner recovery using a temporary Web override
with the verified Linux Node toolchain. On 2026-10-05, intermittent Docker Hub
access also allowed the standard Web Dockerfile and standard four-service stack
to pass. A later standard build retry again timed out resolving
`node:22-alpine`, so preserve the successful evidence but treat registry
availability as environment-dependent. Never treat an override as a successful
standard image build.

The Runner installs its Python protocol dependencies from
`runner-requirements.lock`. When updating `backend/uv.lock`, refresh the pinned
`pydantic`/`httpx` dependency closure in that file from the same lock, then build
the standard Runner image and run `pip check` inside it. The image installs
binary wheels with bounded pip timeout/retries. Its Rust stage uses
`cargo build --release --locked`; only Cargo manifests, the lockfile, and crate
sources enter that stage, so edits to the Python dependency lock or runtime
instructions preserve the Rust build cache. Python application code is copied
after dependency installation.

On 2026-10-15, the standard Runner Dockerfile completed a local build with a
fresh locked Rust compilation and installation of all 11 pinned Python
packages. After a concurrent analyzer edit, a second standard build refreshed
the application copy and reused those Rust/pip layers. The final standard
four-service Compose stack passed Web/API/Runner readiness, all three Golden
scenarios, and the original Runner recovery assertions (`running` before the
kill, then `succeeded` with the complete Attempt 2 trace). Runner/API Python
sources matched the final checkout and neither container had a source bind
mount. Base images and API/Web dependency layers used local Docker cache; this
is standard local build/runtime evidence, not a clean hosted CI run.
The build logs, source hashes, image identities, package check, recovery result,
and database dump are preserved under
`.scratch/evaluation-calibration/20261015-standard-runner-closeout/`, with the
final acceptance artifacts in `final/`. The isolated project's containers were
removed after verification; its image and database volume were retained.

Provider fake-server tests must not be sent through an inherited corporate
proxy. On 2026-10-05 the isolated command below completed the full backend
suite with `188 passed, 2 skipped`; the same suite had produced loopback
timeouts when the proxy variables were inherited. Prefer a one-command
isolated environment:

    env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
      -u http_proxy -u https_proxy -u all_proxy \
      -u NO_PROXY -u no_proxy \
      sh -c 'cd backend && uv run pytest phase1_tests/test_provider_agent.py -q'

If the proxy must remain configured, include `127.0.0.1,localhost` in both
`NO_PROXY` and `no_proxy`, and confirm the fake server binds to loopback. A
proxy-caused fake-server failure is local verification drift, not evidence of
a Provider business regression. Never disable proxy verification globally for
production traffic.

Backend and Rust checks that do not require the frontend toolchain can still
run independently:

    make test-backend
    make test-rust
    cd runner && cargo fmt --all -- --check

Record the exact command, tool versions, proxy treatment, and exit status. The
local results do not replace the CI PostgreSQL multi-connection, Compose, and
Golden checks.

## Repeat the PostgreSQL calibration gate

Use a dedicated PostgreSQL test service with a `CREATEDB` role and provide its
`DATABASE_URL` in SQLAlchemy async format, `postgresql+asyncpg://`, through a
protected environment. Matching `pg_dump` and
`pg_restore` clients must be on `PATH` (CI uses PostgreSQL 16). From `backend/`,
choose an output directory that does not exist:

```sh
PYTHONPATH=. VIRTUAL_ENV= uv run python ../scripts/controlled_calibration_e2e.py \
  --output ../.scratch/calibration-postgres-NEW
```

The harness generates a temporary Runner token in memory by default, creates
unique capture and restore databases, and runs migrations plus the six-case
controlled capture. It restores the actual captured dump, verifies the backup
digest and matching database manifests, and requires complete strict-report
equality after restoration, normalizing only the optional `sha256:` prefix in
valid 64-hex `binding.packet_sha256` and `binding.labels_sha256` values. It keeps
both original report files unchanged. The gate checks unknown-aware coverage and
`assessed_run_dimension_pairs` scope,
while keeping `failure-analyzer.v2`, `provisional_mechanics`, and
`publishable_accuracy=false`. It does not establish independent accuracy.

The output keeps the original capture artifacts in `capture/`, the restored
report in `restored-strict-report.json`, and success or sanitized failure
stage/type in `e2e-summary.json`. Credentials and environment settings are not
diagnostic artifacts. Cleanup runs on success and failure and only drops
databases created by that invocation; retain the output and choose a new path
for a rerun. Existing application databases and dated calibration artifacts
are not cleanup targets.

Use normal URL encoding for reserved password characters, including `%40` and
`%25`; do not double percent signs in `DATABASE_URL`. Alembic handles the
ConfigParser escape internally. If database cleanup fails after report
verification, the sanitized summary identifies `cleanup_restore` or
`cleanup_source` and the command fails; inspect that stage when diagnosing
leftover owned databases.

The `database-recovery` CI job runs the same harness after the PostgreSQL
retention checks, with a five-minute step timeout and the fresh workspace path
`.scratch/calibration-postgres-ci/`. Its artifact upload retains only that
directory for seven days on success or failure. The CI wiring is ready for
the next authorized push; it is not a hosted CI result. See the
[calibration procedure](../evaluation-calibration.md) for the controlled cases
and retained evidence.

At the earlier 2026-10-15 checkpoint, the host-run gate passed under
`.scratch/roadmap-closeout-20261015/postgres-calibration-e2e/capture-run-verified/`.
The evidence directory's `local-verification.json` records zero remaining harness
UUID databases and zero credential matches across 29 artifact files.
`backend-full.log` records `256 passed in 31.89s`, with no skips. The existing
backup/restore compatibility rehearsal also passed; `legacy-recovery.log` and
`legacy-cleanup.json` record its result, owned database cleanup, and preservation
of pre-existing databases. These local checks cover that checkpoint; the earlier
standard-image acceptance remains tied to its earlier source snapshot, and
hosted CI is still pending.

The later encoded-credential gate and final full-suite evidence are indexed in
`.scratch/roadmap-closeout-20261015/ci-sequence-followup/`, alongside separate
local reproductions of the backend and recovery CI steps. The backend job leaves
`RETENTION_POSTGRES_URL` unset and skips two PostgreSQL modules; the recovery job
enables and passes their ten tests. See the
[dated follow-up](../evaluation-calibration.md#encoded-credentials-and-ci-step-follow-up-2026-10-15)
for the final counts, dump identity, and cleanup evidence. These local command
reproductions are distinct from hosted CI and earlier image acceptance.

## Evidence and escalation

Record UTC time, application revision, sanitized Run IDs, endpoint status,
`primary_state`, relevant conflict or Provider code, and toolchain/proxy
conditions. Do not record `RUNNER_TOKEN`, Provider API keys, raw URLs with
credentials, request bodies, or unredacted Provider responses. Escalate with
the diagnostics JSON and Runner/API logs after removing secrets.
