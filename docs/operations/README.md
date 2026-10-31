# Operations runbooks

Repeatable procedures for the current Phase 1 + H1 integration. Decisions
live in ADRs; executable status lives only in `docs/agents/phase2-tasks.md`.
Historical acceptance is archived separately, not presented as current proof.

| Topic | Document |
|---|---|
| Fault classification and probes | [fault-matrix.md](fault-matrix.md) |
| Reliability, token rotation, and local validation | [reliability-and-local-validation.md](reliability-and-local-validation.md) |
| Current acceptance procedure and evidence boundaries | [system-acceptance.md](system-acceptance.md) |
| Operator Agent registry and recovery gate | [agent-registry.md](agent-registry.md) |
| Deferred real-model acceptance and existing endpoint configuration | [local-model.md](local-model.md) |
| Backup and disposable restore rehearsal | [backup-restore.md](backup-restore.md) |
| Experiment retention and redaction | [data-retention.md](data-retention.md) |

Machine-readable rollup: `GET /api/operations/state`. Out-of-process probe:
`make health-probe` or `python scripts/health_probe.py --include-state`.
