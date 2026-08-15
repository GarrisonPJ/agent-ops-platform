"""Out-of-process API probe for AgentOps operational alerting."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def request(api_url: str, path: str, *, timeout_seconds: float) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except OSError as exc:
        raise RuntimeError(f"GET {path} failed: {exc}") from exc
    body = json.loads(raw.decode("utf-8")) if raw else None
    return status, body


def classify_probe(
    *,
    live_status: int | None,
    live_body: Any,
    state_body: Any | None,
) -> dict[str, object]:
    if live_status != 200 or not isinstance(live_body, dict) or live_body.get("status") != "ok":
        return {
            "probe": "api_unavailable",
            "status": "degraded",
            "details": {"live_status": live_status, "live_body": live_body},
        }
    if isinstance(state_body, dict) and "primary_state" in state_body:
        return {
            "probe": "operations_state",
            "status": state_body.get("status", "unknown"),
            "primary_state": state_body["primary_state"],
            "observed_at": state_body.get("observed_at"),
        }
    return {"probe": "api_live", "status": "ok", "details": {"live_body": live_body}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="AgentOps API base URL",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="HTTP timeout for each probe request",
    )
    parser.add_argument(
        "--include-state",
        action="store_true",
        help="Also fetch /api/operations/state when liveness succeeds",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=900,
        help="Operational window passed to /api/operations/state",
    )
    args = parser.parse_args()

    live_status: int | None = None
    live_body: Any = None
    state_body: Any | None = None
    try:
        live_status, live_body = request(
            args.api_url,
            "/api/health/live",
            timeout_seconds=args.timeout_seconds,
        )
        if live_status == 200 and args.include_state:
            _, state_body = request(
                args.api_url,
                f"/api/operations/state?window_seconds={args.window_seconds}",
                timeout_seconds=args.timeout_seconds,
            )
    except RuntimeError as exc:
        summary = {
            "probe": "api_unavailable",
            "status": "degraded",
            "details": {"error": str(exc)},
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1

    summary = classify_probe(
        live_status=live_status,
        live_body=live_body,
        state_body=state_body,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["probe"] == "api_unavailable" or summary.get("status") == "degraded":
        return 1
    if summary.get("primary_state") not in (None, "ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
