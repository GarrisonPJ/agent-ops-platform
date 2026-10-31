from __future__ import annotations

from datetime import timedelta
import io
import json
from pathlib import Path
import runpy

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from test_cp_completion import facts
from app.phase1_models import RunEvent, RunnerAttempt, RunnerJob, utcnow
from app.failure_analyzer import analyze_trajectory
from app.scenario_registry import assertion_suite_for_scenario, require_scenario


AUTH = {"Authorization": "Bearer test-runner-token"}


def test_recovery_script_uses_checkout_scenario_task_contract() -> None:
    script = Path(__file__).parents[2] / "scripts" / "recovery_e2e.py"
    values = runpy.run_path(str(script))
    scenario_id = values["RECOVERY_SCENARIO_ID"]
    assert scenario_id == "checkout-api-latency.v2"
    assert values["RECOVERY_TASK"] == require_scenario(scenario_id).metadata.default_task
    from app.agent_registry import load_registry
    root = Path(__file__).parents[2]
    recovery_id = values["RECOVERY_AGENT_ID"]
    assert recovery_id == "recovery-fixture.v1"
    assert recovery_id not in load_registry(root / "config/agents.toml").by_id
    registry = load_registry(root / "config/agents.recovery.toml")
    assert registry.by_id[recovery_id].command == ("python", "-m", "app.recovery_agent")
    from app.recovery_agent import MARKER
    assert values["RECOVERY_MARKER"] == str(MARKER)
    assert MARKER.parent == Path("/run/agentops/token-handoff")


def test_recovery_gate_waits_for_agent_and_force_recreates_replacement(monkeypatch) -> None:
    script = Path(__file__).parents[2] / "scripts" / "recovery_e2e.py"
    values = runpy.run_path(str(script))
    run_gate = values["run_recovery_test"]
    calls = []
    replies = iter([{"id": "experiment"}, {"id": "run"}, {"status": "running"}])
    namespace = run_gate.__globals__
    monkeypatch.setitem(namespace, "compose", lambda *args: calls.append(args))
    monkeypatch.setitem(namespace, "wait_for_api", lambda *_: None)
    monkeypatch.setitem(namespace, "wait_for_recovery_marker", lambda: calls.append(("marker-ready",)))
    monkeypatch.setitem(namespace, "request", lambda *_args, **_kwargs: next(replies))
    monkeypatch.setitem(namespace, "wait_for_status", lambda _api, _run, expected, _timeout: {"status": next(iter(expected))})
    monkeypatch.setitem(namespace, "assert_recovered_trace", lambda *_: None)
    monkeypatch.setattr(namespace["time"], "sleep", lambda _: None)
    result = run_gate("http://local.invalid")
    assert result["after_replacement"] == "succeeded"
    assert calls.index(("marker-ready",)) < calls.index((True, "kill", "runner"))
    assert calls[-1] == (True, "up", "-d", "--force-recreate", "runner")


def test_recovery_stream_parser_preserves_sse_event_payloads() -> None:
    script = Path(__file__).parents[2] / "scripts" / "recovery_e2e.py"
    values = runpy.run_path(str(script))
    parse_stream = values["_parse_event_stream"]
    assert parse_stream(
        'id: 1\nevent: run_started\ndata: {"sequence":1,"type":"run_started"}\n\n'
        ': keep-alive\n\n'
        'id: 2\nevent: run_completed\ndata: {"sequence":2,"type":"run_completed"}\n\n'
    ) == [
        {"sequence": 1, "type": "run_started"},
        {"sequence": 2, "type": "run_completed"},
    ]


@pytest.mark.asyncio
async def test_recovery_agent_trace_satisfies_checkout_assertions(tmp_path, monkeypatch, capsys) -> None:
    from app import recovery_agent
    from app.env_server import EnvironmentSession
    from app.fixture_agent import AgentSpec
    from app.phase1_schemas import EvaluationSpec

    marker = tmp_path / "recovery-marker"
    marker.write_text("started\n", encoding="utf-8")
    monkeypatch.setattr(recovery_agent, "MARKER", marker)
    events = []
    session = EnvironmentSession(EvaluationSpec(
        run_id="recovered-run", experiment_id="recovery-experiment",
        scenario_id="checkout-api-latency.v2", task="Investigate checkout API latency",
    ), "a" * 64, lambda kind, payload: events.append((kind, payload)))
    calls = []

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        async def call_tool(self, name, arguments):
            calls.append(name)
            result = await session.dispatch({"name": name, "arguments": arguments})
            return result

    monkeypatch.setattr(recovery_agent, "MCPAgentClient", Client)
    spec = AgentSpec("Investigate checkout API latency", "checkout-api-latency.v2", 6, 60_000, False, {})
    assert await recovery_agent.run(spec) == 0
    assert capsys.readouterr().out == ""  # Agent never fabricates trusted stdout.
    assert calls == ["check_service_health", "query_service_metrics", "fetch_service_logs", "submit_result"]
    world = [payload for _, payload in events if payload["environment"]["kind"] == "world"]
    trajectory = {"status": "success", "steps": [
        {"index": payload["index"], "action": payload["tool_call"], "observation": payload["observation"]}
        for payload in world
    ]}
    report = analyze_trajectory(trajectory, assertion_suite=assertion_suite_for_scenario("checkout-api-latency.v2"))
    assert "planning" not in report.dimensions
    assert events[-1][1]["environment"]["final_check"]["scenario_id"] == "checkout-api-latency.v2"


async def create_run(client: AsyncClient) -> dict:
    experiment = await client.post(
        "/api/experiments",
        json={
            "name": "Runner recovery",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency.v2",
        },
    )
    assert experiment.status_code == 201
    run = await client.post(f"/api/experiments/{experiment.json()['id']}/runs", json={})
    assert run.status_code == 201
    return run.json()


async def claim(client: AsyncClient, runner_id: str) -> dict:
    response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": runner_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def expire_lease(factory, run_id: str) -> None:
    async with factory() as db:
        job = await db.get(RunnerJob, run_id)
        assert job is not None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()


def event(run_id: str, sequence: int, event_type: str, payload: dict | None = None) -> dict:
    payload = {"attempt": 1, **(payload or {})}
    if event_type == "run_started":
        payload["source"] = "environment"
    if event_type == "step_completed":
        payload["environment"] = {"kind": "world", "result": "ok"}
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": utcnow().isoformat(),
        "type": event_type,
        "payload": payload or {},
    }


async def upload(client: AsyncClient, claim_data: dict, events: list[dict]) -> None:
    for item in events:
        item["payload"]["attempt"] = claim_data["attempt"]
    response = await client.post(
        f"/api/internal/runner/runs/{claim_data['run']['run_id']}/events",
        headers=AUTH,
        json={
            "runner_id": claim_data["runner_id"],
            "lease_id": claim_data["lease_id"],
            "events": events,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_old_lease_is_fenced(api) -> None:
    client, factory = api
    run = await create_run(client)

    first = await claim(client, "runner-1")
    first["runner_id"] = "runner-1"
    assert first["attempt"] == 1
    assert first["next_sequence"] == 1
    await upload(client, first, [event(run["id"], 1, "run_started")])
    await expire_lease(factory, run["id"])

    replacement = await claim(client, "runner-2")
    replacement["runner_id"] = "runner-2"
    assert replacement["lease_id"] != first["lease_id"]
    assert replacement["attempt"] == 2
    assert replacement["next_sequence"] == 2
    assert "lease expired" in replacement["recovery_reason"]

    stale_event = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": first["lease_id"],
            "events": [event(run["id"], 2, "process_output")],
        },
    )
    assert stale_event.status_code == 403
    assert stale_event.json()["code"] == "INVALID_LEASE"

    await upload(
        client,
        replacement,
        [
            event(run["id"], 2, "run_started", {"attempt": 2}),
            event(
                run["id"],
                3,
                "step_completed",
                {
                    "index": 0,
                    "tool_call": {"name": "check_service_health", "arguments": {}},
                    "observation": "Service is healthy.",
                },
            ),
        ],
    )
    completed = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-2", "status": "succeeded", "execution": facts(run["id"], replacement["attempt"])},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "failed"  # No accepted final check or complete suite.
    assert completed.json()["run"]["metrics"]["steps"] == 1

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        rows = list(
            (
                await db.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run["id"])
                    .order_by(RunEvent.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert job is not None
        assert job.attempt == 2
        assert job.runner_id == "runner-2"
        assert [row.sequence for row in rows] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_cancel_intent_survives_runner_recovery(api) -> None:
    client, factory = api
    run = await create_run(client)
    first = await claim(client, "runner-1")

    heartbeat = await client.post(
        f"/api/internal/runner/jobs/{first['lease_id']}/heartbeat",
        headers=AUTH,
        json={"runner_id": "runner-1"},
    )
    assert heartbeat.status_code == 200
    cancelling = await client.post(f"/api/runs/{run['id']}/cancel")
    assert cancelling.status_code == 200
    assert cancelling.json()["status"] == "cancelling"
    await expire_lease(factory, run["id"])

    replacement = await claim(client, "runner-2")
    cancel_command = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/heartbeat",
        headers=AUTH,
        json={"runner_id": "runner-2"},
    )
    assert cancel_command.status_code == 200
    assert cancel_command.json()["command"] == "cancel"

    completed = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-2", "status": "succeeded", "execution": facts(run["id"], replacement["attempt"])},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_expired_lease_exhaustion_marks_run_terminal(api) -> None:
    client, factory = api
    run = await create_run(client)

    for attempt, runner_id in enumerate(("runner-1", "runner-2", "runner-3"), start=1):
        claimed = await claim(client, runner_id)
        assert claimed["attempt"] == attempt
        await expire_lease(factory, run["id"])

    no_work = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "replacement"},
    )
    assert no_work.status_code == 204

    current = await client.get(f"/api/runs/{run['id']}")
    assert current.status_code == 200
    assert current.json()["status"] == "failed"
    assert "3 attempts" in current.json()["error"]

    analysis = await client.get(f"/api/runs/{run['id']}/analysis")
    assert analysis.status_code == 200

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        assert job is not None
        assert job.attempt == 3
        assert job.lease_id == claimed["lease_id"]
        assert job.runner_id == "runner-3"  # Retained for same-identity terminal ACK retry.
        attempts = list(
            (
                await db.execute(
                    select(RunnerAttempt)
                    .where(RunnerAttempt.run_id == run["id"])
                    .order_by(RunnerAttempt.attempt.asc())
                )
            )
            .scalars()
            .all()
        )
        assert [
            (item.attempt, item.runner_id, item.outcome)
            for item in attempts
        ] == [
            (1, "runner-1", "recovered"),
            (2, "runner-2", "recovered"),
            (3, "runner-3", "failed"),
        ]

    diagnostics = await client.get(f"/api/operations/runs/{run['id']}")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["metrics"]["lease_recoveries"] == 3
    assert [item["attempt"] for item in diagnostics.json()["attempts"]] == [1, 2, 3]

    overview = await client.get("/api/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["lease_recoveries"] == 3
