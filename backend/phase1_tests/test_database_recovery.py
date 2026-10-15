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
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["environment"] = env
        captured["timeout"] = timeout
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
    assert captured["timeout"] == 120
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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "collision", "restore", "consumer"])
async def test_restored_backup_only_drops_owned_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from app import database_recovery

    operations = []

    async def create(_admin_url, target):
        operations.append(("create", target))
        if failure == "collision":
            raise RecoveryError("restore target already exists")

    async def drop(_admin_url, target):
        operations.append(("drop", target))

    def restore(name, arguments, environment):
        assert name == "pg_restore"
        assert "--exit-on-error" in arguments
        assert environment["PGDATABASE"] == "agentops_restore_owned"
        operations.append(("restore", environment["PGDATABASE"]))
        if failure == "restore":
            raise RecoveryError("pg_restore failed")

    monkeypatch.setattr(database_recovery, "_create_database", create)
    monkeypatch.setattr(database_recovery, "_drop_database", drop)
    monkeypatch.setattr(database_recovery, "_run_tool", restore)

    async def exercise():
        async with database_recovery.restored_backup(
            DATABASE_URL, "agentops_restore_owned", tmp_path / "snapshot.dump"
        ) as target_url:
            assert database_name(target_url) == "agentops_restore_owned"
            assert target_url.startswith("postgresql://")
            operations.append(("consumer", "agentops_restore_owned"))
            if failure == "consumer":
                raise RecoveryError("report failed")

    if failure:
        with pytest.raises(RecoveryError):
            await exercise()
    else:
        await exercise()
    assert operations[0] == ("create", "agentops_restore_owned")
    if failure == "collision":
        assert operations == [("create", "agentops_restore_owned")]
    else:
        assert operations[-1] == ("drop", "agentops_restore_owned")


@pytest.mark.asyncio
async def test_restored_backup_rejects_target_before_database_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import database_recovery

    async def unexpected_create(*_args):
        pytest.fail("invalid restore target must not reach database creation")

    monkeypatch.setattr(database_recovery, "_create_database", unexpected_create)
    with pytest.raises(RecoveryError, match="differ"):
        async with database_recovery.restored_backup(
            DATABASE_URL, "agentops", tmp_path / "snapshot.dump"
        ):
            pytest.fail("invalid restore target must not yield")


def test_backup_timeout_removes_partial_file_without_exposing_tool_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import database_recovery

    monkeypatch.setattr(database_recovery.shutil, "which", lambda _name: "/usr/bin/pg_dump")

    def interrupted(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 120, output="sensitive tool output")

    monkeypatch.setattr(database_recovery.subprocess, "run", interrupted)
    output = tmp_path / "partial.dump"
    with pytest.raises(RecoveryError, match="pg_dump exceeded its timeout") as error:
        backup_database(DATABASE_URL, output)
    assert not output.exists()
    assert "sensitive" not in str(error.value)
