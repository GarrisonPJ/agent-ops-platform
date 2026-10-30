"""Durable H1-2 provenance and Control Plane reconstruction contract tests.

Isolated API/SQLite tests persist Environment facts, then complete through the API.
Controls/check markers are not scores, status, or world assertion operands.
Read env_server.py for capture and phase1_service.py for the authoritative reducer.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest

from app.durable_events import MAX_PROVIDER_TOKENS, normalize_event_payload
from app.env_server import EnvironmentSession, jsonl_emitter
from app.phase1_models import Run, RunnerJob
from test_cp_completion import facts
from app.phase1_schemas import EvaluationSpec
from app.phase1_service import _trajectory_from_events
from app.scenario_registry import assertion_suite_for_scenario
from app.scoring import compute_score

AUTH = {"Authorization": "Bearer test-runner-token"}
HEALTH = {"name": "check_service_health", "arguments": {"service": "checkout-api"}}


def test_env_server_provenance_normalization_is_bounded_and_idempotent():
    raw = {
        "index": 0,
        "observation": "Error: unknown_tool.",
        "tool_call": {"name": "untrusted-shell", "arguments": {"secret": "drop"}},
        "score": 1,
        "status": "succeeded",
        "expected": "drop",
        "environment": {
            "kind": "world",
            "result": "unknown_tool",
            "finalized": False,
            "score": 1,
            "status": "succeeded",
            "expected": "drop",
        },
    }
    normalized = normalize_event_payload("step_completed", raw)
    assert normalized == {
        "index": 0,
        "observation": "Error: unknown_tool.",
        "environment": {
            "kind": "world",
            "result": "unknown_tool",
            "finalized": False,
            "check_called": False,
        },
    }
    assert normalize_event_payload("step_completed", normalized) == normalized
    assert normalize_event_payload(
        "run_started", {"attempt": 2, "source": "environment", "token": "drop"}
    ) == {
        "attempt": 2,
        "source": "environment",
    }
    for source in [None, "agent", "secret", {}, ["environment"], True]:
        assert normalize_event_payload(
            "run_started", {"attempt": 1, "source": source}
        ) == {"attempt": 1}
    for invalid in [
        {"kind": "world", "result": "arbitrary-secret"},
        {"kind": "control", "result": "ok", "control": "shell"},
        {"kind": "control", "result": "ok"},
        {"kind": "world", "result": "ok", "control": "submit_result"},
        {"kind": "world", "result": "ok", "finalized": "true"},
    ]:
        assert normalize_event_payload("step_completed", {"environment": invalid}) == {}
    usage = {
        "kind": "control",
        "result": "ok",
        "control": "report_usage",
        "usage": {"source": "agent_reported", "token_prompt": MAX_PROVIDER_TOKENS},
    }
    safe = normalize_event_payload("step_completed", {"environment": usage})
    assert safe["environment"]["usage"]["token_prompt"] == MAX_PROVIDER_TOKENS
    assert normalize_event_payload("step_completed", safe) == safe
    for invalid_usage in [
        {"token_prompt": -1},
        {"score": 1},
        {"token_prompt": MAX_PROVIDER_TOKENS + 1},
    ]:
        usage["usage"] = invalid_usage
        assert (
            "usage"
            not in normalize_event_payload("step_completed", {"environment": usage})[
                "environment"
            ]
        )


async def create_claim(client):
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Environment persistence",
                "task": "Investigate latency",
            },
        )
    ).json()
    run = (
        await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    ).json()
    claim = (
        await client.post(
            "/api/internal/runner/jobs/claim",
            headers=AUTH,
            json={"runner_id": "runner-env"},
        )
    ).json()
    return run, claim


async def persist(client, run, claim, records, offset=0):
    for record in records:
        record["payload"].setdefault("attempt", claim["attempt"])
        if record["type"] == "run_started":
            record["payload"]["source"] = "environment"
    events = [
        {
            "schema_version": 1,
            "run_id": run["id"],
            "sequence": index + 1 + offset,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            **record,
        }
        for index, record in enumerate(records)
    ]
    response = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-env",
            "lease_id": claim["lease_id"],
            "events": events,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted_through"] == len(events) + offset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [None, {}, {"token_prompt": 30}, {"token_prompt": 30, "token_completion": 9}],
)
async def test_env_server_persist_reconstruct_controls_unknown_usage_and_cp_authority(
    api, usage
):
    client, factory = api
    run, claim = await create_claim(client)
    output = io.StringIO()
    session = EnvironmentSession(
        EvaluationSpec.model_validate(run["evaluation_spec"]),
        "f123456789abcdef" * 4,
        jsonl_emitter(output),
    )
    await session.dispatch(HEALTH)
    await session.dispatch(
        {
            "name": "query_service_metrics",
            "arguments": {"service": "checkout-api", "metric": "dependency_latency"},
        }
    )
    await session.dispatch(
        {
            "name": "fetch_service_logs",
            "arguments": {
                "service": "checkout-api",
                "query": "payment dependency timeout",
            },
        }
    )
    if usage is not None:
        # Latest report is cumulative, not added to the older 1000+1000 report.
        await session.dispatch(
            {
                "name": "report_usage",
                "arguments": {"token_prompt": 1000, "token_completion": 1000},
            }
        )
        await session.dispatch({"name": "report_usage", "arguments": usage})
    await session.dispatch({"name": "submit_result"})
    await session.dispatch({"name": "submit_result"})
    captured = [json.loads(line) for line in output.getvalue().splitlines()]
    # Even forged score/check fields on trusted-pipe objects are not scoring inputs.
    captured[-1]["payload"].update(
        {"score": 99, "status": "succeeded", "expected": "anything"}
    )

    await persist(
        client,
        run,
        claim,
        [
            {
                "type": "run_started",
                "payload": {
                    "attempt": 1,
                    "source": "environment",
                },
            },
            *captured,
        ],
    )
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
        assert len(trajectory["steps"]) == metrics["steps"] == 3
        assert trajectory["steps"][-1]["action"]["name"] == "fetch_service_logs"
        expected_usage = {
            "source": "agent_reported",
            "token_prompt": None,
            "token_completion": None,
            "context_window": None,
            **(usage or {}),
        }
        assert metrics["agent_usage"] == expected_usage
        assert metrics["token_prompt"] == expected_usage["token_prompt"]
        assert metrics["token_completion"] == expected_usage["token_completion"]
        total = 39 if usage and "token_completion" in usage else None
        assert trajectory["total_tokens"] == metrics["total_tokens"] == total
        assert metrics["environment"] == {"finalized": True, "check_called": True}
        expected = compute_score(
            trajectory,
            assertion_suite=assertion_suite_for_scenario("checkout-api-latency.v1"),
        )
        assert all(item["passed"] for item in expected["assertions"]["assertions"])
    # Finalization never makes a Run successful: the normal lease/CAS completion
    # boundary still chooses status, and only the CP recomputes the score.
    result = await client.post(
        f"/api/internal/runner/jobs/{claim['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-env", "status": "failed", "metrics": {"score": 99}, "execution": facts(run["id"])},
    )
    assert result.status_code == 200, result.text
    completed = result.json()["run"]
    assert completed["status"] == "failed"
    assert completed["score"] == pytest.approx(expected["score"])
    assert completed["score"] != 99
    assert completed["metrics"]["agent_usage"] == expected_usage
    assert completed["metrics"]["environment"]["check_called"] is True


@pytest.mark.asyncio
async def test_env_server_rejections_are_world_steps_but_never_executed_tool_assertions(
    api,
):
    client, factory = api
    run, claim = await create_claim(client)
    output = io.StringIO()
    session = EnvironmentSession(
        EvaluationSpec.model_validate(run["evaluation_spec"]),
        "f123456789abcdef" * 4,
        jsonl_emitter(output),
    )
    # None of these calls executes health: invalid, unknown, over-budget and finalized.
    await session.dispatch({"name": "check_service_health", "arguments": {}})
    for _ in range(5):
        await session.dispatch({"name": "shell", "arguments": {}})
    await session.dispatch(HEALTH)
    await session.dispatch({"name": "submit_result"})
    await session.dispatch(HEALTH)
    captured = [json.loads(line) for line in output.getvalue().splitlines()]
    await persist(
        client,
        run,
        claim,
        [{"type": "run_started", "payload": {"attempt": 1}}, *captured],
    )
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
    assert metrics["steps"] == 8
    assert all(
        item["action"]["name"] == "rejected_tool" for item in trajectory["steps"]
    )
    assert all(item["status"] == "failed" for item in trajectory["steps"])
    assert trajectory["steps"][-2]["rejection"] == "max_steps"
    assert trajectory["steps"][-1]["rejection"] == "finalized"
    result = compute_score(
        trajectory,
        assertion_suite=assertion_suite_for_scenario("checkout-api-latency.v1"),
    )
    assert result["assertions"]["assertions"][0]["score"] == 0
    assert result["assertions"]["assertions"][1]["score"] == 0
    assert result["assertions"]["assertions"][2]["score"] == 0
    assert result["breakdown"]["tool_failure_penalty"] == -0.5


@pytest.mark.asyncio
async def test_env_server_reconstruction_discards_abandoned_attempt_check_and_usage(
    api,
):
    client, factory = api
    run, claim = await create_claim(client)
    records = [
        {"type": "run_started", "payload": {"attempt": 1}},
        {
            "type": "step_completed",
            "payload": {
                "environment": {
                    "kind": "control",
                    "control": "report_usage",
                    "result": "ok",
                    "usage": {
                        "source": "agent_reported",
                        "token_prompt": 999,
                        "token_completion": 999,
                    },
                }
            },
        },
        {
            "type": "step_completed",
            "payload": {
                "environment": {
                    "kind": "control",
                    "control": "submit_result",
                    "result": "ok",
                    "finalized": True,
                    "check_called": True,
                }
            },
        },
        {"type": "run_started", "payload": {"attempt": 2}},
        {
            "type": "step_completed",
            "payload": {
                "tool_call": HEALTH,
                "environment": {"kind": "world", "result": "ok"},
                "observation": "Healthy.",
            },
        },
    ]
    await persist(client, run, claim, records[:3])
    from datetime import timedelta
    from app.phase1_models import utcnow
    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    claim = (await client.post("/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "runner-env"})).json()
    await persist(client, run, claim, records[3:], offset=3)
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
    assert len(trajectory["steps"]) == 1
    assert metrics["environment"] == {"finalized": False, "check_called": False}
    assert metrics["total_tokens"] is None
    assert metrics["agent_usage"]["token_prompt"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["environment", None])
@pytest.mark.parametrize("step_count", [0, 1])
async def test_env_server_latest_attempt_requires_source_and_keeps_usage_unknown(
    api, source, step_count
):
    client, factory = api
    run, claim = await create_claim(client)
    records = [
        {"type": "run_started", "payload": {"attempt": 1, "source": "environment"}},
        {
            "type": "step_completed",
            "payload": {
                "environment": {
                    "kind": "control",
                    "control": "submit_result",
                    "result": "ok",
                    "finalized": True,
                    "check_called": True,
                }
            },
        },
        {
            "type": "run_started",
            "payload": {"attempt": 2, **({"source": source} if source else {})},
        },
    ]
    if step_count:
        records.append(
            {
                "type": "step_completed",
                "payload": {
                    "tool_call": HEALTH,
                    "observation": "Healthy.",
                    **(
                        {"environment": {"kind": "world", "result": "ok"}}
                        if source
                        else {}
                    ),
                },
            }
        )
    await persist(client, run, claim, records[:2])
    from datetime import timedelta
    from app.phase1_models import utcnow
    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    claim = (await client.post("/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "runner-env"})).json()
    if source is None:
        # Missing canonical provenance is rejected, not a legacy zero-usage path.
        events = [{"run_id": run["id"], "sequence": 3 + i,
                   "occurred_at": utcnow().isoformat(), **record}
                  for i, record in enumerate(records[2:])]
        events[0]["payload"]["attempt"] = 2
        result = await client.post(f"/api/internal/runner/runs/{run['id']}/events", headers=AUTH,
            json={"runner_id": "runner-env", "lease_id": claim["lease_id"], "events": events})
        assert result.status_code == 422
        return
    await persist(client, run, claim, records[2:], offset=2)
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
    assert metrics["steps"] == step_count
    assert trajectory["total_tokens"] == metrics["total_tokens"] is None
    assert metrics["environment"] == {"finalized": False, "check_called": False}
    assert metrics["agent_usage"]["context_window"] is None


@pytest.mark.asyncio
async def test_env_server_many_safe_rejected_arguments_persist_every_world_attempt(api):
    from sqlalchemy import func, select
    from app.phase1_models import RunEvent

    client, factory = api
    run, claim = await create_claim(client)
    output = io.StringIO()
    session = EnvironmentSession(
        EvaluationSpec.model_validate(run["evaluation_spec"]),
        "f123456789abcdef" * 4,
        jsonl_emitter(output),
    )
    invalid = {
        "name": "check_service_health",
        "arguments": {
            "service": "checkout-api",
            "metric": "dependency_latency",
            "window": "5m",
            "query": "checkout latency",
            "document_id": "doc-dependency-latency",
        },
    }
    for _ in range(session.max_steps + 1):
        assert (await session.dispatch(invalid)).is_error
    await session.dispatch({"name": "submit_result"})
    assert (await session.dispatch(invalid)).is_error
    captured = [json.loads(line) for line in output.getvalue().splitlines()]
    for event in captured:
        assert (
            normalize_event_payload(event["type"], event["payload"]) == event["payload"]
        )
    events = [
        {"type": "run_started", "payload": {"attempt": 1, "source": "environment"}},
        *captured,
    ]
    await persist(client, run, claim, events)
    await persist(
        client, run, claim, events
    )  # Same facts converge on existing sequences.
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
        count = await db.scalar(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.run_id == run["id"])
        )
    assert count == len(events)
    assert metrics["steps"] == len(trajectory["steps"]) == session.world_attempts == 8
    assert [step["index"] for step in trajectory["steps"]] == [*range(7), 8]
    assert [step["rejection"] for step in trajectory["steps"]] == [
        "invalid_arguments"
    ] * 6 + ["max_steps", "finalized"]
    assert all(
        step["action"]["name"] == "rejected_tool" for step in trajectory["steps"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("controls", ["prepended", "interleaved"])
async def test_env_server_reducer_budget_evidence_uses_final_world_index(api, controls):
    from app.failure_analyzer import analyze_trajectory

    client, factory = api
    run, claim = await create_claim(client)
    output = io.StringIO()
    session = EnvironmentSession(
        EvaluationSpec.model_validate(run["evaluation_spec"]),
        "f123456789abcdef" * 4,
        jsonl_emitter(output),
    )
    if controls == "prepended":
        for _ in range(6):
            await session.dispatch({"name": "report_usage"})
    for _ in range(6):
        if controls == "interleaved":
            await session.dispatch({"name": "report_usage"})
        await session.dispatch(HEALTH)
    captured = [json.loads(line) for line in output.getvalue().splitlines()]
    await persist(
        client,
        run,
        claim,
        [
            {"type": "run_started", "payload": {"attempt": 1, "source": "environment"}},
            *captured,
        ],
    )
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
    assert metrics["steps"] == 6
    assert trajectory["steps"][-1]["index"] == 11
    report = analyze_trajectory(trajectory)
    budget = [item for item in report.evidence if item.dimension == "budget"]
    assert len(budget) == 1
    assert budget[0].step_index == trajectory["steps"][-1]["index"]
    assert budget[0].severity == 1.0
    assert budget[0].details == {"step_count": 6, "max_steps": 6}


@pytest.mark.asyncio
@pytest.mark.parametrize("refund", [False, True])
async def test_env_server_final_check_exact_ledger_survives_durable_retry(api, refund):
    from sqlalchemy import select

    from app.final_evidence import validate_final_check, validate_order_final_observation
    from app.phase1_models import RunEvent

    client, factory = api
    experiment = (await client.post("/api/experiments", json={
        "name": "Final evidence persistence", "task": "Resolve order",
        "scenario_id": "order-support.v1",
    })).json()
    run = (await client.post(f"/api/experiments/{experiment['id']}/runs", json={})).json()
    claim = (await client.post(
        "/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "runner-env"},
    )).json()
    token = "f123456789abcdef" * 4
    output = io.StringIO()
    session = EnvironmentSession(
        EvaluationSpec.model_validate(run["evaluation_spec"]), token, jsonl_emitter(output),
    )
    if refund:
        for name in ["inspect_order", "authorize_payment", "ship_order", "request_refund", "request_refund"]:
            arguments = {"order_id": "order-1001"}
            if name == "request_refund":
                arguments["reason"] = "shipping_failed"
            assert not (await session.dispatch({"name": name, "arguments": arguments})).is_error
    expected_observation = session.environment.check().final_observation
    assert not (await session.dispatch({"name": "submit_result"})).is_error
    assert (await session.dispatch({"name": "submit_result"})).is_error
    captured = [json.loads(line) for line in output.getvalue().splitlines()]
    events = [
        {"type": "run_started", "payload": {"attempt": 1, "source": "environment"}},
        *captured,
    ]
    await persist(client, run, claim, events)
    await persist(client, run, claim, events)
    async with factory() as db:
        persisted = (await db.scalars(
            select(RunEvent).where(RunEvent.run_id == run["id"]).order_by(RunEvent.sequence)
        )).all()
    assert len(persisted) == len(events)
    final_checks = [
        event.payload["environment"]["final_check"] for event in persisted
        if "final_check" in event.payload.get("environment", {})
    ]
    assert final_checks == [{
        "scenario_id": "order-support.v1", "final_observation": expected_observation,
    }]
    parsed = validate_final_check(
        final_checks[0], scenario_id=run["evaluation_spec"]["scenario_id"]
    )
    order = validate_order_final_observation(parsed.final_observation).order
    assert order.refund_count == (1 if refund else 0)
    assert len(order.refunds) == (1 if refund else 0)
    assert token not in json.dumps([event.payload for event in persisted])
    for event in persisted:
        assert normalize_event_payload(event.event_type, event.payload) == event.payload
