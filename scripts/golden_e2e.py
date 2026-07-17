"""Exercise the real FastAPI -> PostgreSQL -> Rust Runner Golden loop."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timed_out"}


def request(api_url: str, method: str, path: str, body: dict | None = None) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc
    return json.loads(raw) if raw else None


def wait_for_api(api_url: str, timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            health = request(api_url, "GET", "/api/health")
            if health == {"status": "ok", "protocol_version": 1}:
                return
        except (OSError, RuntimeError):
            pass
        time.sleep(0.25)
    raise TimeoutError("AgentOps API did not become healthy")


def wait_for_run(api_url: str, run_id: str, timeout_seconds: float = 30) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = request(api_url, "GET", f"/api/runs/{run_id}")
        if run["status"] in TERMINAL_STATUSES:
            return run
        time.sleep(0.2)
    raise TimeoutError(f"run {run_id} did not reach a terminal state")


def assert_persisted_trace(api_url: str, run_id: str) -> None:
    req = urllib.request.Request(f"{api_url.rstrip('/')}/api/runs/{run_id}/stream?after=0")
    with urllib.request.urlopen(req, timeout=10) as response:
        stream = response.read().decode("utf-8")
    if '"type":"step_completed"' not in stream or '"sequence":1' not in stream:
        raise AssertionError(f"run {run_id} did not replay its persisted trace")


def run_golden_loop(api_url: str) -> dict:
    experiment = request(
        api_url,
        "POST",
        "/api/experiments",
        {
            "name": f"Golden checkout latency {int(time.time())}",
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
    if baseline["status"] != "failed":
        raise AssertionError(f"baseline should fail, got {baseline['status']}")
    assert_persisted_trace(api_url, baseline["id"])

    detail = request(api_url, "GET", f"/api/experiments/{experiment['id']}")
    candidate = detail["candidate_policy"]
    if not candidate or candidate["status"] != "candidate":
        raise AssertionError("baseline did not produce a candidate policy")

    candidate = request(api_url, "POST", f"/api/policies/{candidate['id']}/replay")
    replay = wait_for_run(api_url, candidate["replay_run_id"])
    if replay["status"] != "succeeded":
        raise AssertionError(f"replay should succeed, got {replay['status']}")
    assert_persisted_trace(api_url, replay["id"])

    detail = request(api_url, "GET", f"/api/experiments/{experiment['id']}")
    validated = detail["candidate_policy"]
    if validated["status"] != "validated" or validated["score_delta"] <= 0:
        raise AssertionError("candidate was not validated with a positive score delta")

    active = request(api_url, "POST", f"/api/policies/{validated['id']}/activate")
    if active["status"] != "active":
        raise AssertionError("validated policy was not activated")
    final_detail = request(api_url, "GET", f"/api/experiments/{experiment['id']}")
    if final_detail["active_policy"]["id"] != active["id"]:
        raise AssertionError("experiment does not expose the activated policy")

    return {
        "experiment_id": experiment["id"],
        "baseline": {
            "id": baseline["id"],
            "status": baseline["status"],
            "score": baseline["score"],
        },
        "replay": {
            "id": replay["id"],
            "status": replay["status"],
            "score": replay["score"],
        },
        "policy": {
            "id": active["id"],
            "status": active["status"],
            "score_delta": active["score_delta"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()
    wait_for_api(args.api_url)
    print(json.dumps(run_golden_loop(args.api_url), indent=2))


if __name__ == "__main__":
    main()
