"""Deterministic checkout-latency agent used by the Rust local runner.

The default protocol is one EvaluationSpec JSON object on stdin and raw JSONL
events on stdout. The runner adds envelope metadata, emits lifecycle events,
and maps the process exit code to the terminal run status.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

import app.scenarios  # noqa: F401 — register built-in Scenarios
from app.phase1_schemas import EvaluationSpec
from app.scenario_registry import UnknownScenarioError, run_scenario


def _emit(event_type: str, payload: dict) -> None:
    print(json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")), flush=True)


def _read_spec(args: argparse.Namespace) -> EvaluationSpec:
    if args.spec_json:
        raw = args.spec_json
    elif args.spec_file:
        raw = Path(args.spec_file).read_text(encoding="utf-8")
    elif os.getenv("AGENTOPS_EVALUATION_SPEC"):
        raw = os.environ["AGENTOPS_EVALUATION_SPEC"]
    else:
        raw = sys.stdin.read()
    return EvaluationSpec.model_validate_json(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic AgentOps demo scenario")
    parser.add_argument("--spec-json")
    parser.add_argument("--spec-file")
    args = parser.parse_args()
    try:
        spec = _read_spec(args)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"invalid evaluation spec: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run_scenario(spec, _emit))
    except UnknownScenarioError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
