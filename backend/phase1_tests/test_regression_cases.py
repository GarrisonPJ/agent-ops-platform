from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.phase1_service import _is_regression_case_source_unique_violation
from app.phase1_models import RegressionCase


AUTH = {"Authorization": "Bearer test-runner-token"}


def envelope(run_id: str, sequence: int, event_type: str, payload: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload or {},
    }


def step(run_id: str, sequence: int, index: int, tool: str, arguments: dict) -> dict:
    return envelope(
        run_id,
        sequence,
        "step_completed",
        {
            "index": index,
            "decision_summary": "Collect the next signal.",
            "tool_call": {"name": tool, "arguments": arguments},
            "observation": "The requested signal was recorded.",
            "latency_ms": 25,
            "token_prompt": 10,
            "token_completion": 5,
            "context_window": {"used": 100, "limit": 8192},
        },
    )


async def create_run(client: AsyncClient, *, execution_mode: str = "fixture") -> tuple[dict, dict]:
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Regression cases",
                "task": "Investigate checkout API latency",
                "scenario_id": "checkout-api-latency.v1",
                "execution_mode": execution_mode,
            },
        )
    ).json()
    run = (await client.post(f"/api/experiments/{experiment['id']}/runs", json={})).json()
    return experiment, run


async def claim(client: AsyncClient, runner_id: str = "runner-1") -> dict:
    return (
        await client.post(
            "/api/internal/runner/jobs/claim",
            headers=AUTH,
            json={"runner_id": runner_id},
        )
    ).json()


async def complete(client: AsyncClient, claimed: dict, status: str) -> dict:
    response = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": status},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def upload(client: AsyncClient, claimed: dict, events: list[dict]) -> None:
    response = await client.post(
        f"/api/internal/runner/runs/{claimed['run']['run_id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-1",
            "lease_id": claimed["lease_id"],
            "events": events,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_failed_fixture_run_can_be_saved_idempotently(api) -> None:
    client, _ = api
    experiment, run = await create_run(client)
    claimed = await claim(client)
    await complete(client, claimed, "failed")

    first = await client.post(f"/api/runs/{run['id']}/regression-case")
    second = await client.post(f"/api/runs/{run['id']}/regression-case")

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["reproducibility"] == "deterministic"
    assert first.json()["eligible_for_replay"] is True
    detail = (await client.get(f"/api/experiments/{experiment['id']}"))
    assert detail.json()["regression_cases"][0]["source_run_id"] == run["id"]
    assert detail.json()["runs"][0]["regression_case_id"] == first.json()["id"]
    listed = await client.get(f"/api/experiments/{experiment['id']}/regression-cases")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [first.json()["id"]]


@pytest.mark.asyncio
async def test_concurrent_regression_case_creation_returns_the_same_case(api, monkeypatch) -> None:
    client, _ = api
    _, run = await create_run(client)
    await complete(client, await claim(client), "failed")

    original_commit = AsyncSession.commit
    commits_reached = 0
    all_commits_reached = asyncio.Barrier(2)
    first_commit_done = asyncio.Event()

    async def commit_after_both_regression_cases_reach_commit(session: AsyncSession) -> None:
        nonlocal commits_reached
        if any(isinstance(item, RegressionCase) for item in session.new):
            commits_reached += 1
            commit_number = commits_reached
            await asyncio.wait_for(all_commits_reached.wait(), timeout=5)
            if commit_number == 1:
                await original_commit(session)
                first_commit_done.set()
                return
            await asyncio.wait_for(first_commit_done.wait(), timeout=5)
            raise IntegrityError(
                "INSERT INTO regression_cases",
                {},
                sqlite3.IntegrityError(
                    "UNIQUE constraint failed: regression_cases.source_run_id"
                ),
            )
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_after_both_regression_cases_reach_commit)

    responses = await asyncio.wait_for(
        asyncio.gather(
            client.post(f"/api/runs/{run['id']}/regression-case"),
            client.post(f"/api/runs/{run['id']}/regression-case"),
        ),
        timeout=5,
    )

    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    detail = await client.get(f"/api/experiments/{run['experiment_id']}")
    assert detail.json()["runs"][0]["regression_case_id"] == responses[0].json()["id"]
    listed = await client.get(f"/api/experiments/{run['experiment_id']}/regression-cases")
    assert [item["id"] for item in listed.json()] == [responses[0].json()["id"]]


def test_source_unique_conflict_detection_handles_wrapped_asyncpg_error() -> None:
    native_error = Exception(
        'duplicate key value violates unique constraint "uq_regression_cases_source_run"'
    )
    native_error.constraint_name = "uq_regression_cases_source_run"
    adapter_error = Exception("asyncpg adapter error")
    adapter_error.__cause__ = native_error
    error = IntegrityError("INSERT INTO regression_cases", {}, adapter_error)

    assert _is_regression_case_source_unique_violation(error) is True


@pytest.mark.asyncio
async def test_regression_case_creation_does_not_swallow_other_integrity_errors(
    api, monkeypatch
) -> None:
    client, _ = api
    _, run = await create_run(client)
    await complete(client, await claim(client), "failed")

    original_commit = AsyncSession.commit

    async def commit_with_unrelated_conflict(session: AsyncSession) -> None:
        if any(isinstance(item, RegressionCase) for item in session.new):
            raise IntegrityError(
                "INSERT INTO regression_cases",
                {},
                sqlite3.IntegrityError("UNIQUE constraint failed: regression_cases.id"),
            )
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_with_unrelated_conflict)

    with pytest.raises(IntegrityError, match="regression_cases.id"):
        await client.post(f"/api/runs/{run['id']}/regression-case")


@pytest.mark.asyncio
async def test_regression_case_rejects_non_terminal_and_successful_runs(api) -> None:
    client, _ = api
    _, run = await create_run(client)
    pending = await client.post(f"/api/runs/{run['id']}/regression-case")
    assert pending.status_code == 409
    assert pending.json()["code"] == "REGRESSION_CASE_SOURCE_NOT_ELIGIBLE"

    claimed = await claim(client)
    await complete(client, claimed, "succeeded")
    succeeded = await client.post(f"/api/runs/{run['id']}/regression-case")
    assert succeeded.status_code == 409


@pytest.mark.asyncio
async def test_provider_case_records_non_reproducible_boundary(api) -> None:
    client, _ = api
    _, run = await create_run(client, execution_mode="provider")
    await complete(client, await claim(client), "failed")

    response = await client.post(f"/api/runs/{run['id']}/regression-case")
    assert response.status_code == 201
    assert response.json()["reproducibility"] == "provider_boundary"
    assert response.json()["eligible_for_replay"] is False
    assert "cannot be completely reproduced" in response.json()["reproducibility_note"]


@pytest.mark.asyncio
async def test_activation_gates_on_regression_replay_without_overwriting_policy(api) -> None:
    client, _ = api
    experiment, baseline = await create_run(client)
    baseline_claim = await claim(client)
    await complete(client, baseline_claim, "failed")
    case = (await client.post(f"/api/runs/{baseline['id']}/regression-case")).json()

    detail = (await client.get(f"/api/experiments/{experiment['id']}"))
    policy = detail.json()["candidate_policy"]
    main_replay = (await client.post(f"/api/policies/{policy['id']}/replay")).json()
    main_claim = await claim(client)
    replay_id = main_claim["run"]["run_id"]
    await upload(
        client,
        main_claim,
        [
            envelope(replay_id, 1, "run_started"),
            step(replay_id, 2, 0, "check_service_health", {"service": "checkout-api"}),
            step(replay_id, 3, 1, "query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
            step(replay_id, 4, 2, "fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
            envelope(replay_id, 5, "run_completed", {"exit_code": 0}),
        ],
    )
    await complete(client, main_claim, "succeeded")
    updated_policy = (
        await client.get(f"/api/experiments/{experiment['id']}")
    ).json()["candidate_policy"]
    assert updated_policy["status"] == "validated"
    assert updated_policy["score_delta"] > 0

    activation = await client.post(f"/api/policies/{policy['id']}/activate")
    assert activation.status_code == 409
    assert activation.json()["code"] == "REGRESSION_REPLAY_PENDING"
    assert activation.json()["details"]["case_ids"] == [case["id"]]

    after_pending = (
        await client.get(f"/api/experiments/{experiment['id']}")
    ).json()
    pending_policy = after_pending["candidate_policy"]
    assert pending_policy["status"] == "validated"
    regression_run_id = after_pending["regression_cases"][0]["replay_run_id"]
    assert regression_run_id != main_replay["replay_run_id"]
    regression_claim = await claim(client)
    assert regression_claim["run"]["run_id"] == regression_run_id
    await complete(client, regression_claim, "succeeded")

    after_regression = (
        await client.get(f"/api/experiments/{experiment['id']}")
    ).json()["candidate_policy"]
    assert after_regression["status"] == "validated"
    assert after_regression["score_delta"] == pytest.approx(updated_policy["score_delta"])

    activated = await client.post(f"/api/policies/{policy['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["score_delta"] == pytest.approx(updated_policy["score_delta"])
