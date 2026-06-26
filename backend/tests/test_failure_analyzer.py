"""Unit tests for ``app.failure`` — failure visibility analysis.

All tests use pure function calls with inline fixtures — no DB, no LLM.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.failure_analyzer import (
    FailureEvidence,
    FailureReport,
    analyze_trajectories,
    analyze_trajectory,
)


class TestAnalyzeTrajectory:
    """Tests for ``analyze_trajectory()`` — the main analysis entry point."""

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_perfect_trajectory(self, perfect_trajectory: dict[str, Any]) -> None:
        """A perfect trajectory should produce no failures."""
        report = analyze_trajectory(perfect_trajectory)
        assert report.dimensions == {}
        assert report.dominant is None
        assert report.evidence == []
        assert report.needs_human_review is False

    def test_empty_steps(self) -> None:
        """A trajectory with zero steps should not raise and return a clean report."""
        trajectory: dict[str, Any] = {
            "steps": [],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert report.dimensions == {}
        assert report.dominant is None
        assert report.evidence == []
        assert report.needs_human_review is False

    def test_no_evidence_trajectory(self) -> None:
        """Trajectory with steps but no failures should return a clean report."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "hello"}},
                    "observation": "Found relevant results",
                    "latency_ms": 100,
                    "context_window": {"used": 500, "limit": 128000},
                },
                {
                    "index": 1,
                    "action": None,
                    "observation": "Final answer is 42",
                    "latency_ms": 50,
                    "context_window": {"used": 800, "limit": 128000},
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert report.dimensions == {}
        assert report.dominant is None
        assert report.needs_human_review is False

    # ------------------------------------------------------------------
    # Planning dimension — circular reasoning
    # ------------------------------------------------------------------

    def test_planning_loop_detection(self) -> None:
        """Three consecutive tool calls with same name and args trigger planning."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "weather"}},
                    "observation": "Cloudy",
                    "latency_ms": 100,
                },
                {
                    "index": 1,
                    "action": {"name": "search", "arguments": {"q": "weather"}},
                    "observation": "Cloudy",
                    "latency_ms": 100,
                },
                {
                    "index": 2,
                    "action": {"name": "search", "arguments": {"q": "weather"}},
                    "observation": "Cloudy",
                    "latency_ms": 100,
                },
                # Different tool breaks the streak
                {
                    "index": 3,
                    "action": {"name": "read", "arguments": {"url": "http://example.com"}},
                    "observation": "Page content",
                    "latency_ms": 200,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "planning" in report.dimensions
        assert report.dimensions["planning"] > 0

        planning_evidence = [e for e in report.evidence if e.dimension == "planning"]
        assert len(planning_evidence) == 1  # Only the 3rd occurrence
        ev = planning_evidence[0]
        assert ev.severity == 1.0
        assert "search" in ev.reason
        assert "3" in ev.reason  # mentions repetition count
        assert ev.step_index == 2

    def test_planning_loop_four_calls(self) -> None:
        """Four consecutive identical calls produce two pieces of evidence."""
        steps = []
        for i in range(4):
            steps.append({
                "index": i,
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Result",
                "latency_ms": 50,
            })

        trajectory: dict[str, Any] = {
            "steps": steps,
            "status": "failed",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        planning_evidence = [e for e in report.evidence if e.dimension == "planning"]
        assert len(planning_evidence) == 2  # indices 2 and 3
        assert planning_evidence[0].step_index == 2
        assert planning_evidence[1].step_index == 3

    def test_planning_no_loop_different_args(self) -> None:
        """Same tool but different arguments should NOT trigger planning."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "weather"}},
                    "observation": "Sunny",
                    "latency_ms": 100,
                },
                {
                    "index": 1,
                    "action": {"name": "search", "arguments": {"q": "news"}},
                    "observation": "Headlines",
                    "latency_ms": 100,
                },
                {
                    "index": 2,
                    "action": {"name": "search", "arguments": {"q": "weather"}},
                    "observation": "Rainy",
                    "latency_ms": 100,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "planning" not in report.dimensions

    # ------------------------------------------------------------------
    # Execution dimension — error keywords and timeouts
    # ------------------------------------------------------------------

    def test_execution_error_keywords(self) -> None:
        """Steps with error/failed/traceback in observation trigger execution."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Error: connection refused",
                    "latency_ms": 500,
                },
                {
                    "index": 1,
                    "action": {"name": "read", "arguments": {}},
                    "observation": "Failed to fetch resource",
                    "latency_ms": 300,
                },
                {
                    "index": 2,
                    "action": None,
                    "observation": "Completed successfully",
                    "latency_ms": 50,
                },
            ],
            "status": "failed",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "execution" in report.dimensions
        exec_evidence = [e for e in report.evidence if e.dimension == "execution"]
        assert len(exec_evidence) == 2
        assert exec_evidence[0].step_index == 0
        assert exec_evidence[1].step_index == 1
        for ev in exec_evidence:
            assert ev.severity == 1.0

    def test_execution_timeout(self) -> None:
        """Steps with latency > 60s trigger execution timeout."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Taking too long...",
                    "latency_ms": 65_000,
                },
                {
                    "index": 1,
                    "action": None,
                    "observation": "Final answer",
                    "latency_ms": 100,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "execution" in report.dimensions
        exec_evidence = [e for e in report.evidence if e.dimension == "execution"]
        assert len(exec_evidence) == 1
        assert exec_evidence[0].step_index == 0
        assert "timeout" in exec_evidence[0].reason.lower()

    # ------------------------------------------------------------------
    # Context dimension — window pressure
    # ------------------------------------------------------------------

    def test_context_window_over_limit(self) -> None:
        """Context window usage > 95% should trigger context dimension."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Result",
                    "latency_ms": 100,
                    "context_window": {"used": 122_000, "limit": 128_000},
                },
                {
                    "index": 1,
                    "action": None,
                    "observation": "Final answer",
                    "latency_ms": 50,
                    "context_window": {"used": 50_000, "limit": 128_000},
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "context" in report.dimensions
        ctx_evidence = [e for e in report.evidence if e.dimension == "context"]
        assert len(ctx_evidence) == 1
        assert ctx_evidence[0].step_index == 0
        # severity = ratio capped at 1.0
        expected_severity = 122_000 / 128_000
        assert ctx_evidence[0].severity == pytest.approx(expected_severity, rel=1e-3)

    def test_context_window_exactly_at_limit(self) -> None:
        """Exactly 95% is NOT over the limit — should not trigger."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Result",
                    "latency_ms": 100,
                    "context_window": {"used": 121_600, "limit": 128_000},  # 95% exactly
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "context" not in report.dimensions

    def test_context_observation_signal(self) -> None:
        """Observation with truncated/context keywords triggers context."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Content was truncated due to length",
                    "latency_ms": 100,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "context" in report.dimensions
        ctx_evidence = [e for e in report.evidence if e.dimension == "context"]
        assert len(ctx_evidence) == 1
        # Without context_window info, severity defaults to 0.5
        assert ctx_evidence[0].severity == 0.5

    # ------------------------------------------------------------------
    # Budget dimension — step limit exceeded
    # ------------------------------------------------------------------

    def test_budget_exceeded(self) -> None:
        """Step count >= max_steps and status != success triggers budget."""
        steps = []
        for i in range(15):
            steps.append({
                "index": i,
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": f"Result {i}",
                "latency_ms": 50,
            })

        trajectory: dict[str, Any] = {
            "steps": steps,
            "status": "failed",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "budget" in report.dimensions
        budget_evidence = [e for e in report.evidence if e.dimension == "budget"]
        assert len(budget_evidence) == 1
        assert budget_evidence[0].severity == 1.0
        assert "15" in budget_evidence[0].reason

    def test_budget_not_exceeded(self) -> None:
        """Step count below max steps should NOT trigger budget."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Result",
                    "latency_ms": 50,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "budget" not in report.dimensions

    def test_budget_success_at_limit(self) -> None:
        """Even at max steps, success status does NOT trigger budget."""
        steps = []
        for i in range(15):
            steps.append({
                "index": i,
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": f"Result {i}",
                "latency_ms": 50,
            })

        trajectory: dict[str, Any] = {
            "steps": steps,
            "status": "success",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert "budget" not in report.dimensions

    # ------------------------------------------------------------------
    # Cross-dimensional — needs_human_review
    # ------------------------------------------------------------------

    def test_needs_human_review_three_dimensions(self) -> None:
        """Three or more dimensions with failures triggers needs_human_review."""
        steps: list[dict[str, Any]] = []
        # 15 steps to hit budget limit
        for i in range(15):
            step: dict[str, Any] = {
                "index": i,
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": f"Result {i}",
                "latency_ms": 100,
                "context_window": {"used": 50_000, "limit": 128_000},
            }
            # Inject errors for specific indices
            if i == 0:
                step["observation"] = "Error: something broke"
            if i == 2:
                step["context_window"] = {"used": 125_000, "limit": 128_000}
            steps.append(step)

        # Steps 0-14 all use same tool+args → planning loop from index 2
        # Step 0 has error → execution
        # Step 2 has context overload → context
        # Step count >= max_steps & failed → budget

        trajectory: dict[str, Any] = {
            "steps": steps,
            "status": "failed",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        assert report.needs_human_review is True

    def test_needs_human_review_two_dimensions_only(self) -> None:
        """Two dimensions with failures should NOT trigger human review."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: connection lost",
                    "latency_ms": 100,
                },
                {
                    "index": 1,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: timeout",
                    "latency_ms": 200,
                },
                {
                    "index": 2,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: failed",
                    "latency_ms": 300,
                },
                {
                    "index": 3,
                    "action": None,
                    "observation": "All failed",
                    "latency_ms": 50,
                },
            ],
            "status": "failed",
            "max_steps": 15,
        }
        # Steps 0-2: same tool+args → planning loop
        # Steps 0-2: error keywords → execution
        # Only 2 dimensions active → needs_human_review should be False
        report = analyze_trajectory(trajectory)
        # Now: planning and execution active → 2 dimensions
        planning_active = report.dimensions.get("planning", 0) > 0
        execution_active = report.dimensions.get("execution", 0) > 0
        assert planning_active and execution_active
        active_dims = sum(1 for r in report.dimensions.values() if r > 0)
        assert active_dims == 2
        assert report.needs_human_review is False

    # ------------------------------------------------------------------
    # Dominant dimension
    # ------------------------------------------------------------------

    def test_dominant_dimension(self) -> None:
        """The dimension with the highest failure rate should be dominant."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: failed",
                    "latency_ms": 100,
                },
                {
                    "index": 1,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: timeout",
                    "latency_ms": 100,
                },
                {
                    "index": 2,
                    "action": None,
                    "observation": "Done",
                    "latency_ms": 50,
                },
            ],
            "status": "failed",
            "max_steps": 15,
        }
        report = analyze_trajectory(trajectory)
        # Steps 0-1: same tool+args → 2 planning evidence at indices 0, 1 → rate 2/3
        # Steps 0-1: error keywords → 2 execution evidence at indices 0, 1 → rate 2/3
        # Both equal, dominant picks the first alphabetically or max...
        # Actually max() returns the first encountered in case of tie.
        assert report.dominant is not None
        # Both have same rate, so one of them is dominant
        assert report.dominant in ("planning", "execution")

    # ------------------------------------------------------------------
    # Evidence structure
    # ------------------------------------------------------------------

    def test_evidence_dataclass_fields(self) -> None:
        """FailureEvidence should store all expected fields."""
        ev = FailureEvidence(
            dimension="execution",
            step_index=1,
            reason="Test error",
            severity=0.8,
            details={"keyword": "error"},
        )
        assert ev.dimension == "execution"
        assert ev.step_index == 1
        assert ev.reason == "Test error"
        assert ev.severity == 0.8
        assert ev.details == {"keyword": "error"}

    def test_evidence_default_details_none(self) -> None:
        """FailureEvidence should default details to None."""
        ev = FailureEvidence(
            dimension="planning",
            step_index=0,
            reason="Loop",
            severity=1.0,
        )
        assert ev.details is None


class TestAnalyzeTrajectories:
    """Tests for ``analyze_trajectories()`` — multi-trajectory aggregation."""

    def test_empty_list(self) -> None:
        """Empty trajectory list should return an empty dict."""
        result = analyze_trajectories([])
        assert result == {}

    def test_single_trajectory(self) -> None:
        """Single trajectory should return its dimension rates."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: failed",
                    "latency_ms": 100,
                },
            ],
            "status": "failed",
            "max_steps": 15,
        }
        result = analyze_trajectories([trajectory])
        # execution has 1/1 steps → rate 1.0
        assert result["execution"] == 1.0
        assert result["planning"] == 0.0
        assert result["context"] == 0.0
        assert result["budget"] == 0.0

    def test_multiple_trajectories_average(self) -> None:
        """Multiple trajectories should average their dimension rates."""
        t1: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "x"}},
                    "observation": "Error: failed",
                    "latency_ms": 100,
                },
            ],
            "status": "failed",
            "max_steps": 15,
        }
        # t2 is perfect — no failures
        t2: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "hello"}},
                    "observation": "Found results",
                    "latency_ms": 50,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        result = analyze_trajectories([t1, t2])
        # execution: (1.0 + 0.0) / 2 = 0.5
        assert result["execution"] == pytest.approx(0.5)
        assert result["planning"] == pytest.approx(0.0)
        assert result["context"] == pytest.approx(0.0)
        assert result["budget"] == pytest.approx(0.0)

    def test_all_perfect_trajectories(self) -> None:
        """All perfect trajectories should result in all-zero rates."""
        traj: dict[str, Any] = {
            "steps": [
                {
                    "index": 0,
                    "action": {"name": "search", "arguments": {"q": "hello"}},
                    "observation": "Found results",
                    "latency_ms": 50,
                },
            ],
            "status": "success",
            "max_steps": 15,
        }
        result = analyze_trajectories([traj, traj, traj])
        for dim in ("planning", "execution", "context", "budget"):
            assert result[dim] == pytest.approx(0.0)
