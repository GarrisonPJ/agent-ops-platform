# Operational fault matrix

Repeatable commands for classifying AgentOps control-plane failures. All
examples assume the Phase 1 Compose stack is running and the API listens on
`http://127.0.0.1:8000`.

## Quick probes

| Symptom | Command | Healthy signal | Degraded signal |
|---|---|---|---|
| API process down | `python scripts/health_probe.py --api-url http://127.0.0.1:8000` | exit `0`, `"probe": "api_live"` | exit `1`, `"probe": "api_unavailable"` |
| Classified operator state | `python scripts/health_probe.py --include-state` | exit `0`, `"primary_state": "ok"` | exit `1`, non-`ok` `primary_state` |
| Database + schema | `curl -sS http://127.0.0.1:8000/api/health/ready` | HTTP `200`, `"schema": "ok"` | HTTP `503`, `"schema": "unavailable"` |
| Runner availability | `curl -sS http://127.0.0.1:8000/api/health/runner` | HTTP `200`, `"active_runner_count" >= 1` | HTTP `503`, `"active_runner_count": 0` |
| Machine-readable rollup | `curl -sS 'http://127.0.0.1:8000/api/operations/state?window_seconds=900'` | HTTP `200`, `"primary_state": "ok"` | HTTP `200`, `"status": "degraded"` |

## Primary state precedence

`GET /api/operations/state` always returns HTTP `200`. Inspect
`primary_state` and the `states[]` list:

1. `database_unavailable` — PostgreSQL unreachable from the API process.
2. `schema_drift` — live `alembic_version` differs from the application head.
3. `runner_unavailable` — no authenticated Runner heartbeat inside the
   availability window.
4. `lease_expired` — recoverable Runs hold an expired lease.
5. `provider_rate_limited` — terminal Runs in the window report
   `PROVIDER_RATE_LIMITED`.
6. `provider_unavailable` — terminal Runs in the window report timeout,
   unavailable, or HTTP provider faults.
7. `ok` — none of the above are active.

Provider outage and rate limiting are intentionally separate states so paging
rules can throttle retries without masking hard outages.

## Correlation workflow

1. Confirm API liveness with `/api/health/live` or `scripts/health_probe.py`.
2. Read `GET /api/operations/state` for the classified primary state,
   bounded incidents, and the embedded operations summary.
3. For a specific Run, use `GET /api/operations/runs/{run_id}` to inspect lease,
   attempt history, and sanitized provider telemetry.
4. For migration or backup issues, follow
   [backup-restore.md](backup-restore.md). For retention work, follow
   [data-retention.md](data-retention.md).

## Example degraded responses

Schema drift after a partial deploy:

```bash
curl -sS http://127.0.0.1:8000/api/health/ready | jq .
curl -sS http://127.0.0.1:8000/api/operations/state | jq '.primary_state,.states'
```

Missing Runner with queued work:

```bash
curl -sS http://127.0.0.1:8000/api/health/runner | jq .
curl -sS http://127.0.0.1:8000/api/operations/state | jq '.summary.queue_depth,.primary_state'
```

Provider rate limit vs outage in the same window:

```bash
curl -sS 'http://127.0.0.1:8000/api/operations/state?window_seconds=3600' \
  | jq '.states[] | select(.code | startswith("provider_"))'
```
