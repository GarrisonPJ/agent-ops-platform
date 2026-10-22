"""Per-Run history/check and legacy fixture event helpers for built-in worlds.

Environment implementations record validated calls here before checking them.
History is instance-local; assertion evidence never owns terminal Run scoring.
Read checkout.py for an Environment and its retained fixture loop.
"""

from __future__ import annotations

from copy import deepcopy

from app.environment import (
    EnvironmentCheck,
    InjectedFault,
    InvalidToolArgumentsError,
)
from app.scenario_assertions import AssertionSuite, evaluate_assertion
from app.scenario_registry import EmitFn


class TrajectoryEnvironment:
    """Shared history/reset/check implementation for the three built-in worlds."""

    def __init__(self, suite: AssertionSuite) -> None:
        self._suite = suite
        self.reset(0)

    @property
    def fault_schedule(self) -> tuple[InjectedFault, ...]:
        return ()

    def reset(self, seed: int) -> None:
        self._seed = seed
        self._steps: list[dict[str, object]] = []

    def _record(self, name: str, args: dict[str, object], observation: str) -> str:
        self._steps.append(
            {"action": {"name": name, "arguments": deepcopy(args)}, "observation": observation}
        )
        return observation

    def check(self) -> EnvironmentCheck:
        # No combined score, status transition, or persistence belongs to a world.
        trajectory = {"steps": self._steps}
        return EnvironmentCheck(
            combination=self._suite.combination,
            trajectory=tuple(
                evaluate_assertion(assertion, trajectory) for assertion in self._suite.assertions
            ),
        )


def require_exact_arguments(
    name: str, args: dict[str, object], *alternatives: dict[str, object]
) -> None:
    """Enforce the existing literal contracts, including int versus bool."""
    if any(
        args == expected
        and all(type(args[key]) is type(value) for key, value in expected.items())
        for expected in alternatives
    ):
        return
    raise InvalidToolArgumentsError(f"arguments do not match the contract for {name}")


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
