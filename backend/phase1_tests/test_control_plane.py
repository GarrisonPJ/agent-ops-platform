from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_baseline(client: AsyncClient) -> tuple[dict, dict]:
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Checkout latency",
                "task": "Investigate checkout API latency",
                "scenario_id": "checkout-api-latency",
            },
        )
    ).json()
    response = await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    assert response.status_code == 201
    return experiment, response.json()


async def claim(client: AsyncClient, runner_id: str = "runner-1") -> dict:
    response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": runner_id},
    )
    assert response.status_code == 200
    return response.json()


def envelope(run_id: str, sequence: int, event_type: str, payload: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload or {},
    }


def step(run_id: str, sequence: int, index: int, tool: str, observation: str) -> dict:
    return envelope(
        run_id,
        sequence,
        "step_completed",
        {
            "index": index,
            "decision_summary": "Collect the next diagnostic signal.",
            "tool_call": {"name": tool, "arguments": {"service": "checkout-api"}},
            "observation": observation,
            "latency_ms": 25,
            "token_prompt": 50,
            "token_completion": 20,
            "context_window": {"used": 1000, "limit": 8192},
        },
    )


async def upload(client: AsyncClient, claim_data: dict, events: list[dict]) -> int:
    response = await client.post(
        f"/api/internal/runner/runs/{claim_data['run']['run_id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": claim_data["lease_id"],
            "events": events,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["accepted_through"]


@pytest.mark.asyncio
async def test_full_baseline_replay_activate_loop(api) -> None:
    client, _ = api
    experiment, baseline = await create_baseline(client)
    baseline_claim = await claim(client)
    assert baseline_claim["run"]["evaluation_spec"] == baseline["evaluation_spec"]

    baseline_events = [envelope(baseline["id"], 1, "run_started")]
    baseline_events.extend(
        step(
            baseline["id"],
            index + 2,
            index,
            "fetch_service_logs",
            "The same noisy samples are returned and the cause remains inconclusive.",
        )
        for index in range(6)
    )
    baseline_events.append(envelope(baseline["id"], 8, "run_failed", {"exit_code": 1}))
    assert await upload(client, baseline_claim, baseline_events) == 8

    completed = await client.post(
        f"/api/internal/runner/jobs/{baseline_claim['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "failed", "error": "step budget exhausted"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "failed"

    analysis = await client.get(f"/api/runs/{baseline['id']}/analysis")
    assert analysis.status_code == 200
    assert set(analysis.json()["dimensions"]) >= {"planning", "budget"}

    experiment_detail = (await client.get(f"/api/experiments/{experiment['id']}")).json()
    candidate = experiment_detail["candidate_policy"]
    assert candidate["status"] == "candidate"
    assert candidate["patch"]["instruction_patch"]

    replay_response = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert replay_response.status_code == 201
    replay_policy = replay_response.json()
    assert replay_policy["status"] == "replaying"
    assert replay_policy["replay_run_id"]

    replay_claim = await claim(client)
    replay_spec = replay_claim["run"]["evaluation_spec"]
    baseline_spec = baseline_claim["run"]["evaluation_spec"]
    for key in ("experiment_id", "scenario_id", "task", "seed", "limits"):
        assert replay_spec[key] == baseline_spec[key]
    assert baseline_spec["policy"] is None
    assert replay_spec["policy"] == candidate["patch"]

    replay_id = replay_claim["run"]["run_id"]
    replay_events = [
        envelope(replay_id, 1, "run_started"),
        step(replay_id, 2, 0, "check_service_health", "Service is healthy; latency is elevated."),
        step(replay_id, 3, 1, "query_service_metrics", "Payment dependency p95 is 1.8s."),
        step(replay_id, 4, 2, "fetch_service_logs", "Payment connection pool is saturated."),
        envelope(replay_id, 5, "run_completed", {"exit_code": 0}),
    ]
    assert await upload(client, replay_claim, replay_events) == 5
    replay_completed = await client.post(
        f"/api/internal/runner/jobs/{replay_claim['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "succeeded"},
    )
    assert replay_completed.status_code == 200
    assert replay_completed.json()["score"] > completed.json()["score"]

    validated = (await client.get(f"/api/experiments/{experiment['id']}")).json()[
        "candidate_policy"
    ]
    assert validated["status"] == "validated"
    assert validated["score_delta"] > 0
    activated = await client.post(f"/api/policies/{candidate['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    final_detail = (await client.get(f"/api/experiments/{experiment['id']}")).json()
    assert final_detail["active_policy"]["id"] == candidate["id"]
    assert final_detail["candidate_policy"] is None


@pytest.mark.asyncio
async def test_event_upload_is_idempotent_and_sse_replays_after_sequence(api) -> None:
    client, _ = api
    _, run = await create_baseline(client)
    job = await claim(client)
    events = [
        envelope(run["id"], 1, "run_started"),
        step(run["id"], 2, 0, "fetch_service_logs", "No conclusive signal."),
    ]
    assert await upload(client, job, events) == 2
    assert await upload(client, job, events) == 2
    final_events = [
        step(run["id"], index + 3, index + 1, "fetch_service_logs", "No conclusive signal.")
        for index in range(5)
    ]
    final_events.append(envelope(run["id"], 8, "run_failed"))
    assert await upload(client, job, final_events) == 8
    await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "failed"},
    )
    stream = await client.get(f"/api/runs/{run['id']}/stream?after=6")
    assert stream.status_code == 200
    assert '"sequence":7' in stream.text
    assert '"sequence":8' in stream.text
    assert '"sequence":6' not in stream.text


@pytest.mark.asyncio
async def test_runner_auth_claim_and_queued_cancel(api) -> None:
    client, _ = api
    unauthorized = await client.post(
        "/api/internal/runner/jobs/claim", json={"runner_id": "runner-1"}
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "RUNNER_UNAUTHORIZED"

    _, run = await create_baseline(client)
    cancelled = await client.post(f"/api/runs/{run['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    empty = await client.post(
        "/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "runner-1"}
    )
    assert empty.status_code == 204


@pytest.mark.asyncio
async def test_sequence_gap_and_conflict_are_rejected(api) -> None:
    client, _ = api
    _, run = await create_baseline(client)
    job = await claim(client)
    gap = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": job["lease_id"],
            "events": [envelope(run["id"], 2, "run_started")],
        },
    )
    assert gap.status_code == 409
    assert gap.json()["code"] == "EVENT_SEQUENCE_GAP"
    assert await upload(client, job, [envelope(run["id"], 1, "run_started")]) == 1
    conflict = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": job["lease_id"],
            "events": [envelope(run["id"], 1, "process_output", {"content": "different"})],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "EVENT_CONFLICT"
