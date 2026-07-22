"""Terminate a real Runner and verify lease recovery with a replacement."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "infra" / "docker" / "docker-compose.phase1.yml"
RECOVERY_COMPOSE = ROOT / "infra" / "docker" / "docker-compose.phase1.recovery.yml"
LEASE_SECONDS = 15
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timed_out"}


def compose(recovery: bool, *args: str) -> None:
    command = ["docker", "compose", "-f", str(BASE_COMPOSE)]
    if recovery:
        command.extend(["-f", str(RECOVERY_COMPOSE)])
    command.extend(args)
    subprocess.run(command, cwd=ROOT, check=True)


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
            if request(api_url, "GET", "/api/health") == {
                "status": "ok",
                "protocol_version": 1,
            }:
                return
        except (OSError, RuntimeError):
            pass
        time.sleep(0.25)
    raise TimeoutError("AgentOps API did not become healthy")


def wait_for_status(
    api_url: str,
    run_id: str,
    expected: set[str],
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = request(api_url, "GET", f"/api/runs/{run_id}")
        if run["status"] in expected:
            return run
        time.sleep(0.25)
    raise TimeoutError(f"run {run_id} did not reach {sorted(expected)}")


def assert_recovered_trace(api_url: str, run_id: str) -> None:
    req = urllib.request.Request(f"{api_url.rstrip('/')}/api/runs/{run_id}/stream?after=0")
    with urllib.request.urlopen(req, timeout=10) as response:
        stream = response.read().decode("utf-8")
    if '"attempt":2' not in stream:
        raise AssertionError("recovered trace does not contain an Attempt 2 marker")


def run_recovery_test(api_url: str) -> dict:
    compose(True, "up", "-d", "--force-recreate", "runner")
    compose(
        True,
        "exec",
        "-T",
        "runner",
        "rm",
        "-f",
        "/tmp/agentops-recovery-marker",
    )

    experiment = request(
        api_url,
        "POST",
        "/api/experiments",
        {
            "name": f"Runner recovery {int(time.time())}",
            "task": "Verify Runner recovery",
            "scenario_id": "checkout-api-latency",
        },
    )
    run = request(
        api_url,
        "POST",
        f"/api/experiments/{experiment['id']}/runs",
        {"seed": 42},
    )
    run_id = run["id"]
    first_state = wait_for_status(api_url, run_id, {"claimed", "running"}, 20)

    compose(True, "kill", "runner")
    time.sleep(LEASE_SECONDS + 2)
    stranded = request(api_url, "GET", f"/api/runs/{run_id}")
    if stranded["status"] in TERMINAL_STATUSES:
        raise AssertionError(f"run completed before fault injection: {stranded['status']}")

    compose(True, "up", "-d", "runner")
    final_state = wait_for_status(api_url, run_id, {"succeeded"}, 45)
    assert_recovered_trace(api_url, run_id)
    return {
        "run_id": run_id,
        "before_runner_kill": first_state["status"],
        "after_replacement": final_state["status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()
    wait_for_api(args.api_url)
    try:
        print(json.dumps(run_recovery_test(args.api_url), indent=2))
    finally:
        compose(False, "up", "-d", "--force-recreate", "runner")


if __name__ == "__main__":
    main()
