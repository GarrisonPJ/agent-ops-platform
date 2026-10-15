from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update

from app.calibration_report import LabelSet, ReviewPacket, ReviewRecord, report_labeled_runs
from app.phase1_models import Experiment, Run, RunEvent
from scripts import controlled_calibration_capture
from scripts.controlled_calibration_capture import CONTRACTS, capture_cases, export_artifacts


@pytest.mark.asyncio
async def test_controlled_capture_persists_context_contracts_and_fake_provider_errors(api, tmp_path: Path) -> None:
    client, factory = api
    environment_before = dict(os.environ)
    captured = await capture_cases(client, factory, "test-runner-token")
    assert dict(os.environ) == environment_before
    by_name = {case["name"]: case for case in captured}
    assert len(captured) == len(CONTRACTS) == 6
    assert by_name["provider_timeout"]["expected_dimensions"] == ["execution"]
    assert by_name["provider_invalid_response"]["expected_dimensions"] == ["execution"]
    # Only the CLI's real pg_dump is capture evidence; this file tests digest binding.
    snapshot = tmp_path / "mechanics-only-snapshot"
    snapshot.write_bytes(b"test-only snapshot identity; not a database backup")
    report = await export_artifacts(factory, tmp_path, captured, snapshot)
    assert report["binding"]["status"] == "bound"
    assert report["accuracy_status"] == "provisional_mechanics"
    assert report["disagreements"] == []
    packet = json.loads((tmp_path / "packet.json").read_text())
    trajectories = {case["run_id"]: case["trajectory"] for case in packet["cases"]}
    high = trajectories[by_name["context_pressure"]["run_id"]]
    low = trajectories[by_name["context_low_pressure"]["run_id"]]
    unknown = trajectories[by_name["context_unknown"]["run_id"]]
    assert all(step["context_window"] == {"used": 8191, "limit": 8192} for step in high["steps"])
    assert all(step["context_window"] == {"used": 1200, "limit": 8192} for step in low["steps"])
    assert all(step["context_window"] is None for step in unknown["steps"])
    assert high["run_status"] == low["run_status"] == unknown["run_status"] == "succeeded"
    success = trajectories[by_name["provider_success"]["run_id"]]
    assert len(success["steps"]) == 3
    assert all(step["context_window"] is None for step in success["steps"])
    assert by_name["provider_success"]["provider_request_count"] == 4
    assert not success["provider_error"]
    for name, code in (("provider_timeout", "PROVIDER_TIMEOUT"), ("provider_invalid_response", "PROVIDER_INVALID_RESPONSE")):
        trajectory = trajectories[by_name[name]["run_id"]]
        assert trajectory["steps"] == []
        assert trajectory["run_status"] == "failed"
        assert trajectory["provider_error"]
        assert by_name[name]["provider_request_count"] == 1
        async with factory() as db:
            events = (await db.scalars(select(RunEvent).where(RunEvent.run_id == by_name[name]["run_id"]).order_by(RunEvent.sequence))).all()
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            assert [event.payload["provider_error"]["code"] for event in events if "provider_error" in event.payload] == [code]
    record = json.loads((tmp_path / "review-record.json").read_text())
    assert record["reviewer"]["status"] == "agent_generated_provisional"
    assert record["publishable_accuracy"] is False
    manifest = json.loads((tmp_path / "capture-manifest.json").read_text())
    assert "Unknown cases" in manifest["context_contract"]
    assert "controlled-fake-provider-secret" not in (tmp_path / "packet.json").read_text()

    async with factory() as db:
        event = await db.scalar(select(RunEvent).where(RunEvent.run_id == by_name["context_pressure"]["run_id"], RunEvent.event_type == "step_completed").limit(1))
        await db.execute(update(RunEvent).where(RunEvent.id == event.id).values(payload={**event.payload, "context_window": {"used": 1200, "limit": 8192}}))
        await db.commit()
        with pytest.raises(ValueError, match="trajectory digest"):
            await report_labeled_runs(db, LabelSet.model_validate_json((tmp_path / "provisional-labels.json").read_text()),
                                      packet=ReviewPacket.model_validate(packet), review_record=ReviewRecord.model_validate(record),
                                      packet_sha256=record["packet"]["packet_sha256"], labels_sha256=record["packet"]["labels_sha256"], require_binding=True)


@pytest.mark.asyncio
async def test_controlled_capture_refuses_existing_domain_data(api) -> None:
    client, factory = api
    created = await client.post("/api/experiments", json={"name": "Existing data", "task": "Keep this experiment"})
    assert created.status_code == 201
    with pytest.raises(ValueError, match="empty domain tables"):
        await capture_cases(client, factory, "test-runner-token")
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.asyncio
async def test_controlled_capture_refuses_stale_schema_before_writes(api) -> None:
    client, factory = api
    async with factory() as db:
        await db.execute(text("UPDATE alembic_version SET version_num = '0001_phase1'"))
        await db.commit()
    with pytest.raises(ValueError, match="current Alembic head"):
        await capture_cases(client, factory, "test-runner-token")
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Experiment)) == 0
        assert await db.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.asyncio
async def test_run_capture_binds_token_and_restores_environment_on_failure(api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _client, factory = api
    engine = factory.kw["bind"]
    monkeypatch.setattr(controlled_calibration_capture, "make_engine", lambda _url: engine)

    async def interrupted_capture(client, _factory, token):
        response = await client.post("/api/internal/runner/jobs/claim", headers={"Authorization": f"Bearer {token}"}, json={"runner_id": "controlled-capture"})
        assert response.status_code == 204
        raise RuntimeError("controlled interruption after authentication")

    monkeypatch.setattr(controlled_calibration_capture, "capture_cases", interrupted_capture)
    before = dict(os.environ)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        await controlled_calibration_capture.run_capture(
            "postgresql+asyncpg://unused/agentops_calibration_token_test", "capture-specific-token", tmp_path / "capture"
        )
    assert dict(os.environ) == before


@pytest.mark.asyncio
async def test_capture_expected_labels_are_fixed_before_analysis(api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import calibration_report
    from app.failure_analyzer import FailureReport

    client, factory = api
    captured = await capture_cases(client, factory, "test-runner-token")
    # Perturb report predictions only: persisted terminal analysis already ran.
    monkeypatch.setattr(calibration_report, "analyze_trajectory", lambda *_args, **_kwargs: FailureReport())
    snapshot = tmp_path / "mechanics-only-snapshot"
    snapshot.write_bytes(b"test-only snapshot identity")
    with pytest.raises(ValueError, match="contract/report disagreement"):
        await export_artifacts(factory, tmp_path, captured, snapshot)
    labels = json.loads((tmp_path / "provisional-labels.json").read_text())
    expected = {case["run_id"]: case["expected_dimensions"] for case in captured}
    assert {case["run_id"]: case["dimensions"] for case in labels["cases"]} == expected
    disagreements = json.loads((tmp_path / "strict-report.json").read_text())["disagreements"]
    assert {case["run_id"]: case["false_negatives"] for case in disagreements} == {
        case["run_id"]: case["expected_dimensions"] for case in captured if case["expected_dimensions"]
    }
