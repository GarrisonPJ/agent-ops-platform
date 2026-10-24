"""Focused contracts for the additive order-support.v1 Environment and handler.

Tests drive per-Run tools, the retained fixture, and isolated SQLite API events.
Final ledger state must prove exactly one refund; old Scenario routes stay intact.
Read scenarios/order_support.py for the world and test_environment.py for H1-1.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys

import pytest

import app.scenarios  # noqa: F401 — register built-in worlds
from app.durable_events import MAX_EVENT_CONTENT, normalize_event_payload
from app.environment import Environment, InvalidToolArgumentsError, UnknownToolError
from app.phase1_schemas import EvaluationSpec, ExecutionMode, PolicyPatch
from app.scenario_assertions import AssertionCombination, evaluate_assertion_suite
from app.scenario_ids import DEFAULT_SCENARIO_ID
from app.scenario_registry import (
    ScenarioParamError,
    assertion_suite_for_scenario,
    candidate_policy_for_scenario,
    create_environment,
    ensure_registered_scenario,
    list_scenarios,
    require_scenario,
    run_scenario,
)
from app.scenarios.order_support import (
    AMOUNT_CENTS,
    ORDER_ID,
    ORDER_OUTCOME_ASSERTIONS,
    ORDER_TRAJECTORY_ASSERTIONS,
    PROVIDER_UNAVAILABLE,
    REFUND_ID,
    SCENARIO_ID,
    TOOLS,
    OrderSupportEnvironment,
    OrderSupportScenario,
    Refund,
)
from app.scoring import compute_score


ORDER_ARGS = {"order_id": ORDER_ID}
REFUND_ARGS = {**ORDER_ARGS, "reason": "shipping_failed"}
ACTIONS = [
    ("inspect_order", ORDER_ARGS),
    ("authorize_payment", ORDER_ARGS),
    ("ship_order", ORDER_ARGS),
    ("request_refund", REFUND_ARGS),
    ("request_refund", REFUND_ARGS),
    ("inspect_order", ORDER_ARGS),
]
ROOT = Path(__file__).resolve().parents[2]


def make_spec(*, replay: bool = False, **overrides) -> EvaluationSpec:
    values = {
        "run_id": "run-order",
        "experiment_id": "experiment-order",
        "scenario_id": SCENARIO_ID,
        "task": require_scenario(SCENARIO_ID).metadata.default_task,
        "seed": 42,
        "policy": candidate_policy_for_scenario(SCENARIO_ID) if replay else None,
    }
    return EvaluationSpec(**(values | overrides))


def act(environment: Environment) -> list[str]:
    return [environment.call(name, dict(args)) for name, args in ACTIONS]


def final_order(environment: Environment) -> dict:
    return json.loads(environment.check().final_observation)["order"]


def fixture_events(spec: EvaluationSpec) -> tuple[int, list[tuple[str, dict]]]:
    events = []
    code = require_scenario(SCENARIO_ID).handler.run_fixture(
        spec, lambda kind, payload: events.append((kind, payload))
    )
    return code, events


def trajectory(events: list[tuple[str, dict]]) -> dict:
    return {"steps": [
        {"action": payload["tool_call"], "observation": payload["observation"],
         "latency_ms": payload["latency_ms"]}
        for kind, payload in events if kind == "step_completed"
    ]}


def test_order_support_explicit_registration_factory_and_instruction_only_policy() -> None:
    entry = require_scenario(SCENARIO_ID)
    assert entry.metadata.id == "order-support.v1"
    assert entry.metadata in list_scenarios()
    assert isinstance(entry.handler, OrderSupportScenario)
    assert entry.environment_factory is OrderSupportEnvironment
    assert entry.metadata.allowed_tools == TOOLS
    assert entry.metadata.assertions == (*ORDER_TRAJECTORY_ASSERTIONS, *ORDER_OUTCOME_ASSERTIONS)
    assert entry.metadata.assertion_combination == AssertionCombination.ALL
    assert ensure_registered_scenario(SCENARIO_ID, {}) == {}
    with pytest.raises(ScenarioParamError):
        create_environment(make_spec(scenario_params={"order-id": "not-allowlisted"}))
    patch = candidate_policy_for_scenario(SCENARIO_ID)
    assert patch.tool_priority == {}
    assert patch.max_steps == 6
    assert PolicyPatch.model_validate_json(patch.model_dump_json()) == patch
    assert DEFAULT_SCENARIO_ID == "checkout-api-latency.v1"
    assert entry.handler.__dict__ == {}  # No shared mutable world on the handler.


@pytest.mark.parametrize("seed", [0, 42, 2_147_483_647])
def test_order_support_reset_rebuilds_state_history_and_faults(seed: int) -> None:
    environment = create_environment(make_spec(seed=seed))
    initial = environment.check()
    schedule = environment.fault_schedule
    observations = act(environment)
    checked = environment.check()
    assert all(item.passed for item in (*checked.trajectory, *checked.outcome))
    assert observations[2] == schedule[0].observation
    assert observations[3] == schedule[1].observation
    assert environment.reset(seed) is None
    assert environment.check() == initial
    assert environment.fault_schedule == schedule
    assert act(environment) == observations
    assert environment.check() == checked
    assert act(create_environment(make_spec(seed=seed))) == observations


def test_order_support_instances_do_not_share_store_history_faults_or_definitions() -> None:
    first = create_environment(make_spec())
    second = create_environment(make_spec(run_id="other-run"))
    initial = second.check()
    definitions = second.tool_definitions()
    modified = first.tool_definitions()
    modified[0]["function"]["parameters"]["properties"].clear()
    assert first.tool_definitions() == second.tool_definitions() == definitions
    assert first is not second
    observations = act(first)
    assert second.check() == initial
    assert final_order(second)["refunds"] == []
    assert act(second) == observations
    checked = second.check()
    first.reset(42)
    assert second.check() == checked
    assert final_order(second)["refund_count"] == 1


@pytest.mark.parametrize("mode", list(ExecutionMode))
@pytest.mark.parametrize("replay", [False, True])
def test_order_support_world_ignores_run_identity_policy_and_mode(mode, replay) -> None:
    reference = create_environment(make_spec())
    other = create_environment(make_spec(replay=replay, execution_mode=mode, run_id="other"))
    assert act(other) == act(reference)
    assert other.check() == reference.check()


@pytest.mark.parametrize("name,args", [(name, args) for name, args in ACTIONS[:4]])
def test_order_support_tool_schemas_match_exact_valid_arguments(name, args) -> None:
    environment = create_environment(make_spec())
    definitions = environment.tool_definitions()
    assert tuple(item["function"]["name"] for item in definitions) == TOOLS
    definition = next(item["function"] for item in definitions if item["function"]["name"] == name)
    assert definition["parameters"] == {
        "type": "object",
        "properties": {key: {"type": "string", "const": value} for key, value in args.items()},
        "required": list(args),
        "additionalProperties": False,
    }
    assert isinstance(environment.call(name, dict(args)), str)


@pytest.mark.parametrize("name,args", [(name, args) for name, args in ACTIONS[:4]])
@pytest.mark.parametrize("progress", [0, 2, 3, 4])
def test_order_support_invalid_arguments_are_atomic_even_at_fault_boundaries(name, args, progress) -> None:
    environment = create_environment(make_spec())
    reference = create_environment(make_spec())
    for tool, arguments in ACTIONS[:progress]:
        environment.call(tool, dict(arguments))
        reference.call(tool, dict(arguments))
    initial = environment.check()
    invalid = [None, [], "invalid", {}, args | {"extra": "not-allowed"}]
    for key in args:
        invalid.append({other: value for other, value in args.items() if other != key})
        invalid.extend(args | {key: value} for value in [None, True, False, 1, 1.0, [], {}, "wrong"])
    for arguments in invalid:
        with pytest.raises(InvalidToolArgumentsError):
            environment.call(name, arguments)
        assert environment.check() == initial
    with pytest.raises(UnknownToolError):
        environment.call("execute_sql", {"query": "anything"})
    assert environment.check() == initial
    for tool, arguments in ACTIONS[progress:]:
        assert environment.call(tool, dict(arguments)) == reference.call(tool, dict(arguments))
    assert environment.check() == reference.check()


def test_order_support_refund_requires_authorization_and_failed_shipment() -> None:
    environment = create_environment(make_spec())
    assert json.loads(environment.call("request_refund", REFUND_ARGS))["result"] == "refund_not_allowed"
    assert json.loads(environment.call("ship_order", ORDER_ARGS))["result"] == "payment_required"
    assert final_order(environment)["refund_count"] == 0
    environment.call("authorize_payment", ORDER_ARGS)
    authorized = environment.check().final_observation
    assert json.loads(environment.call("authorize_payment", ORDER_ARGS))["result"] == "payment_already_authorized"
    assert environment.check().final_observation == authorized
    assert json.loads(environment.call("request_refund", REFUND_ARGS))["result"] == "refund_not_allowed"
    assert environment.check().final_observation == authorized
    assert not any(item.passed for item in environment.check().outcome)
    environment.call("ship_order", ORDER_ARGS)
    before_refund = environment.check().final_observation
    assert json.loads(environment.call("ship_order", ORDER_ARGS))["result"] == "shipping_already_failed"
    assert environment.check().final_observation == before_refund
    environment.call("request_refund", REFUND_ARGS)
    assert all(item.passed for item in environment.check().outcome)


def test_order_support_timeout_after_commit_and_duplicates_prove_exactly_once() -> None:
    environment = create_environment(make_spec())
    for name, args in ACTIONS[:3]:
        environment.call(name, dict(args))
    assert not all(item.passed for item in environment.check().outcome)
    first_refund = json.loads(environment.call("request_refund", REFUND_ARGS))
    assert first_refund["result"] == "refund_timeout_after_commit"
    assert first_refund["order"]["refund_count"] == 1
    assert all(item.passed for item in environment.check().outcome)
    final_snapshot = environment.check().final_observation
    for _ in range(3):
        duplicate = json.loads(environment.call("request_refund", REFUND_ARGS))
        assert duplicate["result"] == "refund_already_recorded"
        assert duplicate["order"] == first_refund["order"]
        assert environment.check().final_observation == final_snapshot
    environment.call("authorize_payment", ORDER_ARGS)
    environment.call("ship_order", ORDER_ARGS)
    assert environment.check().final_observation == final_snapshot
    assert final_order(environment) == {
        "order_id": ORDER_ID,
        "amount_cents": AMOUNT_CENTS,
        "payment_status": "refunded",
        "shipment_status": "failed",
        "refund_count": 1,
        "refunded_amount_cents": AMOUNT_CENTS,
        "refunds": [{"refund_id": REFUND_ID, "amount_cents": AMOUNT_CENTS}],
    }


def test_order_support_outcome_checks_actual_ledger_not_refund_tool_names() -> None:
    environment = create_environment(make_spec())
    environment.call("request_refund", REFUND_ARGS)
    assert not all(item.passed for item in environment.check().outcome)
    act(environment)
    assert all(item.passed for item in environment.check().outcome)
    # Deliberately corrupt the private ledger to prove the assertions detect a
    # second refund even when the tool history is identical to a passing world.
    environment._orders[ORDER_ID].refunds.append(Refund("duplicate-refund", AMOUNT_CENTS))
    checked = environment.check()
    assert checked.outcome[0].passed is False
    assert checked.outcome[3].passed is False
    assert checked.outcome[4].passed is False
    assert json.loads(checked.final_observation)["order"]["refund_count"] == 2


def test_order_support_check_snapshot_is_canonical_bounded_read_only_and_not_a_run_score(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("Environment.check must not own Run scoring")

    monkeypatch.setattr("app.scoring.compute_score", forbidden)
    environment = create_environment(make_spec())
    for observation in act(environment):
        assert len(observation) <= MAX_EVENT_CONTENT
        assert observation == json.dumps(json.loads(observation), sort_keys=True, separators=(",", ":"))
        assert normalize_event_payload("step_completed", {"observation": observation})["observation"] == observation
    checked = environment.check()
    snapshot = checked.final_observation
    assert len(snapshot) <= MAX_EVENT_CONTENT
    assert snapshot == json.dumps(json.loads(snapshot), sort_keys=True, separators=(",", ":"))
    assert environment.check() == checked
    parsed = json.loads(snapshot)
    parsed["order"]["refunds"].clear()
    assert environment.check() == checked
    assert checked.combination == AssertionCombination.ALL
    assert [asdict(item) for item in checked.outcome] == evaluate_assertion_suite(
        assertion_suite_for_scenario(SCENARIO_ID),
        {"steps": [{"observation": snapshot}]},
    )["assertions"][len(ORDER_TRAJECTORY_ASSERTIONS):]


def test_order_support_check_rejects_oversized_snapshot_without_truncation(monkeypatch) -> None:
    environment = create_environment(make_spec())
    checked = environment.check()
    monkeypatch.setattr("app.scenarios.order_support.MAX_EVENT_CONTENT", 10)
    with pytest.raises(ValueError, match="durable observation limit"):
        environment.check()
    monkeypatch.setattr("app.scenarios.order_support.MAX_EVENT_CONTENT", MAX_EVENT_CONTENT)
    assert environment.check() == checked


def test_order_support_records_caller_owned_arguments_by_value() -> None:
    environment = create_environment(make_spec())
    args = dict(ORDER_ARGS)
    environment.call("inspect_order", args)
    args["order_id"] = "changed"
    assert environment._steps[0]["action"]["arguments"] == ORDER_ARGS


@pytest.mark.parametrize("replay,exit_code", [(False, 1), (True, 0)])
def test_order_support_fixture_baseline_replay_and_registered_assertions(replay, exit_code) -> None:
    spec = make_spec(replay=replay)
    code, events = fixture_events(spec)
    assert code == exit_code
    assert all(kind == "step_completed" for kind, _ in events)
    steps = [payload for _, payload in events]
    assert [step["index"] for step in steps] == list(range(6))
    assert [step["tool_call"]["name"] for step in steps] == (
        [name for name, _ in ACTIONS] if replay else ["inspect_order"] * 6
    )
    independent_world = create_environment(spec)
    for step in steps:
        tool = step["tool_call"]
        assert independent_world.call(tool["name"], tool["arguments"]) == step["observation"]
    score = compute_score(trajectory(events), assertion_suite=assertion_suite_for_scenario(SCENARIO_ID))
    assert score["assertions"]["aggregate"] == (1.0 if replay else 0.0)
    if replay:
        assert all(item["passed"] for item in score["assertions"]["assertions"])
    else:
        assert steps[0]["observation"] == steps[-1]["observation"]
        assert json.loads(steps[-1]["observation"])["order"]["refund_count"] == 0
    assert fixture_events(spec) == (code, events)  # Handler retains no world across Runs.


def test_order_support_fixture_with_insufficient_policy_budget_has_unmet_outcome() -> None:
    spec = make_spec(replay=True)
    spec.policy.max_steps = 3
    code, events = fixture_events(spec)
    assert code == 1
    assert len(events) == 3
    assert json.loads(events[-1][1]["observation"])["order"]["refund_count"] == 0
    assert evaluate_assertion_suite(
        assertion_suite_for_scenario(SCENARIO_ID), trajectory(events)
    )["aggregate"] == 0.0


@pytest.mark.parametrize("replay,exit_code", [(False, 1), (True, 0)])
def test_order_support_demo_agent_compatibility_jsonl(replay, exit_code) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.demo_agent"],
        input=make_spec(replay=replay).model_dump_json(),
        text=True, capture_output=True, check=False, cwd=ROOT / "backend",
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
    )
    assert result.returncode == exit_code, result.stderr
    emitted = [(event["type"], event["payload"]) for event in map(json.loads, result.stdout.splitlines())]
    assert emitted == fixture_events(make_spec(replay=replay))[1]


@pytest.mark.asyncio
async def test_order_support_provider_is_explicitly_unavailable_without_shared_loop(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("H1-6 must not call a provider, fabricate fixture success, or create a provider world")

    monkeypatch.setattr("app.phase1_provider.run_provider_agent", forbidden)
    monkeypatch.setattr(OrderSupportEnvironment, "__init__", forbidden)
    events = []
    assert await run_scenario(
        make_spec(execution_mode=ExecutionMode.PROVIDER),
        lambda kind, payload: events.append((kind, payload)),
    ) == 1
    assert events == [("process_output", {"stream": "stderr", "content": PROVIDER_UNAVAILABLE})]


@pytest.mark.parametrize("name,args", [(name, args) for name, args in ACTIONS[:4]])
def test_order_support_durable_normalization_retains_only_fixed_order_arguments(name, args) -> None:
    payload = {"tool_call": {"name": name, "arguments": args}}
    assert normalize_event_payload("step_completed", payload) == payload
    extra = args | {"secret": "drop", "service": "checkout-api", "reason": "shipping_failed"}
    assert normalize_event_payload("step_completed", {
        "tool_call": {"name": name, "arguments": extra}
    }) == payload
    for key in args:
        for wrong in ("wrong", 1, True, None, [], {}):
            normalized = normalize_event_payload("step_completed", {
                "tool_call": {"name": name, "arguments": args | {key: wrong}}
            })
            assert key not in normalized["tool_call"]["arguments"]
    assert normalize_event_payload("step_completed", {
        "tool_call": {"name": name, "arguments": []}
    }) == {}
    assert normalize_event_payload("step_completed", {
        "tool_call": {"name": "delete_order", "arguments": args}
    }) == {}


@pytest.mark.parametrize("name", [
    "check_service_health", "query_service_metrics", "fetch_service_logs",
    "search_documents", "fetch_document", "submit_answer",
    "invoke_service", "retry_call", "degrade_route",
])
def test_order_support_durable_additions_do_not_broaden_legacy_tool_arguments(name) -> None:
    assert normalize_event_payload("step_completed", {"tool_call": {
        "name": name, "arguments": {
            "service": "checkout-api", "order_id": ORDER_ID, "reason": "shipping_failed"
        },
    }}) == {"tool_call": {"name": name, "arguments": {"service": "checkout-api"}}}


@pytest.mark.asyncio
async def test_order_support_isolated_closed_loop_persists_final_state_evidence(api) -> None:
    from test_control_plane import AUTH, claim, envelope, upload

    client, _ = api
    scenarios = (await client.get("/api/scenarios")).json()
    metadata = next(item for item in scenarios if item["id"] == SCENARIO_ID)
    assert metadata["allowed_tools"] == list(TOOLS)
    created = await client.post("/api/experiments", json={
        "name": "Order support fixture", "scenario_id": SCENARIO_ID,
        "task": metadata["default_task"],
    })
    assert created.status_code == 201
    experiment_id = created.json()["id"]
    baseline = await client.post(f"/api/experiments/{experiment_id}/runs", json={"seed": 42})
    assert baseline.status_code == 201

    async def execute_claim(expected_code: int) -> dict:
        job = await claim(client)
        spec = EvaluationSpec.model_validate(job["run"]["evaluation_spec"])
        code, events = fixture_events(spec)
        assert code == expected_code
        persisted = [envelope(spec.run_id, 1, "run_started", {"attempt": 1})]
        persisted.extend(envelope(spec.run_id, index + 2, kind, payload)
                         for index, (kind, payload) in enumerate(events))
        status = "succeeded" if code == 0 else "failed"
        persisted.append(envelope(spec.run_id, len(persisted) + 1,
                                  "run_completed" if code == 0 else "run_failed", {"exit_code": code}))
        assert await upload(client, job, persisted) == len(persisted)
        completed = await client.post(
            f"/api/internal/runner/jobs/{job['lease_id']}/complete",
            headers=AUTH, json={"runner_id": "runner-1", "status": status},
        )
        assert completed.status_code == 200
        stored = completed.json()
        expected = compute_score(trajectory(events), assertion_suite=assertion_suite_for_scenario(SCENARIO_ID))
        assert stored["metrics"]["assertions"] == expected["assertions"]
        stream = await client.get(f"/api/runs/{spec.run_id}/stream?after=0")
        assert '"refund_count"' in stream.text.replace('\\"', '"')
        return stored

    failed = await execute_claim(1)
    assert failed["status"] == "failed"
    assert failed["metrics"]["assertions"]["aggregate"] == 0.0
    analysis = (await client.get(f"/api/runs/{failed['id']}/analysis")).json()
    assert "loop_no_progress" in analysis["failure_types"]
    detail = (await client.get(f"/api/experiments/{experiment_id}")).json()
    candidate = detail["candidate_policy"]
    assert candidate["status"] == "candidate"
    assert candidate["patch"] == candidate_policy_for_scenario(SCENARIO_ID).model_dump()
    replay_response = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert replay_response.status_code == 201
    succeeded = await execute_claim(0)
    for key in ("scenario_id", "scenario_params", "seed", "task", "limits", "execution_mode"):
        assert succeeded["evaluation_spec"][key] == failed["evaluation_spec"][key]
    assert succeeded["metrics"]["assertions"]["aggregate"] == 1.0
    assert succeeded["score"] > failed["score"]
    validated = (await client.get(f"/api/experiments/{experiment_id}")).json()["candidate_policy"]
    assert validated["status"] == "validated"
    assert validated["score_delta"] > 0
    activated = await client.post(f"/api/policies/{candidate['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"  # Existing explicit human gate.
