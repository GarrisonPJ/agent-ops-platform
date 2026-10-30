"""Strict final-check model and durable normalization contract tests.

Fresh in-memory operands exercise the Environment-to-CP handoff without a DB.
Malformed state must fail closed; valid state is not an assertion-success claim.
Read test_env_server.py for one-time generation from a real world's check.
"""

from __future__ import annotations

import json

import pytest

from app.durable_events import normalize_event_payload
from app.final_evidence import (
    FinalCheck,
    MAX_FINAL_OBSERVATION_BYTES,
    MAX_FINAL_OBSERVATION_CHARS,
    validate_final_check,
    validate_order_final_observation,
)


TRAJECTORY_IDS = [
    "checkout-api-latency.v1",
    "multi-step-research.v1",
    "api-fault-orchestration.v1",
]


def ledger():
    # Intentionally not the expected successful ledger: types/presence only.
    return {"order": {
        "order_id": "another-order",
        "amount_cents": 19,
        "payment_status": "pending",
        "shipment_status": "pending",
        "refund_count": 0,
        "refunded_amount_cents": 0,
        "refunds": [],
    }}


def evidence(observation=None, *, scenario_id="order-support.v1"):
    return {"scenario_id": scenario_id, "final_observation": observation}


def accepted(final_check):
    return {"environment": {
        "kind": "control", "control": "submit_result", "result": "ok",
        "finalized": True, "check_called": True, "final_check": final_check,
    }}


@pytest.mark.parametrize("scenario_id", TRAJECTORY_IDS)
def test_final_evidence_trajectory_requires_explicit_null(scenario_id):
    raw = evidence(scenario_id=scenario_id)
    assert validate_final_check(raw, scenario_id=scenario_id).model_dump() == raw
    for invalid in [{"scenario_id": scenario_id}, evidence("{}", scenario_id=scenario_id)]:
        with pytest.raises(ValueError):
            validate_final_check(invalid)


def test_final_evidence_valid_ledger_is_not_expected_value_injection():
    raw = evidence(json.dumps(ledger(), indent=2))
    parsed = validate_final_check(raw, scenario_id="order-support.v1")
    assert parsed.model_dump() == raw
    assert validate_final_check(parsed) == parsed
    assert validate_order_final_observation(raw["final_observation"]).order.amount_cents == 19
    with pytest.raises(ValueError, match="mismatch"):
        validate_final_check(raw, scenario_id=TRAJECTORY_IDS[0])
    with pytest.raises(ValueError):
        validate_final_check(FinalCheck.model_construct(**evidence("{}")))


@pytest.mark.parametrize("field", [
    "score", "status", "evaluations", "trajectory", "outcome", "expected", "combination",
])
def test_final_evidence_forbids_authority_fields(field):
    with pytest.raises(ValueError):
        validate_final_check({**evidence(scenario_id=TRAJECTORY_IDS[0]), field: 1})


@pytest.mark.parametrize("raw", [
    None, [], {}, {"scenario_id": True, "final_observation": None},
    evidence(scenario_id="checkout-api-latency.v2"),
    evidence(scenario_id="unregistered.v1"), evidence(1), evidence(b"{}"), evidence(),
])
def test_final_evidence_rejects_invalid_shape_identity_or_operand(raw):
    with pytest.raises(ValueError):
        validate_final_check(raw)


@pytest.mark.parametrize("path", [
    ("order",),
    *(('order', name) for name in ledger()["order"]),
])
def test_final_evidence_requires_every_ledger_path(path):
    raw = ledger()
    target = raw
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(ValueError):
        validate_final_check(evidence(json.dumps(raw)))


@pytest.mark.parametrize("field", ["amount_cents", "refund_count", "refunded_amount_cents"])
@pytest.mark.parametrize("value", [True, False, 1.0, -1, "1", None])
def test_final_evidence_rejects_non_integer_or_negative_ledger_counters(field, value):
    raw = ledger()
    raw["order"][field] = value
    with pytest.raises(ValueError):
        validate_final_check(evidence(json.dumps(raw)))


@pytest.mark.parametrize("refund", [
    {}, {"refund_id": 1, "amount_cents": 0},
    {"refund_id": "r", "amount_cents": True},
    {"refund_id": "r", "amount_cents": 1.0},
    {"refund_id": "r", "amount_cents": -1},
    {"refund_id": "r", "amount_cents": 1, "score": 1},
])
def test_final_evidence_refunds_require_strict_operands(refund):
    raw = ledger()
    raw["order"]["refunds"] = [refund]
    with pytest.raises(ValueError):
        validate_final_check(evidence(json.dumps(raw)))


@pytest.mark.parametrize("mutation", [
    {"payment_status": "paid"}, {"shipment_status": "shipped"},
    {"order_id": None}, {"refunds": {}}, {"score": 1},
])
def test_final_evidence_rejects_invalid_order_shape_or_vocabulary(mutation):
    raw = ledger()
    raw["order"].update(mutation)
    with pytest.raises(ValueError):
        validate_final_check(evidence(json.dumps(raw)))


@pytest.mark.parametrize("raw", [
    '[]', 'null', '{}', '{',
    '{"order":{},"order":' + json.dumps(ledger()["order"]) + '}',
    json.dumps(ledger()).replace('"order_id": "another-order"', '"order_id":"a","order_id":"b"'),
    *(json.dumps(ledger()).replace('"amount_cents": 19', '"amount_cents": ' + number)
      for number in ["NaN", "Infinity", "-Infinity", "1e999"]),
])
def test_final_evidence_strict_json_rejects_duplicates_and_nonfinite(raw):
    with pytest.raises(ValueError):
        validate_final_check(evidence(raw))


@pytest.mark.parametrize("character", ["😀", "\u0000", '"', "\\"])
def test_final_evidence_unicode_bounds_apply_to_operand_not_outer_json(character):
    raw = ledger()
    raw["order"]["order_id"] = ""
    base = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    encoded_character_length = len(json.dumps(character, ensure_ascii=False)) - 2
    count, padding = divmod(MAX_FINAL_OBSERVATION_CHARS - len(base), encoded_character_length)
    raw["order"]["order_id"] = character * count + "x" * padding
    observation = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    assert len(observation) == MAX_FINAL_OBSERVATION_CHARS
    assert len(observation.encode("utf-8")) <= MAX_FINAL_OBSERVATION_BYTES
    if character == "😀":
        assert len(json.dumps(observation).encode()) > MAX_FINAL_OBSERVATION_BYTES
    assert validate_final_check(evidence(observation)).final_observation == observation
    with pytest.raises(ValueError):
        validate_final_check(evidence(observation + " "))
    raw["order"]["order_id"] = "\ud800"
    with pytest.raises(ValueError):
        validate_final_check(evidence(json.dumps(raw, ensure_ascii=False)))


@pytest.mark.parametrize("raw", [
    evidence(scenario_id=TRAJECTORY_IDS[0]), evidence(json.dumps(ledger(), indent=2)),
])
def test_final_evidence_normalizer_retains_exact_model_idempotently(raw):
    payload = accepted(raw)
    payload["environment"].update({"score": 1, "status": "succeeded", "evaluations": [1]})
    normalized = normalize_event_payload("step_completed", payload)
    assert normalized == accepted(raw)
    assert normalize_event_payload("step_completed", normalized) == normalized
    assert payload["environment"]["score"] == 1  # no caller mutation


@pytest.mark.parametrize("raw", [None, {}, evidence("{}"), {**evidence(scenario_id=TRAJECTORY_IDS[0]), "score": 1}])
def test_final_evidence_normalizer_rejects_invalid_present_model(raw):
    with pytest.raises(ValueError):
        normalize_event_payload("step_completed", accepted(raw))


@pytest.mark.parametrize("field,value", [
    ("kind", "world"), ("control", "report_usage"), ("result", "internal_error"),
    ("result", "duplicate_submit"), ("finalized", False), ("finalized", 1),
    ("check_called", False), ("check_called", "true"),
    ("control", None), ("kind", None), ("finalized", None), ("check_called", None),
])
def test_final_evidence_normalizer_rejects_nonaccepted_provenance(field, value):
    payload = accepted(evidence(scenario_id=TRAJECTORY_IDS[0]))
    payload["environment"][field] = value
    with pytest.raises(ValueError):
        normalize_event_payload("step_completed", payload)
    del payload["environment"][field]
    with pytest.raises(ValueError):
        normalize_event_payload("step_completed", payload)
