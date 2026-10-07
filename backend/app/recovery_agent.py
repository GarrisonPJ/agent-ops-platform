"""Long-lived deterministic agent used by the real-stack recovery test."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


MARKER = Path(
    os.getenv("AGENTOPS_RECOVERY_MARKER", "/tmp/agentops-recovery-marker")
)


def emit(event_type: str, payload: dict) -> None:
    print(
        json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")),
        flush=True,
    )


def main() -> int:
    sys.stdin.read()
    if not MARKER.exists():
        MARKER.write_text("started\n", encoding="utf-8")
        emit(
            "process_output",
            {"stream": "stdout", "content": "Waiting for Runner recovery"},
        )
        time.sleep(60)
        return 1

    recovered_steps = (
        (
            "Establish whether the service is healthy before reading broad logs.",
            "check_service_health",
            {"service": "checkout-api"},
            "Service is healthy but checkout latency is elevated.",
        ),
        (
            "Use metrics to localize the latency bottleneck.",
            "query_service_metrics",
            {"service": "checkout-api", "metric": "dependency_latency"},
            "Payment dependency p95 increased from 110ms to 1.8s.",
        ),
        (
            "Fetch only the evidence-backed payment timeout logs.",
            "fetch_service_logs",
            {"service": "checkout-api", "query": "payment dependency timeout"},
            "Requests are delayed by payment-gateway connection pool saturation.",
        ),
    )
    for index, (summary, tool, arguments, observation) in enumerate(recovered_steps):
        emit(
            "step_completed",
            {
                "index": index,
                "decision_summary": summary,
                "tool_call": {"name": tool, "arguments": arguments},
                "observation": observation,
                "latency_ms": 1,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
