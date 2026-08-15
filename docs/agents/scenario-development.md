# Adding a built-in Scenario

This guide describes how to register a new allowlisted Scenario in Phase 1.4+. It follows [ADR-0006](../adr/0006-scenario-registry-boundary.md): explicit Python registration, no arbitrary code execution, and Runner passthrough via `EvaluationSpec` only.

## 1. Implement the Scenario module

Create `backend/app/scenarios/<slug>.py` with:

1. **Metadata** — stable id (pattern `^[a-z0-9][a-z0-9-]{1,62}$`), human name, description, default task, tool allowlist, and optional `ScenarioParamDef` entries.
2. **Fixture path** — deterministic baseline (should fail analyzably) and replay (should succeed in fewer steps when `policy` is present on the spec).
3. **Provider path** — checkout uses the OpenAI-compatible boundary; other Scenarios may delegate to fixture until a narrow provider surface exists.
4. **`candidate_policy_patch()`** — typed `PolicyPatch` using only tools from the union in `ALLOWED_POLICY_TOOLS` (`phase1_schemas.py`).

Reuse `emit_step()` from `scenarios/_helpers.py` so events match the existing analyzer and scoring pipeline.

Register the Scenario at module import time:

```python
register_scenario(ScenarioMetadata(...), MyScenario())
```

Import the module from `backend/app/scenarios/__init__.py` so registration runs when the API starts.

## 2. Wire the Control Plane

- **Experiment creation** — `create_experiment()` already validates `scenario_id` and `scenario_params` through the registry.
- **EvaluationSpec** — `_evaluation_spec()` copies persisted `scenario_params` onto each Run.
- **Policy generation** — `_create_candidate()` calls `candidate_policy_for_scenario()`; no checkout-specific patch should remain in `phase1_service.py`.

No database migration is required unless you add new Experiment fields. Params are stored on `experiments.scenario_params` (revision `0007`).

## 3. Update contracts

1. Add a golden fixture under `contracts/v1/fixtures/evaluation-spec.<scenario-id>.json`.
2. Extend `backend/phase1_tests/test_protocol_contract.py` to load it.
3. Run `PYTHONPATH=backend uv run --project backend python backend/scripts/export_protocol_schema.py`.
4. If new policy tools are introduced, extend `ALLOWED_POLICY_TOOLS` in `phase1_schemas.py` and `ALLOWED_TOOLS` in `runner/crates/agentops-protocol/src/lib.rs`.

## 4. Expose the Scenario in the workbench

1. `GET /api/scenarios` lists registry metadata automatically.
2. Add RTK Query coverage in `frontend/src/services/experimentsApi.ts` if not already present.
3. Ensure `NewExperimentPage` renders the registry list and sends `scenario_id` (and params when required).
4. Update recorded handlers under `frontend/src/services/recorded/` when `VITE_MOCK_API=true`.

## 5. Test checklist

- Registry unit tests: registered lookup, unknown id rejection, param validation.
- API tests: `POST /api/experiments` with valid/invalid `scenario_id` and `scenario_params`.
- Full baseline → analysis → replay loop for at least one new Scenario in `phase1_tests/`.
- `make test` / CI matrix: backend, rust, frontend, compose, golden-e2e.

## 6. Document the change

- Add the Scenario row to **Built-in scenarios** in `CONTEXT.md`.
- Keep `ROADMAP.md` Phase 1.4 acceptance honest — mark Complete only when CI evidences the new Scenario in the closed loop.
