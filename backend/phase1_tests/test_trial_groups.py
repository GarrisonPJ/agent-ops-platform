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


# --- H3-2: aggregates and group policy validation through real completion ---

from test_control_plane import envelope, step, upload  # noqa: E402
from test_cp_completion import facts  # noqa: E402


async def claim_any(client) -> dict | None:
    response = await client.post("/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "runner-1"})
    return response.json() if response.status_code == 200 else None


async def finish(client, claimed: dict, outcome: str) -> dict:
    """Drive one claimed Run: 's' = evidence-first success, 'f' = repeated logs failure, 'c' = cancel."""
    run_id = claimed["run"]["run_id"]
    if outcome == "c":
        cancelled = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        completed = await client.post(
            f"/api/internal/runner/jobs/{claimed['lease_id']}/complete", headers=AUTH,
            json={"runner_id": "runner-1", "status": "cancelled", "execution": facts(run_id)})
        assert completed.status_code == 200, completed.text
        return completed.json()["run"]
    if outcome == "s":
        events = [
            envelope(run_id, 1, "run_started"),
            step(run_id, 2, 0, "check_service_health", "Service is healthy; latency is elevated."),
            step(run_id, 3, 1, "query_service_metrics", "Payment dependency p95 is 1.8s."),
            step(run_id, 4, 2, "fetch_service_logs", "Payment connection pool is saturated."),
            envelope(run_id, 5, "step_completed", {"environment": {
                "kind": "control", "control": "submit_result", "result": "ok",
                "finalized": True, "check_called": True,
                "final_check": {"scenario_id": "checkout-api-latency.v2", "final_observation": None},
            }}),
        ]
        body = {"runner_id": "runner-1", "status": "succeeded", "execution": facts(run_id)}
    else:
        events = [envelope(run_id, 1, "run_started")] + [
            step(run_id, index + 2, index, "fetch_service_logs", "The same noisy samples are returned.")
            for index in range(6)
        ]
        body = {"runner_id": "runner-1", "status": "failed", "error": "step budget exhausted",
                "execution": facts(run_id)}
    await upload(client, claimed, events)
    completed = await client.post(f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
                                  headers=AUTH, json=body)
    assert completed.status_code == 200, completed.text
    assert completed.json()["run"]["status"] == {"s": "succeeded", "f": "failed"}[outcome]
    return completed.json()["run"]


async def drive(client, pattern: str, count: int | None = None) -> list[dict]:
    """Claim `count` queued Runs and finish each by its trial_index: pattern[trial_index - 1]."""
    finished = []
    total = len(pattern) if count is None else count
    while len(finished) < total:
        claimed = await claim_any(client)
        assert claimed is not None
        run = (await client.get(f"/api/runs/{claimed['run']['run_id']}")).json()
        finished.append(await finish(client, claimed, pattern[run["trial_index"] - 1]))
    return finished


async def group_of(client, experiment_id: str, group_id: str) -> dict:
    detail = (await client.get(f"/api/experiments/{experiment_id}")).json()
    return next(item for item in detail["trial_groups"] if item["id"] == group_id)


async def baseline_group(client, pattern: str, k: int) -> tuple[dict, dict]:
    experiment = await create_experiment(client)
    created = (await client.post(f"/api/experiments/{experiment['id']}/trials",
                                 json={"n": len(pattern), "k": k})).json()
    assert (created["state"], created["terminal"], created["pass_at_k"]) == ("running", 0, None)
    await drive(client, pattern)
    return experiment, await group_of(client, experiment["id"], created["id"])


async def test_group_publishes_pass_k_only_when_every_member_is_terminal(api) -> None:
    client, _ = api
    experiment = await create_experiment(client)
    created = (await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 3, "k": 2})).json()
    await drive(client, "fsf", count=2)
    partial = await group_of(client, experiment["id"], created["id"])
    assert (partial["state"], partial["terminal"], partial["pass_at_1"], partial["pass_at_k"]) == (
        "running", 2, None, None)
    await drive(client, "fsf", count=1)
    done = await group_of(client, experiment["id"], created["id"])
    success_score = next(r for r in (await client.get(f"/api/experiments/{experiment['id']}")).json()["runs"]
                         if r["status"] == "succeeded")["score"]
    assert (done["state"], done["terminal"], done["successes"]) == ("complete", 3, 1)
    assert done["pass_at_1"] == pytest.approx(1 / 3)
    assert done["pass_at_k"] == 0.0  # C(1, 2) / C(3, 2)
    assert done["mean_score"] == pytest.approx(success_score / 3)
    assert done["mean_steps"] == pytest.approx((6 + 3 + 6) / 3)
    assert done["run_ids"] == created["run_ids"]


async def test_one_candidate_policy_per_baseline_group(api) -> None:
    client, factory = api
    experiment, _ = await baseline_group(client, "fff", 1)
    async with factory() as db:
        from app.phase1_models import Policy
        policies = (await db.execute(select(Policy).where(Policy.experiment_id == experiment["id"]))).scalars().all()
    assert len(policies) == 1


async def test_group_replay_waits_for_published_baseline_group(api) -> None:
    client, _ = api
    experiment = await create_experiment(client)
    await client.post(f"/api/experiments/{experiment['id']}/trials", json={"n": 2, "k": 1})
    await drive(client, "ff", count=1)
    candidate = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]
    response = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert response.status_code == 409
    assert response.json()["code"] == "BASELINE_GROUP_NOT_PUBLISHED"


async def test_cancelled_baseline_member_blocks_group_replay(api) -> None:
    client, _ = api
    experiment, group = await baseline_group(client, "fc", 1)
    assert group["state"] == "cancelled" and group["pass_at_1"] is None
    candidate = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]
    response = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert response.status_code == 409


async def replay_group(client, experiment: dict) -> tuple[dict, dict]:
    candidate = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]
    replaying = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert replaying.status_code == 201
    assert replaying.json()["status"] == "replaying"
    again = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert again.json()["replay_run_id"] == replaying.json()["replay_run_id"]  # invariant 19
    detail = (await client.get(f"/api/experiments/{experiment['id']}")).json()
    group = next(item for item in detail["trial_groups"] if item["policy_id"] == candidate["id"])
    return candidate, group


async def test_candidate_group_mirrors_baseline_group(api) -> None:
    client, _ = api
    experiment, baseline = await baseline_group(client, "fsf", 2)
    candidate, group = await replay_group(client, experiment)
    assert (group["n"], group["k"], group["seed"], group["state"]) == (3, 2, baseline["seed"], "running")
    runs = {run["id"]: run for run in (await client.get(f"/api/experiments/{experiment['id']}")).json()["runs"]}
    members = [runs[run_id] for run_id in group["run_ids"]]
    assert [m["trial_index"] for m in members] == [1, 2, 3]
    assert {m["kind"] for m in members} == {"replay"}
    assert [runs[m["source_run_id"]]["trial_index"] for m in members] == [1, 2, 3]
    assert {m["evaluation_spec"]["seed"] for m in members} == {baseline["seed"]}
    assert all(m["evaluation_spec"]["policy"] == candidate["patch"] for m in members)


@pytest.mark.parametrize(("baseline_pattern", "candidate_pattern", "k", "expected"), [
    ("fsf", "sss", 2, "validated"),
    ("ssf", "sff", 2, "candidate"),   # pass^k drops
    ("fsf", "fsf", 1, "candidate"),   # no mean improvement
    ("fsf", "ssc", 2, "candidate"),   # cancelled candidate member: never published
    ("fff", "sff", 3, "candidate"),   # one lucky success against pass^k = 0 (tightened rule)
])
async def test_candidate_group_validation(api, baseline_pattern, candidate_pattern, k, expected) -> None:
    client, _ = api
    experiment, baseline = await baseline_group(client, baseline_pattern, k)
    candidate, group = await replay_group(client, experiment)
    await drive(client, candidate_pattern, count=len(candidate_pattern) - 1)
    policy = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]
    assert policy["status"] == "replaying"  # nothing decided until the group is complete
    await drive(client, candidate_pattern, count=1)
    detail = (await client.get(f"/api/experiments/{experiment['id']}")).json()
    decided = detail["candidate_policy"]
    final_group = next(item for item in detail["trial_groups"] if item["id"] == group["id"])
    assert decided["status"] == expected
    if final_group["state"] == "complete":
        assert decided["score_delta"] == pytest.approx(final_group["mean_score"] - baseline["mean_score"])
    else:
        assert decided["score_delta"] is None


async def test_single_trial_group_keeps_invariant_seven(api) -> None:
    client, _ = api
    experiment, baseline = await baseline_group(client, "f", 1)
    candidate, group = await replay_group(client, experiment)
    await drive(client, "s")
    decided = (await client.get(f"/api/experiments/{experiment['id']}")).json()["candidate_policy"]
    assert decided["status"] == "validated"
    assert decided["score_delta"] > 0
