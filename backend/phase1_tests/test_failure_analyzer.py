from __future__ import annotations

from app.failure_analyzer import FailureClassification, analyze_trajectory
from app.scenario_assertions import AssertionCombination, AssertionSuite, ScenarioAssertion


def step(index: int, *, tool: str = "search_documents", arguments: dict | None = None) -> dict:
    return {
        "index": index,
        "action": {"name": tool, "arguments": arguments or {"query": "checkout latency"}},
        "observation": "The same result list is returned.",
        "latency_ms": 25,
    }


def test_repeated_tool_calls_are_classified_as_loop_no_progress() -> None:
    report = analyze_trajectory(
        {"status": "failed", "max_steps": 6, "steps": [step(index) for index in range(6)]}
    )

    assert report.dominant_classification == FailureClassification.LOOP_NO_PROGRESS.value
    assert report.classification_rates[FailureClassification.LOOP_NO_PROGRESS.value] > 0
    assert all(
        evidence.failure_type == FailureClassification.LOOP_NO_PROGRESS.value
        for evidence in report.evidence
        if evidence.dimension == "planning"
    )


def test_failed_tool_assertions_produce_selection_and_parameter_classifications() -> None:
    suite = AssertionSuite(
        combination=AssertionCombination.ALL,
        assertions=(
            ScenarioAssertion(type="tool-used", tool="fetch_document"),
            ScenarioAssertion(
                type="tool-args-match",
                tool="search_documents",
                arguments={"query": "different topic"},
            ),
        ),
    )
    report = analyze_trajectory(
        {"status": "failed", "max_steps": 6, "steps": [step(0)]},
        assertion_suite=suite,
    )

    classifications = {evidence.failure_type for evidence in report.evidence}
    assert classifications >= {
        FailureClassification.TOOL_SELECTION_ERROR.value,
        FailureClassification.PARAMETER_ERROR.value,
    }


def test_provider_error_and_timeout_are_run_level_classifications() -> None:
    provider_report = analyze_trajectory(
        {"status": "failed", "run_status": "failed", "provider_error": True, "steps": []}
    )
    timeout_report = analyze_trajectory(
        {"status": "failed", "run_status": "timed_out", "steps": []}
    )

    assert provider_report.dominant_classification == FailureClassification.PROVIDER_ERROR.value
    assert timeout_report.dominant_classification == FailureClassification.TIMEOUT.value


def test_legacy_trajectory_analysis_still_uses_existing_dimensions() -> None:
    report = analyze_trajectory(
        {
            "status": "failed",
            "max_steps": 6,
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "fetch_service_logs", "arguments": {}},
                    "observation": "The request failed.",
                    "latency_ms": 25,
                }
            ],
        }
    )

    assert "execution" in report.dimensions
    assert report.evidence[0].dimension == "execution"
