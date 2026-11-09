"""Closed allowlist of failure codes an Agent-under-test may print on stderr.

An Agent adapter prints exactly one of these codes as the LAST line of stderr
before it exits; the Runner reports it, unchanged, as ``agent_error_code`` in
the completion's execution facts (``app.phase1_schemas.ExecutionFacts``).
The Rust copy lives in ``runner/crates/agentops-protocol/src/lib.rs``
(``AGENT_ERROR_CODES``); ``phase1_tests/test_runner_protocol_contract.py``
fails when the two sets diverge.

Where it sits in one Run: the Agent prints a code, the Runner's stderr tail
matches it exactly, ``complete_job`` validates it against this enum, and
``_trajectory_from_events`` turns it into diagnostics for the failure analyzer.

Invariant: a code is Agent-reported and untrusted. It never changes Run status,
score, success, terminal kind or the ADR-0012 trusted-evidence gate. Adding a
code means editing this file and the Rust const together, and nothing else.

Next file to read: ``app.reference_agent`` (emitter) or ``app.failure_analyzer``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class AgentErrorCategory(StrEnum):
    # Attribution for analysis only. The model is part of the Agent-under-test,
    # so `provider` means infrastructure (endpoint unusable), never model choices.
    PROVIDER = "provider"
    ENVIRONMENT_CONNECTION = "environment_connection"
    AGENT_STARTUP = "agent_startup"
    AGENT_BEHAVIOR = "agent_behavior"


class AgentErrorCode(StrEnum):
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_CONFIGURATION_ERROR = "PROVIDER_CONFIGURATION_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_HTTP_ERROR = "PROVIDER_HTTP_ERROR"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_UNKNOWN = "PROVIDER_UNKNOWN"
    PROVIDER_STEP_LIMIT = "PROVIDER_STEP_LIMIT"
    PROVIDER_UNSUPPORTED_TOOL = "PROVIDER_UNSUPPORTED_TOOL"
    PROVIDER_SUBMIT_REQUIRED = "PROVIDER_SUBMIT_REQUIRED"
    PROVIDER_SUBMIT_INVALID = "PROVIDER_SUBMIT_INVALID"
    PI_PROVIDER_ERROR = "PI_PROVIDER_ERROR"
    PI_STOP_LENGTH = "PI_STOP_LENGTH"
    MCP_ENDPOINT_INVALID = "MCP_ENDPOINT_INVALID"
    MCP_TOKEN_INVALID = "MCP_TOKEN_INVALID"
    MCP_DISCOVERY_INVALID = "MCP_DISCOVERY_INVALID"
    MCP_CONNECTION_FAILED = "MCP_CONNECTION_FAILED"
    MCP_CALL_INVALID = "MCP_CALL_INVALID"
    MCP_CALL_FAILED = "MCP_CALL_FAILED"
    MCP_USAGE_REJECTED = "MCP_USAGE_REJECTED"
    INVALID_SPEC = "INVALID_SPEC"
    AGENT_FAILED = "AGENT_FAILED"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_CANCELLED = "AGENT_CANCELLED"
    PI_NOT_FOUND = "PI_NOT_FOUND"
    PI_SUBMIT_REQUIRED = "PI_SUBMIT_REQUIRED"
    PI_EXIT_NONZERO = "PI_EXIT_NONZERO"
    PI_ABORTED = "PI_ABORTED"


_C = AgentErrorCategory
_K = AgentErrorCode

# Explicit table, no prefix rules: PROVIDER_STEP_LIMIT and friends are the
# model's own outcome even though the legacy ProviderFailureKind taxonomy
# counted them as provider failures.
AGENT_ERROR_CATEGORIES: Final[dict[AgentErrorCode, AgentErrorCategory]] = {
    _K.PROVIDER_NOT_CONFIGURED: _C.PROVIDER,
    _K.PROVIDER_CONFIGURATION_ERROR: _C.PROVIDER,
    _K.PROVIDER_TIMEOUT: _C.PROVIDER,
    _K.PROVIDER_UNAVAILABLE: _C.PROVIDER,
    _K.PROVIDER_RATE_LIMITED: _C.PROVIDER,
    _K.PROVIDER_HTTP_ERROR: _C.PROVIDER,
    _K.PROVIDER_INVALID_RESPONSE: _C.PROVIDER,
    _K.PROVIDER_UNKNOWN: _C.PROVIDER,
    _K.PI_PROVIDER_ERROR: _C.PROVIDER,
    _K.MCP_ENDPOINT_INVALID: _C.ENVIRONMENT_CONNECTION,
    _K.MCP_TOKEN_INVALID: _C.ENVIRONMENT_CONNECTION,
    _K.MCP_DISCOVERY_INVALID: _C.ENVIRONMENT_CONNECTION,
    _K.MCP_CONNECTION_FAILED: _C.ENVIRONMENT_CONNECTION,
    _K.MCP_CALL_INVALID: _C.ENVIRONMENT_CONNECTION,
    _K.MCP_CALL_FAILED: _C.ENVIRONMENT_CONNECTION,
    _K.MCP_USAGE_REJECTED: _C.ENVIRONMENT_CONNECTION,
    _K.INVALID_SPEC: _C.AGENT_STARTUP,
    _K.PI_NOT_FOUND: _C.AGENT_STARTUP,
    _K.PROVIDER_STEP_LIMIT: _C.AGENT_BEHAVIOR,
    _K.PROVIDER_UNSUPPORTED_TOOL: _C.AGENT_BEHAVIOR,
    _K.PROVIDER_SUBMIT_REQUIRED: _C.AGENT_BEHAVIOR,
    _K.PROVIDER_SUBMIT_INVALID: _C.AGENT_BEHAVIOR,
    _K.PI_STOP_LENGTH: _C.AGENT_BEHAVIOR,
    _K.PI_SUBMIT_REQUIRED: _C.AGENT_BEHAVIOR,
    _K.PI_EXIT_NONZERO: _C.AGENT_BEHAVIOR,
    _K.PI_ABORTED: _C.AGENT_BEHAVIOR,
    _K.AGENT_FAILED: _C.AGENT_BEHAVIOR,
    _K.AGENT_TIMEOUT: _C.AGENT_BEHAVIOR,
    _K.AGENT_CANCELLED: _C.AGENT_BEHAVIOR,
}


def agent_error_category(code: str) -> AgentErrorCategory | None:
    """Return the category of an allowlisted code, else None."""
    try:
        return AGENT_ERROR_CATEGORIES[AgentErrorCode(code)]
    except ValueError:
        return None
