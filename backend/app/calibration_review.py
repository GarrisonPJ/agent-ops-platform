"""Export a blind review packet from persisted terminal Runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict, deque
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.phase1_database import make_engine
from app.phase1_models import Experiment, Run
from app.phase1_schemas import TERMINAL_RUN_STATUSES
from app.phase1_service import _trajectory_from_events
from app.scenario_registry import is_registered

# Register the built-in Scenario versions accepted by the report command.
import app.scenarios  # noqa: F401


async def build_review_packet(
    db: AsyncSession, *, limit: int, source_snapshot: str
) -> dict[str, object]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if not source_snapshot.strip():
        raise ValueError("source snapshot must be identified")

    rows = (
        await db.execute(
            select(Run, Experiment)
            .join(Experiment, Run.experiment_id == Experiment.id)
            .where(Run.status.in_(TERMINAL_RUN_STATUSES))
            .order_by(Run.queued_at, Run.id)
        )
    ).all()
    strata = defaultdict(deque)
    eligible_count = 0
    for run, experiment in rows:
        scenario_id = run.evaluation_spec.get("scenario_id")
        if scenario_id != experiment.scenario_id or not is_registered(scenario_id):
            continue
        eligible_count += 1
        strata[
            (
                experiment.scenario_id,
                experiment.execution_mode,
                run.kind,
                run.status,
            )
        ].append((run, experiment))

    selected = []
    while len(selected) < limit and any(strata.values()):
        for key in sorted(strata):
            if strata[key] and len(selected) < limit:
                selected.append(strata[key].popleft())

    cases = []
    for run, experiment in selected:
        trajectory, _ = await _trajectory_from_events(db, run)
        cases.append(
            {
                "run_id": run.id,
                "scenario_id": experiment.scenario_id,
                "execution_mode": experiment.execution_mode,
                "kind": run.kind,
                "task": experiment.task,
                "trajectory": trajectory,
            }
        )
    return {
        "source_snapshot": source_snapshot,
        "selection": "terminal Runs, round-robin by scenario/mode/kind/status; oldest first within each group",
        "eligible_count": eligible_count,
        "cases": cases,
    }


async def _run(limit: int, source_snapshot: str, database_url: str) -> dict[str, object]:
    engine = make_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            return await build_review_packet(db, limit=limit, source_snapshot=source_snapshot)
    finally:
        await engine.dispose()


def write_packet(path: Path, packet: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(packet, output, indent=2)
        output.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-snapshot", required=True)
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set")
    try:
        packet = asyncio.run(_run(args.limit, args.source_snapshot, database_url))
        if not packet["cases"]:
            raise ValueError("database has no eligible terminal Runs to review")
        write_packet(args.output, packet)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote {len(packet['cases'])} blind Run cases to {args.output}")


if __name__ == "__main__":
    main()
