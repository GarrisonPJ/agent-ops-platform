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

from typing import get_args

from app.agent_error_codes import AgentErrorCode
from app.durable_events import TerminalFailureKind
from app.phase1_schemas import (
    ALLOWED_POLICY_TOOLS,
    TERMINAL_RUN_STATUSES,
    ClaimedRun,
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    CompletionResponse,
    EvaluationLimits,
    EvaluationSpec,
    EventEnvelope,
    EventUploadRequest,
    EventUploadResponse,
    ExecutionFacts,
    ExecutionFailure,
    ExecutionMode,
    HeartbeatRequest,
    HeartbeatResponse,
    PolicyPatch,
    RunResponse,
    RunStatus,
)

RUST_LIB = (
    Path(__file__).resolve().parents[2]
    / "runner/crates/agentops-protocol/src/lib.rs"
)


def _rust_source() -> str:
    return RUST_LIB.read_text()


def rust_struct_fields(name: str) -> set[str]:
    """Wire field names of a Rust struct, honouring ``#[serde(rename = "x")]``."""
    match = re.search(
        rf"pub struct {name}\b[^{{]*\{{(.*?)^\}}",
        _rust_source(),
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"struct {name} not found in {RUST_LIB}"
    fields: set[str] = set()
    renamed: str | None = None
    for line in match.group(1).splitlines():
        rename = re.search(r'#\[serde\([^\]]*\brename = "([^"]+)"', line)
        if rename:
            renamed = rename.group(1)
            continue
        field = re.match(r"\s*pub (\w+)\s*:", line)
        if field:
            fields.add(renamed or field.group(1))
            renamed = None
    assert renamed is None, f"dangling serde rename in {name}"
    return fields


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def rust_enum_values(name: str) -> set[str]:
    """Wire values of a ``rename_all = "snake_case"`` unit-variant Rust enum."""
    source = _rust_source()
    match = re.search(
        rf'((?:#\[[^\n]*\]\n)+)pub enum {name}\b\s*\{{(.*?)^\}}',
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"enum {name} not found in {RUST_LIB}"
    assert 'rename_all = "snake_case"' in match.group(1), f"{name} must be snake_case"
    body = match.group(2)
    assert "rename" not in body, f"{name} has per-variant rename; extend this test"
    variants = re.findall(r"^\s*(?:#\[\w+\]\s*)?([A-Z]\w*)\s*,", body, re.MULTILINE)
    assert variants, f"no variants parsed for {name}"
    return {_snake(v) for v in variants}


def rust_str_array(name: str) -> list[str]:
    match = re.search(
        rf"(?:pub )?const {name}: [^=]*= &?\[(.*?)\];", _rust_source(), re.DOTALL
    )
    assert match, f"const {name} not found"
    return re.findall(r'"([^"]*)"', match.group(1))


@pytest.mark.parametrize(
    ("rust_name", "model"),
    [
        ("RunResponse", RunResponse),
        ("CompletionResponse", CompletionResponse),
        ("ClaimedRun", ClaimedRun),
        ("ClaimResponse", ClaimResponse),
        ("EvaluationSpec", EvaluationSpec),
        ("ExecutionFacts", ExecutionFacts),
        ("PolicyPatch", PolicyPatch),
        ("ExecutionLimits", EvaluationLimits),
        ("HeartbeatRequest", HeartbeatRequest),
        ("HeartbeatResponse", HeartbeatResponse),
        ("EventBatchRequest", EventUploadRequest),
        ("EventBatchResponse", EventUploadResponse),
        ("ClaimRequest", ClaimRequest),
        ("CompleteRequest", CompleteRequest),
        ("EventEnvelope", EventEnvelope),
    ],
)
def test_rust_struct_fields_match_backend_model(rust_name, model):
    assert rust_struct_fields(rust_name) == set(model.model_fields)


def test_rust_rename_is_honoured_for_event_type():
    assert "type" in rust_struct_fields("EventEnvelope")
    assert "event_type" not in rust_struct_fields("EventEnvelope")


@pytest.mark.parametrize(
    ("rust_name", "expected"),
    [
        ("ExecutionFailure", {m.value for m in ExecutionFailure}),
        ("TerminalFailureKind", {m.value for m in TerminalFailureKind}),
        ("ExecutionMode", {m.value for m in ExecutionMode}),
        (
            "CompletionStatus",
            set(get_args(CompletionResponse.model_fields["status"].annotation)),
        ),
        (
            "RunnerCommand",
            set(get_args(HeartbeatResponse.model_fields["command"].annotation)),
        ),
    ],
)
def test_rust_enum_values_match_backend(rust_name, expected):
    assert rust_enum_values(rust_name) == expected


def test_rust_completion_status_is_the_terminal_run_status_set():
    assert rust_enum_values("CompletionStatus") == set(TERMINAL_RUN_STATUSES)
    assert rust_enum_values("CompletionStatus") <= {s.value for s in RunStatus}


def test_rust_event_types_match_backend_envelope():
    rust = rust_str_array("ALLOWED_EVENT_TYPES")
    assert len(rust) == len(set(rust))
    assert set(rust) == set(get_args(EventEnvelope.model_fields["type"].annotation))


def test_rust_tool_allowlist_matches_python():
    rust = rust_str_array("ALLOWED_TOOLS")
    assert len(rust) == len(set(rust))
    assert set(rust) == set(ALLOWED_POLICY_TOOLS)


def test_rust_agent_error_allowlist_matches_python():
    """The Runner reports only codes the backend accepts, and vice versa."""
    source = _rust_source()
    match = re.search(
        r"pub const AGENT_ERROR_CODES: &\[&str\] = &\[(.*?)\];", source, re.DOTALL
    )
    assert match, "AGENT_ERROR_CODES not found"
    rust_codes = re.findall(r'"([^"]*)"', match.group(1))
    assert len(rust_codes) == len(set(rust_codes)), "duplicate Rust code"
    assert set(rust_codes) == {code.value for code in AgentErrorCode}
    bound = re.search(r"pub const MAX_AGENT_ERROR_LINE_BYTES: usize = (\d+);", source)
    assert bound and all(len(code) <= int(bound.group(1)) for code in rust_codes)
