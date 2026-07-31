"""Seed one Run trace and verify a disposable PostgreSQL backup restoration."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg

from app.database_recovery import (
    RecoveryError,
    normalize_postgres_url,
    rehearse_restore,
)


async def seed_run_trace(database_url: str) -> tuple[str, str]:
    experiment_id = str(uuid4())
    run_id = str(uuid4())
    recorded_at = datetime.now(timezone.utc)
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO experiments (
                    id, name, task, scenario_id, execution_mode, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                experiment_id,
                "Backup restore rehearsal",
                "Verify a restored Run trace",
                "checkout-api-latency",
                "fixture",
                recorded_at,
            )
            await connection.execute(
                """
                INSERT INTO runs (
                    id, experiment_id, kind, status, metrics, evaluation_spec,
                    error, queued_at, claimed_at, started_at, completed_at
                ) VALUES (
                    $1, $2, $3, $4, $5::json, $6::json,
                    $7, $8, $8, $8, $8
                )
                """,
                run_id,
                experiment_id,
                "baseline",
                "failed",
                json.dumps({"event_retries": 0}),
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": run_id,
                        "experiment_id": experiment_id,
                        "scenario_id": "checkout-api-latency",
                        "task": "Verify a restored Run trace",
                        "seed": 42,
                        "execution_mode": "fixture",
                        "policy": None,
                        "limits": {
                            "timeout_ms": 60_000,
                            "max_output_bytes": 1_048_576,
                        },
                    }
                ),
                "recovery rehearsal fixture",
                recorded_at,
            )
            await connection.execute(
                """
                INSERT INTO runner_jobs (run_id, attempt)
                VALUES ($1, 1)
                """,
                run_id,
            )
            await connection.executemany(
                """
                INSERT INTO run_events (
                    run_id, sequence, event_type, payload, occurred_at
                ) VALUES ($1, $2, $3, $4::json, $5)
                """,
                [
                    (
                        run_id,
                        1,
                        "run_started",
                        json.dumps({"attempt": 1}),
                        recorded_at,
                    ),
                    (
                        run_id,
                        2,
                        "run_failed",
                        json.dumps({"attempt": 1, "status": "failed"}),
                        recorded_at,
                    ),
                ],
            )
    finally:
        await connection.close()
    return experiment_id, run_id


async def remove_fixture(database_url: str, experiment_id: str) -> None:
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            "DELETE FROM experiments WHERE id = $1", experiment_id
        )
    finally:
        await connection.close()


async def run() -> dict[str, object]:
    source_value = os.getenv("DATABASE_URL")
    target_name = os.getenv("RESTORE_DATABASE_NAME")
    if not source_value:
        raise RecoveryError("DATABASE_URL is required")
    if not target_name:
        raise RecoveryError("RESTORE_DATABASE_NAME is required")
    source_url = normalize_postgres_url(source_value)
    experiment_id, run_id = await seed_run_trace(source_url)
    try:
        result = await rehearse_restore(source_url, target_name, run_id=run_id)
    finally:
        await remove_fixture(source_url, experiment_id)
    if result["event_count"] != 2:
        raise RecoveryError("restored fixture trace has an unexpected event count")
    return result


def main() -> None:
    result = asyncio.run(run())
    print(json.dumps({"status": "ok", **result}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
