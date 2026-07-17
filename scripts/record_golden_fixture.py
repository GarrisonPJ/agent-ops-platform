"""Record the real Golden loop and normalize only IDs and timestamps for Git."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import urllib.request

from golden_e2e import request, wait_for_api, wait_for_run


EXPERIMENT_ID = "00000000-0000-4000-8000-000000000101"
BASELINE_ID = "00000000-0000-4000-8000-000000000102"
REPLAY_ID = "00000000-0000-4000-8000-000000000103"
POLICY_ID = "00000000-0000-4000-8000-000000000104"
SECONDARY_ID = "00000000-0000-4000-8000-000000000105"


def stream_events(api_url: str, run_id: str) -> list[dict]:
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/runs/{run_id}/stream?after=0"
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        text = response.read().decode("utf-8")
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def normalize_run(run: dict, *, kind: str) -> dict:
    normalized = copy.deepcopy(run)
    run_id = BASELINE_ID if kind == "baseline" else REPLAY_ID
    normalized["id"] = run_id
    normalized["experiment_id"] = EXPERIMENT_ID
    normalized["source_run_id"] = None if kind == "baseline" else BASELINE_ID
    normalized["policy_id"] = None if kind == "baseline" else POLICY_ID
    normalized["evaluation_spec"]["run_id"] = run_id
    normalized["evaluation_spec"]["experiment_id"] = EXPERIMENT_ID
    if kind == "baseline":
        normalized["queued_at"] = "2026-07-16T08:00:01Z"
        normalized["started_at"] = "2026-07-16T08:00:02Z"
        normalized["completed_at"] = "2026-07-16T08:00:03Z"
    else:
        normalized["queued_at"] = "2026-07-16T08:01:00Z"
        normalized["started_at"] = "2026-07-16T08:01:01Z"
        normalized["completed_at"] = "2026-07-16T08:01:02Z"
    return normalized


def normalize_policy(policy: dict) -> dict:
    normalized = copy.deepcopy(policy)
    normalized["id"] = POLICY_ID
    normalized["experiment_id"] = EXPERIMENT_ID
    normalized["source_run_id"] = BASELINE_ID
    normalized["parent_policy_id"] = None
    normalized["replay_run_id"] = REPLAY_ID
    normalized["created_at"] = "2026-07-16T08:00:04Z"
    return normalized


def normalize_events(events: list[dict], *, run_id: str, replay: bool) -> list[dict]:
    normalized = copy.deepcopy(events)
    prefix = "2026-07-16T08:01:" if replay else "2026-07-16T08:00:"
    for event in normalized:
        event["run_id"] = run_id
        event["occurred_at"] = f"{prefix}{min(event['sequence'], 59):02d}Z"
    return normalized


def record(api_url: str) -> dict:
    experiment = request(
        api_url,
        "POST",
        "/api/experiments",
        {
            "name": "Checkout latency investigation",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency",
        },
    )
    baseline = request(
        api_url,
        "POST",
        f"/api/experiments/{experiment['id']}/runs",
        {"seed": 42},
    )
    baseline = wait_for_run(api_url, baseline["id"])
    baseline_analysis = request(
        api_url, "GET", f"/api/runs/{baseline['id']}/analysis"
    )
    detail = request(api_url, "GET", f"/api/experiments/{experiment['id']}")
    candidate = detail["candidate_policy"]
    candidate = request(
        api_url, "POST", f"/api/policies/{candidate['id']}/replay"
    )
    replay = wait_for_run(api_url, candidate["replay_run_id"])
    replay_analysis = request(api_url, "GET", f"/api/runs/{replay['id']}/analysis")
    detail = request(api_url, "GET", f"/api/experiments/{experiment['id']}")
    candidate = detail["candidate_policy"]

    baseline_events = stream_events(api_url, baseline["id"])
    replay_events = stream_events(api_url, replay["id"])
    baseline = normalize_run(baseline, kind="baseline")
    replay = normalize_run(replay, kind="replay")
    candidate = normalize_policy(candidate)
    baseline_analysis["run_id"] = BASELINE_ID
    replay_analysis["run_id"] = REPLAY_ID

    return {
        "experiment": {
            "id": EXPERIMENT_ID,
            "name": "Checkout latency investigation",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency",
            "created_at": "2026-07-16T08:00:00Z",
            "runs": [replay, baseline],
            "active_policy": None,
            "candidate_policy": candidate,
        },
        "secondary_experiment": {
            "id": SECONDARY_ID,
            "name": "Fresh checkout investigation",
            "task": "Investigate checkout API latency",
            "scenario_id": "checkout-api-latency",
            "created_at": "2026-07-15T14:20:00Z",
            "runs": [],
            "active_policy": None,
            "candidate_policy": None,
        },
        "baseline_analysis": baseline_analysis,
        "replay_analysis": replay_analysis,
        "events": {
            BASELINE_ID: normalize_events(
                baseline_events, run_id=BASELINE_ID, replay=False
            ),
            REPLAY_ID: normalize_events(
                replay_events, run_id=REPLAY_ID, replay=True
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "frontend/src/services/recorded/goldenJourney.json"
        ),
    )
    args = parser.parse_args()
    wait_for_api(args.api_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record(args.api_url), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Recorded Golden fixture: {args.output}")


if __name__ == "__main__":
    main()
