# System acceptance: current procedure and evidence limits

This is the current H1 acceptance entry, not the 2026-10-15 acceptance of an older implementation. That record is archived in [history](../history/system-acceptance-2026-10-15.md). Executable status lives only in [phase2-tasks.md](../agents/phase2-tasks.md); this document describes checks and their authority.

## Current boundary (2026-10-31)

H1 engineering implementation is integrated. The two Runner P2 repairs have scoped independent review, and parent affected regressions, exact-payload checks and the standard-image replacement run below passed. Local option-1 engineering closeout is complete; this document keeps release, hosted-CI, cold-cache, cross-platform and live-model limits explicit rather than treating individual green tests as broader acceptance.

The operator **explicitly deferred real-local-model trace/analysis acceptance** and assigned Windows model installation elsewhere. No Ollama/vLLM inference, GPU utilization, other-WSL/container-to-Windows connectivity, or successful model task is claimed. Fake-Provider reference-Agent tests are not substitutes. See the [model handoff](local-model.md).

No staging, commit, push, deployment, H2, existing-data reset/deletion, or public access is part of this local engineering closeout.

## Parent validation checkpoints

All counts below are local parent execution, not reviewer-executed tests or hosted CI. Earlier failed attempts remain failed evidence; subsequent passes do not rewrite them.

| Boundary | Latest demonstrated check | Not established by it |
|---|---|---|
| CP/backend | Full suite from `backend/`: **931 passed**, zero skips, fresh PostgreSQL 16 database; retention/state contention included | Populated historical migration preservation, hosted CI, live models |
| Rust | Locked offline fmt/Clippy (`-D warnings`), workspace **15 protocol + 3 Golden + 59 Runner tests**; exit-boundary subset repeated five times | Exhaustive schedules, other platforms, a second scorer |
| P2 security/framing | Red symlink and retained-tail reproductions; no-follow/nonregular descriptor validation; prefix-consumed + observed-Agent-exit barriers, exact sequences, clean/fragment EOF, natural exit, output cap, pre-fallback child-reaping assertion; scoped independent review **OK** | Malicious same-user isolation or universal simultaneous-failure precedence |
| Frontend | Linux **Node 24.15.0**: format/typecheck, **60 tests / 7 files**, build; separate CI-mode Chromium rerun **2 passed** | Re-execution on CI's Node 22, hosted browser deployment or all browsers. First combined invocation timed out in E2E; its owned Vite group was stopped before rerun |
| Standard Runner image | Original standard network build performed online Cargo/pip work; closeout rebuild of unchanged Dockerfile refreshed Rust after P2 repair. `pip check`, imports, CLI and uid/mode smoke passed | Cold-cache/no-cache/fresh-base build or network-reproducibility guarantee. Dependency/base layers reused; historical offline-derived images remain separate |
| Actual fresh named volume | Rebuilt Runner + standard API: three canonical v2 Golden loops; different Runner container, recovery Attempt 2 success, same capability digest, uid 10001, directory 0700/record 0600, record deletion after authoritative ACK | Graceful daemon shutdown; bounded Compose stop can exit 137. Resources were stopped/retained, not deleted |
| Dependency/Compose | Recursive **31-pin** Linux/Python 3.12 closure, standard and recovery Compose configs | Other Python/platform dependency graphs |
| Contracts | Current generated schemas exactly match Pydantic bytes; all JSON parses; reviewed schema delta is only canonical checkout default `.v1`→`.v2` | A green dirty-index `make check-contracts`: it still exits **2** until an authorized delivery baseline contains that intentional delta |
| Earlier v3 calibration/restore | Six fixed cases, **32 actual events**, source/restored strict binding/report match; context known 2 / unknown 4, `provisional_mechanics`, `publishable_accuracy=false`; backup/restore at schema `0009_experiment_agent_id`, 15 foreign keys | New model accuracy, independent labeling or a rerun after every documentation-only change |
| Recorded Preview | Actual canonical v2 baseline/replay recording; scores 0.13333333333333333 → 1.0, six → three world calls, one accepted finalization each, nullable telemetry and lifecycle-bounded ordered timestamps | ID-only relabeling, live models, or TypeScript scoring authority |

The first closeout smoke used the wrong executable name `agentops-runner` and exited 127 after successful dependency/import checks; the corrected `agentops` invocation passed. This was a probe error, not an image-path repair. Nonfatal bundle-size/buildx warnings remain tooling/optimization debt, not hidden failures or H2 work.

## Repeat without damaging existing resources

1. Record source identity, dirty paths/index state, exact commands/tool versions, image IDs, deadlines and exit codes. Freeze copied bytes and hashes, including untracked files and deletions; a Git HEAD alone does not identify a dirty payload.
2. Use fresh owned PostgreSQL databases and isolated Compose project/volumes/network. Set `DATABASE_URL` and `RETENTION_POSTGRES_URL` only to those resources. Run backend tests from `backend/`; a root invocation can fail subprocess imports or Alembic setup and is not accepted evidence.
3. Run `make fmt` after Rust/frontend edits, Rust locked fmt/Clippy/workspace tests, frontend format/typecheck/unit/build/Recorded E2E, recursive requirements validation and both Compose configs. Use fixture/fake Providers; no GPU/model/key CI dependency.
4. Export protocol schemas. Compare generated bytes to current Pydantic models and inspect the approved default delta. Keep `make check-contracts` unchanged and disclose its dirty-index result. On an authorized clean delivery baseline, rerun that gate and hosted CI; do not stage or revert semantics merely to produce green output.
5. Rebuild affected standard Dockerfiles; do not replace them with offline/source-mounted variants without explicitly changing the evidence claim. Repeat Golden baseline/replay/comparison/human activation and controlled Runner kill/recreation. Observe capability digests only, never raw capability values.
6. For cancellation/fencing, require rejected old heartbeat/event/completion without changed facts. For recovery, prove a different container, later Attempt, contiguous sequences, same Run capability and valid-ACK cleanup. Readiness before kill must be an observed marker, not a sleep guess.
7. Independent reviewers inspect immutable exact payloads read-only; execution/hashes supplied by the parent remain attributed to the parent. Scope their verdicts. Parent alone decides acceptance and records residual limits; no component review automatically accepts whole H1.
8. Bound subprocess/process-group lifetime, preserve failed logs, stop only invocation-owned services, retain existing application resources. Record stop-137 separately from child-group cleanup. Use [backup/restore](backup-restore.md) and [calibration](../evaluation-calibration.md) procedures for those gates.

## Claims that remain separate

- **Delivery baseline:** uncommitted payload validation is not commit/publication/hosted CI. The intentional contract-index delta is disclosed, not silently waived.
- **Real model:** explicitly deferred, not passed. Reopen H1-5 with a supplied approved endpoint and actual persisted trace/analysis.
- **Research:** calibration mechanics cannot support representative accuracy; H5 is future work.
- **Safety/platform:** trusted single-host operation is not public/multi-user security, malicious Agent sandboxing, Windows-native Runner support, or exhaustive parser/scheduling equivalence.

Current commands and proxy/toolchain advice: [reliability-and-local-validation.md](reliability-and-local-validation.md). Current architecture: [closed-loop architecture](../agentops_closed_loop_architecture.md). Historical results are not relabeled as current evidence.
