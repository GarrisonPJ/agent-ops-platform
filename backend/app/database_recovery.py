"""Safe PostgreSQL backup and disposable restore rehearsal commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

import asyncpg

from app.migrations import application_alembic_head


DISPOSABLE_DATABASE_PATTERN = re.compile(
    r"^agentops_restore_[a-z0-9][a-z0-9_]{1,44}$"
)
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
LIBPQ_QUERY_ENV = {
    "application_name": "PGAPPNAME",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
}


class RecoveryError(RuntimeError):
    """Raised when backup or restore safety and verification fails."""


@dataclass(frozen=True)
class DatabaseManifest:
    revision: str
    table_counts: dict[str, int]
    foreign_key_count: int
    run_id: str
    trace: list[dict[str, Any]]


def normalize_postgres_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise RecoveryError("DATABASE_URL must use a PostgreSQL scheme")
    if not parts.hostname or not parts.path.strip("/"):
        raise RecoveryError("DATABASE_URL must include a host and database name")
    return urlunsplit(("postgresql", parts.netloc, parts.path, parts.query, ""))


def database_name(value: str) -> str:
    name = unquote(urlsplit(normalize_postgres_url(value)).path.lstrip("/"))
    if not name or "/" in name:
        raise RecoveryError("DATABASE_URL contains an invalid database name")
    return name


def database_url_for(value: str, name: str) -> str:
    parts = urlsplit(normalize_postgres_url(value))
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/{quote(name, safe='')}", parts.query, "")
    )


def validate_disposable_database_name(name: str, source_name: str) -> None:
    if name == source_name:
        raise RecoveryError("restore target must differ from the source database")
    if not DISPOSABLE_DATABASE_PATTERN.fullmatch(name):
        raise RecoveryError(
            "restore target must match agentops_restore_[a-z0-9_]+ and be at most 63 characters"
        )


def libpq_environment(value: str) -> dict[str, str]:
    parts = urlsplit(normalize_postgres_url(value))
    environment = os.environ.copy()
    environment["PGHOST"] = parts.hostname or ""
    environment["PGDATABASE"] = database_name(value)
    if parts.port is not None:
        environment["PGPORT"] = str(parts.port)
    if parts.username is not None:
        environment["PGUSER"] = unquote(parts.username)
    if parts.password is not None:
        environment["PGPASSWORD"] = unquote(parts.password)
    for key, query_value in parse_qsl(parts.query, keep_blank_values=True):
        variable = LIBPQ_QUERY_ENV.get(key)
        if variable is not None:
            environment[variable] = query_value
    return environment


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RecoveryError(f"{name} is required but was not found on PATH")


def _run_tool(name: str, arguments: list[str], environment: dict[str, str]) -> None:
    _require_tool(name)
    result = subprocess.run(
        [name, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RecoveryError(f"{name} failed with exit code {result.returncode}")


def backup_database(value: str, output: Path, *, snapshot: str | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError as exc:
        raise RecoveryError(f"backup output already exists: {output}") from exc
    os.close(descriptor)
    arguments = [
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(output),
    ]
    if snapshot is not None:
        arguments.extend(["--snapshot", snapshot])
    try:
        _run_tool("pg_dump", arguments, libpq_environment(value))
        if not output.is_file() or output.stat().st_size == 0:
            raise RecoveryError("pg_dump did not create a non-empty backup")
        output.chmod(0o600)
    except Exception:
        output.unlink(missing_ok=True)
        raise


def _quoted_identifier(value: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise RecoveryError(f"unsafe PostgreSQL identifier: {value}")
    return f'"{value}"'


async def _create_database(admin_url: str, target_name: str) -> None:
    connection = await asyncpg.connect(admin_url)
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", target_name
        )
        if exists:
            raise RecoveryError(
                f"restore target already exists and will not be modified: {target_name}"
            )
        await connection.execute(f"CREATE DATABASE {_quoted_identifier(target_name)}")
    finally:
        await connection.close()


async def _drop_database(admin_url: str, target_name: str) -> None:
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(
            f"DROP DATABASE {_quoted_identifier(target_name)} WITH (FORCE)"
        )
    finally:
        await connection.close()


async def _table_counts(connection: asyncpg.Connection[Any]) -> dict[str, int]:
    rows = await connection.fetch(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
        """
    )
    counts: dict[str, int] = {}
    for row in rows:
        table_name = str(row["tablename"])
        counts[table_name] = int(
            await connection.fetchval(
                f"SELECT count(*) FROM {_quoted_identifier(table_name)}"
            )
        )
    return counts


async def _foreign_key_status(
    connection: asyncpg.Connection[Any],
) -> tuple[int, int]:
    row = await connection.fetchrow(
        """
        SELECT
            count(*)::integer AS total,
            count(*) FILTER (WHERE convalidated)::integer AS validated
        FROM pg_constraint constraint_record
        JOIN pg_namespace namespace_record
          ON namespace_record.oid = constraint_record.connamespace
        WHERE namespace_record.nspname = 'public'
          AND constraint_record.contype = 'f'
        """
    )
    assert row is not None
    return int(row["total"]), int(row["validated"])


def _normalized_payload(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def capture_manifest(
    connection: asyncpg.Connection[Any], run_id: str | None
) -> DatabaseManifest:
    revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    expected_revision = application_alembic_head()
    if revision != expected_revision:
        raise RecoveryError(
            f"source schema is not at the application head ({expected_revision})"
        )

    selected_run_id = run_id
    if selected_run_id is None:
        selected_run_id = await connection.fetchval(
            """
            SELECT run_id
            FROM run_events
            GROUP BY run_id
            ORDER BY count(*) DESC, run_id
            LIMIT 1
            """
        )
    if not isinstance(selected_run_id, str):
        raise RecoveryError("source database has no Run trace to verify")

    run_exists = await connection.fetchval(
        "SELECT 1 FROM runs WHERE id = $1", selected_run_id
    )
    if not run_exists:
        raise RecoveryError(f"Run trace does not belong to a Run: {selected_run_id}")
    event_rows = await connection.fetch(
        """
        SELECT sequence, event_type, payload
        FROM run_events
        WHERE run_id = $1
        ORDER BY sequence
        """,
        selected_run_id,
    )
    if not event_rows:
        raise RecoveryError(f"Run has no persisted events: {selected_run_id}")
    trace = [
        {
            "sequence": int(row["sequence"]),
            "event_type": str(row["event_type"]),
            "payload": _normalized_payload(row["payload"]),
        }
        for row in event_rows
    ]
    sequences = [item["sequence"] for item in trace]
    if sequences != list(range(1, len(sequences) + 1)):
        raise RecoveryError(f"Run trace is not contiguous: {selected_run_id}")

    foreign_key_count, validated_foreign_keys = await _foreign_key_status(connection)
    if foreign_key_count == 0 or validated_foreign_keys != foreign_key_count:
        raise RecoveryError("database foreign keys are missing or not validated")
    return DatabaseManifest(
        revision=str(revision),
        table_counts=await _table_counts(connection),
        foreign_key_count=foreign_key_count,
        run_id=selected_run_id,
        trace=trace,
    )


def compare_manifests(
    source: DatabaseManifest, restored: DatabaseManifest
) -> dict[str, Any]:
    if restored.revision != source.revision:
        raise RecoveryError("restored schema revision differs from the backup")
    if restored.table_counts != source.table_counts:
        raise RecoveryError("restored table row counts differ from the backup")
    if restored.foreign_key_count != source.foreign_key_count:
        raise RecoveryError("restored foreign-key count differs from the backup")
    if restored.run_id != source.run_id or restored.trace != source.trace:
        raise RecoveryError("restored Run trace differs from the backup")
    return {
        "schema_revision": restored.revision,
        "table_counts": restored.table_counts,
        "foreign_key_count": restored.foreign_key_count,
        "run_id": restored.run_id,
        "event_count": len(restored.trace),
    }


async def rehearse_restore(
    value: str, target_name: str, *, run_id: str | None = None
) -> dict[str, Any]:
    source_url = normalize_postgres_url(value)
    source_name = database_name(source_url)
    validate_disposable_database_name(target_name, source_name)
    admin_url = database_url_for(source_url, "postgres")
    target_url = database_url_for(source_url, target_name)
    created_target = False

    with tempfile.TemporaryDirectory(prefix="agentops-backup-") as temporary_directory:
        backup_path = Path(temporary_directory) / "agentops.dump"
        source_connection = await asyncpg.connect(source_url)
        transaction = source_connection.transaction(
            isolation="repeatable_read", readonly=True
        )
        await transaction.start()
        try:
            snapshot = await source_connection.fetchval("SELECT pg_export_snapshot()")
            source_manifest = await capture_manifest(source_connection, run_id)
            backup_database(source_url, backup_path, snapshot=str(snapshot))
        finally:
            await transaction.rollback()
            await source_connection.close()

        try:
            await _create_database(admin_url, target_name)
            created_target = True
            _run_tool(
                "pg_restore",
                [
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    target_name,
                    str(backup_path),
                ],
                libpq_environment(target_url),
            )
            target_connection = await asyncpg.connect(target_url)
            try:
                restored_manifest = await capture_manifest(
                    target_connection, source_manifest.run_id
                )
            finally:
                await target_connection.close()
            return compare_manifests(source_manifest, restored_manifest)
        finally:
            if created_target:
                await _drop_database(admin_url, target_name)


def _database_url_from_environment(variable: str) -> str:
    value = os.getenv(variable)
    if not value:
        raise RecoveryError(f"required environment variable is not set: {variable}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="environment variable containing the source PostgreSQL URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="create a custom-format backup")
    backup_parser.add_argument("--output", type=Path, required=True)
    rehearse_parser = subparsers.add_parser(
        "rehearse", help="restore into a disposable database and verify it"
    )
    rehearse_parser.add_argument("--target-database", required=True)
    rehearse_parser.add_argument("--run-id")
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        value = _database_url_from_environment(args.database_url_env)
        if args.command == "backup":
            backup_database(value, args.output)
            result: dict[str, Any] = {
                "status": "ok",
                "backup_file": str(args.output),
            }
        else:
            result = asyncio.run(
                rehearse_restore(
                    value,
                    args.target_database,
                    run_id=args.run_id,
                )
            )
            result["status"] = "ok"
        print(json.dumps(result, indent=2, sort_keys=True))
    except (RecoveryError, asyncpg.PostgresError, OSError) as exc:
        parser.exit(1, f"database recovery failed: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
