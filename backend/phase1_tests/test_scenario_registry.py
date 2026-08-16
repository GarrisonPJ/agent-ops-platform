from __future__ import annotations

import pytest

from app.phase1_models import Experiment
from app.scenario_registry import UnknownScenarioError, is_registered, list_scenarios, require_scenario
from app.scenarios.checkout import CHECKOUT_SCENARIO_ID


AUTH = {"Authorization": "Bearer test-runner-token"}


def test_checkout_scenario_is_registered() -> None:
    assert is_registered(CHECKOUT_SCENARIO_ID)
    entry = require_scenario(CHECKOUT_SCENARIO_ID)
    assert entry.metadata.name == "Checkout API Latency"
    assert "check_service_health" in entry.metadata.allowed_tools


def test_builtin_scenarios_are_registered() -> None:
    from app.scenarios.multi_step_research import SCENARIO_ID as RESEARCH_ID
    from app.scenarios.api_fault_orchestration import SCENARIO_ID as FAULT_ID

    assert is_registered(RESEARCH_ID)
    assert is_registered(FAULT_ID)
    assert len(list_scenarios()) >= 3


def test_unknown_scenario_raises() -> None:
    with pytest.raises(UnknownScenarioError):
        require_scenario("not-a-real-scenario.v1")


@pytest.mark.asyncio
async def test_list_scenarios_endpoint(api) -> None:
    client, _ = api
    response = await client.get("/api/scenarios")
    assert response.status_code == 200
    scenarios = response.json()
    assert any(item["id"] == CHECKOUT_SCENARIO_ID for item in scenarios)
    checkout = next(item for item in scenarios if item["id"] == CHECKOUT_SCENARIO_ID)
    assert checkout["default_task"]
    assert checkout["allowed_tools"]


@pytest.mark.asyncio
async def test_create_experiment_rejects_unregistered_scenario(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Invalid scenario",
            "task": "Should fail",
            "scenario_id": "not-a-real-scenario.v1",
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "SCENARIO_NOT_REGISTERED"


@pytest.mark.asyncio
async def test_create_experiment_rejects_unknown_scenario_params(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Invalid params",
            "task": "Should fail",
            "scenario_id": CHECKOUT_SCENARIO_ID,
            "scenario_params": {"unknown-param": "value"},
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "INVALID_SCENARIO_PARAMS"


@pytest.mark.asyncio
async def test_evaluation_spec_includes_scenario_params(api) -> None:
    client, _ = api
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Params passthrough",
                "task": "Investigate checkout API latency",
                "scenario_id": CHECKOUT_SCENARIO_ID,
            },
        )
    ).json()
    run = (
        await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    ).json()
    assert run["evaluation_spec"]["scenario_params"] == {}


@pytest.mark.asyncio
async def test_create_research_experiment_requires_topic_param(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Research",
            "task": "Answer a research question",
            "scenario_id": "multi-step-research.v1",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SCENARIO_PARAMS"


@pytest.mark.asyncio
async def test_create_experiment_rejects_non_versioned_scenario_id(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Legacy id",
            "task": "Should fail validation",
            "scenario_id": "checkout-api-latency",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_research_experiment_with_topic(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Research",
            "task": "Answer a research question",
            "scenario_id": "multi-step-research.v1",
            "scenario_params": {"topic": "checkout latency"},
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["scenario_id"] == "multi-step-research.v1"
    assert payload["scenario_params"] == {"topic": "checkout latency"}


@pytest.mark.asyncio
async def test_create_experiment_rejects_scenario_param_overflow(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Too many params",
            "task": "Should fail",
            "scenario_id": CHECKOUT_SCENARIO_ID,
            "scenario_params": {f"param-{index}": "value" for index in range(21)},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_experiment_rejects_oversized_param_value(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Oversized param",
            "task": "Should fail",
            "scenario_id": "multi-step-research.v1",
            "scenario_params": {"topic": "x" * 201},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_run_rejects_unregistered_scenario(api) -> None:
    client, factory = api
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Stale scenario",
                "task": "Investigate checkout API latency",
                "scenario_id": CHECKOUT_SCENARIO_ID,
            },
        )
    ).json()
    async with factory() as db:
        stored = await db.get(Experiment, experiment["id"])
        assert stored is not None
        stored.scenario_id = "not-a-real-scenario.v1"
        await db.commit()

    response = await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    assert response.status_code == 400
    assert response.json()["code"] == "SCENARIO_NOT_REGISTERED"


@pytest.mark.asyncio
async def test_claimed_run_includes_scenario_params_in_evaluation_spec(api) -> None:
    client, _ = api
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Research",
                "task": "Answer a research question",
                "scenario_id": "multi-step-research.v1",
                "scenario_params": {"topic": "checkout latency"},
            },
        )
    ).json()
    run = (
        await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    ).json()
    claim = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert claim.status_code == 200
    spec = claim.json()["run"]["evaluation_spec"]
    assert spec["scenario_id"] == "multi-step-research.v1"
    assert spec["scenario_params"] == {"topic": "checkout latency"}
    assert spec["run_id"] == run["id"]

