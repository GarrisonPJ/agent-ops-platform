"""Backend half of the backend -> frontend zod drift guard.

What this is: seeds a SQLite app in-process, records the real JSON the
frontend consumes (agents, scenarios, experiments, runs, analyses, policies,
regression cases, run events) and compares it to the committed snapshot
``frontend/src/test/contract/backend-responses.json``.
Where it sits: pytest here writes/checks the snapshot; the vitest file
``frontend/src/test/contract/backend-contract.test.ts`` parses every entry
with the strict zod schemas in ``frontend/src/schemas/phase1.ts``.
Invariant: ids and timestamps are normalised, so the snapshot only changes
when a response shape or value changes. A stale snapshot fails; regenerate
with ``UPDATE_CONTRACT_SNAPSHOT=1 uv run pytest phase1_tests/test_frontend_contract_snapshot.py``.
Next file to read: ``frontend/src/schemas/phase1.ts``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from test_control_plane import envelope, step, upload
from test_cp_completion import facts
from test_trial_groups import AUTH, claim_any, create_experiment, drive

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "frontend/src/test/contract/backend-responses.json"
EXTERNAL_REGISTRY = REPO / "config/agents.external.toml"

ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?")


def normalise(value: object, ids: dict[str, str]) -> object:
    """Replace uuids with first-seen placeholders and timestamps with a marker."""
    if isinstance(value, dict):
        return {key: normalise(item, ids) for key, item in value.items()}
    if isinstance(value, list):
        return [normalise(item, ids) for item in value]
    if isinstance(value, str):
        value = TIMESTAMP_PATTERN.sub("<timestamp>", value)
        return ID_PATTERN.sub(lambda m: ids.setdefault(m.group(0), f"<id-{len(ids) + 1}>"), value)
    return value


def canonical_order(value: object) -> object:
    """Order ``runs`` by (kind, trial_index): trial members share a queued_at, so the
    API's order among them is not stable and is not part of the contract."""
    if isinstance(value, dict):
        return {
            key: (
                sorted(item, key=lambda r: (r["kind"], r["trial_index"] or 0))
                if key == "runs" and isinstance(item, list)
                else canonical_order(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [canonical_order(item) for item in value]
    return value


def sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture(autouse=True)
def external_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_AGENT_REGISTRY", str(EXTERNAL_REGISTRY))


async def get_json(client, path: str):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.text)
    return response.json()


async def record(client) -> dict[str, object]:
    out: dict[str, object] = {}
    out["agents"] = await get_json(client, "/api/agents")
    out["scenarios"] = await get_json(client, "/api/scenarios")

    # Experiment 1: completed trial group (fail, success, fail) -> candidate policy.
    exp = await create_experiment(client)
    created = await client.post(
        f"/api/experiments/{exp['id']}/trials", json={"n": 3, "k": 2, "seed": 7}
    )
    assert created.status_code == 201
    await drive(client, "fsf")
    detail = await get_json(client, f"/api/experiments/{exp['id']}")
    out["experiment_trials_complete"] = detail
    out["policy_candidate"] = detail["candidate_policy"]
    runs = detail["runs"]
    # drive("fsf") makes trial 1 fail and trial 2 succeed; pick by index, not list order.
    by_trial = {r["trial_index"]: r for r in runs if r["kind"] == "baseline"}
    for status, run in (("failed", by_trial[1]), ("succeeded", by_trial[2])):
        assert run["status"] == status
        out[f"run_{status}"] = run
        out[f"analysis_{status}"] = await get_json(client, f"/api/runs/{run['id']}/analysis")
        stream = await client.get(f"/api/runs/{run['id']}/stream?after=0")
        out[f"events_{status}"] = sse_events(stream.text)
    regression = await client.post(f"/api/runs/{by_trial[1]['id']}/regression-case")
    assert regression.status_code in (200, 201), regression.text
    out["regression_case"] = regression.json()
    out["experiment_with_regression"] = await get_json(client, f"/api/experiments/{exp['id']}")
    # Experiment 3: cancelled trial group member.
    exp3 = await create_experiment(client)
    await client.post(f"/api/experiments/{exp3['id']}/trials", json={"n": 2, "k": 1})
    await drive(client, "fc")
    out["experiment_trials_cancelled"] = await get_json(client, f"/api/experiments/{exp3['id']}")

    # Experiment 4: single Run that times out after process output; then reject path.
    exp4 = await create_experiment(client)
    await client.post(f"/api/experiments/{exp4['id']}/runs", json={})
    claimed = await claim_any(client)
    run_id = claimed["run"]["run_id"]
    await upload(
        client,
        claimed,
        [
            envelope(run_id, 1, "run_started"),
            step(run_id, 2, 0, "fetch_service_logs", "Same samples again."),
            envelope(run_id, 3, "process_output", {"stream": "stderr", "content": "agent log"}),
            envelope(
                run_id, 4, "process_output", {"kind": "agent", "stream": "stdout", "content": "x"}
            ),
        ],
    )
    done = await client.post(
        f"/api/internal/runner/jobs/{claimed['lease_id']}/complete",
        headers=AUTH,
        json={"runner_id": "runner-1", "status": "timed_out", "execution": facts(run_id)},
    )
    assert done.status_code == 200, done.text
    out["run_timed_out"] = await get_json(client, f"/api/runs/{run_id}")
    out["analysis_timed_out"] = await get_json(client, f"/api/runs/{run_id}/analysis")
    stream = await client.get(f"/api/runs/{run_id}/stream?after=0")
    out["events_timed_out"] = sse_events(stream.text)
    detail4 = await get_json(client, f"/api/experiments/{exp4['id']}")
    if detail4["candidate_policy"]:
        rejected = await client.post(
            f"/api/policies/{detail4['candidate_policy']['id']}/reject", json={"reason": "no"}
        )
        assert rejected.status_code == 200, rejected.text
        out["policy_rejected"] = rejected.json()

    # Experiment 2: provider-mode external-agent trial group, still running.
    exp2 = await create_experiment(
        client, execution_mode="provider", agent_id="pi-coding-agent.v1"
    )
    await client.post(f"/api/experiments/{exp2['id']}/trials", json={"n": 2, "k": 1})
    await drive(client, "ff", count=1)
    out["experiment_trials_running"] = await get_json(client, f"/api/experiments/{exp2['id']}")

    candidate = detail["candidate_policy"]
    replay = await client.post(f"/api/policies/{candidate['id']}/replay")
    assert replay.status_code == 201, replay.text
    out["policy_replaying"] = replay.json()
    out["experiment_replay_group"] = await get_json(client, f"/api/experiments/{exp['id']}")

    out["experiments_list"] = await get_json(client, "/api/experiments")
    return out


async def test_frontend_contract_snapshot_is_current(api) -> None:
    client, _ = api
    recorded = normalise(canonical_order(await record(client)), {})
    rendered = json.dumps(recorded, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_CONTRACT_SNAPSHOT") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(rendered)
        return
    assert SNAPSHOT.exists(), "snapshot missing; run with UPDATE_CONTRACT_SNAPSHOT=1"
    assert json.loads(SNAPSHOT.read_text()) == recorded, (
        "backend responses drifted from frontend/src/test/contract/backend-responses.json; "
        "check frontend zod schemas, then rerun with UPDATE_CONTRACT_SNAPSHOT=1"
    )


def test_normalise_replaces_ids_and_timestamps_stably() -> None:
    uid = "de0a8f6f-2dd4-415b-a4b5-636547622936"
    other = "11111111-2222-3333-4444-555555555555"
    value = {"a": [uid, other, uid], "t": "2026-11-09T11:45:04.774525", "n": 3}
    assert normalise(value, {}) == {
        "a": ["<id-1>", "<id-2>", "<id-1>"],
        "t": "<timestamp>",
        "n": 3,
    }
