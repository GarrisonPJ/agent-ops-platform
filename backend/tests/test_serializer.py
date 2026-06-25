"""Unit tests for ``app.serializer`` and ``app.config``."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import Settings, get_settings, settings
from app.runtime import Step as RuntimeStep
from app.runtime import ToolCall
from app.serializer import render_step, render_trajectory


class TestRenderStep:
    """Tests for ``render_step(step, view="full")``."""

    def test_full_tool_call_step(self) -> None:
        """A step with thought + tool call + observation serializes correctly."""
        step = RuntimeStep(
            index=0,
            thought="I need to search the web",
            action=ToolCall(id="call_1", name="search", arguments={"q": "hello"}),
            observation="Found some results",
            latency_ms=150,
            started_at=1000.0,
            container_id="container-abc",
            token_prompt=50,
            token_completion=100,
        )
        result = render_step(step, view="full")

        assert result["index"] == 0
        assert result["thought"] == "I need to search the web"
        assert result["action"] == {
            "id": "call_1",
            "name": "search",
            "arguments": {"q": "hello"},
        }
        assert result["observation"] == "Found some results"
        assert result["latency_ms"] == 150
        assert result["started_at"] == 1000.0
        assert result["container_id"] == "container-abc"
        assert result["token_prompt"] == 50
        assert result["token_completion"] == 100

    def test_none_action_final_answer(self) -> None:
        """A final-answer step (action=None) serializes action as None."""
        step = RuntimeStep(
            index=1,
            thought="Here is the answer",
            action=None,
            observation="Final answer: 42",
            latency_ms=50,
            started_at=2000.0,
        )
        result = render_step(step, view="full")
        assert result["action"] is None
        assert result["observation"] == "Final answer: 42"
        assert result["latency_ms"] == 50

    def test_context_window_present(self) -> None:
        """Context window info is correctly serialized."""
        step = RuntimeStep(
            index=0,
            thought="Thinking...",
            action=ToolCall(id="call_1", name="search", arguments={}),
            observation="Done",
            latency_ms=100,
            started_at=1000.0,
        )
        result = render_step(step, view="full")
        assert result["context_window"] == {"used": 0, "limit": 0}

    def test_token_fields_none(self) -> None:
        """When token fields are None, they serialize as None."""
        step = RuntimeStep(
            index=0,
            thought="Thinking...",
            action=None,
            observation="Answer",
            latency_ms=10,
            started_at=1000.0,
        )
        result = render_step(step, view="full")
        assert result["token_prompt"] is None
        assert result["token_completion"] is None


# ---------------------------------------------------------------------------
# Fixtures for render_trajectory tests
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_steps():
    """Create minimal ORM-alike step objects for testing."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            index=0,
            thought="I need to search",
            action={"name": "search", "arguments": {"q": "hello"}},
            observation="Found results",
            latency_ms=100,
            container_id=None,
            context_window={"used": 500, "limit": 128000},
            started_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            token_prompt=50,
            token_completion=150,
        ),
        SimpleNamespace(
            index=1,
            thought="Let me read more",
            action={"name": "read", "arguments": {"url": "https://example.com"}},
            observation="Page content loaded",
            latency_ms=200,
            container_id="c-123",
            context_window={"used": 600, "limit": 128000},
            started_at=datetime(2025, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
            token_prompt=100,
            token_completion=200,
        ),
        SimpleNamespace(
            index=2,
            thought="Here is the answer",
            action=None,
            observation="Final answer: hello world",
            latency_ms=50,
            container_id=None,
            context_window={"used": 700, "limit": 128000},
            started_at=datetime(2025, 1, 1, 12, 0, 2, tzinfo=timezone.utc),
            token_prompt=30,
            token_completion=70,
        ),
    ]


@pytest.fixture
def sample_trajectory(sample_steps):
    """Create a minimal ORM-alike trajectory for testing."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id="traj-123",
        task="Test task",
        status="success",
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        total_tokens=600,
        context_window_peak=700,
        score=0.95,
        score_breakdown={
            "success_reward": 1.0,
            "cost_penalty": -0.006,
            "latency_penalty": -0.0035,
            "tool_failure_penalty": -0.0,
        },
        steps=sample_steps,
    )


class TestRenderTrajectory:
    """Tests for ``render_trajectory(trajectory)``."""

    def test_detail_shape(self, sample_trajectory) -> None:
        """Trajectory detail includes id, task, status, score and steps."""
        result = render_trajectory(sample_trajectory)

        assert result["id"] == "traj-123"
        assert result["task"] == "Test task"
        assert result["status"] == "success"
        assert result["score"] == 0.95
        assert result["score_breakdown"]["success_reward"] == 1.0
        assert len(result["steps"]) == 3

    def test_steps_contain_all_fields(self, sample_trajectory) -> None:
        """Each step in the detail has the expected fields."""
        result = render_trajectory(sample_trajectory)
        step = result["steps"][0]

        assert step["index"] == 0
        assert step["thought"] == "I need to search"
        assert step["action"] == {"name": "search", "arguments": {"q": "hello"}}
        assert step["observation"] == "Found results"
        assert step["latency_ms"] == 100
        assert step["container_id"] is None
        assert step["context_window"] == {"used": 500, "limit": 128000}
        assert step["token_prompt"] == 50
        assert step["token_completion"] == 150
        assert "started_at" in step

    def test_optional_fields_none(self, sample_trajectory) -> None:
        """When score/breakdown/total_tokens are None, they appear as None."""
        from types import SimpleNamespace

        traj = SimpleNamespace(
            id="traj-none",
            task="No score",
            status="running",
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            total_tokens=None,
            context_window_peak=None,
            score=None,
            score_breakdown=None,
            steps=[],
        )
        result = render_trajectory(traj)
        assert result["score"] is None
        assert result["score_breakdown"] is None
        assert result["total_tokens"] is None
        assert result["context_window_peak"] is None
        assert result["steps"] == []


class TestConfigDi:
    """Tests for ``app.config.get_settings()`` factory."""

    def test_get_settings_returns_settings_instance(self) -> None:
        """``get_settings()`` returns a ``Settings`` instance."""
        result = get_settings()
        assert isinstance(result, Settings)

    def test_get_settings_matches_module_level(self) -> None:
        """``get_settings()`` returns the same object as ``settings``."""
        assert get_settings() is settings

    def test_settings_has_expected_attributes(self) -> None:
        """``Settings`` instance has expected config attributes."""
        assert hasattr(settings, "llm_base_url")
        assert hasattr(settings, "llm_api_key")
        assert hasattr(settings, "llm_model")
        assert hasattr(settings, "cors_origins")
