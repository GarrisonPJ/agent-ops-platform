"""Tests for the policy compiler (pure function, no DB required)."""

from __future__ import annotations

import pytest

from app.failure import FailureEvidence, FailureReport
from app.policy import (
    DIMENSION_THRESHOLDS,
    compile_policy,
    PolicyPatch,
)


def _make_report(
    dimensions: dict[str, float] | None = None,
    evidence: list | None = None,
    dominant: str | None = None,
    needs_human_review: bool = False,
) -> FailureReport:
    return FailureReport(
        dimensions=dimensions or {},
        dominant=dominant,
        evidence=evidence or [],
        needs_human_review=needs_human_review,
    )


class TestCompilePolicy:
    """Pure-function tests for compile_policy()."""

    def test_no_failures_returns_none(self):
        """Empty dimensions → return None."""
        report = _make_report()
        result = compile_policy(report, [])
        assert result is None

    def test_execution_above_threshold_returns_patch(self):
        """Execution > 0.25 → PolicyPatch with system_prompt_suffix."""
        report = _make_report(
            dimensions={"execution": 0.30},
            evidence=[
                FailureEvidence(
                    dimension="execution",
                    step_index=0,
                    reason="Error detected",
                    severity=1.0,
                )
            ],
        )
        result = compile_policy(report, ["traj-1"])
        assert result is not None
        assert "system_prompt_suffix" in result.patch
        assert isinstance(result.patch["system_prompt_suffix"], str)
        assert len(result.patch["system_prompt_suffix"]) > 0

    def test_budget_above_threshold_returns_override(self):
        """Budget > 0.20 → PolicyPatch with max_steps_override."""
        report = _make_report(
            dimensions={"budget": 0.30},
            evidence=[
                FailureEvidence(
                    dimension="budget",
                    step_index=0,
                    reason="Budget exhausted",
                    severity=1.0,
                )
            ],
        )
        result = compile_policy(report, ["traj-1"])
        assert result is not None
        assert result.patch.get("max_steps_override") is not None
        assert isinstance(result.patch["max_steps_override"], int)

    def test_exec_planning_c2_combination(self):
        """Exec + Planning above thresholds → C2 rationale."""
        report = _make_report(
            dimensions={"execution": 0.30, "planning": 0.40},
            evidence=[
                FailureEvidence(
                    dimension="execution",
                    step_index=0,
                    reason="Error",
                    severity=1.0,
                ),
                FailureEvidence(
                    dimension="planning",
                    step_index=1,
                    reason="Circular reasoning",
                    severity=1.0,
                ),
            ],
        )
        result = compile_policy(report, ["traj-1"])
        assert result is not None
        assert "C2" in result.rationale
        assert "system_prompt_suffix" in result.patch

    def test_three_dimensions_returns_none(self):
        """≥3 dimensions with rate > 0 → return None (needs human review)."""
        report = _make_report(
            dimensions={
                "execution": 0.30,
                "planning": 0.40,
                "context": 0.50,
            },
            evidence=[
                FailureEvidence(
                    dimension=d, step_index=i, reason="x", severity=1.0
                )
                for i, d in enumerate(["execution", "planning", "context"])
            ],
            needs_human_review=True,
        )
        result = compile_policy(report, ["traj-1"])
        assert result is None

    def test_budget_context_c5_combination(self):
        """Budget + Context → C5 combination."""
        report = _make_report(
            dimensions={"budget": 0.30, "context": 0.50},
            evidence=[
                FailureEvidence(
                    dimension="budget",
                    step_index=0,
                    reason="Budget",
                    severity=1.0,
                ),
                FailureEvidence(
                    dimension="context",
                    step_index=1,
                    reason="Context pressure",
                    severity=0.8,
                ),
            ],
        )
        result = compile_policy(report, ["traj-1"])
        assert result is not None
        assert "C5" in result.rationale
        assert result.patch.get("context_strategy") == "aggressive_eviction"

    def test_source_trajectories_preserved(self):
        """Source trajectory IDs are preserved in the PolicyPatch."""
        report = _make_report(
            dimensions={"execution": 0.30},
            evidence=[
                FailureEvidence(
                    dimension="execution",
                    step_index=0,
                    reason="Error",
                    severity=1.0,
                )
            ],
        )
        result = compile_policy(report, ["traj-a", "traj-b"])
        assert result is not None
        assert result.source_trajectories == ["traj-a", "traj-b"]

    def test_confidence_high_two_severe_dimensions(self):
        """≥2 dimensions with severity > 0.5 → confidence = high."""
        report = _make_report(
            dimensions={"execution": 0.30, "budget": 0.25},
            evidence=[
                FailureEvidence(
                    dimension="execution",
                    step_index=0,
                    reason="Error",
                    severity=0.8,
                ),
                FailureEvidence(
                    dimension="budget",
                    step_index=1,
                    reason="Budget",
                    severity=0.9,
                ),
            ],
        )
        result = compile_policy(report, ["traj-1"])
        assert result is not None
        assert result.confidence == "high"

    def test_dimensions_just_below_threshold(self):
        """All dimensions below threshold → return None."""
        for dim, threshold in DIMENSION_THRESHOLDS.items():
            report = _make_report(
                dimensions={dim: threshold * 0.99},
                evidence=[
                    FailureEvidence(
                        dimension=dim,
                        step_index=0,
                        reason="Minor issue",
                        severity=0.1,
                    )
                ],
            )
            result = compile_policy(report, ["traj-1"])
            assert result is None, f"{dim} just below threshold should not compile"
