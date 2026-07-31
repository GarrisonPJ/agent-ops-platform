from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config


BACKEND_DIR = Path(__file__).parents[1]


def fingerprint(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def test_0006_sanitizes_legacy_provider_data(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "0005_runner_presence")

    raw_payload = {
        "stream": "stderr",
        "content": "Provider execution failed.",
        "provider": {
            "model": "legacy-model",
            "request_id": "req-event",
            "latency_ms": 25,
            "request_count": 1,
            "token_prompt": 3,
            "token_completion": 0,
            "endpoint": "https://provider.invalid/v1",
        },
        "provider_error": {
            "code": "PROVIDER_TIMEOUT",
            "message": "Provider request timed out",
            "retryable": True,
            "attempts": 2,
            "request_id": "req-error",
            "headers": {"authorization": "secret"},
        },
    }
    raw_metrics = {
        "event_retries": 2,
        "steps": 0,
        "latency_ms": 25,
        "token_prompt": 3,
        "token_completion": 0,
        "total_tokens": 3,
        "provider": {
            "model": "legacy-model",
            "latency_ms": 25,
            "request_count": 1,
            "token_prompt": 3,
            "token_completion": 0,
            "total_tokens": 3,
            "request_ids": ["req-event", "req-error"],
            "endpoint": "https://provider.invalid/v1",
        },
        "provider_error": raw_payload["provider_error"],
        "score_breakdown": {"success": 0.0},
        "request_id": "completion-secret",
        "credential": "drop me",
    }
    recorded_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO runs (
                id, experiment_id, kind, status, metrics, evaluation_spec, queued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-legacy",
                "experiment-legacy",
                "baseline",
                "failed",
                json.dumps(raw_metrics),
                json.dumps({"execution_mode": "provider"}),
                recorded_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO run_events (
                run_id, sequence, event_type, payload, occurred_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "run-legacy",
                1,
                "process_output",
                json.dumps(raw_payload),
                recorded_at,
            ),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM run_events WHERE run_id = ?",
                ("run-legacy",),
            ).fetchone()[0]
        )
        metrics = json.loads(
            connection.execute(
                "SELECT metrics FROM runs WHERE id = ?",
                ("run-legacy",),
            ).fetchone()[0]
        )
        attempt_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'runner_attempts'
            """
        ).fetchone()

    assert attempt_table == ("runner_attempts",)
    assert payload["provider"] == {
        "model": "legacy-model",
        "latency_ms": 25,
        "request_count": 1,
        "token_prompt": 3,
        "token_completion": 0,
        "request_fingerprint": fingerprint("req-event"),
    }
    assert payload["provider_error"] == {
        "code": "PROVIDER_TIMEOUT",
        "message": "Provider request timed out",
        "retryable": True,
        "attempts": 2,
        "request_fingerprint": fingerprint("req-error"),
    }
    assert metrics["provider"] == {
        "model": "legacy-model",
        "latency_ms": 25,
        "request_count": 1,
        "token_prompt": 3,
        "token_completion": 0,
        "total_tokens": 3,
        "request_fingerprints": [
            fingerprint("req-event"),
            fingerprint("req-error"),
        ],
    }
    assert metrics["provider_error"] == payload["provider_error"]
    assert "request_id" not in str(payload)
    assert "request_id" not in str(metrics)
    assert "provider.invalid" not in str(payload)
    assert "provider.invalid" not in str(metrics)
    assert "secret" not in str(metrics)
    assert "drop me" not in str(metrics)
