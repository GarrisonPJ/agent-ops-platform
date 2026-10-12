"""Offline dimension metrics for independently labeled, persisted Runs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.failure_analyzer import ANALYZER_REVISION, analyze_trajectory
from app.phase1_database import make_engine
from app.phase1_models import Experiment, Run, RunEvent
from app.phase1_schemas import TERMINAL_RUN_STATUSES
from app.phase1_service import _trajectory_from_events
from app.scenario_registry import assertion_suite_for_scenario

# Register built-in Scenario assertion suites before evaluating stored Runs.
import app.scenarios  # noqa: F401


DIMENSIONS = ("planning", "execution", "context", "budget")
Dimension = Literal["planning", "execution", "context", "budget"]


class LabeledRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    dimensions: set[Dimension]


class LabelSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    cases: list[LabeledRun] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_run_ids(self) -> LabelSet:
        run_ids = [case.run_id for case in self.cases]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("label set contains duplicate run_id values")
        return self


class PacketCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    scenario_id: str = Field(min_length=1)
    execution_mode: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    task: str = Field(min_length=1)
    trajectory: dict[str, object]
    trajectory_sha256: str | None = Field(default=None, min_length=1)
    # Optional for historical packets; strict binding requires this field.
    event_stream_sha256: str | None = Field(default=None, min_length=1)


class ReviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional for historical packets; strict binding requires this field.
    analyzer_revision: str | None = Field(default=None, min_length=1)
    source_snapshot: str = Field(min_length=1)
    selection: str = ""
    eligible_count: int = Field(default=0, ge=0)
    cases: list[PacketCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> ReviewPacket:
        run_ids = [case.run_id for case in self.cases]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("review packet contains duplicate run_id values")
        return self


class PacketBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional for historical sidecars; strict binding requires this field.
    analyzer_revision: str | None = Field(default=None, min_length=1)
    source_snapshot: str = Field(min_length=1)
    case_count: int = Field(ge=1)
    packet_sha256: str | None = Field(default=None, min_length=1)
    labels_sha256: str | None = Field(default=None, min_length=1)


class ReviewerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "agent_generated_provisional", "independent_human", "multi_reviewer"
    ]
    reviewer_ids: list[str] = Field(min_length=1)
    method: str = Field(min_length=1)
    reviewed_at: str | None = None


class AdjudicationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "complete", "not_applicable"]
    adjudicator: str | None = None
    decision: str | None = None


class ReviewCaseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    scenario_id: str = Field(min_length=1)
    execution_mode: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    task: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    uncertainty: str | None = None


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"] = "v1"
    packet: PacketBinding
    reviewer: ReviewerMetadata
    adjudication: AdjudicationMetadata
    cases: list[ReviewCaseMetadata] = Field(min_length=1)
    publishable_accuracy: bool = False

    @model_validator(mode="after")
    def validate_review_status(self) -> ReviewRecord:
        case_ids = [case.run_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("review record contains duplicate run_id values")
        reviewer_ids = [reviewer_id.strip() for reviewer_id in self.reviewer.reviewer_ids]
        if any(not reviewer_id for reviewer_id in reviewer_ids):
            raise ValueError("reviewer IDs must be non-empty")
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("reviewer IDs must be unique")
        if (
            self.reviewer.status == "agent_generated_provisional"
            and self.publishable_accuracy
        ):
            raise ValueError("agent-generated review cannot be publishable")
        if (
            self.reviewer.status == "multi_reviewer"
            and len(self.reviewer.reviewer_ids) < 2
        ):
            raise ValueError("multi-reviewer record requires at least two reviewer_ids")
        if self.adjudication.status == "complete" and (
            not self.adjudication.adjudicator
            or not self.adjudication.adjudicator.strip()
            or not self.adjudication.decision
            or not self.adjudication.decision.strip()
        ):
            raise ValueError(
                "complete adjudication requires adjudicator and decision"
            )
        if self.publishable_accuracy and (
            self.reviewer.status not in {"independent_human", "multi_reviewer"}
            or len(reviewer_ids) < 2
        ):
            raise ValueError(
                "publishable review requires at least two unique human reviewer_ids"
            )
        if self.publishable_accuracy and self.adjudication.status != "complete":
            raise ValueError("publishable review requires completed adjudication")
        if self.publishable_accuracy:
            for case in self.cases:
                if not any(evidence.strip() for evidence in case.evidence):
                    raise ValueError(
                        "publishable review requires evidence for every case"
                    )
                if not case.uncertainty or not case.uncertainty.strip():
                    raise ValueError(
                        "publishable review requires uncertainty for every case"
                    )
        return self


def canonical_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def event_stream_digest(events: list[RunEvent]) -> str:
    """Digest persisted events without exposing their payloads in a packet."""

    normalized = []
    for event in sorted(events, key=lambda item: item.sequence):
        occurred_at = event.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        normalized.append(
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "occurred_at": occurred_at.astimezone(timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                "payload": event.payload,
            }
        )
    return canonical_digest(normalized)


async def persisted_event_stream_digest(db: AsyncSession, run_id: str) -> str:
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
    return event_stream_digest(events)


def _normalized_digest(value: str, *, field: str = "digest") -> str:
    digest = value.removeprefix("sha256:").lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return digest


def _require_source_snapshot_digest(value: str) -> None:
    try:
        digest = _normalized_digest(value, field="source_snapshot")
    except ValueError as exc:
        raise ValueError(
            "source_snapshot must use the canonical sha256:<64hex> form"
        ) from exc
    if value != f"sha256:{digest}":
        raise ValueError("source_snapshot must use the canonical sha256:<64hex> form")


def _binding_summary(
    labels: LabelSet,
    *,
    packet: ReviewPacket | None,
    review_record: ReviewRecord | None,
    packet_sha256: str | None,
    labels_sha256: str | None,
    require_binding: bool,
) -> dict[str, object]:
    if packet is None:
        if require_binding:
            raise ValueError("packet binding is required")
        return {"status": "unbound"}

    packet_by_id = {case.run_id: case for case in packet.cases}
    label_ids = {case.run_id for case in labels.cases}
    packet_ids = set(packet_by_id)
    if label_ids != packet_ids:
        missing = sorted(str(run_id) for run_id in packet_ids - label_ids)
        unexpected = sorted(str(run_id) for run_id in label_ids - packet_ids)
        raise ValueError(
            "labels do not match review packet Run IDs "
            f"(missing={missing}, unexpected={unexpected})"
        )

    if review_record is None:
        if require_binding:
            raise ValueError("structured review record is required")
        return {
            "status": "packet_bound",
            "source_snapshot": packet.source_snapshot,
            "case_count": len(packet.cases),
            "metadata_status": "missing",
        }

    if review_record.publishable_accuracy and not require_binding:
        raise ValueError("publishable accuracy requires strict binding")

    if require_binding and packet_sha256 is None:
        raise ValueError("packet file digest is required")
    if require_binding and packet.analyzer_revision is None:
        raise ValueError("review packet analyzer_revision is required")
    if require_binding and review_record.packet.analyzer_revision is None:
        raise ValueError("review record analyzer_revision is required")
    if require_binding and review_record.packet.packet_sha256 is None:
        raise ValueError("review record packet_sha256 is required")
    if require_binding and labels_sha256 is None:
        raise ValueError("labels file digest is required")
    if require_binding and review_record.packet.labels_sha256 is None:
        raise ValueError("review record labels_sha256 is required")

    _require_source_snapshot_digest(packet.source_snapshot)
    _require_source_snapshot_digest(review_record.packet.source_snapshot)
    if review_record.packet.source_snapshot != packet.source_snapshot:
        raise ValueError("review record source_snapshot does not match review packet")
    if (
        require_binding
        and packet.analyzer_revision != review_record.packet.analyzer_revision
    ):
        raise ValueError(
            "review record analyzer_revision does not match review packet"
        )
    if require_binding and packet.analyzer_revision != ANALYZER_REVISION:
        raise ValueError(
            "review packet analyzer_revision does not match current analyzer revision"
        )
    if review_record.packet.case_count != len(packet.cases):
        raise ValueError("review record case_count does not match review packet")
    if packet_sha256 is not None and review_record.packet.packet_sha256 is not None:
        if _normalized_digest(
            review_record.packet.packet_sha256, field="packet_sha256"
        ) != _normalized_digest(packet_sha256, field="packet_sha256"):
            raise ValueError("review record packet_sha256 does not match review packet")
    if labels_sha256 is not None and review_record.packet.labels_sha256 is not None:
        if _normalized_digest(
            review_record.packet.labels_sha256, field="labels_sha256"
        ) != _normalized_digest(labels_sha256, field="labels_sha256"):
            raise ValueError("review record labels_sha256 does not match labels")

    review_by_id = {case.run_id: case for case in review_record.cases}
    if set(review_by_id) != packet_ids:
        raise ValueError("review record Run IDs do not match review packet")
    for run_id, packet_case in packet_by_id.items():
        review_case = review_by_id[run_id]
        for field in ("scenario_id", "execution_mode", "kind", "task"):
            if getattr(review_case, field) != getattr(packet_case, field):
                raise ValueError(
                    f"review record {field} does not match packet for {run_id}"
                )

    return {
        "status": "bound",
        "analyzer_revision": packet.analyzer_revision,
        "source_snapshot": packet.source_snapshot,
        "packet_sha256": packet_sha256,
        "labels_sha256": labels_sha256,
        "case_count": len(packet.cases),
        "reviewer_status": review_record.reviewer.status,
        "adjudication_status": review_record.adjudication.status,
        "publishable_accuracy": review_record.publishable_accuracy,
    }


def _metric(counts: dict[str, int]) -> dict[str, int | float | None]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return {
        **counts,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def summarize_dimensions(
    comparisons: list[tuple[set[str], set[str]]],
) -> dict[str, object]:
    if not comparisons:
        raise ValueError("at least one labeled Run is required")
    known = set(DIMENSIONS)
    if any((expected | predicted) - known for expected, predicted in comparisons):
        raise ValueError("labels or predictions contain an unknown dimension")

    counts_by_dimension = {
        dimension: {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for dimension in DIMENSIONS
    }
    for expected, predicted in comparisons:
        for dimension, counts in counts_by_dimension.items():
            if dimension in expected:
                cell = "tp" if dimension in predicted else "fn"
            else:
                cell = "fp" if dimension in predicted else "tn"
            counts[cell] += 1

    totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for counts in counts_by_dimension.values():
        for key in totals:
            totals[key] += counts[key]
    return {
        "by_dimension": {
            dimension: _metric(counts)
            for dimension, counts in counts_by_dimension.items()
        },
        "micro": _metric(totals),
    }


async def report_labeled_runs(
    db: AsyncSession,
    labels: LabelSet,
    *,
    packet: ReviewPacket | None = None,
    review_record: ReviewRecord | None = None,
    packet_sha256: str | None = None,
    labels_sha256: str | None = None,
    require_binding: bool = False,
) -> dict[str, object]:
    binding = _binding_summary(
        labels,
        packet=packet,
        review_record=review_record,
        packet_sha256=packet_sha256,
        labels_sha256=labels_sha256,
        require_binding=require_binding,
    )
    packet_cases = {case.run_id: case for case in packet.cases} if packet else {}
    comparisons: list[tuple[set[str], set[str]]] = []
    disagreements: list[dict[str, object]] = []
    for case in labels.cases:
        run_id = str(case.run_id)
        run = await db.get(Run, run_id)
        if run is None:
            raise ValueError(f"labeled Run does not exist: {run_id}")
        if run.status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"labeled Run is not terminal: {run_id}")
        scenario_id = run.evaluation_spec.get("scenario_id")
        if not isinstance(scenario_id, str):
            raise ValueError(f"labeled Run has no Scenario: {run_id}")

        packet_case: PacketCase | None = None
        if packet is not None:
            packet_case = packet_cases[case.run_id]
            experiment = await db.get(Experiment, run.experiment_id)
            if experiment is None:
                raise ValueError(f"labeled Run experiment does not exist: {run_id}")
            database_identity = {
                "scenario_id": experiment.scenario_id,
                "execution_mode": experiment.execution_mode,
                "kind": run.kind,
                "task": experiment.task,
            }
            packet_identity = {
                "scenario_id": packet_case.scenario_id,
                "execution_mode": packet_case.execution_mode,
                "kind": packet_case.kind,
                "task": packet_case.task,
            }
            if database_identity != packet_identity:
                raise ValueError(
                    f"review packet identity does not match database Run: {run_id}"
                )
            packet_run_status = packet_case.trajectory.get("run_status")
            if packet_run_status != run.status:
                raise ValueError(
                    f"review packet status does not match database Run: {run_id}"
                )

        trajectory, _ = await _trajectory_from_events(db, run)
        packet_trajectory_sha256 = (
            packet_case.trajectory_sha256 if packet_case is not None else None
        )
        if (
            packet_case is not None
            and packet_trajectory_sha256 is None
            and require_binding
        ):
            raise ValueError(f"review packet trajectory_sha256 is required: {run_id}")
        if packet_trajectory_sha256 is not None:
            declared_digest = _normalized_digest(
                packet_trajectory_sha256, field="trajectory_sha256"
            )
            packet_digest = _normalized_digest(
                canonical_digest(packet_case.trajectory), field="trajectory_sha256"
            )
            current_digest = _normalized_digest(
                canonical_digest(trajectory), field="trajectory_sha256"
            )
            if declared_digest != packet_digest:
                raise ValueError(
                    f"review packet trajectory_sha256 does not match packet: {run_id}"
                )
            if declared_digest != current_digest:
                raise ValueError(
                    f"RunEvent trajectory digest does not match review packet: {run_id}"
                )
        packet_event_stream_sha256 = (
            packet_case.event_stream_sha256 if packet_case is not None else None
        )
        if (
            packet_case is not None
            and packet_event_stream_sha256 is None
            and require_binding
        ):
            raise ValueError(f"review packet event_stream_sha256 is required: {run_id}")
        if packet_event_stream_sha256 is not None:
            declared_event_digest = _normalized_digest(
                packet_event_stream_sha256, field="event_stream_sha256"
            )
            current_event_digest = _normalized_digest(
                await persisted_event_stream_digest(db, run_id),
                field="event_stream_sha256",
            )
            if declared_event_digest != current_event_digest:
                raise ValueError(
                    f"RunEvent event-stream digest does not match review packet: {run_id}"
                )
        predicted = set(
            analyze_trajectory(
                trajectory, assertion_suite=assertion_suite_for_scenario(scenario_id)
            ).dimensions
        )
        expected = set(case.dimensions)
        comparisons.append((expected, predicted))
        if predicted != expected:
            disagreements.append(
                {
                    "run_id": run_id,
                    "false_positives": sorted(predicted - expected),
                    "false_negatives": sorted(expected - predicted),
                }
            )

    return {
        "label_set": labels.name,
        "provenance": labels.provenance,
        "analyzer_revision": ANALYZER_REVISION,
        "binding": binding,
        "accuracy_status": (
            "unbound"
            if packet is None
            else "publishable"
            if review_record is not None and review_record.publishable_accuracy
            else "provisional_mechanics"
        ),
        "case_count": len(labels.cases),
        **summarize_dimensions(comparisons),
        "disagreements": disagreements,
    }


async def _run(
    labels: LabelSet,
    database_url: str,
    *,
    packet: ReviewPacket | None = None,
    review_record: ReviewRecord | None = None,
    packet_sha256: str | None = None,
    labels_sha256: str | None = None,
    require_binding: bool = False,
) -> dict[str, object]:
    engine = make_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            return await report_labeled_runs(
                db,
                labels,
                packet=packet,
                review_record=review_record,
                packet_sha256=packet_sha256,
                labels_sha256=labels_sha256,
                require_binding=require_binding,
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--review-record", type=Path)
    parser.add_argument(
        "--require-binding",
        action="store_true",
        help=(
            "require a packet and structured review record with matching "
            "digests, identities, and analyzer revision"
        ),
    )
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set")
    try:
        if args.review_record is not None and args.packet is None:
            raise ValueError("--review-record requires --packet")
        labels_bytes = args.labels.read_bytes()
        labels = LabelSet.model_validate_json(labels_bytes)
        packet_bytes = args.packet.read_bytes() if args.packet is not None else None
        packet = ReviewPacket.model_validate_json(packet_bytes) if packet_bytes else None
        review_record = (
            ReviewRecord.model_validate_json(
                args.review_record.read_bytes()
            )
            if args.review_record is not None
            else None
        )
        packet_sha256 = (
            hashlib.sha256(packet_bytes).hexdigest()
            if packet_bytes is not None
            else None
        )
        labels_sha256 = hashlib.sha256(labels_bytes).hexdigest()
        report = asyncio.run(
            _run(
                labels,
                database_url,
                packet=packet,
                review_record=review_record,
                packet_sha256=packet_sha256,
                labels_sha256=labels_sha256,
                require_binding=args.require_binding,
            )
        )
    except (OSError, ValidationError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
