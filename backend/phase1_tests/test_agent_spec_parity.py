"""Guard the Agent-side strict spec models against backend drift.

What this is: a parity test between ``app.fixture_agent`` (``extra=forbid``,
shared by the fixture, reference and pi agents through ``read_spec``) and the
backend ``EvaluationSpec`` family in ``app.phase1_schemas``.
Where it sits: the Control Plane writes the spec, the Runner forwards it, the
Agent re-parses it; every hop is strict, so a field added to one side only
fails every Run at the Agent.
Invariant: field sets and tool allowlists must be equal on every hop.
Next file to read: ``phase1_tests/test_runner_protocol_contract.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import fixture_agent
from app.phase1_schemas import (
    ALLOWED_POLICY_TOOLS,
    EvaluationLimits,
    EvaluationSpec,
    PolicyPatch,
)

RUST_LIB = (
    Path(__file__).resolve().parents[2]
    / "runner/crates/agentops-protocol/src/lib.rs"
)


@pytest.mark.parametrize(
    ("agent_model", "backend_model"),
    [
        (fixture_agent._EvaluationSpec, EvaluationSpec),
        (fixture_agent._Policy, PolicyPatch),
        (fixture_agent._Limits, EvaluationLimits),
    ],
)
def test_agent_spec_field_sets_match_backend(agent_model, backend_model):
    assert set(agent_model.model_fields) == set(backend_model.model_fields)


def test_agent_limit_defaults_match_backend():
    assert fixture_agent._Limits().model_dump() == EvaluationLimits().model_dump()


def test_agent_accepts_a_dumped_backend_spec():
    spec = EvaluationSpec(
        run_id="r",
        experiment_id="e",
        task="t",
        agent_id="fixture-agent.v1",
        policy=PolicyPatch(
            instruction_patch=["x"], tool_priority={"fetch_service_logs": 0.5}, max_steps=5
        ),
        scenario_params={"topic": "x"},
    )
    parsed = fixture_agent._EvaluationSpec.model_validate(spec.model_dump(mode="json"))
    assert parsed.policy is not None and parsed.policy.max_steps == 5


def test_tool_allowlists_are_equal_across_python_and_rust():
    source = RUST_LIB.read_text()
    match = re.search(r"const ALLOWED_TOOLS: [^=]*= \[(.*?)\];", source, re.DOTALL)
    assert match, "ALLOWED_TOOLS not found in Rust protocol"
    rust_tools = set(re.findall(r'"([^"]*)"', match.group(1)))
    assert set(fixture_agent.POLICY_TOOLS) == set(ALLOWED_POLICY_TOOLS)
    assert rust_tools == set(ALLOWED_POLICY_TOOLS)
