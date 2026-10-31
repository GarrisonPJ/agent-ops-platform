# Adding a built-in Environment

This guide describes the current canonical split, following ADR-0006/0007/0008/0012.
Protocol v1 retains the field name `scenario_id`; prose calls the world an Environment.
Adding another world is separate scoped work, not implicit authorization to expand H1 or H2.

## 1. Version and register the world

- Choose `<name>.v<N>`. Changed semantics require a new identity, not in-place changes,
  aliases, migration, historical rescoring, or implicit replay upgrades.
- Only Runs with the same Environment identity are score-comparable.
- Current built-ins are checkout/research/fault v2 and `order-support.v1`.
  The three retired v1 IDs fail `SCENARIO_NOT_REGISTERED`.
- Register explicitly in `backend/app/scenarios/__init__.py`; there is no dynamic
  import or arbitrary-command API.

`register_scenario(metadata, handler, environment_factory)` still has three arguments.
Metadata owns declared params, tools, default task, assertions and combination.
The retained `ScenarioHandler` provides candidate policy patches and legacy direct
execution regressions. Its `run_fixture`/`run_provider` are not production dispatch.
Do not delete those regressions or use their name-only Provider adapter for stateful tools.

## 2. Implement the Environment interface

Read `backend/app/environment.py`, `scenario_registry.create_environment()` and
`scenarios/order_support.py`.

- Factory receives only a fresh normalized `dict[str, str]` of declared params.
- `reset(seed)` rebuilds instance-local state/history. Same seed and actions reproduce
  observations; no world state is cached on a registry entry or handler.
- `tool_definitions()` exposes only this world's tools; returned data must not mutate
  another instance's definitions.
- `call(name, arguments)` validates strictly and rejects atomically. World rules depend
  on seed/state/actions, not Policy, execution mode, Run identity or Agent claims.
- `check()` returns immutable bounded trajectory/outcome evidence, not score or status.
- `fault_schedule` declares deterministic world faults; it is not Agent-controlled.

Production `app.env_server` owns MCP auth, budgets, source-separated call capture and
reserved `report_usage`/`submit_result`. Do not call `emit_step()` from an independent
Agent or fabricate world facts in a diagnostic adapter. Refused world attempts remain
facts; reserved controls are excluded from world step-count and tool assertions.

## 3. Declare assertions and final state

Use `backend/app/scenario_assertions.py`:

| Assertion | Meaning |
|---|---|
| `tool-used` | A named accepted tool appears |
| `tool-args-match` | Bounded projected arguments match |
| `tool-sequence` | Required named tools occur in order |
| `step-count` | All world attempts, including refusals, satisfy bounds |
| `equals` / `contains` / `json-match` | Trusted final observation satisfies outcome |

Thresholds and weights remain authoritative. `ALL` uses minimum, `ANY` maximum,
`WEIGHTED` weighted mean; do not replace these with a generic all-passed check.
CP success requires registered aggregate `>= 1.0` plus valid submission/check/current
Attempt evidence and no overriding execution failure. Quality score and analyzer
classifications are separate from that gate.

For stateful worlds, `check().final_observation` is a canonical immutable snapshot,
at most 4,000 characters; reject oversized snapshots rather than truncate JSON.
The Environment emits it in the accepted submit control's `final_check` tuple with
its own `scenario_id`. CP validates raw provenance/Attempt before normalization and
requires submission/check/final-check in the same event. Do not trust Agent arguments,
expected results, score, evaluations, aggregate or status in that tuple.

`order-support.v1` illustrates why tool calls are not outcomes: the first refund writes
one ledger receipt but returns timeout-after-commit. Retry returns the same receipt.
Its `ALL` suite requires the sequence, at most six world attempts and exactly one full
refund in the final ledger. Two refund calls are not two refunds. This changes none
of the other suites' combinations and introduces no second scorer.

## 4. Keep Agent decisions separate

`fixture_agent.py` is the deterministic MCP client for baseline/replay; the reference
Agent sends the same Environment tool definitions to an explicitly configured local
OpenAI-compatible Provider. Neither imports world/scoring/CP internals nor emits
trusted RunEvents. Never silently run fixture decisions in Provider mode.

A new fixture script needs its own reviewed Agent-side decision flow. Candidate policy
patches must remain typed, bounded and within `ALLOWED_POLICY_TOOLS`; instruction-only
patches need not expand the allowlist. Do not add external adapter support as part of
this guide; that is H2.

## 5. Wire contracts and workbench

1. API validates registration and declared params when creating experiments and Runs.
   `_evaluation_spec()` persists immutable identity/params; Runner supplies the spec
   independently to Environment and registered Agent.
2. CP calls `candidate_policy_for_scenario()` rather than embedding world-specific
   policy logic in `phase1_service.py`.
3. Add a protocol fixture and tests; run `make contracts`. New world tools require a
   bounded durable tool/argument projection as well as any policy allowlist change.
4. `GET /api/scenarios` exposes metadata. Update recorded workbench fixtures/handlers;
   do not invent aliases in frontend fallback logic.
5. Update `CONTEXT.md`, the scoped task and Chinese learning chapter. A semantic
   boundary change needs an ADR before implementation.

## 6. Verification checklist

- Registry: identity, duplicate, unknown, declared/required params, retired-ID rejection.
- World: reset determinism, fresh-instance isolation, atomic argument rejection,
  deterministic fault progression, immutable check and tamper-sensitive final ledger.
- Environment/MCP: auth/Origin, bounded spec/readiness, controls, refused attempts,
  repeated submit/frozen world and real final-check capture.
- Agent: real MCP calls, baseline/replay, no trusted stdout, strict tool/Provider
  validation, timeout/retry and bounded output, local fake Provider only.
- CP: normalize/persist/reconstruct, latest Attempt isolation, assertion combinations,
  scoring/terminal ownership and malformed/contradictory evidence refusal.
- Full Golden: baseline → analysis → candidate → replay → comparison → human activate,
  using production Rust dispatch/API and a new isolated PostgreSQL database.
- Preserve legacy tests; fixture/fake Provider CI must not require GPU, network keys
  or a real model. Run contracts, `make fmt`, backend/Rust/frontend gates and diff checks.

Component tests, host-process tests, standard image installation, container replacement
and live-model acceptance are different claims. Record exactly which boundary passed;
never treat a Dockerfile or Compose config check as runtime acceptance.
