from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.calibration_review import build_review_packet, write_packet
from app.phase1_models import Experiment, Run, RunAnalysis, RunEvent, new_id, utcnow


@pytest.mark.asyncio
async def test_review_packet_balances_strata_without_exposing_analysis(api) -> None:
    _, factory = api
    async with factory() as db:
        for scenario_id, status in (
            ("checkout-api-latency.v1", "failed"),
            ("checkout-api-latency.v1", "failed"),
            ("multi-step-research.v1", "succeeded"),
        ):
            experiment_id, run_id = new_id(), new_id()
            db.add(
                Experiment(
                    id=experiment_id,
                    name="Blind review",
                    task="Investigate a persisted trace",
                    scenario_id=scenario_id,
                    scenario_params={},
                )
            )
            db.add(
                Run(
                    id=run_id,
                    experiment_id=experiment_id,
                    kind="baseline",
                    status=status,
                    score=0.9,
                    metrics={"private_metric": 1},
                    evaluation_spec={"scenario_id": scenario_id},
                )
            )
            db.add(
                RunEvent(
                    run_id=run_id,
                    sequence=1,
                    event_type="step_completed",
                    payload={"index": 0, "observation": "The call failed."},
                    occurred_at=utcnow(),
                )
            )
            db.add(
                RunAnalysis(
                    run_id=run_id,
                    dimensions={"execution": 1.0},
                    evidence=[{"reason": "hidden prediction"}],
                )
            )
        legacy_experiment_id, legacy_run_id = new_id(), new_id()
        db.add(
            Experiment(
                id=legacy_experiment_id,
                name="Legacy",
                task="Old fixture",
                scenario_id="checkout-api-latency",
                scenario_params={},
            )
        )
        db.add(
            Run(
                id=legacy_run_id,
                experiment_id=legacy_experiment_id,
                kind="baseline",
                status="failed",
                metrics={},
                evaluation_spec={"scenario_id": "checkout-api-latency"},
            )
        )
        await db.commit()

    async with factory() as db:
        packet = await build_review_packet(db, limit=2, source_snapshot="test snapshot")

    assert packet["eligible_count"] == 3
    assert packet["analyzer_revision"] == "failure-analyzer.v2"
    assert {case["scenario_id"] for case in packet["cases"]} == {
        "checkout-api-latency.v1",
        "multi-step-research.v1",
    }
    assert all(
        case["trajectory"]["steps"][0]["observation"] == "The call failed."
        for case in packet["cases"]
    )
    assert all(
        case["trajectory_sha256"].startswith("sha256:")
        for case in packet["cases"]
    )
    assert all(
        case["event_stream_sha256"].startswith("sha256:")
        for case in packet["cases"]
    )
    serialized = json.dumps(packet)
    assert "hidden prediction" not in serialized
    assert "private_metric" not in serialized
    assert '"score"' not in serialized


def test_review_packet_file_is_private_and_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "packet.json"
    write_packet(output, {"cases": []})

    assert output.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        write_packet(output, {"cases": ["different"]})
    assert json.loads(output.read_text()) == {"cases": []}
