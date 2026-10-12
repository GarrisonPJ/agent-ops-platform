from __future__ import annotations

import hashlib
import json
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.calibration_report import (
    LabelSet,
    ReviewPacket,
    ReviewRecord,
    _binding_summary,
    canonical_digest,
    event_stream_digest,
    main,
    report_labeled_runs,
    summarize_dimensions,
)
from app.failure_analyzer import ANALYZER_REVISION
from app.phase1_models import Experiment, Run, RunEvent, new_id, utcnow
from app.phase1_service import _trajectory_from_events


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
async def test_report_binds_labels_to_packet_and_review_metadata(api) -> None:
    _, factory = api
    experiment_id, run_id = new_id(), new_id()
    async with factory() as db:
        db.add(
            Experiment(
                id=experiment_id,
                name="Calibration binding",
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
        db.add(
            RunEvent(
                run_id=run_id,
                sequence=1,
                event_type="step_completed",
                payload={
                    "index": 0,
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
            "name": "bound labels",
            "provenance": "test-only",
            "cases": [{"run_id": run_id, "dimensions": ["planning"]}],
        }
    )
    async with factory() as db:
        run = await db.get(Run, run_id)
        assert run is not None
        trajectory, _ = await _trajectory_from_events(db, run)
        events = list(
            (
                await db.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.sequence.asc())
                )
            )
            .scalars()
            .all()
        )
        event_stream_sha256 = event_stream_digest(events)
    packet = ReviewPacket.model_validate(
        {
            "analyzer_revision": ANALYZER_REVISION,
            "source_snapshot": "sha256:" + "1" * 64,
            "eligible_count": 1,
            "cases": [
                {
                    "run_id": run_id,
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                    "trajectory": trajectory,
                    "trajectory_sha256": canonical_digest(trajectory),
                    "event_stream_sha256": event_stream_sha256,
                }
            ],
        }
    )
    review_record = ReviewRecord.model_validate(
        {
            "packet": {
                "analyzer_revision": ANALYZER_REVISION,
                "source_snapshot": "sha256:" + "1" * 64,
                "case_count": 1,
                "packet_sha256": "a" * 64,
                "labels_sha256": "b" * 64,
            },
            "reviewer": {
                "status": "agent_generated_provisional",
                "reviewer_ids": ["test-agent"],
                "method": "test-only visible trajectory review",
            },
            "adjudication": {"status": "pending"},
            "cases": [
                {
                    "run_id": run_id,
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                    "evidence": ["step 0"],
                }
            ],
        }
    )

    async with factory() as db:
        report = await report_labeled_runs(
            db,
            labels,
            packet=packet,
            review_record=review_record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )

    assert report["binding"] == {
        "status": "bound",
        "analyzer_revision": ANALYZER_REVISION,
        "source_snapshot": "sha256:" + "1" * 64,
        "packet_sha256": "a" * 64,
        "labels_sha256": "b" * 64,
        "case_count": 1,
        "reviewer_status": "agent_generated_provisional",
        "adjudication_status": "pending",
        "publishable_accuracy": False,
    }
    assert report["accuracy_status"] == "provisional_mechanics"

    packet.cases[0].event_stream_sha256 = None
    with pytest.raises(ValueError, match="event_stream_sha256 is required"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="a" * 64,
                labels_sha256="b" * 64,
                require_binding=True,
            )
    packet.cases[0].event_stream_sha256 = event_stream_sha256

    with pytest.raises(ValueError, match="packet_sha256"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="b" * 64,
                labels_sha256="b" * 64,
                require_binding=True,
            )

    with pytest.raises(ValueError, match="labels_sha256"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="a" * 64,
                labels_sha256="c" * 64,
                require_binding=True,
            )

    original_observation = packet.cases[0].trajectory["steps"][0]["observation"]
    packet.cases[0].trajectory["steps"][0]["observation"] = "Tampered packet."
    with pytest.raises(ValueError, match="does not match packet"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="a" * 64,
                labels_sha256="b" * 64,
                require_binding=True,
            )
    packet.cases[0].trajectory["steps"][0]["observation"] = original_observation

    review_record.cases[0].task = "Different task"
    with pytest.raises(ValueError, match="task"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="a" * 64,
                labels_sha256="b" * 64,
                require_binding=True,
            )

    review_record.cases[0].task = "Investigate checkout latency"
    async with factory() as db:
        event = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run_id))
        ).scalar_one()
        event.payload = {**event.payload, "observation": "A changed observation."}
        await db.commit()
    with pytest.raises(ValueError, match="RunEvent trajectory digest"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="a" * 64,
                labels_sha256="b" * 64,
                require_binding=True,
            )

    async with factory() as db:
        event = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run_id))
        ).scalar_one()
        event.payload = {
            **event.payload,
            "observation": original_observation,
            "unprojected": "changed",
        }
        await db.commit()
    with pytest.raises(ValueError, match="RunEvent event-stream digest"):
        async with factory() as db:
            await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256="a" * 64,
                labels_sha256="b" * 64,
                require_binding=True,
            )


def test_strict_binding_requires_and_matches_analyzer_revision() -> None:
    run_id = uuid4()
    labels = LabelSet.model_validate(
        {
            "name": "labels",
            "provenance": "test-only",
            "cases": [{"run_id": str(run_id), "dimensions": []}],
        }
    )
    case = {
        "run_id": str(run_id),
        "scenario_id": "checkout-api-latency.v1",
        "execution_mode": "fixture",
        "kind": "baseline",
        "task": "Investigate checkout latency",
        "trajectory": {"run_status": "failed", "steps": []},
    }
    review_case = {
        "run_id": str(run_id),
        "scenario_id": "checkout-api-latency.v1",
        "execution_mode": "fixture",
        "kind": "baseline",
        "task": "Investigate checkout latency",
    }
    packet = ReviewPacket.model_validate(
        {"source_snapshot": "sha256:" + "1" * 64, "cases": [case]}
    )
    record = ReviewRecord.model_validate(
        {
            "packet": {
                "analyzer_revision": ANALYZER_REVISION,
                "source_snapshot": "sha256:" + "1" * 64,
                "case_count": 1,
                "packet_sha256": "a" * 64,
                "labels_sha256": "b" * 64,
            },
            "reviewer": {
                "status": "agent_generated_provisional",
                "reviewer_ids": ["agent"],
                "method": "test-only",
            },
            "adjudication": {"status": "pending"},
            "cases": [review_case],
        }
    )
    with pytest.raises(ValueError, match="packet analyzer_revision is required"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )


    packet.analyzer_revision = ANALYZER_REVISION
    record.packet.analyzer_revision = None
    with pytest.raises(ValueError, match="record analyzer_revision is required"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )

    packet.analyzer_revision = "failure-analyzer.v0"
    record.packet.analyzer_revision = ANALYZER_REVISION
    with pytest.raises(ValueError, match="does not match review packet"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )

    record.packet.analyzer_revision = "failure-analyzer.v0"
    with pytest.raises(ValueError, match="current analyzer revision"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )


def test_strict_binding_requires_canonical_source_snapshot_digest() -> None:
    run_id = uuid4()
    labels = LabelSet.model_validate(
        {
            "name": "labels",
            "provenance": "test-only",
            "cases": [{"run_id": str(run_id), "dimensions": []}],
        }
    )
    packet = ReviewPacket.model_validate(
        {
            "analyzer_revision": ANALYZER_REVISION,
            "source_snapshot": "sha256:" + "1" * 64,
            "cases": [
                {
                    "run_id": str(run_id),
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                    "trajectory": {"run_status": "failed", "steps": []},
                }
            ],
        }
    )
    record = ReviewRecord.model_validate(
        {
            "packet": {
                "analyzer_revision": ANALYZER_REVISION,
                "source_snapshot": "snapshot-1",
                "case_count": 1,
                "packet_sha256": "a" * 64,
                "labels_sha256": "b" * 64,
            },
            "reviewer": {
                "status": "agent_generated_provisional",
                "reviewer_ids": ["agent"],
                "method": "test-only",
            },
            "adjudication": {"status": "pending"},
            "cases": [
                {
                    "run_id": str(run_id),
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="source_snapshot.*sha256"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )

    packet.source_snapshot = f"sha256:{'a' * 64}"
    record.packet.source_snapshot = f"sha256:{'b' * 64}"
    with pytest.raises(ValueError, match="source_snapshot does not match"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256="a" * 64,
            labels_sha256="b" * 64,
            require_binding=True,
        )


def test_review_record_rejects_publishable_agent_labels() -> None:
    with pytest.raises(ValidationError, match="agent-generated"):
        ReviewRecord.model_validate(
            {
                "packet": {
                    "source_snapshot": "snapshot-1",
                    "case_count": 1,
                },
                "reviewer": {
                    "status": "agent_generated_provisional",
                    "reviewer_ids": ["test-agent"],
                    "method": "test-only",
                },
                "adjudication": {"status": "pending"},
                "cases": [
                    {
                        "run_id": str(uuid4()),
                        "scenario_id": "checkout-api-latency.v1",
                        "execution_mode": "fixture",
                        "kind": "baseline",
                        "task": "Investigate checkout latency",
                    }
                ],
                "publishable_accuracy": True,
            }
        )


def test_review_record_rejects_incomplete_human_gate() -> None:
    base = {
        "packet": {"source_snapshot": "snapshot-1", "case_count": 1},
        "reviewer": {
            "status": "multi_reviewer",
            "reviewer_ids": ["reviewer-a", "reviewer-a"],
            "method": "independent review",
        },
        "adjudication": {"status": "complete"},
        "cases": [
            {
                "run_id": str(uuid4()),
                "scenario_id": "checkout-api-latency.v1",
                "execution_mode": "fixture",
                "kind": "baseline",
                "task": "Investigate checkout latency",
            }
        ],
        "publishable_accuracy": True,
    }
    with pytest.raises(ValidationError, match="reviewer IDs must be unique"):
        ReviewRecord.model_validate(base)

    base["reviewer"]["reviewer_ids"] = ["reviewer-a", "reviewer-b"]
    with pytest.raises(ValidationError, match="complete adjudication"):
        ReviewRecord.model_validate(base)

    base["adjudication"] = {
        "status": "complete",
        "adjudicator": "adjudicator-a",
        "decision": "accepted",
    }
    base["reviewer"]["reviewer_ids"] = ["reviewer-a", " "]
    with pytest.raises(ValidationError, match="non-empty"):
        ReviewRecord.model_validate(base)


def test_review_record_requires_two_human_reviewers_for_publishable_accuracy() -> None:
    with pytest.raises(ValidationError, match="two unique human"):
        ReviewRecord.model_validate(
            {
                "packet": {"source_snapshot": "snapshot-1", "case_count": 1},
                "reviewer": {
                    "status": "independent_human",
                    "reviewer_ids": ["reviewer-a"],
                    "method": "independent review",
                },
                "adjudication": {
                    "status": "complete",
                    "adjudicator": "adjudicator-a",
                    "decision": "accepted",
                },
                "cases": [
                    {
                        "run_id": str(uuid4()),
                        "scenario_id": "checkout-api-latency.v1",
                        "execution_mode": "fixture",
                        "kind": "baseline",
                        "task": "Investigate checkout latency",
                    }
                ],
                "publishable_accuracy": True,
            }
        )


def test_publishable_review_requires_case_evidence_and_uncertainty() -> None:
    payload = {
        "packet": {"source_snapshot": "snapshot-1", "case_count": 1},
        "reviewer": {
            "status": "multi_reviewer",
            "reviewer_ids": ["reviewer-a", "reviewer-b"],
            "method": "independent review",
        },
        "adjudication": {
            "status": "complete",
            "adjudicator": "adjudicator-a",
            "decision": "accepted",
        },
        "cases": [
            {
                "run_id": str(uuid4()),
                "scenario_id": "checkout-api-latency.v1",
                "execution_mode": "fixture",
                "kind": "baseline",
                "task": "Investigate checkout latency",
                "evidence": [],
            }
        ],
        "publishable_accuracy": True,
    }
    with pytest.raises(ValidationError, match="evidence"):
        ReviewRecord.model_validate(payload)

    payload["cases"][0]["evidence"] = ["step trace reviewed"]
    with pytest.raises(ValidationError, match="uncertainty"):
        ReviewRecord.model_validate(payload)


def test_publishable_accuracy_requires_strict_binding() -> None:
    run_id = uuid4()
    labels = LabelSet.model_validate(
        {
            "name": "labels",
            "provenance": "human",
            "cases": [{"run_id": str(run_id), "dimensions": []}],
        }
    )
    packet = ReviewPacket.model_validate(
        {
            "source_snapshot": "snapshot-1",
            "cases": [
                {
                    "run_id": str(run_id),
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                    "trajectory": {"run_status": "failed", "steps": []},
                }
            ],
        }
    )
    record = ReviewRecord.model_validate(
        {
            "packet": {"source_snapshot": "snapshot-1", "case_count": 1},
            "reviewer": {
                "status": "multi_reviewer",
                "reviewer_ids": ["reviewer-a", "reviewer-b"],
                "method": "independent review",
            },
            "adjudication": {
                "status": "complete",
                "adjudicator": "adjudicator-a",
                "decision": "accepted",
            },
            "cases": [
                {
                    "run_id": str(run_id),
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                    "evidence": ["step trace reviewed"],
                    "uncertainty": "low",
                }
            ],
            "publishable_accuracy": True,
        }
    )
    with pytest.raises(ValueError, match="strict binding"):
        _binding_summary(
            labels,
            packet=packet,
            review_record=record,
            packet_sha256=None,
            labels_sha256=None,
            require_binding=False,
        )


def test_cli_require_binding_rejects_missing_packet(tmp_path, monkeypatch) -> None:
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "name": "labels",
                "provenance": "test",
                "cases": [{"run_id": str(uuid4()), "dimensions": []}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setattr(
        sys,
        "argv",
        ["calibration_report", "--labels", str(labels_path), "--require-binding"],
    )
    with pytest.raises(SystemExit, match="2"):
        main()


def test_cli_require_binding_rejects_missing_packet_digest(tmp_path, monkeypatch) -> None:
    run_id = str(uuid4())
    labels_bytes = json.dumps(
        {
            "name": "labels",
            "provenance": "test",
            "cases": [{"run_id": run_id, "dimensions": []}],
        },
        separators=(",", ":"),
    ).encode()
    packet_bytes = json.dumps(
        {
            "source_snapshot": "sha256:" + "1" * 64,
            "cases": [
                {
                    "run_id": run_id,
                    "scenario_id": "checkout-api-latency.v1",
                    "execution_mode": "fixture",
                    "kind": "baseline",
                    "task": "Investigate checkout latency",
                    "trajectory": {"run_status": "failed", "steps": []},
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    labels_path = tmp_path / "labels.json"
    packet_path = tmp_path / "packet.json"
    record_path = tmp_path / "record.json"
    labels_path.write_bytes(labels_bytes)
    packet_path.write_bytes(packet_bytes)
    record_path.write_text(
        json.dumps(
            {
                "packet": {
                    "source_snapshot": "sha256:" + "1" * 64,
                    "case_count": 1,
                    "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
                },
                "reviewer": {
                    "status": "agent_generated_provisional",
                    "reviewer_ids": ["agent"],
                    "method": "test",
                },
                "adjudication": {"status": "pending"},
                "cases": [
                    {
                        "run_id": run_id,
                        "scenario_id": "checkout-api-latency.v1",
                        "execution_mode": "fixture",
                        "kind": "baseline",
                        "task": "Investigate checkout latency",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "calibration_report",
            "--labels",
            str(labels_path),
            "--packet",
            str(packet_path),
            "--review-record",
            str(record_path),
            "--require-binding",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        main()


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
