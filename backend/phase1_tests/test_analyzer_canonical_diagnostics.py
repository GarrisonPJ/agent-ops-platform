"""Regress ADR-0013's two canonical analyzer rules.

These pure tests exercise shared diagnostics before CP/offline integration.
They must not turn reported usage into measured steps or alter assertions.
Read test_controlled_calibration_capture.py for persisted six-contract coverage.
"""

from copy import deepcopy

import pytest

from app.failure_analyzer import ANALYZER_REVISION, analyze_trajectory
from app.scenario_assertions import AssertionCombination, AssertionSuite, ScenarioAssertion, evaluate_assertion_suite


def world_step():
    return {"index": 0, "action": {"name": "rejected_tool", "arguments": {}},
            "status": "failed", "observation": "Refused.", "context_window": None}


@pytest.mark.parametrize("steps", [[], [world_step()], [world_step()] * 3])
def test_high_reported_context_is_run_level_and_not_divided_by_world_steps(steps):
    trajectory = {"steps": steps, "agent_usage": {
        "source": "agent_reported", "context_window": {"used": 8191, "limit": 8192},
    }}
    before = deepcopy(trajectory)
    report = analyze_trajectory(trajectory)
    assert ANALYZER_REVISION == "failure-analyzer.v3"
    assert report.dimensions["context"] == 1.0
    evidence = [item for item in report.evidence if item.dimension == "context"]
    assert len(evidence) == 1
    assert evidence[0].step_index is None
    assert evidence[0].details == {"source": "agent_reported", "ratio": 8191 / 8192}
    assert "Agent-reported" in evidence[0].reason
    assert trajectory == before


@pytest.mark.parametrize("usage", [
    None, {}, {"context_window": {"used": 8191, "limit": 8192}},
    {"source": "measured", "context_window": {"used": 8191, "limit": 8192}},
    *({"source": "agent_reported", "context_window": window} for window in [
        None, {}, {"used": 1200, "limit": 8192}, {"used": 95, "limit": 100},
        {"used": 1, "limit": 0}, {"used": -1, "limit": 1},
        {"used": True, "limit": 1}, {"used": "99", "limit": 100},
        {"used": 99.0, "limit": 100}, {"used": 99, "limit": True},
        {"used": 99, "limit": -1}, {"used": 10_000_001, "limit": 100},
        {"used": 10_000_000, "limit": 10_000_001},
    ]),
])
def test_low_absent_or_invalid_reported_usage_adds_no_context_signal(usage):
    assert "context" not in analyze_trajectory({"steps": [], "agent_usage": usage}).dimensions


@pytest.mark.parametrize("precomputed", [False, True])
def test_pretool_provider_failure_does_not_infer_planning_but_assertions_still_fail(precomputed):
    suite = AssertionSuite(combination=AssertionCombination.ALL, assertions=(
        ScenarioAssertion(type="tool-used", tool="fetch_document"),
        ScenarioAssertion(type="tool-args-match", tool="fetch_document", arguments={"document_id": "doc-1"}),
    ))
    trajectory = {"steps": [], "provider_error": True, "run_status": "failed"}
    result = evaluate_assertion_suite(suite, trajectory)
    before = deepcopy(result)
    report = analyze_trajectory(trajectory, **(
        {"assertion_result": result} if precomputed else {"assertion_suite": suite}
    ))
    assert report.dimensions == {"execution": 1.0}
    assert {item.failure_type for item in report.evidence} == {"provider_error"}
    assert result == before
    assert result["aggregate"] == 0
    assert all(not item["passed"] for item in result["assertions"])


@pytest.mark.parametrize("steps,provider_error", [([], False), ([world_step()], True)])
@pytest.mark.parametrize("precomputed", [False, True])
def test_nonempty_or_nonprovider_failure_keeps_planning_attribution(steps, provider_error, precomputed):
    suite = AssertionSuite(combination=AssertionCombination.ALL, assertions=(ScenarioAssertion(type="tool-used", tool="fetch_document"),))
    trajectory = {"steps": steps, "provider_error": provider_error, "run_status": "failed"}
    report = analyze_trajectory(trajectory, **(
        {"assertion_result": evaluate_assertion_suite(suite, trajectory)}
        if precomputed else {"assertion_suite": suite}
    ))
    assert "planning" in report.dimensions
    assert any(item.failure_type == "tool_selection_error" for item in report.evidence)


@pytest.mark.parametrize("precomputed", [False, True])
@pytest.mark.parametrize("status", ["failed", "timed_out"])
def test_zero_step_context_keeps_classified_summary_and_review_threshold(precomputed, status):
    suite = AssertionSuite(combination=AssertionCombination.ALL, assertions=(
        ScenarioAssertion(type="tool-used", tool="check_service_health"),
    ))
    trajectory = {"steps": [], "run_status": status, "agent_usage": {
        "source": "agent_reported", "context_window": {"used": 99, "limit": 100},
    }}
    report = analyze_trajectory(trajectory, **(
        {"assertion_result": evaluate_assertion_suite(suite, trajectory)}
        if precomputed else {"assertion_suite": suite}
    ))
    assert report.dominant_classification == ("timeout" if status == "timed_out" else "tool_selection_error")
    assert report.needs_human_review is (status == "timed_out")
    assert set(report.dimensions) == ({"execution", "context", "planning"}
                                      if status == "timed_out" else {"context", "planning"})


def test_legacy_step_context_still_works_without_canonical_usage():
    step = {**world_step(), "context_window": {"used": 99, "limit": 100}}
    report = analyze_trajectory({"steps": [step]})
    assert report.dimensions == {"context": 1.0}
    assert report.evidence[0].step_index == 0
