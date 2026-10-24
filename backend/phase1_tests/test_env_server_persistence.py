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
from app.phase1_models import Run
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


async def persist(client, run, claim, records):
    events = [
        {
            "schema_version": 1,
            "run_id": run["id"],
            "sequence": index + 1,
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
    assert response.json()["accepted_through"] == len(events)


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
    captured[-1]["payload"]["environment"].update(
        {"score": 99, "trajectory": [{"score": 99}]}
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
        json={"runner_id": "runner-env", "status": "failed", "metrics": {"score": 99}},
    )
    assert result.status_code == 200, result.text
    completed = result.json()
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
    await persist(client, run, claim, records)
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
async def test_env_server_latest_attempt_source_selects_unknown_or_legacy_zero(
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
    await persist(client, run, claim, records)
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
    assert metrics["steps"] == step_count
    assert (
        trajectory["total_tokens"] == metrics["total_tokens"] == (None if source else 0)
    )
    if source:
        assert metrics["environment"] == {"finalized": False, "check_called": False}
        assert metrics["agent_usage"]["context_window"] is None
    else:
        assert "environment" not in metrics and "agent_usage" not in metrics
