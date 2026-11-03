# H2-1 pi live acceptance — 2026-11-03

This is a dated local acceptance record, not a task tracker or an accuracy
study. Current task status: [phase2-tasks.md](../agents/phase2-tasks.md).
Adapter boundary: [ADR-0009](../adr/0009-agent-registry-trust-boundary.md) H2
amendment. Reading guide: [03-plug-in-your-agent.md](../learn/03-plug-in-your-agent.md).

## Exercised boundary

- Source: `abba9c0` (adapter `1d21203`, discovery amendment `813df76`) with no
  uncommitted product changes. Hosted CI for `abba9c0` passed (run `41451633067`).
- Agents: `reference-openai-compatible.v1` and `pi-coding-agent.v1` from
  `config/agents.external.toml`. pi `1.3.0` (`@earendil-works/pi-coding-agent`)
  on WSL Node `v24.22.1`.
- Model: the already installed Windows-native llama.cpp service from H1-5,
  build `b12018-8956c769e`, alias `swift-1.5-27b-iq3xxs`, context 8192. It was
  started with the operator's own `Manage-Swift.ps1 -Action Start` through WSL
  interop. No model, runtime, network or firewall setting changed.
- Runner settings for both Agents: timeout 60000 ms, one retry, `max_tokens=256`,
  `temperature=0`, `reasoning_effort=none`, `parallel_tool_calls=false`. The
  pi adapter maps the last four into pi's `models.json`. pi's own request
  timeout and three-attempt retry apply instead of the first two.
- Path: fresh invocation-owned PostgreSQL 16.15 (`postgres:16-alpine`, host
  network, loopback listener), Alembic head, host API, host Rust Runner
  (`cargo build --locked`), `python -m app.env_server`, then
  `scripts/local_model_acceptance.py --agent-id ...` for all four canonical
  Environments. Canonical Run budget 60000 ms.
- No world, assertion, success gate, scoring or analyzer rule was changed.

## Outcomes

| Environment | reference | pi |
|---|---|---|
| research | **succeeded**, 0.96202, 3 world calls, 12.3 s | **succeeded**, 1.0, 3 world calls, 11.4 s |
| checkout | **failed**, 0.39042, 6 world calls (4 rejected empty-argument logs calls), 16.7 s | **timed_out**, 0.86667, 2 world calls, 62.0 s |
| fault | **failed**, 0.42272, initial `invoke_service` violates the sequence assertion | **succeeded**, 1.0, retry → degrade → invoke |
| order | **succeeded**, 0.56859, exactly one refund | **succeeded**, 0.66667, same trajectory |

Run IDs: reference research `a8759b54-b757-44fd-91e6-64538dfdf8b8`, checkout
`1967798e-6860-4609-a73e-c33188a09807`, fault `e6e3209f-0b9a-435e-9d6f-b36c4cb21a00`,
order `35b0bc78-9a1a-4a55-b456-7ded919cd777`; pi research
`ab1eb166-b913-43aa-b9fb-5c78fddb9850`, checkout `3b3d88c9-a4c0-4e12-b5d0-202da1932f7c`,
fault `f1111876-ded7-4c18-9cd7-7143b75e5918`, order `6dbe1311-6e05-4f26-a2bc-e59ef1ce14e0`.

The four reference outcomes and scores match the H1-5 record exactly.

## What the comparison shows

- **pi hides invalid tool calls from the Environment.** On checkout the model
  sent `fetch_service_logs` with empty arguments, as it did for the reference
  Agent. pi validates arguments against the MCP `inputSchema` itself and
  returned "Validation failed" to the model about 20 times without calling the
  Environment, until the Run timed out. The reference loop forwarded the same
  calls, so the Environment recorded four rejected world attempts and its step
  budget ended the Run in 16.7 s. A direct reproduction outside the Runner
  showed the same loop. Environment-side capture cannot see these attempts.
- **Context and token figures are unknown for pi.** pi never calls
  `report_usage`. Reference Runs carry Agent-reported totals (3,798–9,808
  tokens), a token penalty in the quality score, and a `budget` dimension on
  checkout. pi Runs have none of these.
- **Quality scores are not comparable across scaffolds.** pi's timed-out
  checkout scores 0.86667, above the reference's failed 0.39042, because its
  failed attempts and token use are invisible. Compare the success gate and
  status, not the quality score.
- **Same model, different first action.** At temperature 0, pi starts fault
  orchestration with `retry_call` and succeeds; the reference loop starts with
  `invoke_service` and fails the existing sequence assertion. One Run per
  cell cannot separate scaffold effect from prompt sensitivity.
- **Analyzer.** Failure types: reference checkout `loop_no_progress`
  (planning, execution, budget dimensions), reference fault `loop_no_progress`,
  pi checkout `loop_no_progress` + `timeout`. Order keeps the known
  `execution` 0.667 diagnostic from injected fault wording despite success,
  for both Agents. This is unchanged analyzer behaviour, not accuracy evidence.

## Deterministic checks for this increment

Parent-local execution, not independent review:

- Backend `phase1_tests`: 1001 passed, 2 PostgreSQL-only modules skipped, on
  Python 3.13 and on Python 3.12 with a PostgreSQL `DATABASE_URL`.
- Rust workspace 15 + 3 + 60 tests, fmt and Clippy `-D warnings`; frontend
  format, typecheck, 60 tests, build; `make check-contracts` unchanged.
- Hosted CI: `1d21203` failed in backend on a Python 3.12 test-only
  `communicate()` issue, fixed in `c58a8c0`; `abba9c0` passed every job
  including `golden-e2e`.

## Limits

One Run per Agent and Environment at temperature 0. This is not a pass rate,
pass^k (H3), analyzer accuracy (H5), or a claim about pi with other models,
other pi versions, or a containerised Runner. The capture directories (Run
JSON, SSE, analysis, diagnostics, logs) stay outside the repository; no API
key, capability token or Provider body is published.
