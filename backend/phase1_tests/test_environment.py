"""Contract tests for the isolated H1-1 Environment surface.

Tests create registered per-Run worlds and exercise reset/call/check directly.
Legacy routes and scoring remain unchanged until a promotion/version decision.
Read test_provider_agent.py for the preserved Phase 1 provider boundary.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

import app.scenarios  # noqa: F401 — register built-in worlds
from app.environment import Environment, InvalidToolArgumentsError, UnknownToolError
from app.phase1_schemas import EvaluationSpec, ExecutionMode
from app.scenario_assertions import evaluate_assertion, evaluate_assertion_suite
from app.scenario_registry import (
    ScenarioParamError,
    UnknownScenarioError,
    assertion_suite_for_scenario,
    create_environment,
    require_scenario,
)


CHECKOUT_ID = "checkout-api-latency.v1"
RESEARCH_ID = "multi-step-research.v1"
FAULT_ID = "api-fault-orchestration.v1"
PARAMS = {
    CHECKOUT_ID: {},
    RESEARCH_ID: {"topic": "checkout latency"},
    FAULT_ID: {"service": "payments-api"},
}
ACTIONS = {
    CHECKOUT_ID: [
        ("check_service_health", {"service": "checkout-api"}),
        ("query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
        ("fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
    ],
    RESEARCH_ID: [
        ("search_documents", {"query": "checkout latency"}),
        ("fetch_document", {"document_id": "doc-dependency-latency"}),
        ("submit_answer", {"answer": "Checkout latency is caused by payment-gateway connection pool saturation."}),
    ],
    FAULT_ID: [
        ("retry_call", {"service": "payments-api", "attempts": 2}),
        ("degrade_route", {"service": "payments-api", "mode": "cached-read"}),
        ("invoke_service", {"service": "payments-api"}),
    ],
}
OBSERVATIONS = {
    CHECKOUT_ID: [
        "Service is healthy but checkout latency is elevated.",
        "Payment dependency p95 increased from 110ms to 1.8s.",
        "Requests are delayed by payment-gateway connection pool saturation.",
    ],
    RESEARCH_ID: [
        "Found two relevant documents about dependency latency.",
        "The document explains payment-gateway pool saturation.",
        "Answer accepted with supporting citations.",
    ],
    FAULT_ID: [
        "Retry succeeded after one transient 503 response.",
        "Fallback route returned stale but acceptable responses.",
        "Service responded 200 OK through the degraded route.",
    ],
}
FAULT = "HTTP 503 Service Unavailable: upstream connection pool saturated."
BROAD_LOGS = "The same noisy request samples are returned; the cause is still inconclusive."


def make_spec(scenario_id: str, *, policy: bool = False, **overrides) -> EvaluationSpec:
    values = {
        "run_id": "run-environment",
        "experiment_id": "experiment-environment",
        "scenario_id": scenario_id,
        "task": require_scenario(scenario_id).metadata.default_task,
        "scenario_params": dict(PARAMS[scenario_id]),
        "seed": 123,
        "policy": require_scenario(scenario_id).handler.candidate_policy_patch() if policy else None,
    }
    return EvaluationSpec(**(values | overrides))


def act(environment: Environment, scenario_id: str) -> list[str]:
    return [environment.call(name, dict(args)) for name, args in ACTIONS[scenario_id]]


@pytest.mark.parametrize("scenario_id", PARAMS)
def test_environment_reset_same_seed_reproduces_calls_and_checks(scenario_id: str) -> None:
    spec = make_spec(scenario_id)
    environment = create_environment(spec)
    initial = environment.check()
    observations = act(environment, scenario_id)
    checked = environment.check()
    assert observations == OBSERVATIONS[scenario_id]
    assert checked != initial
    assert all(item.passed for item in checked.trajectory)
    assert checked.outcome == ()  # No new outcome assertions change existing v1 suites.
    assert environment.reset(spec.seed) is None
    assert environment.check() == initial
    assert act(environment, scenario_id) == observations
    assert environment.check() == checked


@pytest.mark.parametrize("scenario_id", PARAMS)
def test_environment_instances_do_not_share_history_or_definitions(scenario_id: str) -> None:
    first = create_environment(make_spec(scenario_id))
    second = create_environment(make_spec(scenario_id, run_id="another-run"))
    assert first is not second
    initial = second.check()
    original_definitions = second.tool_definitions()
    definitions = first.tool_definitions()
    assert [tool["function"]["name"] for tool in definitions] == list(
        require_scenario(scenario_id).metadata.allowed_tools
    )
    definitions[0]["function"]["parameters"]["properties"].clear()
    assert first.tool_definitions() == second.tool_definitions() == original_definitions
    act(first, scenario_id)
    assert second.check() == initial
    assert act(second, scenario_id) == OBSERVATIONS[scenario_id]
    first.reset(123)
    assert all(item.passed for item in second.check().trajectory)


@pytest.mark.parametrize("scenario_id", PARAMS)
@pytest.mark.parametrize("mode", list(ExecutionMode))
def test_environment_world_ignores_policy_mode_and_run_identity(
    scenario_id: str, mode: ExecutionMode
) -> None:
    baseline = create_environment(make_spec(scenario_id))
    replay = create_environment(
        make_spec(scenario_id, policy=True, execution_mode=mode, run_id="replay")
    )
    assert baseline.tool_definitions() == replay.tool_definitions()
    assert baseline.fault_schedule == replay.fault_schedule
    if scenario_id == FAULT_ID:
        for environment in (baseline, replay):
            assert environment.call("invoke_service", {"service": "payments-api"}) == FAULT
    assert act(baseline, scenario_id) == act(replay, scenario_id)
    assert baseline.check() == replay.check()


@pytest.mark.parametrize("scenario_id", PARAMS)
def test_environment_check_reuses_registered_assertions_without_scoring(
    scenario_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("Environment.check must not compute a Run score")

    monkeypatch.setattr("app.scoring.compute_score", forbidden)
    environment = create_environment(make_spec(scenario_id))
    observations = act(environment, scenario_id)
    expected = evaluate_assertion_suite(
        assertion_suite_for_scenario(scenario_id),
        {"steps": [
            {"action": {"name": name, "arguments": args}, "observation": observation}
            for (name, args), observation in zip(ACTIONS[scenario_id], observations)
        ]},
    )
    checked = environment.check()
    assert checked.combination.value == expected["combination"]
    assert [asdict(item) for item in checked.trajectory] == expected["assertions"]
    assert environment.check() == checked  # Read-only; no completion side effects.
    assert set(asdict(checked)) == {"combination", "trajectory", "outcome"}


@pytest.mark.parametrize("scenario_id", PARAMS)
def test_environment_rejects_unknown_tools_and_invalid_arguments_atomically(scenario_id: str) -> None:
    environment = create_environment(make_spec(scenario_id))
    initial = environment.check()
    with pytest.raises(UnknownToolError):
        environment.call("run_shell", {"command": "anything"})
    tool, arguments = ACTIONS[scenario_id][0]
    for invalid in ({}, arguments | {"extra": "invalid"}, {key: None for key in arguments}):
        with pytest.raises(InvalidToolArgumentsError):
            environment.call(tool, invalid)
    assert environment.check() == initial
    assert act(environment, scenario_id) == OBSERVATIONS[scenario_id]


def test_environment_records_caller_owned_arguments_by_value(monkeypatch: pytest.MonkeyPatch) -> None:
    trajectories = []

    def capture(assertion, trajectory):
        trajectories.append(trajectory)
        return evaluate_assertion(assertion, trajectory)

    monkeypatch.setattr("app.scenarios._helpers.evaluate_assertion", capture)
    environment = create_environment(make_spec(CHECKOUT_ID))
    args = {"service": "checkout-api"}
    environment.call("check_service_health", args)
    args["service"] = "changed"
    environment.check()
    assert trajectories[0]["steps"][0]["action"]["arguments"] == {"service": "checkout-api"}


def test_fault_environment_schedule_degradation_reset_and_isolation() -> None:
    first = create_environment(make_spec(FAULT_ID))
    second = create_environment(make_spec(FAULT_ID))
    initial = first.check()
    invoke = {"service": "payments-api"}
    degrade = invoke | {"mode": "cached-read"}
    assert first.fault_schedule[0].tool == "invoke_service"
    assert first.fault_schedule[0].observation == FAULT
    for environment in (first, second):
        assert environment.call("invoke_service", invoke) == FAULT
        assert environment.call("invoke_service", invoke) == FAULT
    first.call("retry_call", invoke | {"attempts": 2})
    assert first.call("invoke_service", invoke) == FAULT
    with pytest.raises(InvalidToolArgumentsError):
        first.call("degrade_route", invoke | {"mode": "unknown"})
    assert first.call("invoke_service", invoke) == FAULT
    assert first.call("degrade_route", degrade) == OBSERVATIONS[FAULT_ID][1]
    assert first.call("invoke_service", invoke) == OBSERVATIONS[FAULT_ID][2]
    assert second.call("invoke_service", invoke) == FAULT
    first.reset(123)
    assert first.check() == initial
    assert first.call("invoke_service", invoke) == FAULT
    first.call("degrade_route", degrade)
    assert first.call("invoke_service", invoke) == OBSERVATIONS[FAULT_ID][2]


@pytest.mark.parametrize("attempts", [True, 2.0, "2", 1, 3])
def test_fault_environment_rejects_attempts_outside_existing_exact_contract(attempts) -> None:
    environment = create_environment(make_spec(FAULT_ID))
    with pytest.raises(InvalidToolArgumentsError):
        environment.call("retry_call", {"service": "payments-api", "attempts": attempts})


@pytest.mark.parametrize("scenario_id", [CHECKOUT_ID, RESEARCH_ID])
def test_environments_without_fault_injection_declare_empty_schedule(scenario_id: str) -> None:
    assert create_environment(make_spec(scenario_id)).fault_schedule == ()


@pytest.mark.parametrize("mode", list(ExecutionMode))
@pytest.mark.parametrize("policy", [False, True])
def test_checkout_environment_accepts_both_legacy_log_contracts(mode, policy) -> None:
    environment = create_environment(make_spec(CHECKOUT_ID, policy=policy, execution_mode=mode))
    assert environment.call("fetch_service_logs", {"service": "checkout-api", "window": "5m"}) == BROAD_LOGS
    assert environment.call("fetch_service_logs", ACTIONS[CHECKOUT_ID][2][1]) == OBSERVATIONS[CHECKOUT_ID][2]


@pytest.mark.parametrize(
    ("arguments", "valid"),
    [
        ({"service": "checkout-api", "window": "5m"}, True),
        ({"service": "checkout-api", "query": "payment dependency timeout"}, True),
        ({"service": "checkout-api", "query": "payment dependency timeout", "window": "5m"}, False),
        ({"service": "checkout-api"}, False),
        ({"window": "5m"}, False),
        ({"service": "wrong", "window": "5m"}, False),
        ({"service": "checkout-api", "window": "10m"}, False),
        ({"service": "checkout-api", "query": "other"}, False),
        ({"service": "checkout-api", "window": 5}, False),
        ({"service": 1, "query": "payment dependency timeout"}, False),
        ({"service": "checkout-api", "query": None}, False),
        ({"service": "checkout-api", "window": "5m", "extra": "value"}, False),
    ],
)
def test_checkout_log_schema_and_call_agree_on_exact_alternatives(arguments, valid) -> None:
    environment = create_environment(make_spec(CHECKOUT_ID))
    schema = environment.tool_definitions()[2]["function"]["parameters"]
    assert schema == {
        "type": "object",
        "properties": {
            "service": {"type": "string", "const": "checkout-api"},
            "query": {"type": "string", "const": "payment dependency timeout"},
            "window": {"type": "string", "const": "5m"},
        },
        "required": ["service"],
        "additionalProperties": False,
        "oneOf": [{"required": ["query"]}, {"required": ["window"]}],
    }
    # Inspect this tool's schema fragment, not a general JSON Schema interpreter.
    matches_schema = (
        set(schema["required"]) <= arguments.keys() <= schema["properties"].keys()
        and sum(set(branch["required"]) <= arguments.keys() for branch in schema["oneOf"]) == 1
        and all(
            type(value) is str and value == schema["properties"][key]["const"]
            for key, value in arguments.items()
            if key in schema["properties"]
        )
    )
    assert matches_schema == valid
    if valid:
        assert isinstance(environment.call("fetch_service_logs", arguments), str)
    else:
        initial = environment.check()
        with pytest.raises(InvalidToolArgumentsError):
            environment.call("fetch_service_logs", arguments)
        assert environment.check() == initial


@pytest.mark.parametrize("scenario_id,param,tool,args", [
    (RESEARCH_ID, {"topic": "another topic"}, "search_documents", {"query": "another topic"}),
    (FAULT_ID, {"service": "other-api"}, "invoke_service", {"service": "other-api"}),
])
def test_environment_factory_copies_validated_parameters(scenario_id, param, tool, args) -> None:
    spec = make_spec(scenario_id, scenario_params=param)
    environment = create_environment(spec)
    checked = environment.check()
    spec.scenario_params.clear()
    assert isinstance(environment.call(tool, args), str)
    assert environment.check() != checked


def test_fault_environment_uses_default_service() -> None:
    environment = create_environment(make_spec(FAULT_ID, scenario_params={}))
    assert environment.call("invoke_service", {"service": "payments-api"}) == FAULT


def test_environment_factory_rejects_invalid_registration_and_params() -> None:
    with pytest.raises(UnknownScenarioError):
        create_environment(make_spec(CHECKOUT_ID).model_copy(update={"scenario_id": "unregistered.v1"}))
    with pytest.raises(ScenarioParamError):
        create_environment(make_spec(RESEARCH_ID, scenario_params={}))
    with pytest.raises(ScenarioParamError):
        create_environment(make_spec(CHECKOUT_ID, scenario_params={"extra": "value"}))


@pytest.mark.parametrize("scenario_id", PARAMS)
def test_legacy_fixture_observations_are_not_routed_through_environment(
    scenario_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("legacy execution must not promote the new world in H1-1")

    entry = require_scenario(scenario_id)
    monkeypatch.setattr(entry.environment_factory, "call", forbidden)
    for policy in (False, True):
        events = []
        spec = make_spec(scenario_id, policy=policy)
        code = entry.handler.run_fixture(spec, lambda kind, payload: events.append((kind, payload)))
        steps = [payload for kind, payload in events if kind == "step_completed"]
        assert code == (0 if policy else 1)
        assert [step["index"] for step in steps] == list(range(3 if policy else 6))
        if policy:
            assert [step["tool_call"]["name"] for step in steps] == [name for name, _ in ACTIONS[scenario_id]]
            assert [step["observation"] for step in steps] == OBSERVATIONS[scenario_id]
        else:
            expected = {
                CHECKOUT_ID: BROAD_LOGS,
                RESEARCH_ID: "The same result list is returned; no new evidence is gathered.",
                FAULT_ID: FAULT,
            }[scenario_id]
            assert [step["observation"] for step in steps] == [expected] * 6
