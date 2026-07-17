from __future__ import annotations

import json
import os
import subprocess
import sys

from app.phase1_schemas import EvaluationSpec, PolicyPatch


def run_agent(spec: EvaluationSpec) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONPATH": os.getcwd()}
    return subprocess.run(
        [sys.executable, "-m", "app.demo_agent"],
        input=spec.model_dump_json(),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def make_spec(policy: PolicyPatch | None = None) -> EvaluationSpec:
    return EvaluationSpec(
        run_id="run-1",
        experiment_id="experiment-1",
        task="Investigate checkout latency",
        policy=policy,
    )


def test_baseline_emits_steps_and_exits_nonzero() -> None:
    result = run_agent(make_spec())
    assert result.returncode == 1
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["type"] for item in events].count("step_completed") == 6
    assert not any(item["type"].startswith("run_") for item in events)


def test_replay_emits_three_evidence_ordered_steps_and_succeeds() -> None:
    patch = PolicyPatch(
        instruction_patch=["Use evidence-first diagnostics."],
        tool_priority={"check_service_health": 1.0},
        max_steps=6,
    )
    result = run_agent(make_spec(patch))
    assert result.returncode == 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    tools = [
        item["payload"]["tool_call"]["name"]
        for item in events
        if item["type"] == "step_completed"
    ]
    assert tools == ["check_service_health", "query_service_metrics", "fetch_service_logs"]
    assert not any(item["type"].startswith("run_") for item in events)
