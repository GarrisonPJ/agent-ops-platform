# 阅读路径

给要自己把这个仓库读懂的人。实现任务以 `docs/agents/phase2-tasks.md` 为准，词汇以 `CONTEXT.md` 为准。

| 顺序 | 文档 | 何时有 |
|---|---|---|
| 0 | [仓库地图与一次 Run](00-map.md) | 现在 |
| — | [术语表](glossary.md) | 现在 |
| 1 | [01-run-lifecycle.md](01-run-lifecycle.md) | 当前 H1 split 生产路径与验收边界 |
| 2 | [Environment、最终状态与 MCP 边界](02-environment-and-mcp.md) | 当前 Environment/MCP、final-check 与订单边界 |
| 3 | `03-plug-in-your-agent.md` | H2-1 完成时 |
| 4 | [04-runner-lease-fencing.md](04-runner-lease-fencing.md) | 当前 lease/fencing、capability 与 ACK 清理 |
| 5 | `05-analyzer-and-calibration.md` | H5 完成时 |
| 6 | `06-pass-k.md` | H3 完成时 |
| 7 | `07-otel-mapping.md` | H4 完成时 |

还没写的章节不要找，它们尚未落地。当前生产 Runner 使用独立 Environment/MCP 与注册 Agent；CP 独占评分和终态。`01` 与 `04` 区分已集成代码、host/PostgreSQL、标准镜像/named-volume 与最终 exact-payload 验收；真实 llama.cpp + PostgreSQL trace/分析已补齐，但保留实际模型失败；H1 本地完成不代表发布、hosted CI 或分析器准确率。H2 及后续章节不提前实施。

建议第一遍只读 `00-map.md`，并沿其中的函数打开代码。不要从 `docs/history/` 读起，那里是带日期的验收记录。
