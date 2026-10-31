# Phase 1 验证记录（历史摘要）

此文是截至 2026-10-15 的历史，不是当前任务或 CI 基线。2026-10-31 整理时合并重复的 roadmap/handoff 流水账，去掉不可在干净 checkout 使用的本地证据路径；原逐日文本仍可在 Git 历史查看。保留有价值的决策、修复、带日期验证和局限，不把旧待办重新当作需求。

当前入口：[任务清单](../agents/phase2-tasks.md)、[领域](../../CONTEXT.md)、[里程碑](../../ROADMAP.zh-CN.md)。当日详细验收已独立归档为 [system-acceptance-2026-10-15.md](system-acceptance-2026-10-15.md)。

## 已完成里程碑

| 阶段 | 已实现边界 | 决策 |
|---|---|---|
| Phase 1.1 | 同一 logical Run 的确定性重启、Attempt fencing、连续全局 sequence、最多三次 Attempt | ADR-0002 |
| Phase 1.2 | Runner-only OpenAI-compatible Provider 配置、有界重试/超时、受控工具和安全 telemetry | ADR-0003 |
| Phase 1.3 | 诊断/健康、真实备份恢复、Experiment 聚合 retention、脱敏和机器告警分类 | ADR-0004、0005 |
| Phase 1.4 | allowlist、版本化 `scenario_id`、参数限制和 controlled assertions；当时为三个世界 v1 | ADR-0006 |
| 闭环深化 | 确定性分析、Before/After、失败 Run 固化为 RegressionCase、每个候选自己的 replay gate、人工激活 | CONTEXT 不变量 |

H1 后续把三个世界切换为 v2 并拆分 Agent；此处历史 v1 不得升级标记或据此重评分。

## 有日期的验证与修复

- **2026-08-23：** hosted Actions `31922310312` 六 job 成功；多步研究/故障 Provider 复用有界客户端。闭环深化当时本地为 backend 141 passed/1 skipped、frontend 27、Recorded E2E 2；skip 不证明 PostgreSQL。
- **2026-08-31：** `2cd081f` 加固 RegressionCase 创建、Policy replay 抢占和 terminal side-effect 幂等；当时 backend 149 passed/1 skipped。
- **2026-09-18..25：** 取消/心跳/event/complete 的 CAS/row-lock、重复上传汇聚、不同 Policy 激活串行化、Runner broken-pipe 清理、token fail-fast、空 Provider 响应拒绝。独立 PostgreSQL 并发+retention 10 项通过；backend 最后 186 passed/2 skipped 的两个 skip 仍需单独 PostgreSQL 证据。
- **2026-09-24..25：** 校验下载摘要后的 Linux Node 22 在隔离副本通过 format/typecheck、27 单测、build、2 Recorded E2E。Windows npm shim、root-owned Vite cache 和代理误导 loopback fake Provider 曾造成环境失败；不把绕行结果称为标准镜像或新 CI。
- **2026-09-29：** Router 7.18.2 锁定并接入 production audit。只读旧数据副本仅含未注册 legacy 场景，无合格校准样本；没有伪造当前 packet。标准 Web registry 访问仍间歇失败。
- **2026-10-05：** 标准四服务栈曾成功通过 Golden/Runner recovery；最新 registry 重试也可能超时，网络可用性不是保证。hosted Actions `36029244922`（`fbe51c5`）六 job 成功。修复后的五 Run fixture sample 包含完整 recovery 轨迹；agent 标签仅是机制草稿，不是准确率。
- **2026-10-06..12：** packet/labels、source snapshot、Run 元数据、trajectory/event-stream digest、analyzer revision 严格绑定逐步实现；原 packet 不回写。外部 snapshot 来源真实性不由报告证明。分析盲审不等于 outcome-blind/double-blind。
- **2026-10-13：** 三世界 Golden 覆盖补齐；故障 replay 必须三步且 aggregate=1.0，不能只靠正分差。一次 Runner 标准构建阻塞后的源码只读挂载属于 override 证据，不冒充标准镜像成功。
- **2026-10-15：** 零步骤 Provider 故障纳入 execution，analyzer 升 v2；六受控合同覆盖 high/low/unknown context、fake Provider success/timeout/invalid response。旧二元标签批次和语义错误旧样本保留为历史，停止用于 gate。
- **2026-10-15：** unknown-aware 报告只统计 assessed Run/dimension pairs：六 Run/30 events，context known 2/unknown 4、micro known 20/unknown 4。真实 dump 恢复、source/restored manifests 与严格报告一致；状态仍为 `bound`、`provisional_mechanics`、`publishable_accuracy=false`。
- **2026-10-15：** encoded URL 经 ConfigParser 的百分号处理与 cleanup diagnostics 修复。最终完整 backend 265 项无 skip、frontend 34、Recorded E2E 2、Rust fmt/Clippy/workspace 通过。标准镜像无应用源码挂载；Docker cache 允许，不声称 cold-cache。
- **2026-10-15：** 实际 PostgreSQL 取消/complete 与 queued claim 锁顺序统一 Job → Run；命名 SSE 原生监听修复。真实 checkout 浏览器、回归激活门、`after=1` 续接、取消/旧 lease 拒绝、Runner replacement、15 外键备份恢复通过。RegressionCase 卡片可能需刷新是当时非阻断 UI 局限。

## 历史门槛与后续处置

Phase 1 当时要求独立人工标签/仲裁；2026-10-17 的现行政策改为披露自标局限的作者一致性研究，不阻塞 Harness 工程。机制合同永远不能冒充代表性模型准确率；当前规则见[校准流程](../evaluation-calibration.md)。

旧 issue、PRD、UI/架构草稿和根目录 handoff 所述前置执行器、开放 MCP 总线、自动回放/激活、训练导出、托管 demo 等已由 CONTEXT/ROADMAP/ADR 取代；不是“所有旧验收项均已实现”。本隔离树没有可继续执行的本地旧 tracker，也不复制或修改受保护原工作区的本地归档。保留 ADR 原编号，当前任务只在 Phase 2 清单维护。

Phase 1 hosted CI 最后确认 `8bd2ef0` 六 job 绿。该结果不替代当前 H1 的 exact-payload、标准镜像、volume 或真实模型验收。
