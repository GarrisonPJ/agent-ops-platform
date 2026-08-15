from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.database_recovery import (
    DatabaseManifest,
    RecoveryError,
    backup_database,
    compare_manifests,
    database_name,
    database_url_for,
    libpq_environment,
    normalize_postgres_url,
    validate_disposable_database_name,
)


DATABASE_URL = (
    "postgresql+asyncpg://agentops:p%40ss@localhost:5432/agentops"
    "?sslmode=require"
)


def test_database_url_is_parsed_without_exposing_credentials() -> None:
    normalized = normalize_postgres_url(DATABASE_URL)
    assert normalized.startswith("postgresql://")
    assert database_name(normalized) == "agentops"
    target = database_url_for(normalized, "agentops_restore_ci")
    assert database_name(target) == "agentops_restore_ci"

    environment = libpq_environment(DATABASE_URL)
    assert environment["PGHOST"] == "localhost"
    assert environment["PGPORT"] == "5432"
    assert environment["PGUSER"] == "agentops"
    assert environment["PGPASSWORD"] == "p@ss"
    assert environment["PGDATABASE"] == "agentops"
    assert environment["PGSSLMODE"] == "require"


@pytest.mark.parametrize(
    "target",
    [
        "agentops",
        "postgres",
        "agentops_restore_X",
        "restore_ci",
        "agentops_restore_a",
        "agentops_restore_" + "a" * 50,
    ],
)
def test_restore_target_must_be_distinct_and_disposable(target: str) -> None:
    with pytest.raises(RecoveryError):
        validate_disposable_database_name(target, "agentops")


def test_restore_target_accepts_scoped_name() -> None:
    validate_disposable_database_name("agentops_restore_ci", "agentops")
    validate_disposable_database_name("agentops_restore_ci_01", "agentops")


def test_backup_uses_libpq_environment_instead_of_command_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["environment"] = env
        output = Path(command[command.index("--file") + 1])
        output.write_bytes(b"custom dump")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.database_recovery.shutil.which", lambda _name: "/usr/bin/pg_dump")
    monkeypatch.setattr("app.database_recovery.subprocess.run", fake_run)
    output = tmp_path / "backup.dump"
    backup_database(DATABASE_URL, output, snapshot="snapshot-1")

    command = captured["command"]
    assert isinstance(command, list)
    assert "p@ss" not in " ".join(command)
    assert DATABASE_URL not in " ".join(command)
    assert command[-2:] == ["--snapshot", "snapshot-1"]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["PGPASSWORD"] == "p@ss"
    assert output.read_bytes() == b"custom dump"
    assert output.stat().st_mode & 0o777 == 0o600


def test_backup_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "backup.dump"
    output.write_bytes(b"existing backup")

    with pytest.raises(RecoveryError, match="already exists"):
        backup_database(DATABASE_URL, output)

    assert output.read_bytes() == b"existing backup"


def test_manifest_comparison_checks_counts_foreign_keys_and_trace() -> None:
    manifest = DatabaseManifest(
        revision="0006_runner_attempts",
        table_counts={"runs": 1, "run_events": 2},
        foreign_key_count=9,
        run_id="run-1",
        trace=[
            {"sequence": 1, "event_type": "run_started", "payload": {"attempt": 1}},
            {"sequence": 2, "event_type": "run_failed", "payload": {"status": "failed"}},
        ],
    )
    result = compare_manifests(manifest, manifest)
    assert result["schema_revision"] == "0006_runner_attempts"
    assert result["event_count"] == 2

    mismatched = DatabaseManifest(
        revision=manifest.revision,
        table_counts={"runs": 1, "run_events": 1},
        foreign_key_count=manifest.foreign_key_count,
        run_id=manifest.run_id,
        trace=manifest.trace,
    )
    with pytest.raises(RecoveryError, match="row counts"):
        compare_manifests(manifest, mismatched)
