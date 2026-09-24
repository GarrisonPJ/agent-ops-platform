"""Small, human-readable labels for analyzer and scoring calibration tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.failure_analyzer import FailureClassification
from app.scenario_ids import (
    API_FAULT_ORCHESTRATION_SCENARIO_ID,
    CHECKOUT_SCENARIO_ID,
    MULTI_STEP_RESEARCH_SCENARIO_ID,
)


@dataclass(frozen=True)
class LabeledTrajectory:
    """A trajectory with the dimensions and labels a reviewer would expect."""

    name: str
    trajectory: dict[str, Any]
    expected_dimensions: frozenset[str]
    expected_classifications: frozenset[str] = frozenset()


def _step(
    index: int,
    *,
    tool: str,
    observation: str,
    arguments: dict[str, Any] | None = None,
    latency_ms: int = 25,
    context_used: int = 1_200,
    context_limit: int = 8_192,
) -> dict[str, Any]:
    return {
        "index": index,
        "action": {"name": tool, "arguments": arguments or {}},
        "observation": observation,
        "latency_ms": latency_ms,
        "context_window": {"used": context_used, "limit": context_limit},
    }


def _trajectory(
    steps: list[dict[str, Any]], *, status: str = "failed", max_steps: int = 6, **extra: Any
) -> dict[str, Any]:
    return {"status": status, "max_steps": max_steps, "steps": steps, **extra}


def _repeated_loop() -> list[dict[str, Any]]:
    return [
        _step(
            index,
            tool="search_documents",
            arguments={"query": "checkout latency"},
            observation="The same result list is returned.",
        )
        for index in range(6)
    ]


FAILURE_ANALYZER_FIXTURES = (
    LabeledTrajectory(
        name="clean_success",
        trajectory=_trajectory(
            [
                _step(
                    0,
                    tool="submit_answer",
                    observation="Answer accepted with supporting evidence.",
                )
            ],
            status="success",
        ),
        expected_dimensions=frozenset(),
    ),
    LabeledTrajectory(
        name="repeated_loop_hits_budget",
        trajectory=_trajectory(_repeated_loop()),
        expected_dimensions=frozenset({"planning", "budget"}),
        expected_classifications=frozenset(
            {FailureClassification.LOOP_NO_PROGRESS.value}
        ),
    ),
    LabeledTrajectory(
        name="provider_error_with_step",
        trajectory=_trajectory(
            [
                _step(
                    0,
                    tool="invoke_service",
                    observation="The provider stopped before returning a result.",
                )
            ],
            run_status="failed",
            provider_error=True,
        ),
        expected_dimensions=frozenset({"execution"}),
        expected_classifications=frozenset(
            {FailureClassification.PROVIDER_ERROR.value}
        ),
    ),
    LabeledTrajectory(
        name="step_timeout",
        trajectory=_trajectory(
            [
                _step(
                    0,
                    tool="fetch_document",
                    observation="The upstream did not respond.",
                    latency_ms=60_001,
                )
            ]
        ),
        expected_dimensions=frozenset({"execution"}),
        expected_classifications=frozenset({FailureClassification.TIMEOUT.value}),
    ),
    LabeledTrajectory(
        name="context_window_pressure",
        trajectory=_trajectory(
            [
                _step(
                    0,
                    tool="fetch_document",
                    observation="Retrieved the requested evidence.",
                    context_used=8_191,
                )
            ]
        ),
        expected_dimensions=frozenset({"context"}),
    ),
    LabeledTrajectory(
        name="budget_exhaustion_without_other_signal",
        trajectory=_trajectory(
            [
                _step(
                    index,
                    tool=f"tool_{index}",
                    observation="The step completed normally.",
                )
                for index in range(6)
            ]
        ),
        expected_dimensions=frozenset({"budget"}),
    ),
    LabeledTrajectory(
        name="http_503_execution_failure",
        trajectory=_trajectory(
            [
                _step(
                    0,
                    tool="invoke_service",
                    observation=(
                        "HTTP 503 Service Unavailable: upstream connection pool saturated."
                    ),
                )
            ]
        ),
        expected_dimensions=frozenset({"execution"}),
    ),
    LabeledTrajectory(
        name="http_200_service_available_control",
        trajectory=_trajectory(
            [
                _step(
                    0,
                    tool="invoke_service",
                    observation="HTTP 200 OK: Service available through the primary route.",
                )
            ],
            status="success",
        ),
        expected_dimensions=frozenset(),
    ),
)


@dataclass(frozen=True)
class ScenarioCalibrationFixture:
    scenario_id: str
    expected_baseline_dimensions: frozenset[str]


SCENARIO_CALIBRATION_FIXTURES = (
    ScenarioCalibrationFixture(
        scenario_id=CHECKOUT_SCENARIO_ID,
        expected_baseline_dimensions=frozenset({"planning", "budget"}),
    ),
    ScenarioCalibrationFixture(
        scenario_id=MULTI_STEP_RESEARCH_SCENARIO_ID,
        expected_baseline_dimensions=frozenset({"planning", "budget"}),
    ),
    ScenarioCalibrationFixture(
        scenario_id=API_FAULT_ORCHESTRATION_SCENARIO_ID,
        expected_baseline_dimensions=frozenset({"planning", "execution", "budget"}),
    ),
)
