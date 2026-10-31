# History and document disposition

These are dated records, not current design, commands to resume, or a second task tracker. Start with [README](../../README.md), [CONTEXT](../../CONTEXT.md), [roadmap](../../ROADMAP.md), and the single [Phase 2 task list](../agents/phase2-tasks.md).

| Document | Retained value |
|---|---|
| [phase1-verification-log.md](phase1-verification-log.md) | English index and historical hosted-CI anchor |
| [phase1-verification-log.zh-CN.md](phase1-verification-log.zh-CN.md) | Consolidated Chinese milestone/fix/validation history |
| [system-acceptance-2026-10-15.md](system-acceptance-2026-10-15.md) | Detailed acceptance of that exact historical source/runtime boundary |
| [calibration-evidence-log.md](calibration-evidence-log.md) | Dated mechanics checkpoints and labeling rubric |

## H1 documentation consolidation (2026-10-31)

- Removed root `agent-ops-platform-handoff-2026-09-12.md`: its resumed commands, local paths, obsolete blockers and repeated verification narratives were superseded by the above history, current runbooks, CONTEXT and task list. The prior tracked text remains in Git history; it is not an instruction source.
- Consolidated the long Chinese Phase 1 roadmap extract instead of retaining a second day-by-day handoff. Kept important commits/dates, red/green defect history, limits and publication-policy evolution; removed inaccessible local evidence citations.
- Moved the 2026-10-15 acceptance narrative from operations to this history directory; the operations path now describes current repeatable checks and limits, not old counts as current acceptance.
- Kept **ADR-0001..0014 and their original identities**. Amended stale implementation statements only where needed; H3/H4 accepted decisions remain future implementations. Age alone is not a reason to erase a binding trust decision.
- Reviewed the isolated tree's tracked and local Markdown inventory: no standalone early issue/PRD/UI-design documents remain here to implement or delete. Earlier removed executor/open-tool-bus/UI plans are historical, not outstanding requirements. Local archives in another/protected worktree were neither copied nor mutated.
- README/Chinese README, roadmap translations, architecture, learning and operations entries now separate implemented H1 from future adapters/pass^k/OTel, local engineering evidence from hosted CI, and explicitly deferred real-model acceptance from a pass.

Current calibration rules: [evaluation-calibration.md](../evaluation-calibration.md). Current acceptance procedure: [system-acceptance.md](../operations/system-acceptance.md). No tracked document depends on a local-only evidence directory to establish a claim.
