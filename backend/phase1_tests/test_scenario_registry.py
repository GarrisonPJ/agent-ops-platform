from __future__ import annotations

import pytest

from app.scenario_registry import UnknownScenarioError, is_registered, list_scenarios, require_scenario
from app.scenarios.checkout import CHECKOUT_SCENARIO_ID


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
        require_scenario("not-a-real-scenario")


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
            "scenario_id": "not-a-real-scenario",
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
            "scenario_id": "multi-step-research",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SCENARIO_PARAMS"


@pytest.mark.asyncio
async def test_create_research_experiment_with_topic(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/experiments",
        json={
            "name": "Research",
            "task": "Answer a research question",
            "scenario_id": "multi-step-research",
            "scenario_params": {"topic": "checkout latency"},
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["scenario_id"] == "multi-step-research"
    assert payload["scenario_params"] == {"topic": "checkout latency"}

