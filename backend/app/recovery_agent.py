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

    emit(
        "step_completed",
        {
            "index": 0,
            "decision_summary": "Complete after the replacement Runner claims the job.",
            "tool_call": {"name": "check_service_health", "arguments": {}},
            "observation": "Replacement Runner completed the recovered attempt.",
            "latency_ms": 1,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
