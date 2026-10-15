"""Verify controlled capture, its actual PostgreSQL backup, and restored strict reporting.

Run from backend with PYTHONPATH=. and DATABASE_URL set. The URL supplies server
credentials only: this command owns two new disposable databases, never the
configured database. RUNNER_TOKEN is optional and is never written to artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import make_url

from app.database_recovery import (
    RecoveryError,
    capture_manifest,
    compare_manifests,
    database_url_for,
    normalize_postgres_url,
    restored_backup,
)


BACKEND_DIRECTORY = Path(__file__).resolve().parents[1] / "backend"


def _write_json(path: Path, value: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


@asynccontextmanager
async def _capture_database(service_url: str) -> AsyncIterator[tuple[str, str]]:
    admin_url = database_url_for(service_url, "postgres")
    suffix = uuid4().hex
    source_name = f"agentops_calibration_{suffix}"
    restore_name = f"agentops_restore_{suffix}"
    source_url = make_url(service_url).set(
        drivername="postgresql+asyncpg", database=source_name
    ).render_as_string(hide_password=False)
    connection = await asyncpg.connect(admin_url, timeout=10, command_timeout=30)
    created = False
    try:
        await connection.execute(f'CREATE DATABASE "{source_name}"')
        created = True
        yield source_url, restore_name
    finally:
        try:
            if created:
                await connection.execute(f'DROP DATABASE "{source_name}" WITH (FORCE)')
        finally:
            await connection.close()


def _command(arguments: list[str], database_url: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=BACKEND_DIRECTORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        # Child output can include connection details; retain only a safe stage/type.
        raise RecoveryError("calibration subprocess failed")
    return result.stdout


def _verify_report(report: dict[str, object]) -> None:
    if (
        report["case_count"] != 6
        or report["evaluation_scope"] != "assessed_run_dimension_pairs"
        or report["accuracy_status"] != "provisional_mechanics"
        or report["disagreements"] != []
    ):
        raise RecoveryError("unexpected controlled report contract")
    binding = report["binding"]
    if (
        binding["status"] != "bound"
        or binding["case_count"] != 6
        or binding["reviewer_status"] != "agent_generated_provisional"
        or binding["adjudication_status"] != "pending"
        or binding["publishable_accuracy"] is not False
    ):
        raise RecoveryError("unexpected controlled report provenance")
    expected_coverage = {
        "by_dimension": {
            dimension: {"known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0}
            for dimension in ("planning", "execution", "budget")
        } | {
            "context": {"known_count": 2, "unknown_count": 4, "total_count": 6, "ratio": 2 / 6}
        },
        "micro": {"known_count": 20, "unknown_count": 4, "total_count": 24, "ratio": 20 / 24},
    }
    if report["coverage"] != expected_coverage:
        raise RecoveryError("unexpected controlled unknown coverage")


def _compare_reports(captured: dict[str, object], restored: dict[str, object]) -> None:
    normalized = []
    for report in (captured, restored):
        binding = dict(report["binding"])
        for field in ("packet_sha256", "labels_sha256"):
            digest = binding[field]
            if not isinstance(digest, str) or re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest) is None:
                raise RecoveryError("invalid report file digest")
            binding[field] = digest.removeprefix("sha256:")
        normalized.append({**report, "binding": binding})
    if normalized[0] != normalized[1]:
        raise RecoveryError("restored report differs from captured report")


async def run(database_url: str, token: str, output: Path) -> dict[str, object]:
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    stage = "initialize"
    summary: dict[str, object] = {"status": "failed", "publishable_accuracy": False}
    try:
        normalize_postgres_url(database_url)
        # These modules create the default engine on import. Keep any URL parsing
        # failure inside the sanitized CLI boundary, before creating a database.
        from scripts.controlled_calibration_capture import file_digest, run_capture

        stage = "create_source"
        async with _capture_database(database_url) as (source_url, restore_name):
            stage = "migrate"
            await asyncio.to_thread(_command, ["-m", "alembic", "upgrade", "head"], source_url)
            stage = "capture"
            capture_output = output / "capture"
            original_report = await run_capture(source_url, token, capture_output)
            stage = "snapshot_identity"
            snapshot = capture_output / "snapshot.dump"
            snapshot_digest = file_digest(snapshot)
            manifest = json.loads((capture_output / "capture-manifest.json").read_text())
            packet = json.loads((capture_output / "packet.json").read_text())
            record = json.loads((capture_output / "review-record.json").read_text())
            if any(value != snapshot_digest for value in (
                manifest["source_snapshot"], packet["source_snapshot"],
                record["packet"]["source_snapshot"], original_report["binding"]["source_snapshot"],
            )):
                raise RecoveryError("actual backup digest differs from capture artifacts")
            if manifest["publishable_accuracy"] is not False or record["publishable_accuracy"] is not False:
                raise RecoveryError("controlled artifacts must remain provisional")
            connection = await asyncpg.connect(normalize_postgres_url(source_url), timeout=10, command_timeout=30)
            try:
                source_manifest = await capture_manifest(connection, manifest["cases"][0]["run_id"])
            finally:
                await connection.close()
            if source_manifest.table_counts.get("runs") != 6 or source_manifest.table_counts.get("run_events") != 30:
                raise RecoveryError("unexpected captured Run or event count")
            stage = "restore"
            async with restored_backup(source_url, restore_name, snapshot) as restored_url:
                connection = await asyncpg.connect(restored_url, timeout=10, command_timeout=30)
                try:
                    restored_manifest = await capture_manifest(connection, source_manifest.run_id)
                finally:
                    await connection.close()
                compare_manifests(source_manifest, restored_manifest)
                stage = "restored_report"
                report_url = make_url(restored_url).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
                restored_report = json.loads(await asyncio.to_thread(_command, [
                    "-m", "app.calibration_report",
                    "--labels", str(capture_output / "provisional-labels.json"),
                    "--packet", str(capture_output / "packet.json"),
                    "--review-record", str(capture_output / "review-record.json"),
                    "--require-binding",
                ], report_url))
                _write_json(output / "restored-strict-report.json", restored_report)
                stage = "verify_report"
                _verify_report(restored_report)
                _compare_reports(original_report, restored_report)
                if file_digest(snapshot) != snapshot_digest:
                    raise RecoveryError("backup changed during verification")
        summary.update(
            status="ok", case_count=6, event_count=30,
            source_snapshot=snapshot_digest,
            schema_revision=source_manifest.revision,
            binding=restored_report["binding"]["status"],
            accuracy_status=restored_report["accuracy_status"],
            evaluation_scope=restored_report["evaluation_scope"],
            coverage=restored_report["coverage"],
        )
    except Exception as exc:
        summary.update(stage=stage, error_type=type(exc).__name__)
        raise
    finally:
        _write_json(output / "e2e-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        parser.error("DATABASE_URL must be set")
    token = os.getenv("RUNNER_TOKEN", "").strip() or secrets.token_urlsafe(32)
    try:
        summary = asyncio.run(run(database_url, token, args.output))
    except Exception as exc:
        parser.exit(1, json.dumps({"status": "failed", "error_type": type(exc).__name__}) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
