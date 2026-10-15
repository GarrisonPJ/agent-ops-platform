from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.database_recovery import RecoveryError
from app.database_recovery import DatabaseManifest
from scripts import controlled_calibration_capture


spec = importlib.util.spec_from_file_location(
    "controlled_calibration_e2e",
    Path(__file__).resolve().parents[2] / "scripts" / "controlled_calibration_e2e.py",
)
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

SERVICE_URL = "postgresql+asyncpg://operator:private-password@localhost/keep_this_database"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "collision", "consumer"])
async def test_capture_database_only_cleans_its_owned_uuid_database(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    connection = AsyncMock()
    if failure == "collision":
        connection.execute.side_effect = RuntimeError("existing database")
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(e2e.asyncpg, "connect", connect)

    async def exercise():
        async with e2e._capture_database(SERVICE_URL) as (source_url, restore_name):
            assert e2e.make_url(source_url).database.startswith("agentops_calibration_")
            assert source_url.startswith("postgresql+asyncpg://")
            assert restore_name.startswith("agentops_restore_")
            if failure == "consumer":
                raise RuntimeError("capture failed")

    if failure:
        with pytest.raises(RuntimeError):
            await exercise()
    else:
        await exercise()
    commands = [call.args[0] for call in connection.execute.await_args_list]
    assert commands[0].startswith('CREATE DATABASE "agentops_calibration_')
    assert all("keep_this_database" not in command for command in commands)
    if failure == "collision":
        assert len(commands) == 1
    else:
        assert len(commands) == 2
        assert commands[1].startswith('DROP DATABASE "agentops_calibration_')
        assert commands[0].split('"')[1] == commands[1].split('"')[1]
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_output_is_untouched_before_database_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "e2e-summary.json").write_text("keep existing result")
    connect = AsyncMock()
    monkeypatch.setattr(e2e.asyncpg, "connect", connect)
    with pytest.raises(FileExistsError):
        await e2e.run(SERVICE_URL, "private-token", existing)
    assert (existing / "e2e-summary.json").read_text() == "keep existing result"
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_migration_cleans_source_and_retains_sanitized_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = AsyncMock()
    monkeypatch.setattr(e2e.asyncpg, "connect", AsyncMock(return_value=connection))

    def fail_migration(_arguments, _url):
        raise RuntimeError(SERVICE_URL + " private-token")

    monkeypatch.setattr(e2e, "_command", fail_migration)
    output = tmp_path / "capture-run"
    with pytest.raises(RuntimeError):
        await e2e.run(SERVICE_URL, "private-token", output)
    summary_text = (output / "e2e-summary.json").read_text()
    assert json.loads(summary_text) == {
        "status": "failed", "publishable_accuracy": False,
        "stage": "migrate", "error_type": "RuntimeError",
    }
    assert "private" not in summary_text
    assert connection.execute.await_count == 2
    assert "DROP DATABASE" in connection.execute.await_args.args[0]


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_subprocess_credentials_stay_in_environment_and_failures_are_not_success(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def fake_run(command, **kwargs):
        assert SERVICE_URL not in " ".join(command)
        assert kwargs["env"]["DATABASE_URL"] == SERVICE_URL
        assert kwargs["timeout"] == 120
        assert kwargs["capture_output"] is True
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 120, output="private-password")
        return subprocess.CompletedProcess(command, 1, "", "private-password")

    monkeypatch.setattr(e2e.subprocess, "run", fake_run)
    with pytest.raises((RecoveryError, subprocess.TimeoutExpired)):
        e2e._command(["-m", "alembic", "upgrade", "head"], SERVICE_URL)


@pytest.mark.parametrize("error_type", [RuntimeError, ConnectionError])
def test_cli_failure_output_excludes_exception_message_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, error_type: type[Exception]
) -> None:
    async def fail(*_args):
        raise error_type(SERVICE_URL + " private-token")

    monkeypatch.setenv("DATABASE_URL", SERVICE_URL)
    monkeypatch.delenv("RUNNER_TOKEN", raising=False)
    monkeypatch.setattr(e2e, "run", fail)
    monkeypatch.setattr(e2e.sys, "argv", ["controlled_calibration_e2e.py", "--output", str(tmp_path / "new")])
    with pytest.raises(SystemExit) as exit_info:
        e2e.main()
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
    assert json.loads(output.err) == {"status": "failed", "error_type": error_type.__name__}


def test_malformed_database_url_is_sanitized_before_import_side_effects(tmp_path: Path) -> None:
    import os

    environment = os.environ.copy()
    environment.update(
        DATABASE_URL="postgresql+asyncpg://agentops:secret-sentinel/missing-host",
        PYTHONPATH=str(e2e.BACKEND_DIRECTORY),
    )
    output = tmp_path / "malformed-url"
    result = subprocess.run(
        [sys.executable, str(Path(e2e.__file__)), "--output", str(output)],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "secret-sentinel" not in combined
    assert "Traceback" not in combined
    summary = json.loads((output / "e2e-summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["stage"] == "initialize"
    assert "secret-sentinel" not in json.dumps(summary)


def valid_report() -> dict[str, object]:
    return {
        "case_count": 6, "evaluation_scope": "assessed_run_dimension_pairs",
        "accuracy_status": "provisional_mechanics", "disagreements": [],
        "binding": {
            "status": "bound", "case_count": 6, "publishable_accuracy": False,
            "reviewer_status": "agent_generated_provisional", "adjudication_status": "pending",
            "packet_sha256": "a" * 64, "labels_sha256": "b" * 64,
        },
        "coverage": {
            "by_dimension": {
                "planning": {"known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0},
                "execution": {"known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0},
                "context": {"known_count": 2, "unknown_count": 4, "total_count": 6, "ratio": 1 / 3},
                "budget": {"known_count": 6, "unknown_count": 0, "total_count": 6, "ratio": 1.0},
            },
            "micro": {"known_count": 20, "unknown_count": 4, "total_count": 24, "ratio": 5 / 6},
        },
    }


@pytest.mark.parametrize("corruption", ["scope", "context_unknown", "micro_known", "binding", "publishable"])
def test_acceptance_rejects_wrong_scope_coverage_or_provenance(corruption: str) -> None:
    report = valid_report()
    e2e._verify_report(report)
    if corruption == "scope":
        report["evaluation_scope"] = "all_runs"
    elif corruption == "context_unknown":
        report["coverage"]["by_dimension"]["context"]["unknown_count"] = 0
    elif corruption == "micro_known":
        report["coverage"]["micro"]["known_count"] = 24
    elif corruption == "binding":
        report["binding"]["status"] = "unbound"
    else:
        report["binding"]["publishable_accuracy"] = True
    with pytest.raises(RecoveryError):
        e2e._verify_report(report)


def test_report_comparison_allows_only_known_digest_prefix_representation() -> None:
    captured = valid_report()
    restored = deepcopy(captured)
    for field in ("packet_sha256", "labels_sha256"):
        captured["binding"][field] = "sha256:" + captured["binding"][field]
    e2e._compare_reports(captured, restored)
    assert captured["binding"]["packet_sha256"].startswith("sha256:")
    restored["binding"]["labels_sha256"] = "c" * 64
    with pytest.raises(RecoveryError, match="differs"):
        e2e._compare_reports(captured, restored)


@pytest.mark.parametrize("digest", ["sha256:sha256:" + "a" * 64, "a" * 63, "g" * 64, 123])
def test_report_comparison_rejects_malformed_digest(digest: object) -> None:
    captured = valid_report()
    restored = deepcopy(captured)
    restored["binding"]["packet_sha256"] = digest
    with pytest.raises(RecoveryError, match="invalid report file digest"):
        e2e._compare_reports(captured, restored)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "restore", "report", "restore_cleanup", "source_cleanup"])
async def test_harness_restores_captured_file_and_cleans_both_databases_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    operations = []
    output = tmp_path / "new-output"
    captured_snapshot = output / "capture" / "snapshot.dump"
    report = valid_report()

    @asynccontextmanager
    async def source_database(_url):
        operations.append("create_source")
        try:
            yield SERVICE_URL.replace("keep_this_database", "agentops_calibration_uuid"), "agentops_restore_uuid"
        finally:
            operations.append("drop_source")
            if failure == "source_cleanup":
                raise ConnectionError("cleanup lost connection with private-token")

    async def capture(_url, token, directory):
        assert token == "private-token"
        directory.mkdir()
        captured_snapshot.write_bytes(b"exact captured snapshot")
        digest = controlled_calibration_capture.file_digest(captured_snapshot)
        report["binding"]["source_snapshot"] = digest
        e2e._write_json(directory / "packet.json", {"source_snapshot": digest})
        e2e._write_json(directory / "review-record.json", {
            "packet": {"source_snapshot": digest}, "publishable_accuracy": False,
        })
        e2e._write_json(directory / "capture-manifest.json", {
            "source_snapshot": digest, "publishable_accuracy": False,
            "cases": [{"run_id": "captured-run"}],
        })
        return report

    @asynccontextmanager
    async def restore(_url, target, snapshot):
        assert snapshot == captured_snapshot
        assert snapshot.read_bytes() == b"exact captured snapshot"
        assert target == "agentops_restore_uuid"
        operations.append("create_restore")
        try:
            if failure == "restore":
                raise RecoveryError("restore failed with private-token")
            yield "postgresql://operator:private-password@localhost/agentops_restore_uuid"
        finally:
            operations.append("drop_restore")
            if failure == "restore_cleanup":
                raise ConnectionError("cleanup lost connection with private-token")

    def command(arguments, url):
        if "app.calibration_report" not in arguments:
            return ""
        assert "--require-binding" in arguments
        assert url.startswith("postgresql+asyncpg://")
        assert e2e.make_url(url).database == "agentops_restore_uuid"
        if failure == "report":
            raise RecoveryError("strict report failed with private-token")
        return json.dumps(report)

    manifest = DatabaseManifest("current-head", {"runs": 6, "run_events": 30}, 1, "captured-run", [])
    monkeypatch.setattr(e2e, "_capture_database", source_database)
    monkeypatch.setattr(controlled_calibration_capture, "run_capture", capture)
    monkeypatch.setattr(e2e, "restored_backup", restore)
    monkeypatch.setattr(e2e, "_command", command)
    monkeypatch.setattr(e2e, "capture_manifest", AsyncMock(return_value=manifest))
    monkeypatch.setattr(e2e.asyncpg, "connect", AsyncMock(return_value=AsyncMock()))

    if failure:
        with pytest.raises((RecoveryError, ConnectionError)):
            await e2e.run(SERVICE_URL, "private-token", output)
    else:
        summary = await e2e.run(SERVICE_URL, "private-token", output)
        assert summary["status"] == "ok"
        assert json.loads((output / "restored-strict-report.json").read_text()) == report
    assert operations == ["create_source", "create_restore", "drop_restore", "drop_source"]
    summary_text = (output / "e2e-summary.json").read_text()
    assert "private" not in summary_text
    if failure:
        assert json.loads(summary_text)["stage"] == {
            "restore": "restore", "report": "restored_report",
            "restore_cleanup": "cleanup_restore", "source_cleanup": "cleanup_source",
        }[failure]
        assert json.loads(summary_text)["status"] == "failed"
