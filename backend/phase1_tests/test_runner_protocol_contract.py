"""Guard the Rust Runner's strict response structs against backend drift.

The Rust protocol structs use ``#[serde(deny_unknown_fields)]``, so any field
the backend adds to a response model the Runner parses (e.g. H3's
``trial_group_id``/``trial_index`` on ``RunResponse``) breaks every completion
unless the Rust struct gains it too. This test reads the struct field names
from ``runner/crates/agentops-protocol/src/lib.rs`` with a regex (no cargo, no
network, fully deterministic) and asserts they equal the backend Pydantic
model's field set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent_error_codes import AgentErrorCode
from app.phase1_schemas import (
    ClaimedRun,
    ClaimResponse,
    CompletionResponse,
    EvaluationSpec,
    ExecutionFacts,
    RunResponse,
)

RUST_LIB = (
    Path(__file__).resolve().parents[2]
    / "runner/crates/agentops-protocol/src/lib.rs"
)


def rust_struct_fields(name: str) -> set[str]:
    source = RUST_LIB.read_text()
    match = re.search(
        rf"pub struct {name}\b[^{{]*\{{(.*?)^\}}", source, re.DOTALL | re.MULTILINE
    )
    assert match, f"struct {name} not found in {RUST_LIB}"
    body = match.group(1)
    assert "rename" not in body, f"{name} uses serde rename; extend this test"
    return set(re.findall(r"^\s*pub (\w+)\s*:", body, re.MULTILINE))


@pytest.mark.parametrize(
    ("rust_name", "model"),
    [
        ("RunResponse", RunResponse),
        ("CompletionResponse", CompletionResponse),
        ("ClaimedRun", ClaimedRun),
        ("ClaimResponse", ClaimResponse),
        ("EvaluationSpec", EvaluationSpec),
        ("ExecutionFacts", ExecutionFacts),
    ],
)
def test_rust_struct_fields_match_backend_model(rust_name, model):
    assert rust_struct_fields(rust_name) == set(model.model_fields)


def test_rust_agent_error_allowlist_matches_python():
    """The Runner reports only codes the backend accepts, and vice versa."""
    source = RUST_LIB.read_text()
    match = re.search(
        r"pub const AGENT_ERROR_CODES: &\[&str\] = &\[(.*?)\];", source, re.DOTALL
    )
    assert match, "AGENT_ERROR_CODES not found"
    rust_codes = re.findall(r'"([^"]*)"', match.group(1))
    assert len(rust_codes) == len(set(rust_codes)), "duplicate Rust code"
    assert set(rust_codes) == {code.value for code in AgentErrorCode}
    bound = re.search(r"pub const MAX_AGENT_ERROR_LINE_BYTES: usize = (\d+);", source)
    assert bound and all(len(code) <= int(bound.group(1)) for code in rust_codes)
