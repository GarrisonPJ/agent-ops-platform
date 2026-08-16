"""Allowlisted Scenario registry for the Phase 1 control plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from app.phase1_schemas import (
    EvaluationSpec,
    ExecutionMode,
    PolicyPatch,
    validate_scenario_id,
    validate_scenario_params,
)
from app.scenario_assertions import AssertionCombination, AssertionSuite, ScenarioAssertion

EmitFn = Callable[[str, dict], None]


class ScenarioHandler(Protocol):
    """Stable execution surface for a registered built-in Scenario."""

    def run_fixture(self, spec: EvaluationSpec, emit: EmitFn) -> int: ...

    async def run_provider(self, spec: EvaluationSpec, emit: EmitFn) -> int: ...

    def candidate_policy_patch(self) -> PolicyPatch: ...


@dataclass(frozen=True)
class ScenarioParamDef:
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class ScenarioMetadata:
    id: str
    name: str
    description: str
    default_task: str
    allowed_tools: tuple[str, ...]
    params: tuple[ScenarioParamDef, ...] = ()
    assertions: tuple[ScenarioAssertion, ...] = ()
    assertion_combination: AssertionCombination = AssertionCombination.WEIGHTED


@dataclass(frozen=True)
class RegisteredScenario:
    metadata: ScenarioMetadata
    handler: ScenarioHandler


_REGISTRY: dict[str, RegisteredScenario] = {}


def register_scenario(metadata: ScenarioMetadata, handler: ScenarioHandler) -> None:
    if metadata.id in _REGISTRY:
        raise ValueError(f"scenario already registered: {metadata.id}")
    _REGISTRY[metadata.id] = RegisteredScenario(metadata=metadata, handler=handler)


def list_scenarios() -> list[ScenarioMetadata]:
    return [entry.metadata for entry in sorted(_REGISTRY.values(), key=lambda item: item.metadata.id)]


def get_scenario(scenario_id: str) -> RegisteredScenario | None:
    return _REGISTRY.get(scenario_id)


def require_scenario(scenario_id: str) -> RegisteredScenario:
    entry = get_scenario(scenario_id)
    if entry is None:
        raise UnknownScenarioError(scenario_id)
    return entry


def is_registered(scenario_id: str) -> bool:
    return scenario_id in _REGISTRY


def validate_registered_scenario_params(
    scenario_id: str,
    params: dict[str, object],
) -> dict[str, str]:
    entry = require_scenario(scenario_id)
    normalized = validate_scenario_params(params)
    allowed = {param.name for param in entry.metadata.params}
    unknown = set(normalized) - allowed
    if unknown:
        raise ScenarioParamError(
            f"unknown scenario_params for {scenario_id}: {', '.join(sorted(unknown))}"
        )
    missing = [
        param.name
        for param in entry.metadata.params
        if param.required and param.name not in normalized
    ]
    if missing:
        raise ScenarioParamError(
            f"missing required scenario_params for {scenario_id}: {', '.join(sorted(missing))}"
        )
    return normalized


def ensure_registered_scenario(
    scenario_id: str,
    params: dict[str, object],
) -> dict[str, str]:
    """Validate versioned scenario identity, registry membership, and params."""
    validate_scenario_id(scenario_id)
    return validate_registered_scenario_params(scenario_id, params)


async def run_scenario(spec: EvaluationSpec, emit: EmitFn) -> int:
    entry = require_scenario(spec.scenario_id)
    if spec.execution_mode == ExecutionMode.PROVIDER:
        return await entry.handler.run_provider(spec, emit)
    return entry.handler.run_fixture(spec, emit)


def candidate_policy_for_scenario(scenario_id: str) -> PolicyPatch:
    return require_scenario(scenario_id).handler.candidate_policy_patch()


def assertion_suite_for_scenario(scenario_id: str) -> AssertionSuite:
    entry = require_scenario(scenario_id)
    return AssertionSuite(
        combination=entry.metadata.assertion_combination,
        assertions=entry.metadata.assertions,
    )


class UnknownScenarioError(ValueError):
    def __init__(self, scenario_id: str) -> None:
        super().__init__(f"scenario is not registered: {scenario_id}")
        self.scenario_id = scenario_id


class ScenarioParamError(ValueError):
    pass
