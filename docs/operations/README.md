# Operations runbooks

Repeatable control-plane procedures for AgentOps Phase 1.

| Topic | Document |
|---|---|
| Fault classification and probes | [fault-matrix.md](fault-matrix.md) |
| Backup and disposable restore rehearsal | [backup-restore.md](backup-restore.md) |
| Experiment retention and redaction | [data-retention.md](data-retention.md) |

Machine-readable rollup: `GET /api/operations/state`. Out-of-process probe:
`make health-probe` or `python scripts/health_probe.py --include-state`.
