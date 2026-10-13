from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from app.phase1_schemas import EvaluationSpec, PolicyPatch
from app.scenario_registry import assertion_suite_for_scenario, require_scenario
from app.scoring import compute_score


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


def make_research_spec(policy: PolicyPatch | None = None) -> EvaluationSpec:
    return EvaluationSpec(
        run_id="run-research-1",
        experiment_id="experiment-research-1",
        scenario_id="multi-step-research.v1",
        task="Answer a research question",
        scenario_params={"topic": "checkout latency"},
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


def test_research_baseline_repeats_search_and_exits_nonzero() -> None:
    result = run_agent(make_research_spec())
    assert result.returncode == 1
    events = [json.loads(line) for line in result.stdout.splitlines()]
    tools = [
        item["payload"]["tool_call"]["name"]
        for item in events
        if item["type"] == "step_completed"
    ]
    assert tools == ["search_documents"] * 6


def test_research_replay_uses_search_fetch_answer_sequence() -> None:
    patch = PolicyPatch(
        instruction_patch=["Search once, fetch, then answer."],
        tool_priority={"search_documents": 1.0},
        max_steps=6,
    )
    result = run_agent(make_research_spec(patch))
    assert result.returncode == 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    tools = [
        item["payload"]["tool_call"]["name"]
        for item in events
        if item["type"] == "step_completed"
    ]
    assert tools == ["search_documents", "fetch_document", "submit_answer"]


def test_fault_golden_agent_satisfies_baseline_and_replay_contract() -> None:
    values = runpy.run_path(str(Path(__file__).parents[2] / "scripts" / "golden_e2e.py"))
    scenario_id = "api-fault-orchestration.v1"
    config = values["GOLDEN_SCENARIOS"][scenario_id]
    scenario = require_scenario(scenario_id)
    assert config["task"] == scenario.metadata.default_task
    scores = []
    for policy, exit_code, expected_tools in (
        (None, 1, ["invoke_service"] * 6),
        (
            scenario.handler.candidate_policy_patch(),
            0,
            ["retry_call", "degrade_route", "invoke_service"],
        ),
    ):
        spec = EvaluationSpec(
            run_id="run-fault-1",
            experiment_id="experiment-fault-1",
            scenario_id=scenario_id,
            task=config["task"],
            scenario_params=config["scenario_params"],
            policy=policy,
        )
        result = run_agent(spec)
        assert result.returncode == exit_code, result.stderr
        events = [json.loads(line) for line in result.stdout.splitlines()]
        steps = [item["payload"] for item in events if item["type"] == "step_completed"]
        assert [step["tool_call"]["name"] for step in steps] == expected_tools
        assert all(step["tool_call"]["arguments"]["service"] == "payments-api" for step in steps)
        if policy is None:
            assert all("HTTP 503" in step["observation"] for step in steps)
        else:
            assert steps[0]["tool_call"]["arguments"]["attempts"] == 2
            assert steps[1]["tool_call"]["arguments"]["mode"] == "cached-read"
            assert steps[-1]["observation"] == "Service responded 200 OK through the degraded route."
        score = compute_score(
            {
                "status": "success" if exit_code == 0 else "failed",
                "total_tokens": sum(step["token_prompt"] + step["token_completion"] for step in steps),
                "steps": [
                    {
                        "action": step["tool_call"],
                        "observation": step["observation"],
                        "latency_ms": step["latency_ms"],
                    }
                    for step in steps
                ],
            },
            assertion_suite=assertion_suite_for_scenario(scenario_id),
        )
        if policy is not None:
            assert score["assertions"]["aggregate"] == 1.0
        scores.append(score["score"])
    assert scores[1] > scores[0]


@pytest.mark.parametrize(
    ("scenario_args", "expected"),
    [
        (["--scenario", "api-fault-orchestration.v1"], ["api-fault-orchestration.v1"]),
        ([], ["api-fault-orchestration.v1", "checkout-api-latency.v1", "multi-step-research.v1"]),
    ],
)
def test_golden_cli_supports_fault_scenario(scenario_args, expected, monkeypatch, capsys) -> None:
    values = runpy.run_path(str(Path(__file__).parents[2] / "scripts" / "golden_e2e.py"))
    main = values["main"]
    calls = []

    def run_loop(api_url, scenario_id):
        assert api_url == "http://localhost:8000"
        calls.append(scenario_id)
        return {"scenario_id": scenario_id}

    monkeypatch.setitem(main.__globals__, "wait_for_api", lambda api_url: None)
    monkeypatch.setitem(main.__globals__, "run_golden_loop", run_loop)
    monkeypatch.setattr(sys, "argv", ["golden_e2e", *scenario_args])
    main()
    assert calls == expected
    assert json.loads(capsys.readouterr().out) == {
        scenario_id: {"scenario_id": scenario_id} for scenario_id in expected
    }
