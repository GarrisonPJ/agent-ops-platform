from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.calibration_report import LabelSet, report_labeled_runs, summarize_dimensions
from app.phase1_models import Experiment, Run, RunEvent, new_id, utcnow


def test_dimension_metrics_include_all_confusion_matrix_cells() -> None:
    summary = summarize_dimensions(
        [
            ({"planning"}, {"planning", "execution"}),
            ({"execution"}, set()),
            (set(), {"context"}),
        ]
    )

    assert summary["by_dimension"]["planning"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "tn": 2,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert summary["by_dimension"]["execution"] == {
        "tp": 0,
        "fp": 1,
        "fn": 1,
        "tn": 1,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }
    assert summary["by_dimension"]["context"]["recall"] is None
    assert summary["by_dimension"]["budget"] == {
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "tn": 3,
        "precision": None,
        "recall": None,
        "f1": None,
    }
    assert summary["micro"] == {
        "tp": 1,
        "fp": 2,
        "fn": 1,
        "tn": 8,
        "precision": pytest.approx(1 / 3),
        "recall": 0.5,
        "f1": 0.4,
    }
    with pytest.raises(ValueError, match="unknown dimension"):
        summarize_dimensions([(set(), {"new_dimension"})])


def test_label_set_rejects_unknown_dimensions_and_duplicate_runs() -> None:
    run_id = str(uuid4())
    with pytest.raises(ValidationError, match="Input should be"):
        LabelSet.model_validate(
            {
                "name": "review",
                "provenance": "blind review",
                "cases": [{"run_id": run_id, "dimensions": ["unknown"]}],
            }
        )
    with pytest.raises(ValidationError, match="duplicate run_id"):
        LabelSet.model_validate(
            {
                "name": "review",
                "provenance": "blind review",
                "cases": [
                    {"run_id": run_id, "dimensions": []},
                    {"run_id": run_id, "dimensions": ["planning"]},
                ],
            }
        )


@pytest.mark.asyncio
async def test_report_reconstructs_persisted_run_events(api) -> None:
    _, factory = api
    experiment_id, run_id = new_id(), new_id()
    async with factory() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name="Calibration",
                task="Investigate checkout latency",
                scenario_id="checkout-api-latency.v1",
                scenario_params={},
            )
        )
        db.add(
            Run(
                id=run_id,
                experiment_id=experiment_id,
                kind="baseline",
                status="failed",
                metrics={},
                evaluation_spec={"scenario_id": "checkout-api-latency.v1"},
            )
        )
        for index in range(3):
            db.add(
                RunEvent(
                    run_id=run_id,
                    sequence=index + 1,
                    event_type="step_completed",
                    payload={
                        "index": index,
                        "tool_call": {
                            "name": "fetch_service_logs",
                            "arguments": {"service": "checkout-api"},
                        },
                        "observation": "The same inconclusive result was returned.",
                    },
                    occurred_at=utcnow(),
                )
            )
        await db.commit()

    labels = LabelSet.model_validate(
        {
            "name": "test labels",
            "provenance": "test-only, not an accuracy sample",
            "cases": [{"run_id": run_id, "dimensions": ["planning"]}],
        }
    )
    async with factory() as db:
        report = await report_labeled_runs(db, labels)

    assert report["case_count"] == 1
    assert report["by_dimension"]["planning"]["tp"] == 1
    assert report["by_dimension"]["budget"]["tn"] == 1
    assert report["disagreements"] == []


@pytest.mark.asyncio
async def test_report_rejects_missing_and_nonterminal_runs(api) -> None:
    _, factory = api
    missing_id = new_id()
    labels = LabelSet.model_validate(
        {
            "name": "test",
            "provenance": "test-only",
            "cases": [{"run_id": missing_id, "dimensions": []}],
        }
    )
    async with factory() as db:
        with pytest.raises(ValueError, match="does not exist"):
            await report_labeled_runs(db, labels)

    experiment_id, queued_id = new_id(), new_id()
    async with factory() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name="Calibration",
                task="Investigate checkout latency",
                scenario_id="checkout-api-latency.v1",
                scenario_params={},
            )
        )
        db.add(
            Run(
                id=queued_id,
                experiment_id=experiment_id,
                kind="baseline",
                status="queued",
                metrics={},
                evaluation_spec={"scenario_id": "checkout-api-latency.v1"},
            )
        )
        await db.commit()
    queued_labels = LabelSet.model_validate(
        {
            "name": "test",
            "provenance": "test-only",
            "cases": [{"run_id": queued_id, "dimensions": []}],
        }
    )
    async with factory() as db:
        with pytest.raises(ValueError, match="not terminal"):
            await report_labeled_runs(db, queued_labels)
