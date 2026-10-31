# Phase 1 verification log

Index only. The narrative checklist is
[system-acceptance-2026-10-15.md](system-acceptance-2026-10-15.md). Counts below are
the GitHub Actions result on `8bd2ef0` (2026-10-15): six jobs green
(`backend`, `database-recovery`, `frontend`, `rust`, `compose`,
`golden-e2e`); 265 backend tests with PostgreSQL and no skips; 34 frontend
unit tests; 2 recorded E2E tests; 15 Rust runner tests.

| Milestone | Status | Anchor |
|---|---|---|
| Phase 1.1 Runner recovery | Complete | ADR-0002, `scripts/recovery_e2e.py` |
| Phase 1.2 Provider boundary | Complete | ADR-0003 |
| Phase 1.3 Operations | Complete | ADR-0004, ADR-0005, `docs/operations/` |
| Phase 1.4 Scenario registry | Complete | ADR-0006, three `scenario_id`s in `CONTEXT.md` |
| Closed loop (classify, compare, regression gate) | Complete | `failure_analyzer.py`, regression-case API |
| Calibration mechanics gate | Complete as a mechanics gate, not an accuracy claim | `docs/evaluation-calibration.md` |
| Hosted public demo | Dropped | Recorded preview only (`VITE_MOCK_API`) |

Phase 2 started 2026-10-17. It does not reopen these milestones.

The consolidated Chinese history is in
[phase1-verification-log.zh-CN.md](phase1-verification-log.zh-CN.md). The
English roadmap text from commit `8bd2ef0` remains in git history of
`ROADMAP.md`.
