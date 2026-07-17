"""Deterministic checkout-latency agent used by the Rust local runner.

The default protocol is one EvaluationSpec JSON object on stdin and raw JSONL
events on stdout. The runner adds envelope metadata, emits lifecycle events,
and maps the process exit code to the terminal run status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from app.phase1_schemas import EvaluationSpec


def _emit(event_type: str, payload: dict) -> None:
    print(json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")), flush=True)


def _step(
    index: int,
    summary: str,
    tool: str,
    arguments: dict,
    observation: str,
    latency_ms: int,
) -> None:
    _emit(
        "step_completed",
        {
            "index": index,
            "decision_summary": summary,
            "tool_call": {"name": tool, "arguments": arguments},
            "observation": observation,
            "latency_ms": latency_ms,
            "token_prompt": 72 + index * 3,
            "token_completion": 24 + index * 2,
            "context_window": {"used": 1200 + index * 180, "limit": 8192},
        },
    )


def _baseline() -> int:
    _emit("process_output", {"stream": "stdout", "content": "Starting baseline evaluation"})
    for index in range(6):
        _step(
            index,
            "Fetch logs again without gathering a new signal.",
            "fetch_service_logs",
            {"service": "checkout-api", "window": "5m"},
            "The same noisy request samples are returned; the cause is still inconclusive.",
            85 + index * 4,
        )
    return 1


def _replay() -> int:
    _emit("process_output", {"stream": "stdout", "content": "Applying candidate policy"})
    _step(
        0,
        "Establish whether the service is healthy before reading broad logs.",
        "check_service_health",
        {"service": "checkout-api"},
        "Service is healthy but checkout latency is elevated.",
        32,
    )
    _step(
        1,
        "Use metrics to localize the latency bottleneck.",
        "query_service_metrics",
        {"service": "checkout-api", "metric": "dependency_latency"},
        "Payment dependency p95 increased from 110ms to 1.8s.",
        41,
    )
    _step(
        2,
        "Fetch only the evidence-backed payment timeout logs.",
        "fetch_service_logs",
        {"service": "checkout-api", "query": "payment dependency timeout"},
        "Requests are delayed by payment-gateway connection pool saturation.",
        48,
    )
    return 0


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
    if spec.scenario_id != "checkout-api-latency":
        print(f"unsupported scenario: {spec.scenario_id}", file=sys.stderr)
        return 2
    return _replay() if spec.policy is not None else _baseline()


if __name__ == "__main__":
    raise SystemExit(main())
