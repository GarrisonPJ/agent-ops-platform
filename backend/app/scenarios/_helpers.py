"""Shared helpers for built-in Scenario fixtures."""

from __future__ import annotations

from app.scenario_registry import EmitFn


def emit_step(
    emit: EmitFn,
    index: int,
    summary: str,
    tool: str,
    arguments: dict,
    observation: str,
    latency_ms: int,
) -> None:
    emit(
        "step_completed",
        {
            "index": index,
            "decision_summary": summary,
            "tool_call": {"name": tool, "arguments": arguments},
            "observation": observation,
            "latency_ms": latency_ms,
            "token_prompt": 72 + index * 3,
            "token_completion": 24 + index * 2,
            "context_window": {"used": 1200 + index * 180, "limit": 8192},
        },
    )
