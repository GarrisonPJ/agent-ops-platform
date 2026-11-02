"""Capture opt-in provider-backed Agent Runs through an already configured local stack.

The operator starts CP/PostgreSQL/Runner; this script calls only the product API.
It never supplies Provider settings, repairs model output, or treats capture as task success.
Read docs/operations/local-model.md for setup and interpretation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlparse

CASES = {
    "checkout-api-latency.v2": (
        "Investigate checkout API latency using Environment tools, follow the evidence, "
        "and explicitly submit_result when complete.", {},
    ),
    "multi-step-research.v2": (
        "Research checkout latency using the available Environment tools. Gather evidence, "
        "submit an evidence-backed answer, then explicitly call submit_result when complete.",
        {"topic": "checkout latency"},
    ),
    "api-fault-orchestration.v2": (
        "Recover the payments-api dependency. Use a bounded retry, enable a cached fallback, "
        "verify service health, and explicitly submit_result when complete.",
        {"service": "payments-api"},
    ),
    "order-support.v1": (
        "Handle order-1001: inspect, authorize payment, attempt shipping, and refund if shipping "
        "fails. Retry an uncertain refund safely, verify the final order, then submit_result.", {},
    ),
}
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled"}
# Registered provider-backed Agents; pi needs config/agents.external.toml (H2-1).
AGENTS = ("reference-openai-compatible.v1", "pi-coding-agent.v1")


def validate_capture(run: dict, events: list[dict], analysis: dict) -> dict:
    """Verify persistence/identity, not model quality or analyzer accuracy."""
    rid = run["id"]
    if run["status"] not in TERMINAL or analysis.get("run_id") != rid:
        raise ValueError("Run/analysis identity or terminal status mismatch")
    if not events or [event.get("sequence") for event in events] != list(range(1, len(events) + 1)):
        raise ValueError("Trace sequence is missing or noncontiguous")
    if any(event.get("run_id") != rid for event in events):
        raise ValueError("Trace Run identity mismatch")
    expected_terminal = {
        "succeeded": "run_completed", "cancelled": "run_cancelled",
        "failed": "run_failed", "timed_out": "run_failed",
    }[run["status"]]
    if events[-1].get("type") != expected_terminal or events[-1].get("payload", {}).get("status") != run["status"]:
        raise ValueError("Trace terminal disagrees with persisted Run")
    steps = [event["payload"] for event in events if event["type"] == "step_completed"]
    submissions = [step for step in steps if step.get("environment", {}).get("control") == "submit_result"
                   and step["environment"].get("result") == "ok"]
    if run["status"] == "succeeded":
        metrics = run.get("metrics") or {}
        if (len(submissions) != 1 or not submissions[0]["environment"].get("check_called")
                or not submissions[0]["environment"].get("finalized")
                or submissions[0].get("tool_call") != {"name": "submit_result", "arguments": {}}
                or submissions[0]["environment"].get("final_check", {}).get("scenario_id")
                != run["evaluation_spec"]["scenario_id"]
                or metrics.get("assertions", {}).get("aggregate") != 1.0):
            raise ValueError("Successful Run lacks accepted submission/check/assertions")
    return {
        "run_id": rid, "scenario_id": run["evaluation_spec"]["scenario_id"],
        "status": run["status"], "score": run.get("score"),
        "event_count": len(events), "accepted_submissions": len(submissions),
        "world_calls": [step.get("tool_call", {}).get("name") for step in steps
                        if step.get("environment", {}).get("kind") == "world"],
        "failure_rate": analysis.get("failure_rate"),
    }


def capture(api_url: str, scenario: str, output: Path, timeout_seconds: float,
            agent_id: str = AGENTS[0]) -> dict:
    # Bypass proxy env for the explicitly selected local product API. No Provider
    # credential or configuration ever enters the Experiment request.
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None):
        req = urllib.request.Request(api_url.rstrip("/") + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with http.open(req, timeout=10) as response:
            return response.read()

    def save(name, value):
        (output/name).write_text(json.dumps(value, indent=2) + "\n")

    output.mkdir(mode=0o700)
    task, params = CASES[scenario]
    experiment = json.loads(request("/api/experiments", {
        "name": f"Local model acceptance: {scenario} / {agent_id}", "task": task,
        "scenario_id": scenario, "scenario_params": params,
        "execution_mode": "provider", "agent_id": agent_id,
    }))
    save("experiment.json", experiment)
    run = json.loads(request("/api/experiments/" + experiment["id"] + "/runs", {}))
    save("created.json", run)
    rid = run["id"]
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = json.loads(request("/api/runs/" + rid))
        save("final.json", run)
        if run["status"] in TERMINAL:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Observation deadline reached; inspect recorded Run ID before retrying")
        time.sleep(0.5)
    analysis = json.loads(request("/api/runs/" + rid + "/analysis"))
    save("analysis.json", analysis)
    save("diagnostics.json", json.loads(request("/api/operations/runs/" + rid)))
    stream = request("/api/runs/" + rid + "/stream?after=0").decode()
    (output/"events.sse").write_text(stream)
    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
    result = validate_capture(run, events, analysis) | {"agent_id": agent_id}
    save("summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", choices=CASES, action="append")
    parser.add_argument("--output-directory", type=Path, required=True, help="Absent directory; parent must exist")
    parser.add_argument("--timeout-seconds", type=float, default=90)
    parser.add_argument("--require-success", action="store_true", help="Exit nonzero if any selected task fails")
    parser.add_argument("--agent-id", choices=AGENTS, default=AGENTS[0])
    args = parser.parse_args()
    url = urlparse(args.api_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        parser.error("--api-url must be a credential-free loopback HTTP origin")
    if not 1 <= args.timeout_seconds <= 300:
        parser.error("--timeout-seconds must be 1..300")
    args.output_directory.mkdir(mode=0o700)
    if args.output_directory.stat().st_mode & 0o077:
        parser.error("--output-directory must have owner-only permissions")
    scenarios = list(dict.fromkeys(args.scenario or ["multi-step-research.v2"]))
    results = [capture(args.api_url, scenario, args.output_directory/scenario, args.timeout_seconds, args.agent_id)
               for scenario in scenarios]
    summary = {"capture_completed": True, "all_tasks_succeeded": all(r["status"] == "succeeded" for r in results), "runs": results}
    (args.output_directory/"summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return int(args.require_success and not summary["all_tasks_succeeded"])


if __name__ == "__main__":
    raise SystemExit(main())
