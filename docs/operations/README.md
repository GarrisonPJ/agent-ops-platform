# Operations runbooks

Repeatable control-plane procedures for the Phase 1 stack. They stay in
force until a Phase 2 task says a procedure changed. Harness decisions are
in `docs/adr/0007`–`0011`; these runbooks do not restate them.

| Topic | Document |
|---|---|
| Fault classification and probes | [fault-matrix.md](fault-matrix.md) |
| Reliability, token rotation, and local validation | [reliability-and-local-validation.md](reliability-and-local-validation.md) |
| Current-version system acceptance and evidence boundaries | [system-acceptance.md](system-acceptance.md) |
| Backup and disposable restore rehearsal | [backup-restore.md](backup-restore.md) |
| Experiment retention and redaction | [data-retention.md](data-retention.md) |

Machine-readable rollup: `GET /api/operations/state`. Out-of-process probe:
`make health-probe` or `python scripts/health_probe.py --include-state`.
