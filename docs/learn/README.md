# 阅读路径

给要自己把这个仓库读懂的人。实现任务以 `docs/agents/phase2-tasks.md` 为准，词汇以 `CONTEXT.md` 为准。

| 顺序 | 文档 | 何时有 |
|---|---|---|
| 0 | [仓库地图与一次 Run](00-map.md) | 现在 |
| — | [术语表](glossary.md) | 现在 |
| 1 | `01-run-lifecycle.md` | H1 完成时 |
| 2 | [02-environment-and-mcp.md](02-environment-and-mcp.md) | H1-2；Runner 接线待 H1-3 |
| 3 | `03-plug-in-your-agent.md` | H2-1 完成时 |
| 4 | `04-runner-lease-fencing.md` | H1 完成时 |
| 5 | `05-analyzer-and-calibration.md` | H5 完成时 |
| 6 | `06-pass-k.md` | H3 完成时 |
| 7 | `07-otel-mapping.md` | H4 完成时 |

还没写的章节不要找，它们尚未落地。当前代码仍是 Phase 1：一个 Python 子进程同时扮演环境与 agent。Phase 2 的目标形状在 `docs/agentops_closed_loop_architecture.md` 的 “Phase 2 target architecture”。

建议第一遍只读 `00-map.md`，并沿其中的函数打开代码。不要从 `docs/history/` 读起，那里是带日期的验收记录。
