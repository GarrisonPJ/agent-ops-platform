"""Versioned built-in Scenario identifiers shared across the control plane."""

from __future__ import annotations

import re

CHECKOUT_SCENARIO_ID = "checkout-api-latency.v1"
MULTI_STEP_RESEARCH_SCENARIO_ID = "multi-step-research.v1"
API_FAULT_ORCHESTRATION_SCENARIO_ID = "api-fault-orchestration.v1"

DEFAULT_SCENARIO_ID = CHECKOUT_SCENARIO_ID

# <name>.v<N> — same id is comparable across Runs; semantic changes require a new version.
SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]+\.v[0-9]+$")
SCENARIO_PARAM_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
