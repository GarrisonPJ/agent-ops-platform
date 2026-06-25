"""Unit tests for ``app.scoring.compute_score``."""

from __future__ import annotations

from typing import Any

import pytest

from app.scoring import DEFAULT_WEIGHTS, compute_score


class TestComputeScore:
    """Pure function tests — no DB, no LLM, no fixtures from conftest needed."""

    def test_perfect_score(self, perfect_trajectory: dict[str, Any]) -> None:
        """A successful trajectory with low costs should score near 1.0."""
        result = compute_score(perfect_trajectory)
        # success_reward=1.0, cost=500/1000*0.01=0.005, latency=350/1000*0.01=0.0035,
        # tool_failure=0/2*0.5=0
        # score ≈ 1.0 - 0.005 - 0.0035 - 0 = 0.9915
        assert result["score"] == pytest.approx(0.9915, rel=1e-3)
        assert result["breakdown"]["success_reward"] == 1.0
        assert result["breakdown"]["cost_penalty"] == pytest.approx(-0.005, rel=1e-3)
        assert result["breakdown"]["latency_penalty"] == pytest.approx(-0.0035, rel=1e-3)
        assert result["breakdown"]["tool_failure_penalty"] == 0.0

    def test_zero_score_failed(self, failed_trajectory: dict[str, Any]) -> None:
        """A failed trajectory with high costs should score well below zero."""
        result = compute_score(failed_trajectory)
        # success_reward=0.0, cost=2000/1000*0.01=0.02, latency=3000/1000*0.01=0.03,
        # tool_failure=2/2*0.5=0.5
        # score = 0 - 0.02 - 0.03 - 0.5 = -0.55
        assert result["score"] == pytest.approx(-0.55, rel=1e-3)
        assert result["breakdown"]["success_reward"] == 0.0

    def test_no_tool_calls(self, no_tool_calls_trajectory: dict[str, Any]) -> None:
        """No tool calls means zero tool_failure_penalty regardless of observations."""
        result = compute_score(no_tool_calls_trajectory)
        # success_reward=1.0, cost=100/1000*0.01=0.001, latency=100/1000*0.01=0.001,
        # tool_failure=0 (no calls)
        # score ≈ 1.0 - 0.001 - 0.001 - 0 = 0.998
        assert result["score"] == pytest.approx(0.998, rel=1e-3)
        assert result["breakdown"]["tool_failure_penalty"] == 0.0

    def test_all_tool_calls_failed(
        self, all_failed_tool_calls_trajectory: dict[str, Any]
    ) -> None:
        """All tool calls have failure indicators -> max tool_failure_penalty."""
        result = compute_score(all_failed_tool_calls_trajectory)
        # success_reward=1.0, cost=800/1000*0.01=0.008, latency=1150/1000*0.01=0.0115,
        # tool_failure=2/2*0.5=0.5
        # score ≈ 1.0 - 0.008 - 0.0115 - 0.5 = 0.4805
        assert result["score"] == pytest.approx(0.4805, rel=1e-3)
        assert result["breakdown"]["tool_failure_penalty"] == pytest.approx(-0.5, rel=1e-3)

    def test_custom_weights(self) -> None:
        """Custom weights should override defaults and change the result."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Error: failed",
                    "latency_ms": 500,
                },
            ],
            "status": "success",
            "total_tokens": 1000,
            "total_latency_ms": 500,
        }
        # With zero weights: score should just be success_reward = 1.0
        result = compute_score(trajectory, {"cost": 0, "latency": 0, "tool_failure": 0})
        assert result["score"] == 1.0
        assert result["breakdown"]["cost_penalty"] == 0.0
        assert result["breakdown"]["latency_penalty"] == 0.0
        assert result["breakdown"]["tool_failure_penalty"] == 0.0

    def test_custom_weights_magnify_penalties(self) -> None:
        """Higher weights should amplify penalties."""
        trajectory: dict[str, Any] = {
            "steps": [
                {
                    "action": {"name": "search", "arguments": {}},
                    "observation": "Error: failed",
                    "latency_ms": 500,
                },
            ],
            "status": "success",
            "total_tokens": 1000,
            "total_latency_ms": 500,
        }
        result = compute_score(trajectory, {"cost": 10, "latency": 10})
        # cost_penalty = (1000/1000)*10 = 10
        # latency_penalty = (500/1000)*10 = 5
        # tool_failure_penalty = 1/1*0.5 = 0.5
        # score = 1.0 - 10 - 5 - 0.5 = -14.5
        assert result["score"] == pytest.approx(-14.5, rel=1e-3)

    def test_empty_steps(self) -> None:
        """Empty steps array should produce a default score (only status matters)."""
        trajectory: dict[str, Any] = {
            "steps": [],
            "status": "success",
            "total_tokens": 0,
            "total_latency_ms": 0,
        }
        result = compute_score(trajectory)
        # success_reward=1.0, no penalties
        assert result["score"] == 1.0
        assert result["breakdown"]["cost_penalty"] == 0.0
        assert result["breakdown"]["latency_penalty"] == 0.0
        assert result["breakdown"]["tool_failure_penalty"] == 0.0

    def test_default_weights_unchanged(self) -> None:
        """Verify default weights match the spec."""
        assert DEFAULT_WEIGHTS == {"cost": 0.01, "latency": 0.01, "tool_failure": 0.5}
