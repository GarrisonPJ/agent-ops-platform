# Adding a built-in Scenario

> **Status (H1-1 extraction):** Registration now includes a per-Run **Environment** factory alongside the retained Phase 1 `ScenarioHandler`. The isolated Environment interface is `reset`, `tool_definitions`, `call`, `check`, and declared `fault_schedule` ([ADR-0007](../adr/0007-environment-agent-split.md)). Existing fixture/provider execution still uses `run_fixture` / `run_provider`; it has not switched to the new world. Independent agents and MCP serving by `app.env_server` remain later H1 work ([ADR-0008](../adr/0008-environment-mcp-server-and-env-side-capture.md)). Promotion must resolve the existing fixture/provider world differences under the versioning rule before replacing those paths.

This guide describes the current allowlisted registration seam and retained Phase 1 execution paths. It follows [ADR-0006](../adr/0006-scenario-registry-boundary.md): explicit Python registration, no arbitrary code execution, and Runner passthrough via `EvaluationSpec` only.

## Prerequisites

- Choose a **versioned** id: `<name>.v<N>` (for example `inventory-restock.v1`). Semantic changes require a new version; never mutate an existing id in place.
- Only Runs that share the same `scenario_id` are score-comparable.
- Study the three shipped examples under `backend/app/scenarios/`: checkout (Golden reference), multi-step research, and API fault orchestration.

## 1. Implement the Scenario module

Create `backend/app/scenarios/<slug>.py` with:

1. **Metadata** — versioned id, human name, description, default task, tool allowlist, optional `ScenarioParamDef` entries, and terminal `ScenarioAssertion` entries with an `AssertionCombination` mode.
2. **Fixture path** — deterministic baseline (should fail analyzably) and replay (should succeed in fewer steps when `policy` is present on the spec).
3. **Provider path** — expose only this Scenario's explicit tool definitions and deterministic tool executor through the shared bounded `run_provider_agent()` client. Checkout, multi-step research, and API fault orchestration are the reference integrations. Do not silently delegate `provider` mode to the fixture path.
4. **`candidate_policy_patch()`** — typed `PolicyPatch` using only tools from the union in `ALLOWED_POLICY_TOOLS` (`phase1_schemas.py`).
5. **Environment factory** — a constructor or callable with signature `(params: dict[str, str]) -> Environment`, implementing `app.environment.Environment`. Keep mutable state and history on each new instance, not on the registered handler. World observations must depend on seed and actions, not policy or execution mode.

Reuse `emit_step()` from `scenarios/_helpers.py` so events match the existing analyzer and scoring pipeline.

### Assertion vocabulary

Declare terminal scoring in metadata using `backend/app/scenario_assertions.py`:

| Type | Purpose |
|---|---|
| `tool-used` | A named tool appears in the trajectory. |
| `tool-args-match` | A tool call matches expected arguments. |
| `tool-sequence` | Tool names appear in the required order. |
| `step-count` | Step count respects `min_steps` / `max_steps`. |
| `equals` / `contains` / `json-match` | Final observation matches expected text or JSON. |

Each assertion supports `weight` and `threshold`. Combine assertions with `AssertionCombination.ALL`, `WEIGHTED`, or `ANY`. `compute_score()` uses the weighted aggregate as `success_reward` when assertions are present.

Example excerpt:

```python
from app.scenario_assertions import AssertionCombination, ScenarioAssertion

CHECKOUT_ASSERTIONS = (
    ScenarioAssertion(type="tool-sequence", sequence=("check_service_health", "query_service_metrics", "fetch_service_logs"), weight=2.0),
    ScenarioAssertion(type="step-count", max_steps=4, weight=1.0),
)

register_scenario(
    ScenarioMetadata(
        id="checkout-api-latency.v1",
        ...,
        assertions=CHECKOUT_ASSERTIONS,
        assertion_combination=AssertionCombination.WEIGHTED,
    ),
    CheckoutScenario(),
    CheckoutEnvironment,
)
```

The three arguments are `register_scenario(metadata, handler, environment_factory)`.
Pass the Environment constructor/callable, not a shared Environment instance.
`create_environment(spec)` validates registration and the declared params, passes
only a **fresh normalized** `dict[str, str]` to that factory, and then calls
`environment.reset(spec.seed)`. The factory does not receive the full spec,
policy, execution mode, or Run identity. `reset` must rebuild all per-Run state
and history; the same seed and action sequence must reproduce observations.
Use `create_environment(spec)` for the isolated world rather than caching a world
on the registry or bypassing its validation/reset. This does not reroute the
retained `run_fixture` / `run_provider` paths.

Register at module import time and import the module from `backend/app/scenarios/__init__.py`.

## 2. Wire the Control Plane

- **Experiment creation** — `create_experiment()` calls `ensure_registered_scenario()` for id, params, and version pattern validation.
- **Run creation** — `create_baseline_run()` and `replay_policy()` re-validate the Experiment's Scenario before enqueueing a RunnerJob.
- **EvaluationSpec** — `_evaluation_spec()` copies persisted `scenario_params` onto each Run; the Runner passes the full JSON spec to `demo_agent` on stdin.
- **Policy generation** — `_create_candidate()` calls `candidate_policy_for_scenario()`; no Scenario-specific patch should remain in `phase1_service.py`.

Params are stored on `experiments.scenario_params` (Alembic revision `0007`). No new migration is required unless you add Experiment fields.

## 3. Update contracts

1. Add a golden fixture under `contracts/v1/fixtures/evaluation-spec.<scenario-id>.json`.
2. Extend `backend/phase1_tests/test_protocol_contract.py` to load it.
3. Run `make contracts` from the repo root.
4. If new policy tools are introduced, extend `ALLOWED_POLICY_TOOLS` in `phase1_schemas.py` and `ALLOWED_TOOLS` in `runner/crates/agentops-protocol/src/lib.rs`.

## 4. Expose the Scenario in the workbench

1. `GET /api/scenarios` lists registry metadata automatically.
2. `NewExperimentPage` renders the registry list, versioned ids, and required `scenario_params` fields.
3. Update recorded handlers under `frontend/src/services/recorded/` when `VITE_MOCK_API=true`.

## 5. Test checklist

- Registry unit tests: registered lookup, unknown id rejection, param validation, non-versioned id rejection.
- Environment tests in `phase1_tests/test_environment.py`: same-seed reset/call/check determinism, fresh-instance isolation, and valid/invalid tool arguments. Preserve the legacy fixture/provider contracts until their promotion is approved.
- Assertion unit tests in `phase1_tests/test_scenario_assertions.py` for new assertion combinations if needed.
- Agent tests in `phase1_tests/test_demo_agent.py` for baseline/replay fixture semantics.
- Provider tests in `phase1_tests/test_provider_agent.py` with the local fake compatible server: success sequence, persisted event fields, unsupported-tool rejection, timeout/retry, and safe errors. Never call an external API in CI.
- API tests: `POST /api/experiments` and `POST /api/experiments/{id}/runs` with valid/invalid `scenario_id` and `scenario_params`.
- Extend `scripts/golden_e2e.py` (or add a scenario entry to `GOLDEN_SCENARIOS`) so CI exercises the full closed loop without external APIs.
- `make test` / CI matrix: backend, rust, frontend, compose, golden-e2e.

## 6. Document the change

- Add the Scenario row to **Built-in scenarios** in `CONTEXT.md`.
- Keep `ROADMAP.md` honest: record a new Scenario as a bounded follow-up; Phase 2 — Harness milestones and acceptance live in [ROADMAP.md](../../ROADMAP.md) (do not redefine them here).

## Toy Scenario walkthrough

To sanity-check the flow before shipping a fourth production Scenario:

1. Copy `multi_step_research.py` to `toy_echo.py` with id `toy-echo.v1`, one tool `echo_message`, and a baseline that calls it six times.
2. Add one `tool-sequence` assertion and a `step-count` ceiling for replay.
3. Implement `ToyEchoEnvironment(params)` and register with `register_scenario(metadata, ToyEchoScenario(), ToyEchoEnvironment)`; import the module, add a contract fixture, and a single `test_demo_agent.py` case.
4. Confirm `POST /api/experiments` rejects missing params and `GET /api/scenarios` lists the toy entry.
5. Delete or leave unimported until the Scenario is product-ready — the registry rejects duplicate ids, so do not register throwaway Scenarios in `__init__.py` for long-lived branches.
