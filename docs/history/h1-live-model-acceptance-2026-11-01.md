# H1-5 live-model acceptance — 2026-11-01 local / 2026-10-31 UTC

This is a dated local acceptance record, not a second task tracker or an accuracy
study. Current task status: [phase2-tasks.md](../agents/phase2-tasks.md). Repeatable
commands: [local-model.md](../operations/local-model.md).

## Exercised boundary

- Integration baseline: `ec5e27764d55f5b271f543c07c288cfe793e4c02` plus the
  H1-5 Provider-option/capture-command changes delivered with this record.
- Windows-native official llama.cpp `b12018`, commit
  `8956c769e8ababd9fbd89c81764f241d5f515d68`; API response fingerprint
  `b12018-8956c769e`; model alias `swift-1.5-27b-iq3xxs`.
- Installed GGUF: `Swift-1.5-Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf`, SHA-256
  `86969b8bde72e602bfb42deb83eb8bb3706c8f14250641f6444dd2355f934ac2`.
  Hash is installed provenance, not a new full-file hash in this run.
- RTX 5070 Ti Laptop GPU, approximately 12 GB VRAM; installed context 8192,
  parallel 1, Q8 K/V cache. Earlier service check reported 11,689 MiB used and
  only 256 MiB free; that is a point-in-time observation, not a stability guarantee.
- Fresh PostgreSQL **16.15**, Alembic head `0009_experiment_agent_id`, separate
  owned `h1_live` and `h1_tests` databases. WSL-host CP, registered
  `reference-openai-compatible.v1`, direct loopback Windows Provider.
- Settings: request timeout 60000 ms, one additional retry, `max_tokens=256`,
  `temperature=0`, `reasoning_effort=none`, `parallel_tool_calls=false`.
  Canonical overall Run budget remained 60000 ms. No proxy or response rewriting.
- No world, assertion, success gate, scoring, Agent selection or final-check rule
  was changed to obtain success. This task does not start H2.

## Actual real-model outcomes

| Environment / Run | CP outcome | Evidence |
|---|---|---|
| Research `151649da-cdcd-418a-98fc-4aadfd324c77` | **succeeded**, 0.96202 | search → fetch → answer → explicit submit; aggregate 1.0; 10 events; 11,893 ms wall-clock; 3,798 cumulative Agent-reported tokens; analyzer failure_rate 0 |
| Checkout `e230901e-021d-41b2-a7d2-12cd01388f6e` | **failed**, 0.3904166666666667 | health/metrics valid, then four invalid empty-argument log calls; no accepted submission; rejected attempts retained |
| Fault `c725cc73-f90e-4242-bbdf-f863152d5a36` | **failed**, 0.42272 | initial invoke → retry → degrade → invoke → explicit submit; initial probe violates existing sequence assertion; finalization is not sufficient for success |
| Order `a814da32-3fcc-4c55-ada8-9066f68dd15b` | **succeeded**, 0.5685866666666668 | inspect → authorize → failed shipping → uncertain refund → retry refund → inspect → explicit submit; final ledger proves exactly one refund; all seven assertions pass |

Order success has a lower quality score and analyzer execution failure_rate
`0.6666666666666666`: injected shipping/refund error words and final ledger text
remain diagnostic signals. They do not overrule authoritative outcome assertions.
This is an observed analyzer limitation, not evidence of calibrated accuracy; no
rules or labels were changed to hide it. Similarly, aggregate success is not a
claim that a Run contains no intermediate faults.

Earlier SQLite/proxy runs remain separate failed/successful evidence. Checkout
original and expanded `oneOf` probes both returned empty arguments; that does not
isolate a model vs template vs schema-converter root cause. A scripted explicit
argument prompt also failed. No tool arguments were repaired.

## Standard image verification

Rebuilt unchanged `runner/Dockerfile` with the changed Python client:

- Image: `agentops-h1-isolated-runner:direct-model-closeout`.
- ID: `sha256:fa0816f4688b6525035de8f371e0d5a53e0df3e901e37e580448abfaa5aa4334`.
- Existing Rust/base/dependency layers reused; **not cold-cache/fresh-base**.
- `pip check` passed. A fresh named handoff volume and Linux host-network Runner
  reached the same host CP/PostgreSQL and Windows loopback service.
- Research Run `f79782bb-1758-4e01-8ea0-c9696c8f7063` **succeeded**, score 0.96202,
  10 events, 10,981 ms wall-clock, explicit submission and aggregate 1.0.
- Observed its capability record metadata during execution: uid 10001, mode 0600;
  after completion no `.token` remained. API completion returned HTTP 200. Runner
  source validates the bounded wrapper before cleanup; raw ACK body was not
  independently captured in this live run. Existing deterministic ACK regression
  tests remain separate proof of malformed/identity-mismatched wrapper rejection.

The first image command used unsupported legacy-builder `--progress` and exited
125 before building. Retried without that flag and built successfully; preserve
this as a tooling failure, not a hidden build success. A first owned PostgreSQL
startup failed because its Unix socket path exceeded 107 bytes; retry used a short
private socket directory. No existing databases/volumes were reset or deleted.

## Deterministic delivery checks

All results here are parent-local execution, not independent review or hosted CI:

- Backend from `backend/`, fresh owned PostgreSQL URL for retention/contention:
  **976 passed, zero skipped** (includes 45 new offline Provider/capture tests).
- Rust locked offline workspace: **15 protocol + 3 Golden + 59 Runner tests**,
  fmt and Clippy `-D warnings` passed. No Rust source changed in this increment.
- Frontend Linux Node **24.15.0**: format/typecheck, **60 tests / 7 files**, build;
  CI-mode Recorded Chromium E2E **2 passed**. Node 22 was not rerun locally.
- Fresh PostgreSQL fixture Golden: all three canonical baseline-failed →
  replay-succeeded → comparison → explicit activation loops passed.
- Unchanged `make check-contracts` passed on the committed canonical-v2 baseline;
  no generated schema changes. The old dirty-index exit 2 is historical, not a
  current blocker. Recursive Runner closure **31 pins** and both Compose configs
  passed validation. Frontend bundle-size and legacy Docker builder warnings remain.
- GitHub Actions query found **zero branch runs** for `pi-h1-integration` at this
  checkpoint. No current hosted-CI pass is claimed; commit is not CI/publication.

## Acceptance conclusion and limits

H1-1..H1-6 now meet the local engineering + real-reference-Agent trace/analysis
boundary. H1 does **not** require every sampled model task to succeed: the two
failures above must stay failed. This is finite acceptance coverage, not a 2/4
pass-rate estimate, stable repeatability, pass^k (H3), analyzer accuracy (H5),
universal Provider support, public-service safety, or native Windows Runner support.

The live captures retain Run JSON, SSE, analysis, operations diagnostics, exact
operator settings without keys, safe logs and a PostgreSQL dump outside the
repository. Raw capability contents, API keys, hidden reasoning and full Provider
bodies are not published. Local artifact locations are not required to understand
this tracked record. Prior independent reviews apply to their frozen H1 payloads,
not to the new Provider-option increment; this increment has local tests and
parent diff review, not a newly claimed independent verdict.
