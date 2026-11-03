"""H3-1 trial groups: n ordinary Runs on one seed (ADR-0010 and its H3-1 amendment)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.phase1_models import Experiment, Run, RunnerJob, TrialGroup

ROOT = Path(__file__).resolve().parents[1]
AUTH = {"Authorization": "Bearer test-runner-token"}


async def create_experiment(client, **extra) -> dict:
    response = await client.post("/api/experiments", json={
        "name": "Trials", "task": "Investigate checkout API latency",
        "scenario_id": "checkout-api-latency.v2", **extra,
    })
    assert response.status_code == 201
    return response.json()


async def test_trial_group_creates_n_ordinary_runs_on_one_seed(api) -> None:
    client, factory = api
    experiment = await create_experiment(client)
    response = await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 3, "k": 2, "seed": 7})
    assert response.status_code == 201
    group = response.json()
    assert (group["experiment_id"], group["policy_id"], group["n"], group["k"], group["seed"]) == (
        experiment["id"], None, 3, 2, 7)
    runs = group["runs"]
    assert [run["trial_index"] for run in runs] == [1, 2, 3]
    assert {run["trial_group_id"] for run in runs} == {group["id"]}
    assert {(run["kind"], run["status"]) for run in runs} == {("baseline", "queued")}
    # Specs are equal except run_id, so an Agent cannot tell a trial from a single Run.
    for run in runs:
        assert run["evaluation_spec"]["run_id"] == run["id"]
        assert run["evaluation_spec"]["seed"] == 7
    stripped = [{**run["evaluation_spec"], "run_id": None} for run in runs]
    assert stripped[0] == stripped[1] == stripped[2]
    assert set(stripped[0]) == set(runs[0]["evaluation_spec"])  # no trial fields in the protocol spec
    async with factory() as db:
        jobs = (await db.execute(select(RunnerJob).where(RunnerJob.run_id.in_([r["id"] for r in runs])))).scalars().all()
        assert sorted(job.attempt for job in jobs) == [1, 1, 1]
        assert (await db.get(TrialGroup, group["id"])).n == 3


async def test_trial_members_are_claimed_like_any_run(api) -> None:
    client, _ = api
    experiment = await create_experiment(client)
    group = (await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 2, "k": 1})).json()
    claimed = set()
    for runner in ("runner-1", "runner-2"):
        response = await client.post("/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": runner})
        assert response.status_code == 200
        claimed.add(response.json()["run"]["run_id"])
    assert claimed == {run["id"] for run in group["runs"]}
    empty = await client.post("/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "runner-3"})
    assert empty.status_code == 204


async def test_experiment_lists_trials_and_single_runs_side_by_side(api) -> None:
    client, _ = api
    experiment = await create_experiment(client)
    single = (await client.post(f"/api/experiments/{experiment['id']}/runs", json={})).json()
    assert (single["trial_group_id"], single["trial_index"]) == (None, None)
    group = (await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 2, "k": 2})).json()
    detail = (await client.get(f"/api/experiments/{experiment['id']}")).json()
    by_id = {run["id"]: run for run in detail["runs"]}
    assert by_id[single["id"]]["trial_group_id"] is None
    assert {by_id[run["id"]]["trial_group_id"] for run in group["runs"]} == {group["id"]}


@pytest.mark.parametrize("body", [
    {"n": 2, "k": 3}, {"n": 0, "k": 1}, {"n": 21, "k": 1}, {"n": 3, "k": 0},
    {"n": True, "k": 1}, {"n": 3, "k": "2"}, {"n": 3}, {"k": 1}, {"n": 3, "k": 1, "extra": 1},
    {"n": 3, "k": 1, "seed": -1},
])
async def test_trial_group_rejects_invalid_bodies(api, body) -> None:
    client, factory = api
    experiment = await create_experiment(client)
    response = await client.post(f"/api/experiments/{experiment['id']}/trials", json=body)
    assert response.status_code == 422
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(TrialGroup)) == 0
        assert await db.scalar(select(func.count()).select_from(Run)) == 0


async def test_trial_group_for_unknown_experiment_is_404(api) -> None:
    client, _ = api
    response = await client.post("/api/experiments/missing/trials", json={"n": 2, "k": 1})
    assert response.status_code == 404


async def test_database_rejects_duplicate_trial_index_and_k_above_n(api) -> None:
    client, factory = api
    experiment = await create_experiment(client)
    group = (await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 2, "k": 1})).json()
    async with factory() as db:
        source = await db.get(Run, group["runs"][0]["id"])
        db.add(Run(experiment_id=experiment["id"], kind="baseline", trial_group_id=group["id"], trial_index=1,
                   status="queued", evaluation_spec=source.evaluation_spec, metrics={}))
        with pytest.raises(IntegrityError):
            await db.commit()
    async with factory() as db:
        db.add(TrialGroup(experiment_id=experiment["id"], n=2, k=3, seed=42))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_deleting_experiment_deletes_its_groups_and_members(api) -> None:
    client, factory = api
    experiment = await create_experiment(client)
    await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 2, "k": 1})
    async with factory() as db:
        await db.delete(await db.get(Experiment, experiment["id"]))
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(TrialGroup)) == 0
        assert await db.scalar(select(func.count()).select_from(Run)) == 0


def _alembic(environment: dict, *args: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=environment,
                   check=True, capture_output=True, text=True)


def test_migration_upgrade_downgrade_keeps_existing_runs_outside_groups(tmp_path: Path) -> None:
    database = tmp_path / "trials.sqlite3"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    _alembic(environment, "upgrade", "0009_experiment_agent_id")
    spec = {"run_id": "old-run", "experiment_id": "old-experiment", "task": "t", "execution_mode": "fixture"}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO experiments (id, name, task, scenario_id, scenario_params, execution_mode, created_at) "
            "VALUES ('old-experiment', 'Old', 't', 'checkout-api-latency.v2', '{}', 'fixture', '2026-01-01 00:00:00')")
        connection.execute(
            "INSERT INTO runs (id, experiment_id, kind, status, metrics, evaluation_spec, queued_at) "
            "VALUES ('old-run', 'old-experiment', 'baseline', 'succeeded', '{}', ?, '2026-01-01 00:00:00')",
            (json.dumps(spec),))
    _alembic(environment, "upgrade", "0010_trial_groups")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        assert {"trial_group_id", "trial_index"} <= columns
        assert {row[1] for row in connection.execute("PRAGMA table_info(trial_groups)")} == {
            "id", "experiment_id", "policy_id", "n", "k", "seed", "created_at"}
        assert connection.execute("SELECT trial_group_id, trial_index FROM runs").fetchone() == (None, None)
        assert json.loads(connection.execute("SELECT evaluation_spec FROM runs").fetchone()[0]) == spec
    _alembic(environment, "downgrade", "0009_experiment_agent_id")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        assert not {"trial_group_id", "trial_index"} & columns
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trial_groups'").fetchone() is None
        assert connection.execute("SELECT id FROM runs").fetchone() == ("old-run",)
