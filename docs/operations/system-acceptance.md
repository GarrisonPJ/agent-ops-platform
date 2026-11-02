# System acceptance: current procedure and evidence limits

This is the current H1 acceptance entry, not the 2026-10-15 acceptance of an older implementation. That record is archived in [history](../history/system-acceptance-2026-10-15.md). Executable status lives only in [phase2-tasks.md](../agents/phase2-tasks.md); this document describes checks and their authority.

## Current boundary (2026-11-01 local / 2026-10-31 UTC)

H1 engineering implementation is integrated. The two Runner P2 repairs have scoped independent review, and parent affected regressions, exact-payload checks and the standard-image replacement run below passed. H1 local engineering and direct real-model integration acceptance are complete; this document keeps release, cold-cache and cross-platform limits explicit rather than treating individual green tests as broader acceptance. Hosted CI is recorded separately below.

The reopened H1-5 acceptance used an already installed Windows llama.cpp endpoint, fresh PostgreSQL and supported Runner-only generation settings, without a temporary proxy. Research/order succeeded, checkout/fault failures were retained; a rebuilt standard Runner image also completed research. See the [dated record](../history/h1-live-model-acceptance-2026-11-01.md) and [repeatable setup](local-model.md). This is not an Ollama/vLLM, other-WSL, ordinary bridge-network or model-accuracy claim.

H1, including the H1-5 increment, is on `origin/main`. Hosted CI for `43d9e3e` (run `41227642951`) failed at the frontend production audit: zod 4.4.3 matched GHSA-mrhx-c7xc-m3w5, so `golden-e2e` was skipped. After the zod upgrade, run `41437657048` failed installing `postgresql-client-16` on the moving `ubuntu-latest` image. With every job pinned to `ubuntu-24.04`, run `41445179266` on `b32da25` passed backend, rust, frontend, database-recovery, compose and `golden-e2e`. That covers the fixture/fake-Provider CI gates only, not release, cold-cache, cross-platform or live-model acceptance. No existing-data reset/deletion or public deployment is included.

## Parent validation checkpoints

All counts below are local parent execution, not reviewer-executed tests or hosted CI. Earlier failed attempts remain failed evidence; subsequent passes do not rewrite them.

| Boundary | Latest demonstrated check | Not established by it |
|---|---|---|
| CP/backend | Full suite from `backend/`: **976 passed**, zero skips, fresh PostgreSQL 16 database; retention/state contention included in the parent evidence | Populated historical migration preservation or hosted CI; live-model coverage is separate below |
| Rust | Locked offline fmt/Clippy (`-D warnings`), workspace **15 protocol + 3 Golden + 59 Runner tests**; exit-boundary subset repeated five times | Exhaustive schedules, other platforms, a second scorer |
| P2 security/framing | Red symlink and retained-tail reproductions; no-follow/nonregular descriptor validation; prefix-consumed + observed-Agent-exit barriers, exact sequences, clean/fragment EOF, natural exit, output cap, pre-fallback child-reaping assertion; scoped independent review **OK** | Malicious same-user isolation or universal simultaneous-failure precedence |
| Frontend | Linux **Node 24.15.0**: format/typecheck, **60 tests / 7 files**, build; separate CI-mode Chromium rerun **2 passed** | Re-execution on CI's Node 22, hosted browser deployment or all browsers. First combined invocation timed out in E2E; its owned Vite group was stopped before rerun |
| Standard Runner image | Original standard network build performed online Cargo/pip work; closeout rebuild of unchanged Dockerfile refreshed Rust after P2 repair. `pip check`, imports, CLI and uid/mode smoke passed | Cold-cache/no-cache/fresh-base build or network-reproducibility guarantee. Dependency/base layers reused; historical offline-derived images remain separate |
| Actual fresh named volume | Rebuilt Runner + standard API: three canonical v2 Golden loops; different Runner container, recovery Attempt 2 success, same capability digest, uid 10001, directory 0700/record 0600, record deletion after authoritative ACK | Graceful daemon shutdown; bounded Compose stop can exit 137. Resources were stopped/retained, not deleted |
| Dependency/Compose | Recursive **31-pin** Linux/Python 3.12 closure, standard and recovery Compose configs | Other Python/platform dependency graphs |
| Contracts | Unchanged `make check-contracts` **passed** on committed canonical v2; no generated schema delta | Hosted CI or permission to weaken the gate; old dirty-index exit 2 is historical |
| Earlier v3 calibration/restore | Six fixed cases, **32 actual events**, source/restored strict binding/report match; context known 2 / unknown 4, `provisional_mechanics`, `publishable_accuracy=false`; backup/restore at schema `0009_experiment_agent_id`, 15 foreign keys | New model accuracy, independent labeling or a rerun after every documentation-only change |
| Recorded Preview | Actual canonical v2 baseline/replay recording; scores 0.13333333333333333 → 1.0, six → three world calls, one accepted finalization each, nullable telemetry and lifecycle-bounded ordered timestamps; current Chromium rerun 2 passed | ID-only relabeling, live models, or TypeScript scoring authority |
| Direct real model | Host PostgreSQL 16.15 + Swift/llama.cpp: research/order success, checkout/fault failure; explicit submit/check, contiguous trace and analysis captured. Standard Runner image research success, fresh handoff volume uid 10001/0600 record observed then absent after completion | Stable pass rate, every Scenario solved, analyzer accuracy, raw ACK independently captured, Ollama/vLLM/bridge/other-WSL or cold-cache image proof |

The first closeout smoke used the wrong executable name `agentops-runner` and exited 127 after successful dependency/import checks; the corrected `agentops` invocation passed. This was a probe error, not an image-path repair. Nonfatal bundle-size/buildx warnings remain tooling/optimization debt, not hidden failures or H2 work.

## Repeat without damaging existing resources

1. Record source identity, dirty paths/index state, exact commands/tool versions, image IDs, deadlines and exit codes. Freeze copied bytes and hashes, including untracked files and deletions; a Git HEAD alone does not identify a dirty payload.
2. Use fresh owned PostgreSQL databases and isolated Compose project/volumes/network. Set `DATABASE_URL` and `RETENTION_POSTGRES_URL` only to those resources. Run backend tests from `backend/`; a root invocation can fail subprocess imports or Alembic setup and is not accepted evidence.
3. Run `make fmt` after Rust/frontend edits, Rust locked fmt/Clippy/workspace tests, frontend format/typecheck/unit/build/Recorded E2E, recursive requirements validation and both Compose configs. Use fixture/fake Providers; no GPU/model/key CI dependency.
4. Export protocol schemas and keep `make check-contracts` unchanged. It now passes on the committed canonical-v2 baseline. Review any new delta before delivery; do not stage or revert semantics merely to hide drift. Hosted CI remains a separate check; the latest recorded run is in the current boundary above.
5. Rebuild affected standard Dockerfiles; do not replace them with offline/source-mounted variants without explicitly changing the evidence claim. Repeat Golden baseline/replay/comparison/human activation and controlled Runner kill/recreation. Observe capability digests only, never raw capability values.
6. For cancellation/fencing, require rejected old heartbeat/event/completion without changed facts. For recovery, prove a different container, later Attempt, contiguous sequences, same Run capability and valid-ACK cleanup. Readiness before kill must be an observed marker, not a sleep guess.
7. Independent reviewers inspect immutable exact payloads read-only; execution/hashes supplied by the parent remain attributed to the parent. Scope their verdicts. Parent alone decides acceptance and records residual limits; no component review automatically accepts whole H1.
8. Bound subprocess/process-group lifetime, preserve failed logs, stop only invocation-owned services, retain existing application resources. Record stop-137 separately from child-group cleanup. Use [backup/restore](backup-restore.md) and [calibration](../evaluation-calibration.md) procedures for those gates.

## Claims that remain separate

- **Delivery baseline:** local validation/commit is not publication or hosted CI. Hosted CI passed on `b32da25` (run `41445179266`); that is not a release. The old intentional contract-index delta was committed and the unchanged gate passed; it is not a current blocker.
- **Real model:** H1-5 is locally accepted with direct persisted success/failure traces and analysis. Four sampled host Runs and one image Run are finite integration evidence, not a pass-rate or universal compatibility claim. Order's diagnostic error-word signals remain visible despite authoritative outcome success; accuracy is not established.
- **Research:** calibration mechanics cannot support representative accuracy; H5 is future work.
- **Safety/platform:** trusted single-host operation is not public/multi-user security, malicious Agent sandboxing, Windows-native Runner support, or exhaustive parser/scheduling equivalence.

Current commands and proxy/toolchain advice: [reliability-and-local-validation.md](reliability-and-local-validation.md). Current architecture: [closed-loop architecture](../agentops_closed_loop_architecture.md). Historical results are not relabeled as current evidence.
