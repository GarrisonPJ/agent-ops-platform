# 阅读路径

给要自己把这个仓库读懂的人。实现任务以 `docs/agents/phase2-tasks.md` 为准，词汇以 `CONTEXT.md` 为准。

| 顺序 | 文档 | 何时有 |
|---|---|---|
| 0 | [仓库地图与一次 Run](00-map.md) | 现在 |
| — | [术语表](glossary.md) | 现在 |
| 1 | [01-run-lifecycle.md](01-run-lifecycle.md) | H1-3 组件与生产路径边界（H1 集成前） |
| 2 | [Environment、最终状态与 MCP 边界](02-environment-and-mcp.md) | H1-1/H1-2/H1-6 组件；生产接线待集成 |
| 3 | `03-plug-in-your-agent.md` | H2-1 完成时 |
| 4 | [04-runner-lease-fencing.md](04-runner-lease-fencing.md) | H1-3 文档草稿（H1 集成前） |
| 5 | `05-analyzer-and-calibration.md` | H5 完成时 |
| 6 | `06-pass-k.md` | H3 完成时 |
| 7 | `07-otel-mapping.md` | H4 完成时 |

还没写的章节不要找，它们尚未落地。生产执行仍保留 Phase 1 单 Python 子进程路径；H1-1/H1-2 已有独立 Environment/MCP 组件，H1-3 有 additive 双进程监督组件，H1-6 提供订单 ledger。`01` 与 `04` 明确区分组件实现和未完成的生产接线，不代表 H1 已验收。Phase 2 的目标形状在 `docs/agentops_closed_loop_architecture.md` 的 “Phase 2 target architecture”。

建议第一遍只读 `00-map.md`，并沿其中的函数打开代码。不要从 `docs/history/` 读起，那里是带日期的验收记录。
