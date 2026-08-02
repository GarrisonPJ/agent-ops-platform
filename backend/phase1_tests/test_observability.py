from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.phase1_models import Run, RunEvent, RunnerJob, utcnow
from app.phase1_schemas import (
    MAX_PROVIDER_LATENCY_MS,
    MAX_PROVIDER_REQUEST_COUNT,
    MAX_PROVIDER_TOKENS,
)


AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_baseline(client: AsyncClient) -> tuple[dict, dict]:
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "Observability checkout",
                "task": "Investigate checkout API latency",
                "scenario_id": "checkout-api-latency",
                "execution_mode": "provider",
            },
        )
    ).json()
    response = await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    assert response.status_code == 201
    return experiment, response.json()


async def claim(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-observer"},
    )
    assert response.status_code == 200
    return response.json()


def fingerprint(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def event(run_id: str, sequence: int, event_type: str, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_run_diagnostics_projects_durable_correlation_and_metrics(api) -> None:
    client, factory = api
    experiment, run = await create_baseline(client)
    job = await claim(client)
    events = [
        event(run["id"], 1, "run_started", {"attempt": 1}),
        event(
            run["id"],
            2,
            "process_output",
            {
                "stream": "stdout",
                "content": "Provider request completed.",
                "provider": {
                    "model": "observability-model",
                    "request_id": "req-provider-123",
                    "latency_ms": 125,
                    "request_count": 2,
                    "token_prompt": 31,
                    "token_completion": 12,
                    "unexpected": "provider metadata must not persist",
                },
            },
        ),
        event(run["id"], 3, "run_failed", {"attempt": 1, "status": "failed"}),
    ]
    uploaded = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={"runner_id": "runner-observer", "lease_id": job["lease_id"], "events": events},
    )
    assert uploaded.status_code == 200
    completed = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "status": "failed",
            "error": "provider failed",
            "metrics": {
                "event_retries": 2,
                "request_id": "completion-secret",
                "provider": {"request_id": "completion-provider-secret"},
                "unexpected": {"credential": "must be dropped"},
            },
        },
    )
    assert completed.status_code == 200

    now = datetime.now(timezone.utc).replace(microsecond=0)
    async with factory() as db:
        persisted = await db.get(Run, run["id"])
        assert persisted is not None
        persisted.queued_at = now - timedelta(seconds=9)
        persisted.claimed_at = now - timedelta(seconds=6)
        persisted.started_at = now - timedelta(seconds=4)
        persisted.completed_at = now
        persisted_event = (
            await db.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run["id"])
                .order_by(RunEvent.sequence.asc())
            )
        ).scalars().all()
        assert len(persisted_event) == 3
        assert persisted_event[1].payload["provider"] == {
            "model": "observability-model",
            "latency_ms": 125,
            "request_count": 2,
            "token_prompt": 31,
            "token_completion": 12,
            "request_fingerprint": fingerprint("req-provider-123"),
        }
        assert "unexpected" not in persisted_event[1].payload["provider"]
        assert "request_id" not in str(persisted_event[1].payload)
        assert persisted_event[1].payload["content"] == "Provider execution output redacted."
        assert persisted.metrics["event_retries"] == 2
        assert "completion-secret" not in str(persisted.metrics)
        assert "completion-provider-secret" not in str(persisted.metrics)
        assert "credential" not in str(persisted.metrics)
        await db.commit()

    response = await client.get(f"/api/operations/runs/{run['id']}")
    assert response.status_code == 200
    diagnostic = response.json()
    assert diagnostic["run"] == {
        "experiment_id": experiment["id"],
        "run_id": run["id"],
        "execution_mode": "provider",
        "status": "failed",
    }
    assert diagnostic["job"] == {
        "run_id": run["id"],
        "lease_id": job["lease_id"],
        "runner_id": "runner-observer",
        "attempt": 1,
        "recovery_reason": None,
    }
    assert diagnostic["provider"] == {
        "model": "observability-model",
        "request_fingerprints": [fingerprint("req-provider-123")],
        "error": None,
    }
    assert diagnostic["timing"] == {
        "queue_latency_ms": 3000,
        "run_duration_ms": 4000,
    }
    assert diagnostic["metrics"] == {
        "event_count": 3,
        "event_retries": 2,
        "lease_recoveries": 0,
        "provider_latency_ms": 125,
        "provider_tokens": 43,
    }
    assert diagnostic["terminal"] == {"status": "failed", "error": "provider failed"}


@pytest.mark.asyncio
async def test_retry_of_legacy_provider_event_is_idempotent_and_sanitized(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    job = await claim(client)
    raw_payload = {
        "stream": "stderr",
        "content": "Provider execution failed.",
        "provider": {
            "model": "legacy-model",
            "request_id": "req-legacy",
            "latency_ms": 25,
            "request_count": 1,
            "token_prompt": 3,
            "token_completion": 0,
            "unexpected": "drop me",
        },
        "provider_error": {
            "code": "PROVIDER_TIMEOUT",
            "message": "Provider request timed out",
            "retryable": True,
            "attempts": 2,
            "request_id": "req-legacy-error",
            "credential": "drop me",
        },
    }
    async with factory() as db:
        db.add(
            RunEvent(
                run_id=run["id"],
                sequence=1,
                event_type="process_output",
                payload=raw_payload,
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    for _ in range(2):
        retried = await client.post(
            f"/api/internal/runner/runs/{run['id']}/events",
            headers=AUTH,
            json={
                "runner_id": "runner-observer",
                "lease_id": job["lease_id"],
                "events": [event(run["id"], 1, "process_output", raw_payload)],
            },
        )
        assert retried.status_code == 200
        assert retried.json()["accepted_through"] == 1

    async with factory() as db:
        persisted = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run["id"]))
        ).scalar_one()
        assert persisted.payload["provider"] == {
            "model": "legacy-model",
            "latency_ms": 25,
            "request_count": 1,
            "token_prompt": 3,
            "token_completion": 0,
            "request_fingerprint": fingerprint("req-legacy"),
        }
        assert persisted.payload["provider_error"] == {
            "code": "PROVIDER_TIMEOUT",
            "message": "Provider request timed out",
            "retryable": True,
            "attempts": 2,
            "request_fingerprint": fingerprint("req-legacy-error"),
        }
        assert "request_id" not in str(persisted.payload)
        assert "drop me" not in str(persisted.payload)


@pytest.mark.asyncio
async def test_provider_persistence_uses_fixed_safe_diagnostics(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    job = await claim(client)
    malicious = {
        "stream": "stderr",
        "content": (
            "endpoint=https://provider.invalid/v1/chat/completions "
            "headers={'Authorization': 'Bearer raw-secret'} "
            "raw-content=UNIQUE_RAW_PROVIDER_CONTENT "
            "hidden_reasoning=UNIQUE_HIDDEN_REASONING"
        ),
        "endpoint": "https://provider.invalid/v1/chat/completions",
        "headers": {"Authorization": "Bearer raw-secret"},
        "hidden_reasoning": "UNIQUE_HIDDEN_REASONING",
        "provider": {
            "model": "safe-model",
            "latency_ms": 11,
            "request_count": 1,
            "token_prompt": 4,
            "token_completion": 5,
            "request_id": "raw-request-id",
            "raw_content": "UNIQUE_RAW_PROVIDER_CONTENT",
        },
        "provider_error": {
            "code": "PROVIDER_UNKNOWN",
            "message": "https://provider.invalid/v1?api_key=raw-secret UNIQUE_ERROR_MESSAGE",
            "retryable": True,
            "attempts": 2,
            "request_id": "raw-error-request-id",
            "raw_headers": {"Authorization": "Bearer raw-secret"},
        },
    }
    uploaded = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": job["lease_id"],
            "events": [event(run["id"], 1, "process_output", malicious)],
        },
    )
    assert uploaded.status_code == 200
    completed = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "status": "failed",
            "error": "agent failed",
        },
    )
    assert completed.status_code == 200

    async with factory() as db:
        persisted = await db.get(Run, run["id"])
        assert persisted is not None
        persisted_event = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run["id"]))
        ).scalar_one()
        durable = str(persisted_event.payload) + str(persisted.metrics)
        assert persisted_event.payload == {
            "stream": "stderr",
            "content": "Provider execution output redacted.",
            "provider": {
                "model": "safe-model",
                "latency_ms": 11,
                "request_count": 1,
                "token_prompt": 4,
                "token_completion": 5,
                "request_fingerprint": fingerprint("raw-request-id"),
            },
            "provider_error": {
                "code": "PROVIDER_UNKNOWN",
                "message": "Provider request failed",
                "retryable": True,
                "attempts": 2,
                "request_fingerprint": fingerprint("raw-error-request-id"),
            },
        }
        assert "provider.invalid" not in durable
        assert "raw-secret" not in durable
        assert "UNIQUE_RAW_PROVIDER_CONTENT" not in durable
        assert "UNIQUE_HIDDEN_REASONING" not in durable
        assert "UNIQUE_ERROR_MESSAGE" not in durable
        assert "raw-request-id" not in durable
        assert "raw-error-request-id" not in durable
        assert persisted.metrics["provider_error"]["message"] == "Provider request failed"


@pytest.mark.asyncio
async def test_malformed_provider_metadata_cannot_preserve_raw_output(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    job = await claim(client)
    raw_marker = "UNIQUE_MALFORMED_PROVIDER_RAW_OUTPUT"
    payload = {
        "stream": "stdout",
        "content": raw_marker,
        "endpoint": "https://provider.invalid/v1",
        "headers": {"Authorization": "Bearer malformed-secret"},
        "hidden_reasoning": "UNIQUE_MALFORMED_PROVIDER_REASONING",
        "provider": {},
        "provider_error": {"code": "PROVIDER_TIMEOUT"},
    }
    uploaded = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": job["lease_id"],
            "events": [event(run["id"], 1, "process_output", payload)],
        },
    )
    assert uploaded.status_code == 200

    async with factory() as db:
        persisted = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run["id"]))
        ).scalar_one()
        assert persisted.payload == {
            "stream": "stdout",
            "content": "Provider execution output redacted.",
        }
        durable = str(persisted.payload)
        assert raw_marker not in durable
        assert "provider.invalid" not in durable
        assert "malformed-secret" not in durable
        assert "UNIQUE_MALFORMED_PROVIDER_REASONING" not in durable


@pytest.mark.asyncio
async def test_non_provider_process_output_content_is_preserved(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    job = await claim(client)
    content = "ordinary process output with no Provider metadata"
    uploaded = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": job["lease_id"],
            "events": [event(run["id"], 1, "process_output", {"content": content})],
        },
    )
    assert uploaded.status_code == 200

    async with factory() as db:
        persisted = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run["id"]))
        ).scalar_one()
        assert persisted.payload == {"content": content}


@pytest.mark.asyncio
async def test_provider_telemetry_aggregation_saturates_at_schema_limits(api) -> None:
    client, _ = api
    _, run = await create_baseline(client)
    job = await claim(client)
    events = [event(run["id"], 1, "run_started", {"attempt": 1})]
    for sequence in (2, 3):
        events.append(
            event(
                run["id"],
                sequence,
                "process_output",
                {
                    "stream": "stdout",
                    "content": "Provider request completed.",
                    "provider": {
                        "model": "boundary-model",
                        "request_id": f"req-boundary-{sequence}",
                        "latency_ms": 50_000_000,
                        "request_count": 600,
                        "token_prompt": 600_000_000,
                        "token_completion": 600_000_000,
                    },
                },
            )
        )
    events.append(event(run["id"], 4, "run_failed", {"attempt": 1, "status": "failed"}))

    uploaded = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={"runner_id": "runner-observer", "lease_id": job["lease_id"], "events": events},
    )
    assert uploaded.status_code == 200
    completed = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "status": "failed",
            "error": "provider failed",
        },
    )
    assert completed.status_code == 200
    assert completed.json()["metrics"]["provider"] == {
        "model": "boundary-model",
        "latency_ms": MAX_PROVIDER_LATENCY_MS,
        "request_count": MAX_PROVIDER_REQUEST_COUNT,
        "token_prompt": MAX_PROVIDER_TOKENS,
        "token_completion": MAX_PROVIDER_TOKENS,
        "total_tokens": MAX_PROVIDER_TOKENS * 2,
        "request_fingerprints": [
            fingerprint("req-boundary-2"),
            fingerprint("req-boundary-3"),
        ],
    }

    diagnostics = await client.get(f"/api/operations/runs/{run['id']}")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["metrics"]["provider_latency_ms"] == MAX_PROVIDER_LATENCY_MS
    assert diagnostics.json()["metrics"]["provider_tokens"] == MAX_PROVIDER_TOKENS * 2


@pytest.mark.asyncio
async def test_operations_overview_reports_queue_and_terminal_signals(api) -> None:
    client, _ = api
    _, completed_run = await create_baseline(client)
    job = await claim(client)
    uploaded = await client.post(
        f"/api/internal/runner/runs/{completed_run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": job["lease_id"],
            "events": [event(completed_run["id"], 1, "run_started", {"attempt": 1})],
        },
    )
    assert uploaded.status_code == 200
    complete = await client.post(
        f"/api/internal/runner/jobs/{job['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "status": "failed",
            "error": "agent failed",
            "metrics": {"event_retries": 3},
        },
    )
    assert complete.status_code == 200
    _, queued_run = await create_baseline(client)

    response = await client.get("/api/operations/overview")
    assert response.status_code == 200
    overview = response.json()
    assert overview["queue_depth"] == 1
    assert overview["runs_by_status"] == {"failed": 1, "queued": 1}
    assert overview["terminal_outcomes"] == {"failed": 1}
    assert overview["expired_lease_count"] == 0
    assert overview["lease_recoveries"] == 0
    assert overview["event_retries"] == 3
    assert queued_run["status"] == "queued"


@pytest.mark.asyncio
async def test_run_diagnostics_uses_only_the_latest_recovered_attempt(api) -> None:
    client, factory = api
    _, run = await create_baseline(client)
    first = await claim(client)
    first_upload = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-observer",
            "lease_id": first["lease_id"],
            "events": [
                event(run["id"], 1, "run_started", {"attempt": 1}),
                event(
                    run["id"],
                    2,
                    "process_output",
                    {
                        "stream": "stdout",
                        "content": "Provider request completed.",
                        "provider": {
                            "model": "observability-model",
                            "request_id": "req-abandoned-attempt",
                            "latency_ms": 10,
                            "request_count": 1,
                            "token_prompt": 1,
                            "token_completion": 1,
                        },
                    },
                ),
            ],
        },
    )
    assert first_upload.status_code == 200

    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        assert job is not None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    replacement_response = await client.post(
        "/api/internal/runner/jobs/claim",
        headers=AUTH,
        json={"runner_id": "runner-replacement"},
    )
    assert replacement_response.status_code == 200
    replacement = replacement_response.json()
    assert replacement["attempt"] == 2
    assert replacement["next_sequence"] == 3
    replacement_upload = await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-replacement",
            "lease_id": replacement["lease_id"],
            "events": [
                event(run["id"], 3, "run_started", {"attempt": 2}),
                event(
                    run["id"],
                    4,
                    "process_output",
                    {
                        "stream": "stdout",
                        "content": "Provider request completed.",
                        "provider": {
                            "model": "observability-model",
                            "request_id": "req-current-attempt",
                            "latency_ms": 20,
                            "request_count": 1,
                            "token_prompt": 2,
                            "token_completion": 3,
                        },
                    },
                ),
                event(run["id"], 5, "run_failed", {"attempt": 2, "status": "failed"}),
            ],
        },
    )
    assert replacement_upload.status_code == 200
    completed = await client.post(
        f"/api/internal/runner/jobs/{replacement['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-replacement",
            "status": "failed",
            "error": "provider failed",
            "metrics": {"event_retries": 0},
        },
    )
    assert completed.status_code == 200

    response = await client.get(f"/api/operations/runs/{run['id']}")
    assert response.status_code == 200
    diagnostic = response.json()
    assert diagnostic["job"]["attempt"] == 2
    assert diagnostic["metrics"]["lease_recoveries"] == 1
    assert len(diagnostic["attempts"]) == 1
    assert diagnostic["attempts"][0]["attempt"] == 1
    assert diagnostic["attempts"][0]["runner_id"] == "runner-observer"
    assert diagnostic["attempts"][0]["outcome"] == "recovered"
    assert diagnostic["provider"]["request_fingerprints"] == [
        fingerprint("req-current-attempt")
    ]
    assert diagnostic["metrics"]["provider_latency_ms"] == 20
    assert diagnostic["metrics"]["provider_tokens"] == 5
