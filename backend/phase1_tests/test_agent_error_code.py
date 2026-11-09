"""Agent-reported failure code: allowlist, ingress, diagnostics-only semantics.

Pure tests cover the closed allowlist and the analyzer; API/SQLite tests cover the
completion ingress and persistence. The code is an untrusted Agent claim: it must
never change status, score, success or the ADR-0012 evidence gate.
Read app.agent_error_codes, then app.phase1_service._attach_agent_error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_error_codes import (
    AGENT_ERROR_CATEGORIES,
    AgentErrorCategory,
    AgentErrorCode,
    agent_error_category,
)
from app.durable_events import ProviderFailureKind
from app.failure_analyzer import analyze_trajectory
from app.phase1_models import RunAnalysis
from app.phase1_schemas import ExecutionFacts
from app.scenario_assertions import (
    AssertionCombination,
    AssertionSuite,
    ScenarioAssertion,
    evaluate_assertion_suite,
)
from phase1_tests.test_cp_completion import (
    assert_terminal,
    complete,
    create_claim,
    facts,
    packet,
    upload,
)

APP = Path(__file__).resolve().parents[1] / "app"
AGENT_SOURCES = [
    "reference_agent.py",
    "pi_agent.py",
    "fixture_agent.py",
    "mcp_agent_client.py",
]


def test_allowlist_covers_every_code_the_agents_can_emit():
    emitted: set[str] = set()
    for name in AGENT_SOURCES:
        source = (APP / name).read_text()
        emitted |= set(re.findall(r'(?:_error|MCPAgentError)\("([A-Z_]+)"\)', source))
        emitted |= set(re.findall(r'print\("([A-Z_]+)", file=sys\.stderr', source))
        emitted |= set(re.findall(r'^\s+"[a-z]+": "(PI_[A-Z_]+)"', source, re.MULTILINE))
        emitted |= set(re.findall(r'return "((?:PI|AGENT)_[A-Z_]+)"', source))
    assert emitted, "scan found no emitted codes"
    assert emitted <= {code.value for code in AgentErrorCode}, emitted
    # Provider failures arrive as `_error(error.code)`; every enum value is allowed.
    assert {kind.value for kind in ProviderFailureKind} <= {c.value for c in AgentErrorCode}


def test_every_code_has_an_explicit_category():
    assert set(AGENT_ERROR_CATEGORIES) == set(AgentErrorCode)


def test_codes_are_bounded_ascii_and_categorised():
    for code in AgentErrorCode:
        assert re.fullmatch(r"[A-Z_]{1,64}", code.value)
        assert AGENT_ERROR_CATEGORIES[code] in AgentErrorCategory
    assert agent_error_category("PI_PROVIDER_ERROR") is AgentErrorCategory.PROVIDER
    assert agent_error_category("MCP_CALL_FAILED") is AgentErrorCategory.ENVIRONMENT_CONNECTION
    assert agent_error_category("PI_NOT_FOUND") is AgentErrorCategory.AGENT_STARTUP
    assert agent_error_category("INVALID_SPEC") is AgentErrorCategory.AGENT_STARTUP
    for behavior in (
        "PROVIDER_STEP_LIMIT", "PROVIDER_UNSUPPORTED_TOOL", "PROVIDER_SUBMIT_REQUIRED",
        "PROVIDER_SUBMIT_INVALID", "PI_STOP_LENGTH", "PI_SUBMIT_REQUIRED",
        "PI_EXIT_NONZERO", "PI_ABORTED", "AGENT_FAILED", "AGENT_TIMEOUT", "AGENT_CANCELLED",
    ):
        assert agent_error_category(behavior) is AgentErrorCategory.AGENT_BEHAVIOR, behavior
    assert agent_error_category("PROVIDER_TOOL_UNSUPPORTED") is None


def execution(**changes):
    return {
        "run_id": "run-1",
        "attempt": 1,
        "eligible": False,
        "submitted": False,
        "check_succeeded": False,
        "planned_environment_teardown": False,
        "failure": "agent_exit",
        **changes,
    }


def test_execution_facts_code_is_optional_nullable_and_closed():
    assert ExecutionFacts.model_validate(execution()).agent_error_code is None
    assert ExecutionFacts.model_validate(execution(agent_error_code=None)).agent_error_code is None
    parsed = ExecutionFacts.model_validate(execution(agent_error_code="PI_PROVIDER_ERROR"))
    assert parsed.agent_error_code is AgentErrorCode.PI_PROVIDER_ERROR
    for bad in ["PROVIDER_TOOL_UNSUPPORTED", "pi_provider_error", "", " PI_NOT_FOUND", 7, True, {}]:
        with pytest.raises(ValidationError):
            ExecutionFacts.model_validate(execution(agent_error_code=bad))


def agent_error(code):
    return {"code": code, "category": agent_error_category(code).value}


SUITE = AssertionSuite(
    combination=AssertionCombination.ALL,
    assertions=(ScenarioAssertion(type="tool-used", tool="fetch_document"),),
)


def analyse(trajectory):
    return analyze_trajectory(
        trajectory, assertion_result=evaluate_assertion_suite(SUITE, trajectory)
    )


def test_zero_step_run_without_a_code_still_blames_planning():
    report = analyse({"steps": [], "run_status": "failed", "status": "failed"})
    assert report.dominant_classification == "tool_selection_error"


@pytest.mark.parametrize(
    ("code", "dimension", "classification"),
    [
        ("PI_PROVIDER_ERROR", "execution", "provider_error"),
        ("PROVIDER_UNAVAILABLE", "execution", "provider_error"),
        ("MCP_CONNECTION_FAILED", "execution", None),
        ("PI_NOT_FOUND", "execution", None),
        ("INVALID_SPEC", "execution", None),
    ],
)
def test_zero_step_agent_code_is_not_planning_evidence(code, dimension, classification):
    report = analyse(
        {
            "steps": [],
            "run_status": "failed",
            "status": "failed",
            "agent_error": agent_error(code),
        }
    )
    assert "planning" not in report.dimensions
    assert report.dominant_classification == classification
    assert set(report.dimensions) == ({dimension} if dimension else set())
    assert all(item.failure_type != "tool_selection_error" for item in report.evidence)


@pytest.mark.parametrize(
    "code",
    ["PROVIDER_UNSUPPORTED_TOOL", "PROVIDER_STEP_LIMIT", "PI_STOP_LENGTH",
     "PI_SUBMIT_REQUIRED", "AGENT_TIMEOUT"],
)
def test_zero_step_agent_behavior_code_keeps_planning_attribution(code):
    report = analyse(
        {"steps": [], "run_status": "failed", "status": "failed", "agent_error": agent_error(code)}
    )
    assert "planning" in report.dimensions
    assert report.dominant_classification == "tool_selection_error"
    assert all(item.failure_type != "provider_error" for item in report.evidence)
    assert not any("Agent reported" in item.reason for item in report.evidence)


def test_startup_code_reason_names_startup_failure():
    report = analyse(
        {"steps": [], "status": "failed", "agent_error": agent_error("PI_NOT_FOUND")}
    )
    assert [item.reason for item in report.evidence] == ["Agent reported startup failure PI_NOT_FOUND"]


def test_agent_reported_code_is_ignored_for_a_successful_trajectory():
    report = analyze_trajectory(
        {"steps": [], "status": "success", "agent_error": agent_error("PI_PROVIDER_ERROR")}
    )
    assert report.evidence == []


def test_malformed_agent_error_is_ignored_and_provider_event_is_not_double_counted():
    assert analyze_trajectory({"steps": [], "status": "failed", "agent_error": "PI_X"}).evidence == []
    report = analyze_trajectory(
        {
            "steps": [],
            "status": "failed",
            "provider_error": True,
            "agent_error": agent_error("PROVIDER_TIMEOUT"),
        }
    )
    assert len(report.evidence) == 1


@pytest.mark.asyncio
async def test_unknown_code_is_rejected_at_the_completion_ingress(api):
    client, _factory = api
    run, claim = await create_claim(client)
    response = await complete(
        client,
        run,
        claim,
        status="failed",
        execution=facts(
            run["id"], claim["attempt"], eligible=False, failure="agent_exit",
            agent_error_code="PROVIDER_TOOL_UNSUPPORTED",
        ),
    )
    assert response.status_code == 422


async def finish_failed(client, factory, **changes):
    run, claim = await create_claim(client)
    result = await complete(
        client,
        run,
        claim,
        status="failed",
        execution=facts(
            run["id"], claim["attempt"], eligible=False, submitted=False,
            check_succeeded=False, planned_environment_teardown=False,
            failure="agent_exit", **changes,
        ),
    )
    return await assert_terminal(factory, result, expected="failed")


@pytest.mark.asyncio
async def test_code_is_persisted_as_diagnostics_without_changing_the_outcome(api):
    client, factory = api
    plain = await finish_failed(client, factory)
    coded = await finish_failed(client, factory, agent_error_code="PI_PROVIDER_ERROR")

    assert "agent_error_code" not in plain["run"]["metrics"]
    metrics = coded["run"]["metrics"]
    assert metrics["agent_error_code"] == "PI_PROVIDER_ERROR"
    assert metrics["agent_error_category"] == "provider"
    for key in ("status", "score", "error"):
        assert coded["run"][key] == plain["run"][key], key
    assert metrics["assertions"] == plain["run"]["metrics"]["assertions"]
    assert metrics["score_breakdown"] == plain["run"]["metrics"]["score_breakdown"]

    async with factory() as db:
        plain_analysis = await db.get(RunAnalysis, plain["run_id"])
        coded_analysis = await db.get(RunAnalysis, coded["run_id"])
    # The Agent's claim changes the blame, not the outcome.
    assert plain_analysis.dominant_type == "tool_selection_error"
    assert coded_analysis.dominant_type == "provider_error"
    assert "planning" not in coded_analysis.dimensions


@pytest.mark.asyncio
async def test_code_cannot_downgrade_or_upgrade_a_run(api):
    client, factory = api
    run, claim = await create_claim(client)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    result = await complete(
        client,
        run,
        claim,
        execution=facts(run["id"], claim["attempt"], agent_error_code="PI_PROVIDER_ERROR"),
    )
    body = await assert_terminal(factory, result, expected="succeeded")
    assert body["run"]["metrics"]["agent_error_code"] == "PI_PROVIDER_ERROR"
    assert body["run"]["score"] > 0
    async with factory() as db:
        analysis = await db.get(RunAnalysis, run["id"])
    assert analysis.dominant_type is None or analysis.dominant_type != "provider_error"
