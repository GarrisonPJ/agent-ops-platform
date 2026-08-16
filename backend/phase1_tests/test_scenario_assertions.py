from __future__ import annotations

import pytest

from app.scenario_assertions import (
    AssertionCombination,
    AssertionSuite,
    ScenarioAssertion,
    evaluate_assertion,
    evaluate_assertion_suite,
)
from app.scoring import compute_score


def _trajectory(
  tools: list[str],
  *,
  status: str = "success",
  observations: list[str] | None = None,
) -> dict:
    observations = observations or ["ok"] * len(tools)
    return {
        "status": status,
        "steps": [
            {
                "action": {"name": tool, "arguments": {"service": "checkout-api"}},
                "observation": observations[index],
            }
            for index, tool in enumerate(tools)
        ],
    }


def test_tool_used_passes_when_tool_present() -> None:
    result = evaluate_assertion(
        ScenarioAssertion(type="tool-used", tool="fetch_service_logs"),
        _trajectory(["fetch_service_logs"]),
    )
    assert result.passed
    assert result.score == 1.0


def test_tool_args_match_requires_matching_arguments() -> None:
    trajectory = {
        "status": "success",
        "steps": [
            {
                "action": {
                    "name": "fetch_service_logs",
                    "arguments": {"service": "checkout-api", "query": "timeout"},
                },
                "observation": "ok",
            }
        ],
    }
    passed = evaluate_assertion(
        ScenarioAssertion(
            type="tool-args-match",
            tool="fetch_service_logs",
            arguments={"service": "checkout-api", "query": "timeout"},
        ),
        trajectory,
    )
    failed = evaluate_assertion(
        ScenarioAssertion(
            type="tool-args-match",
            tool="fetch_service_logs",
            arguments={"service": "payments-api"},
        ),
        trajectory,
    )
    assert passed.passed
    assert not failed.passed


def test_tool_sequence_scores_prefix_matches() -> None:
    result = evaluate_assertion(
        ScenarioAssertion(
            type="tool-sequence",
            sequence=("check_service_health", "query_service_metrics"),
        ),
        _trajectory(["check_service_health", "query_service_metrics", "fetch_service_logs"]),
    )
    assert result.score == 1.0


def test_step_count_enforces_upper_bound() -> None:
    result = evaluate_assertion(
        ScenarioAssertion(type="step-count", max_steps=4),
        _trajectory(["a", "b", "c", "d", "e"]),
    )
    assert result.score == pytest.approx(0.8)


def test_equals_and_contains_assertions() -> None:
    trajectory = _trajectory(["submit_answer"], observations=["payment pool saturated"])
    assert evaluate_assertion(
        ScenarioAssertion(type="equals", expected="payment pool saturated"),
        trajectory,
    ).passed
    assert evaluate_assertion(
        ScenarioAssertion(type="contains", contains="pool"),
        trajectory,
    ).passed


def test_json_match_reads_final_observation() -> None:
    trajectory = _trajectory(["submit_answer"], observations=['{"status":"ok","code":200}'])
    result = evaluate_assertion(
        ScenarioAssertion(
            type="json-match",
            json_path="status",
            json_expected="ok",
        ),
        trajectory,
    )
    assert result.passed


def test_combination_all_uses_minimum_score() -> None:
    suite = AssertionSuite(
        combination=AssertionCombination.ALL,
        assertions=(
            ScenarioAssertion(type="tool-used", tool="a"),
            ScenarioAssertion(type="tool-used", tool="missing"),
        ),
    )
    result = evaluate_assertion_suite(suite, _trajectory(["a"]))
    assert result["aggregate"] == 0.0


def test_combination_any_uses_maximum_score() -> None:
    suite = AssertionSuite(
        combination=AssertionCombination.ANY,
        assertions=(
            ScenarioAssertion(type="tool-used", tool="missing"),
            ScenarioAssertion(type="tool-used", tool="a"),
        ),
    )
    result = evaluate_assertion_suite(suite, _trajectory(["a"]))
    assert result["aggregate"] == 1.0


def test_combination_weighted_averages_scores() -> None:
    suite = AssertionSuite(
        combination=AssertionCombination.WEIGHTED,
        assertions=(
            ScenarioAssertion(type="tool-used", tool="a", weight=1.0),
            ScenarioAssertion(type="tool-used", tool="missing", weight=1.0),
        ),
    )
    result = evaluate_assertion_suite(suite, _trajectory(["a"]))
    assert result["aggregate"] == 0.5


def test_compute_score_uses_assertion_aggregate_for_success_reward() -> None:
    replay_tools = [
        "check_service_health",
        "query_service_metrics",
        "fetch_service_logs",
    ]
    baseline_tools = ["fetch_service_logs"] * 6
    assertions = (
        ScenarioAssertion(type="tool-sequence", sequence=tuple(replay_tools), weight=2.0),
        ScenarioAssertion(type="step-count", max_steps=4, weight=1.0),
    )
    suite = AssertionSuite(
        combination=AssertionCombination.WEIGHTED,
        assertions=assertions,
    )
    replay_score = compute_score(
        _trajectory(replay_tools, status="success"),
        assertion_suite=suite,
    )
    baseline_score = compute_score(
        _trajectory(baseline_tools, status="failed"),
        assertion_suite=suite,
    )
    assert replay_score["score"] > baseline_score["score"]
    assert replay_score["breakdown"]["success_reward"] > baseline_score["breakdown"]["success_reward"]
