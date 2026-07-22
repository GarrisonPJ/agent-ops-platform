# AgentOps Roadmap

本文件描述未来工程计划，是后续里程碑的事实来源。`CONTEXT.md` 负责当前领域语言与不变量，ADR 负责已接受的架构决策；被 Git 忽略的 `.scratch/` 工作区只保存本地实施记录。

## 方向

AgentOps 将持续打磨为可靠的工具型 Agent 闭环评测系统。优先级由正确性、可恢复性、可观测性和安全运行决定。

Recorded Preview 继续用于离线 UI 开发和确定性回归测试。它不是独立产品里程碑或托管部署目标。

## 当前状态

Phase 1 已完成并通过验收：

- Experiment 可以创建确定性的 Baseline 与 Replay。
- FastAPI 负责领域状态、持久化、Lease、评分、分析和 Policy 决策。
- PostgreSQL 持久化 Run、Job、有序事件、Analysis 与 Policy。
- Rust Runner 领取带 Lease 的 Job，监督白名单 Python 进程，重试事件上传，并处理取消与超时。
- 过期 Runner Lease 会在领取时回收，支持 Attempt 隔离、按 Sequence 重启、取消意图保留和有界耗尽。
- React 提供 Experiment、Trace、Analysis、Improve、Replay 及人工激活/拒绝流程。
- Python、TypeScript 与 Rust 共同验证协议 v1 fixtures。
- CI 验证契约、迁移、后端行为、前端适配器、Rust 进程监督、Compose 和真实 Golden 闭环。
- Policy 始终需要人工显式激活。

## 里程碑

| 优先级 | 里程碑 | 状态 | 结果 |
|---|---|---|---|
| P0 | Phase 1.1 — Runner Recovery | 已完成 | Runner 崩溃或失联后，Run 不会永久卡住。 |
| P1 | Phase 1.2 — OpenAI-compatible Provider | 下一项 | 真实模型复用同一套受监督、有类型、可持久化的流程，同时 CI 不依赖外部 API。 |
| P2 | Phase 1.3 — 可观测性与运维加固 | 已规划 | 可以依据持久信号诊断队列、Lease、Runner、Provider 与迁移故障。 |
| Gate | 安全与访问控制 | 条件触发 | 在引入有副作用工具、不可信用户或共享/公网运行前必须完成。 |

## Phase 1.1 — Runner Recovery

ADR-0002 已确定采用同一个逻辑 Run 的确定性重启语义。已接受事件保持不可变；每次重试追加新的 Attempt 标记并继续全局 Sequence，分析只计算最近一次 Attempt 的事件片段。

范围：

- 检测 `claimed`、`running` 和 `cancelling` Job 的过期 Lease。
- 让遗弃任务可被重新领取，同时禁止旧 Lease 继续修改 Run。
- 持久化并递增 Attempt，记录恢复原因。
- 定义最大尝试次数和耗尽后的明确终态。
- 保留已接受事件，维持 Sequence 与幂等不变量。
- Job 恢复期间保留取消意图。
- 增加真实环境故障测试：运行中终止 Runner，启动替代 Runner，并验证最终状态。

实现结果：

- 过期的 claimed、running 和 cancelling Lease 会在下一次认证 claim 时回收。
- 替代 Runner 的 claim 会递增 Attempt、返回下一个事件 Sequence，并隔离旧 Lease。
- 取消意图会跨恢复保留；总共允许三次 Attempt，耗尽后进入有文档定义的 failed 或 cancelled 终态。
- 后端测试覆盖恢复、旧 Lease 隔离、取消和耗尽。
- Compose 故障测试会在运行中终止 Runner，等待 Lease 过期，启动替代 Runner，并验证 Attempt 2 完成。

验收：

- Runner 消失后，没有 Run 永久停留在 `claimed`、`running` 或 `cancelling`。
- 旧 Runner 无法向恢复后的 Run 追加事件或提交完成。
- 恢复不会重复或改写已接受事件。
- 重试耗尽会产生有文档定义的终态错误。
- 恢复路径通过后端、Rust 与 Docker 集成测试。

## Phase 1.2 — OpenAI-compatible Provider

范围：

- 为 Python Agent 建立一个最小 Provider 边界。
- `base_url`、模型和凭证只在服务端配置。
- 支持超时、有界重试、取消传播和结构化 Provider 错误。
- 持久化模型标识、延迟和 Token 使用量，不泄露凭证或隐藏推理。
- 确定性 checkout 场景继续作为默认 CI 与 Golden E2E 路径。
- 使用本地 Fake OpenAI-compatible Server 验证 Provider；真实服务检查保持显式 opt-in。

验收：

- 同一个 Experiment 流程可选择确定性 Fixture 执行或显式配置的 Provider-backed Agent。
- Provider 失败、超时和取消都产生合法的 Run 终态。
- CI 保持确定性且不需要外部 API Key。
- Recorded Preview 继续回放持久化事实，不实现 Provider 逻辑。

## Phase 1.3 — 可观测性与运维加固

范围：

- 为 Experiment、Run、Job、Lease、Attempt、Runner 和 Provider Request 增加结构化关联字段。
- 度量队列深度、领取延迟、Lease 过期/恢复、Run 时长、事件重试、Provider 延迟/Token 和终态分布。
- 分离 API、数据库与 Runner 可用性的 Liveness/Readiness。
- 让迁移、备份与恢复流程可执行、可验证。
- 定义事件与 Provider 元数据的保留和脱敏规则。
- 只有在采集信号证明有必要时，才增加面向运维者的诊断界面。

验收：

- 无需猜测日志，即可串联一次失败 Run 的 API、数据库 Job、Runner Attempt 与 Provider Call。
- 告警可以区分 Runner 宕机、Lease 过期、Provider 故障和数据库/迁移故障。
- 备份恢复和迁移演练具有可重复执行的验证命令。

## 条件安全门

在启用有副作用工具、由用户提供或不可信的外部地址、不可信账户或共享/公网运行前：

- 编写威胁模型 ADR。
- 增加认证、授权、审计记录、Secret 脱敏和资源限制。
- 定义具有明确 Allow、Block、Escalate 语义的 PreToolUse 决策边界。
- 测试 SSRF、命令注入、跨租户访问、取消与预算限制。

这些控制应在相关能力启用前被完整设计，不能用不完整的“安全层”制造虚假安全感。

## 暂缓能力

Kubernetes 执行、Docker Socket、MCP Transport、向量记忆、Training Export、多框架适配、任意代码执行、多租户、计费和 Policy 自动激活，继续留在当前 roadmap 之外，直到可量化需求将其提升。

当暂缓项进入主线时，应更新本文件，在 `.scratch/` 新建或更新 PRD，并通过 ADR 记录新的架构边界。
