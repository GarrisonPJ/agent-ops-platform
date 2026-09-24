"""Offline dimension metrics for independently labeled, persisted Runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.failure_analyzer import analyze_trajectory
from app.phase1_database import make_engine
from app.phase1_models import Run
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


async def report_labeled_runs(db: AsyncSession, labels: LabelSet) -> dict[str, object]:
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

        trajectory, _ = await _trajectory_from_events(db, run)
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
        "case_count": len(labels.cases),
        **summarize_dimensions(comparisons),
        "disagreements": disagreements,
    }


async def _run(labels: LabelSet, database_url: str) -> dict[str, object]:
    engine = make_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            return await report_labeled_runs(db, labels)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set")
    try:
        labels = LabelSet.model_validate_json(args.labels.read_text(encoding="utf-8"))
        report = asyncio.run(_run(labels, database_url))
    except (OSError, ValidationError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
