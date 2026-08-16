"""Controlled assertion vocabulary for Scenario terminal scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AssertionCombination(StrEnum):
    ALL = "all"
    WEIGHTED = "weighted"
    ANY = "any"


ALLOWED_ASSERTION_TYPES = frozenset(
    {
        "tool-used",
        "tool-args-match",
        "tool-sequence",
        "step-count",
        "equals",
        "contains",
        "json-match",
    }
)


@dataclass(frozen=True)
class ScenarioAssertion:
    type: str
    weight: float = 1.0
    threshold: float = 1.0
    tool: str | None = None
    arguments: dict[str, str] | None = None
    sequence: tuple[str, ...] | None = None
    min_steps: int | None = None
    max_steps: int | None = None
    expected: str | None = None
    contains: str | None = None
    json_path: str | None = None
    json_expected: Any | None = None

    def __post_init__(self) -> None:
        if self.type not in ALLOWED_ASSERTION_TYPES:
            raise ValueError(f"unsupported assertion type: {self.type}")
        if self.weight <= 0:
            raise ValueError("assertion weight must be positive")
        if not 0 < self.threshold <= 1:
            raise ValueError("assertion threshold must be in (0, 1]")


@dataclass(frozen=True)
class AssertionSuite:
    combination: AssertionCombination
    assertions: tuple[ScenarioAssertion, ...]


@dataclass(frozen=True)
class AssertionEvaluation:
    type: str
    score: float
    passed: bool
    weight: float
    threshold: float
    detail: str


def _tool_names(steps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in steps:
        action = step.get("action")
        if isinstance(action, dict):
            name = action.get("name")
            if isinstance(name, str):
                names.append(name)
        elif isinstance(action, str):
            names.append(action)
    return names


def _tool_arguments(step: dict[str, Any]) -> dict[str, Any]:
    action = step.get("action")
    if not isinstance(action, dict):
        return {}
    arguments = action.get("arguments")
    return arguments if isinstance(arguments, dict) else {}


def _arguments_match(expected: dict[str, str], actual: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if str(actual.get(key)) != value:
            return False
    return True


def _sequence_score(required: tuple[str, ...], actual: list[str]) -> float:
    if not required:
        return 1.0
    if len(actual) < len(required):
        return len([1 for index, tool in enumerate(actual) if tool == required[index]]) / len(
            required
        )
    if actual[: len(required)] == list(required):
        return 1.0
    matches = 0
    for index, tool in enumerate(required):
        if index < len(actual) and actual[index] == tool:
            matches += 1
        else:
            break
    return matches / len(required)


def _final_observation(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return ""
    observation = steps[-1].get("observation")
    return observation if isinstance(observation, str) else ""


def _json_path_value(payload: Any, path: str) -> Any:
    current = payload
    for segment in path.split("."):
        if not segment:
            continue
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def evaluate_assertion(
    assertion: ScenarioAssertion, trajectory: dict[str, Any]
) -> AssertionEvaluation:
    steps: list[dict[str, Any]] = trajectory.get("steps", [])
    tool_names = _tool_names(steps)
    score = 0.0
    detail = ""

    if assertion.type == "tool-used":
        score = 1.0 if assertion.tool and assertion.tool in tool_names else 0.0
        detail = f"tools={tool_names}"
    elif assertion.type == "tool-args-match":
        if assertion.tool and assertion.arguments is not None:
            matched = any(
                isinstance(step.get("action"), dict)
                and step["action"].get("name") == assertion.tool
                and _arguments_match(assertion.arguments, _tool_arguments(step))
                for step in steps
            )
            score = 1.0 if matched else 0.0
        detail = f"tool={assertion.tool} arguments={assertion.arguments}"
    elif assertion.type == "tool-sequence":
        required = assertion.sequence or ()
        score = _sequence_score(required, tool_names)
        detail = f"expected={list(required)} actual={tool_names}"
    elif assertion.type == "step-count":
        count = len(steps)
        min_steps = assertion.min_steps
        max_steps = assertion.max_steps
        if min_steps is not None and count < min_steps:
            score = count / min_steps if min_steps else 0.0
        elif max_steps is not None and count > max_steps:
            score = max_steps / count if count else 0.0
        else:
            score = 1.0
        detail = f"steps={count} min={min_steps} max={max_steps}"
    elif assertion.type == "equals":
        actual = _final_observation(steps)
        score = 1.0 if assertion.expected is not None and actual == assertion.expected else 0.0
        detail = f"expected={assertion.expected!r} actual={actual!r}"
    elif assertion.type == "contains":
        actual = _final_observation(steps)
        needle = assertion.contains or ""
        score = 1.0 if needle and needle in actual else 0.0
        detail = f"contains={needle!r} actual={actual!r}"
    elif assertion.type == "json-match":
        actual = _final_observation(steps)
        try:
            payload = json.loads(actual)
        except json.JSONDecodeError:
            payload = None
        if payload is None or assertion.json_path is None:
            score = 0.0
        else:
            value = _json_path_value(payload, assertion.json_path)
            score = 1.0 if value == assertion.json_expected else 0.0
        detail = f"path={assertion.json_path} expected={assertion.json_expected!r}"

    passed = score >= assertion.threshold
    return AssertionEvaluation(
        type=assertion.type,
        score=score,
        passed=passed,
        weight=assertion.weight,
        threshold=assertion.threshold,
        detail=detail,
    )


def _combine_scores(
    combination: AssertionCombination, evaluations: list[AssertionEvaluation]
) -> float:
    if not evaluations:
        return 1.0

    if combination == AssertionCombination.ALL:
        return min(item.score for item in evaluations)

    if combination == AssertionCombination.ANY:
        return max(item.score for item in evaluations)

    total_weight = sum(item.weight for item in evaluations)
    if total_weight <= 0:
        return 0.0
    return sum(item.score * item.weight for item in evaluations) / total_weight


def evaluate_assertion_suite(
    suite: AssertionSuite, trajectory: dict[str, Any]
) -> dict[str, Any]:
    evaluations = [
        evaluate_assertion(assertion, trajectory) for assertion in suite.assertions
    ]
    aggregate = _combine_scores(suite.combination, evaluations)
    return {
        "aggregate": aggregate,
        "combination": suite.combination.value,
        "assertions": [
            {
                "type": item.type,
                "score": item.score,
                "passed": item.passed,
                "weight": item.weight,
                "threshold": item.threshold,
                "detail": item.detail,
            }
            for item in evaluations
        ],
    }
