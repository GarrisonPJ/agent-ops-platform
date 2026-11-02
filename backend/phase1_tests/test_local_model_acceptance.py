"""Offline contract tests for the opt-in local-model capture command.

Validate captured Run/trace consistency without starting a stack or real Provider.
A captured failure must remain failure; successful claims require submission/check facts.
Read scripts/local_model_acceptance.py for the operator entry point.
"""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "local_model_acceptance", Path(__file__).resolve().parents[2]/"scripts/local_model_acceptance.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(status="succeeded"):
    run = {"id": "r", "status": status, "evaluation_spec": {"scenario_id": "multi-step-research.v2"},
           "metrics": {"assertions": {"aggregate": 1.0}}}
    events = [
        {"run_id": "r", "sequence": 1, "type": "run_started", "payload": {}},
        {"run_id": "r", "sequence": 2, "type": "step_completed", "payload": {
            "environment": {"kind": "control", "control": "submit_result", "result": "ok", "finalized": True, "check_called": True,
                            "final_check": {"scenario_id": "multi-step-research.v2"}},
            "tool_call": {"name": "submit_result", "arguments": {}},
        }},
        {"run_id": "r", "sequence": 3, "type": {"succeeded": "run_completed", "cancelled": "run_cancelled"}.get(status, "run_failed"),
         "payload": {"status": status}},
    ]
    return run, events, {"run_id": "r", "failure_rate": 0.0}


def test_successful_capture_requires_persisted_submission_and_terminal():
    assert module.validate_capture(*sample())["status"] == "succeeded"


def test_cancelled_capture_requires_cancelled_terminal_event():
    run, events, analysis = sample("cancelled")
    result = module.validate_capture(run, events, analysis)
    assert result["status"] == "cancelled"


def test_successful_capture_rejects_nonempty_submit_arguments():
    run, events, analysis = sample()
    events[1]["payload"]["tool_call"]["arguments"] = {"answer": "not accepted"}
    with pytest.raises(ValueError):
        module.validate_capture(run, events, analysis)


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_actual_failures_are_preserved_without_requiring_submission(status):
    run, events, analysis = sample(status)
    events[1]["payload"] = {"environment": {"kind": "world", "result": "invalid_arguments"},
                            "tool_call": {"name": "fetch_service_logs"}}
    result = module.validate_capture(run, events, analysis)
    assert result["status"] == status and result["accepted_submissions"] == 0


@pytest.mark.parametrize("fault", ["sequence", "run_id", "terminal", "analysis", "submit", "check", "aggregate"])
def test_inconsistent_success_fails_closed(fault):
    run, events, analysis = copy.deepcopy(sample())
    if fault == "sequence": events[1]["sequence"] = 4
    if fault == "run_id": events[1]["run_id"] = "other"
    if fault == "terminal": events[-1]["payload"]["status"] = "failed"
    if fault == "analysis": analysis["run_id"] = "other"
    if fault == "submit": events[1]["payload"]["environment"]["result"] = "invalid_arguments"
    if fault == "check": events[1]["payload"]["environment"]["check_called"] = False
    if fault == "aggregate": run["metrics"]["assertions"]["aggregate"] = 0.8
    with pytest.raises(ValueError): module.validate_capture(run, events, analysis)


def test_agent_choices_are_registered_provider_agents_only():
    # H2-1 compares the reference loop with pi on the same local model.
    assert module.AGENTS == ("reference-openai-compatible.v1", "pi-coding-agent.v1")
    import subprocess, sys
    result = subprocess.run([sys.executable, module.__file__, "--output-directory", "/nonexistent/x",
                             "--agent-id", "fixture-agent.v1"], capture_output=True, timeout=10)
    assert result.returncode == 2 and b"invalid choice" in result.stderr
