"""Shared fixtures for backend tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def perfect_trajectory() -> dict[str, Any]:
    """A trajectory with all steps successful, minimal costs."""
    return {
        "steps": [
            {
                "action": {"name": "search", "arguments": {"q": "hello"}},
                "observation": "Found results",
                "latency_ms": 100,
            },
            {
                "action": {"name": "read", "arguments": {"url": "https://example.com"}},
                "observation": "Page content",
                "latency_ms": 200,
            },
            {
                "action": None,
                "observation": "Final answer: hello world",
                "latency_ms": 50,
            },
        ],
        "status": "success",
        "total_tokens": 500,
        "total_latency_ms": 350,
    }


@pytest.fixture
def failed_trajectory() -> dict[str, Any]:
    """All steps failed, status is failed."""
    return {
        "steps": [
            {
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Error: connection refused",
                "latency_ms": 1000,
            },
            {
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Timeout: no response",
                "latency_ms": 2000,
            },
        ],
        "status": "failed",
        "total_tokens": 2000,
        "total_latency_ms": 3000,
    }


@pytest.fixture
def no_tool_calls_trajectory() -> dict[str, Any]:
    """Trajectory with no tool calls — direct answer."""
    return {
        "steps": [
            {
                "action": None,
                "observation": "Direct answer: 42",
                "latency_ms": 100,
            },
        ],
        "status": "success",
        "total_tokens": 100,
        "total_latency_ms": 100,
    }


@pytest.fixture
def all_failed_tool_calls_trajectory() -> dict[str, Any]:
    """All tool calls returned failures."""
    return {
        "steps": [
            {
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Error: something went wrong",
                "latency_ms": 500,
            },
            {
                "action": {"name": "read", "arguments": {"url": "x"}},
                "observation": "Failed to fetch",
                "latency_ms": 600,
            },
            {
                "action": None,
                "observation": "Could not complete task",
                "latency_ms": 50,
            },
        ],
        "status": "success",  # overall success despite tool failures
        "total_tokens": 800,
        "total_latency_ms": 1150,
    }
