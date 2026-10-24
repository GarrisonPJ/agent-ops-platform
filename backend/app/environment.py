"""Typed in-process Environment boundary for the H1 world/agent extraction.

The registry creates one world per Run; a future env_server calls this surface.
Observations are deterministic, and check reports assertions, never Run scores.
Read scenarios/checkout.py for a world and scenario_registry.py for its factory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, TypedDict

from app.scenario_assertions import AssertionCombination, AssertionEvaluation


class ToolFunction(TypedDict):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolDefinition(TypedDict):
    """Existing OpenAI-compatible tool shape; parameters is a JSON Schema object."""

    type: Literal["function"]
    function: ToolFunction


@dataclass(frozen=True)
class InjectedFault:
    """A deterministic fault and the condition under which the world injects it."""

    tool: str
    condition: str
    observation: str


@dataclass(frozen=True)
class EnvironmentCheck:
    """Assertion evidence only, not an authoritative score or terminal Run status.

    Existing worlds declare trajectory assertions only. Stateful worlds may also
    report outcome assertions using the same ScenarioAssertion vocabulary.
    The control plane still scores persisted events with the registered suite.
    A stateful world may supply a bounded, canonical final_observation snapshot;
    it is in-process evidence only, not a new persisted event or terminal status.
    """

    combination: AssertionCombination
    trajectory: tuple[AssertionEvaluation, ...]
    outcome: tuple[AssertionEvaluation, ...] = ()
    final_observation: str | None = None


class Environment(Protocol):
    """Per-Run world, independent of agent, execution mode, and policy.

    reset rebuilds state/history and returns no observation. Tool calls return
    strings, or raise UnknownToolError/InvalidToolArgumentsError without changing
    state. Tool definitions are fresh caller-owned values. check is read-only.
    Same seed and valid action sequence reproduce observations and checks; a
    deterministic world need not vary observations across different seeds.
    """

    @property
    def fault_schedule(self) -> tuple[InjectedFault, ...]: ...

    def reset(self, seed: int) -> None: ...

    def tool_definitions(self) -> list[ToolDefinition]: ...

    def call(self, name: str, args: dict[str, object]) -> str: ...

    def check(self) -> EnvironmentCheck: ...


EnvironmentFactory = Callable[[dict[str, str]], Environment]


class UnknownToolError(ValueError):
    """The tool name is outside this world's allowlist."""


class InvalidToolArgumentsError(ValueError):
    """Arguments do not match a built-in deterministic tool contract."""
