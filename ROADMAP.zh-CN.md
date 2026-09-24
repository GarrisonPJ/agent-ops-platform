# AgentOps Roadmap

本文件描述未来工程计划，是后续里程碑的事实来源。`CONTEXT.md` 负责当前领域语言与不变量，ADR 负责已接受的架构决策；被 Git 忽略的 `.scratch/` 工作区只保存本地实施记录。

## 方向

AgentOps 将持续打磨为可靠的工具型 Agent 闭环评测系统。优先级由正确性、可恢复性、可观测性和安全运行决定。

Recorded Preview 继续用于离线 UI 开发和确定性回归测试。它不是独立产品里程碑或托管部署目标。

目标部署范围是单机或可信团队内网。这是工程项目，不是面向公网的 SaaS 产品。因此 Provider 执行继续限定为只读、场景白名单和显式 opt-in；有副作用工具、不可信 Endpoint 与多租户不进入下一里程碑。

## 当前状态

Phase 1 已完成并通过验收：

- Experiment 可以创建确定性的 Baseline 与 Replay。
- FastAPI 负责领域状态、持久化、Lease、评分、分析和 Policy 决策。
- PostgreSQL 持久化 Run、Job、有序事件、Analysis、Policy 与 RegressionCase。
- Rust Runner 领取带 Lease 的 Job，监督白名单 Python 进程，重试事件上传，并处理取消与超时。
- 过期 Runner Lease 会在领取时回收，支持 Attempt 隔离、按 Sequence 重启、取消意图保留和有界耗尽。
- React 提供 Experiment、Trace、Analysis、Improve、Replay 及人工激活/拒绝流程。
- Python、TypeScript 与 Rust 共同验证协议 v1 fixtures。
- CI 验证契约、迁移、数据库恢复、后端行为、前端适配器、Rust 进程监督、Compose 和真实 Golden 闭环。
- 持久化 Run 诊断已经关联队列、Lease、Attempt、Runner、Provider、重试、耗时与终态信号。
- 数据库 Readiness 会验证 Alembic Head，可执行临时库备份恢复演练，持久化 Runner 可用性保持独立可观测。
- 机器可读运维状态（`GET /api/operations/state`）、有界 Operations 聚合、Retention/脱敏执行与进程外健康探测已在 CI 中验证。
- 三个版本化内置 Scenario 均支持确定性 Fixture 和窄边界、场景专属的 Provider 执行。
- 确定性失败归类、持久化 Before/After 对比与回归用例重放已接入人工 Policy 门禁。
- Policy 始终需要人工显式激活。

## 里程碑

| 优先级 | 里程碑 | 状态 | 结果 |
|---|---|---|---|
| P0 | Phase 1.1 — Runner Recovery | 已完成 | Runner 崩溃或失联后，Run 不会永久卡住。 |
| P1 | Phase 1.2 — OpenAI-compatible Provider | 已完成 | 真实模型复用同一套受监督、有类型、可持久化的流程，同时 CI 不依赖外部 API。 |
| P2 | Phase 1.3 — 可观测性与运维加固 | 已完成 | 可以依据持久信号诊断队列、Lease、Runner、Provider 与迁移故障。 |
| P1 | Phase 1.4 — 场景接入 | 已完成 | 操作者从注册内置场景中选择；平台不再局限于一个硬编码 Demo。 |
| P1 | 闭环深化后续 | 已完成 | 失败 Run 成为可归类、可对比并在激活前门禁的回归资产。 |
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

截至 2026-08-15 的实现进度：

- **1.3A — 持久诊断：已实现。** 单 Run 诊断暴露 Run、Job、当前 Lease/Runner、Attempt、Provider、耗时、重试、恢复和终态投影；Operations Overview 汇总队列深度、状态分布、过期 Lease、恢复次数与事件重试。
- **1.3B — 健康与可用性：已实现。** API Liveness、数据库 Readiness 和持久化 Runner Availability 已拆分，并由认证 Runner Presence 驱动。Alembic `0004`、`0005` 与 Compose Readiness 探针已通过 CI 和真实环境验证。
- **1.3C — 诊断正确性与安全关联：已实现。** 不可变的过期 Attempt 历史会保留最终 Lease/Runner 关联，Recovery 总数包含耗尽动作。Provider Telemetry 与 Error 在入库时执行允许列表，Request ID 转为 SHA-256 指纹，`0006` 迁移会净化历史记录。
- **1.3D — 迁移与数据恢复：已实现。** Readiness 会对比实时 `alembic_version` 与应用 Head。仅从环境读取凭据的备份及临时库恢复命令使用导出快照，并验证 Revision、所有 public 表行数、已验证外键与有序 Run Trace；独立 PostgreSQL 16 CI Job 会执行带种子的恢复演练。
- **1.3E — 保留与脱敏：已实现。** 以 Experiment 聚合为原子 Retention Unit；Plan 生成 SHA-256 digest，Execute 绑定 Plan 文件并在 PostgreSQL 锁后复核；`durable_events.py` 统一 Provider/Completion 入库边界，`TerminalFailureKind` 取代自由文本 error；`database-recovery` CI Job 覆盖真实 PostgreSQL 16 保留锁、stale-plan、回滚与备份恢复演练。提交 `80e03e0` 已 fast-forward 到 `main`。
- **1.3F — 告警分类与收尾：已实现。** `GET /api/operations/state` 暴露带优先级的机器可读运维状态（数据库、Schema、Runner、Lease、Provider 故障）；Provider Outage 与 Rate Limit 为独立状态；Operations Overview 与 State 评估使用有界 SQL 聚合；`scripts/health_probe.py` 与 `docs/operations/fault-matrix.md` 提供可重复的进程外探测。提交 `98c903d` 与 `b8f4b8c` 已在 `main`。

Phase 1.3 收尾（2026-08-15）：

- **状态：已完成。** `main` 上的 CI 覆盖 backend `phase1_tests`（含 operational state）、迁移往返、PostgreSQL 备份恢复与 Retention 集成、前端 typecheck/测试/构建/Recorded E2E、Rust fmt/clippy/test、Compose 配置校验、Golden 闭环与 Runner Recovery 演练。
- **证据：** `.github/workflows/ci.yml` 的 `backend`、`database-recovery`、`frontend`、`rust`、`compose`、`golden-e2e` Job；`docs/operations/` Runbook；ADR-0005。

执行计划：

1. **1.3C — 诊断正确性与安全关联：已完成。** 不可变 Attempt 历史、包含耗尽动作的 Recovery 计数、强类型 Provider Error、Request 指纹、历史数据净化，以及恶意元数据/跨版本重试测试均已实现。
2. **1.3D — 迁移与数据恢复（P0）：已完成。** Readiness 已对比实时 `alembic_version` 与应用 Head；可执行的 `pg_dump` 备份和临时数据库恢复演练会验证 Schema Revision、public 表行数、已验证外键与恢复后的 Run Trace，并在独立 PostgreSQL 16 CI Job 中运行。
3. **1.3E — 保留与脱敏（P1）：已完成。** Experiment 聚合 Retention、Plan-file Execute、PostgreSQL 锁后复核、Provider/Completion 脱敏与真实 PostgreSQL 16 集成测试均已实现；Control Plane 运维边界记录在 ADR-0004。
4. **1.3F — 告警分类与收尾（P1）：已完成。** 机器可读运维状态、有界 Operations 查询、Provider Outage 与 Rate Limit 分类、故障矩阵与进程外 API probe 均已实现。
5. **Phase 1.3 收尾：已完成。** 全套 CI 矩阵与运维 Runbook 已就位；下列验收项均有证据。

验收：

- 无需猜测日志，即可串联一次失败 Run 的 API、数据库 Job、Runner Attempt 与 Provider Call。
- 告警可以区分 Runner 宕机、Lease 过期、Provider 故障和数据库/迁移故障。
- 备份恢复和迁移演练具有可重复执行的验证命令。

验收状态：

- 失败 Run 关联：**已完成** — 当前与过期 Attempt（包括耗尽动作）均保留 Lease/Runner 身份和安全 Provider 指纹。
- 故障分类：**已完成** — `/api/operations/state`、健康探测、Schema Drift Readiness、Runner 可用性、Lease 过期，以及独立的 Provider Outage 与 Rate Limit 状态均已实现，并配有可重复故障矩阵。
- 备份与迁移演练：**已完成** — 迁移往返与独立 PostgreSQL 16 备份恢复演练均有可重复的 CI 和本地命令。
- 保留与脱敏：**已完成** — Experiment 聚合 Retention、Plan-file Execute、PostgreSQL 锁后复核、Provider/Completion 脱敏与真实 PostgreSQL 16 集成测试均已实现。

目前没有定义 Phase 1.5。Phase 1.4 及其闭环深化后续均已完成，下一里程碑应依据实际运维数据选择，而非推测性功能。**Deferred until justified** 中的项只有在出现具体需求时才提升优先级。如果开始共享/公网使用、接入不可信 Endpoint/账户或启用有副作用工具，则条件安全门优先。

## Phase 1.4 — 场景接入

PRD：`.scratch/phase1.4-scenario-onboarding/PRD.md`（本地跟踪器）。架构边界：[ADR-0006](docs/adr/0006-scenario-registry-boundary.md)。

CI 收口（2026-08-23）：

- **状态：已完成。** GitHub Actions run `31922310312` 的六个 Job 全部成功：`backend`、`database-recovery`、`frontend`、`rust`、`compose`、`golden-e2e`。
- **Provider 后续：已完成。** `multi-step-research.v1` 与 `api-fault-orchestration.v1` 已复用共享的有界 Provider Client，并且只暴露各自场景专属工具（`340ca9a`、`bddbbdd`）。Fixture 仍是确定性 CI 默认路径。

范围：

- 定对外名称（改名或加限定词），避免与 AgentOps-AI 撞车。
- 协议 v1 中 `scenario_id` 采用 `<name>.v<N>`；同一 id 才可跨 Run 比分；语义变更必须升版本。`schema_version` 保持 1。
- 保留 EvaluationSpec 上可选、有界的 `scenario_params`。
- 注册场景用受控断言词汇判分（`tool-used`、`tool-args-match`、`tool-sequence`、`step-count` 以及结果匹配），带 weight、threshold 与明确组合语义。
- 在创建 Run 时校验场景选择；未注册 ID 返回结构化错误。
- 暴露 `GET /api/scenarios`，并在新建 Experiment 页面提供场景选择器。
- 交付两个额外内置场景（fixture CI + provider opt-in），至少一个走完整 Golden 闭环。
- 记录场景术语、版本化身份、断言词汇与贡献者指南。

验收：

- 注册表与断言重构后 Golden checkout 闭环仍可通过。
- 至少一个额外场景在 CI 中无需外部 API 即可完成完整闭环（`golden_e2e.py` 默认跑 checkout 与 multi-step-research）。
- README、CONTEXT 与 ADR-0006 描述注册表、版本化身份与断言语义；CI 具备全部验收证据后，ROADMAP 将本里程碑标为 **已完成**。

## 闭环深化后续

PRD：`.scratch/closed-loop-depth/PRD.md`（本地跟踪器）。

收口（2026-08-23）：

- **状态：已完成。** 确定性失败分类与指标方向（`ca33d93`）、持久化 Before/After 对比及回归优先高亮（`4847e6e`）、失败 Run 回归固化与激活前重放门禁（`98d387e`）均已进入 `main`。
- Fixture 失败可将 Scenario、Seed 与参数保存为确定性回归资产。Provider 失败会记录明确的不可完全复现边界，不进入自动回放门禁。
- Regression Replay 复用正常 Run 流程、进入 Improve 报告，且不会自动激活 Policy；操作者审查结果后必须再次显式激活。
- 本地收口通过 backend `141 passed, 1 skipped`（PostgreSQL Retention 集成需要 `RETENTION_POSTGRES_URL`）、frontend `27 passed`、TypeScript、生产构建、协议检查、Rust 格式检查，以及 Windows Chromium Recorded E2E `2 passed`。

并发加固跟进（2026-08-31）：

- **状态：已完成。** 当前 backend 全量测试为 `149 passed, 1 skipped`，并发幂等聚焦覆盖通过 `18` 项测试。
- 变更已进入 `main` 的 `2cd081f`：RegressionCase 创建、Policy Replay 抢占和终态完成副作用在竞争下保持数据库级安全与幂等。

## 下一里程碑：运行可靠性与评估准确率

目标是单机或可信内网使用。优先级为先保证运行可靠性，再提高评估准确率；吞吐量和多用户协作明确后置。

### P0：收口状态机正确性

- 让取消、心跳、事件持久化等所有 Run 状态写入都使用 CAS 或行锁。
- 在并发数据库连接下保证重复事件真正幂等；同一序号携带不同 payload 时返回明确冲突。
- 让 Policy 激活、拒绝和 RegressionCase Replay 抢占原子化，证明竞争请求不会留下孤儿 Run/RunnerJob。
- Runner 写入 Agent stdin 或关闭失败时清理整个进程组，并增加 broken-pipe 回归测试。
- 除显式 development 模式外，缺少 `RUNNER_TOKEN` 必须拒绝启动。

验收：PostgreSQL 多连接竞争测试通过；终态 Run 不可被覆盖；重复上传请求汇聚；没有孤儿 Replay 记录；Runner 所有失败路径都回收子进程。

验证更新（2026-09-18）：

- 已为取消、心跳和事件持久化增加 Run 状态 CAS/重读；规范化后的并发重复事件会汇聚，序列冲突仍返回明确错误。
- 已为 RegressionCase Replay 抢占及 Policy 激活/拒绝增加条件 CAS；激活竞争失败的事务会先回滚其 Run 和 RunnerJob，再重读胜者状态。
- Runner stdin 写入或关闭失败时现在会终止 Agent 进程组；broken-pipe 回归测试已通过。
- 缺少 `RUNNER_TOKEN` 时，除 `AGENTOPS_ENV=development` 外均会启动失败；空或结构非法的 Provider 消息会产生 `PROVIDER_INVALID_RESPONSE`。
- 后端验证通过 `161 passed, 1 skipped`；Rust workspace 验证通过，Runner 含 15 个测试。PostgreSQL 多连接专用证据和 frontend 格式化仍受环境限制，作为后续验收项保留。

验证更新（2026-09-19）：

- 后端全量验证通过 `177 passed, 2 skipped`；评估校准与标注 fixture 聚焦测试通过 `27` 项，并包含激活 claim 重试幂等及按 Policy 隔离 RegressionCase Replay 的回归覆盖。
- 临时 PostgreSQL 16 容器中的 4 个状态转换竞争测试通过；包含 Retention 的 PostgreSQL 专项运行合计 `10 passed`。另一次未提供 `RETENTION_POSTGRES_URL` 的本地运行是显式 skip，不应记为本地 Retention 已通过。
- Rust workspace 全部通过，包含 15 个 Runner 测试；Rust 格式检查（`cargo fmt --all -- --check`）和 `git diff --check` 通过。
- 已新增[运行可靠性与本地验证 runbook](docs/operations/reliability-and-local-validation.md)，覆盖 Token 轮换、竞争诊断、Provider 故障解释以及 WSL/代理边界。
- 带 Lease 的 heartbeat、事件持久化和完成操作现在会锁定 RunnerJob 行来隔离旧 Lease；事件上传保留非锁定初读以实现重复汇聚，再在插入前加锁。不同 Policy 的激活会锁定 Experiment 串行化，某个 Policy 的终态 Replay 不会被另一个 Policy 当作回归结果复用。
- Compose 现在要求显式提供 `RUNNER_TOKEN`，API 启动检查和认证使用同一份配置快照；已删除可预测的 `local-runner-token` fallback。
- 当前 WSL 缺少可用的 Linux Node/npm 工具链，frontend 本地验证仍受阻；不宣称本地 frontend 或 frontend 格式化已通过，CI 仍是 frontend 验证基线。
- 当前已为覆盖到的状态转换补齐 PostgreSQL 证据。现有合成 calibration fixture 只能作为回归保护，不能证明评估准确率；独立标注和 precision/recall 报告仍未完成，本地 frontend 可复现性也仍未闭合。

验证更新（2026-09-24）：

- 三个内置 fixture 场景的 baseline/replay 均已通过 Runner API 完成持久化 `RunEvent` 往返测试，覆盖事件顺序、重建后的观测、已保存的分析维度与分数。
- 后端全量验证通过 `180 passed, 2 skipped`，校准聚焦测试通过 `16` 项。新增往返测试使用测试 SQLite 数据库；上方 PostgreSQL 并发证据仍属于 2026-09-19 的验证。
- fixture 标签仍是合成数据。发布评估准确率结论之前，还需独立标注样本及 precision/recall/F1、混淆矩阵报告。

### P1：提高评估质量和运维信心

- Provider 返回空响应或结构非法时必须失败，不能当作成功。
- 持久化 `RunEvent` 往返测试：三个内置 fixture 场景的 baseline 和 replay 已覆盖。
- 将合成校准 smoke set 替换为独立标注样本，报告 precision/recall/F1 与混淆矩阵。
- 稳定 WSL 与代理环境下的本地验证，明确受支持的 Node 工具链。
- 增加 Token 轮换、竞争故障诊断和 Provider 故障解释 runbook。

前端验证更新（2026-09-24）：

- 在 WSL 中使用已核对 SHA256 的 Linux Node `v22.23.3`，`npm ci`、类型检查、27 个单测和生产构建均通过；[本地验证 runbook](docs/operations/reliability-and-local-validation.md) 已记录复现步骤。
- `npm run format:check` 在 24 个此前未修改的前端文件上失败，当前 frontend CI Job 也未执行这项检查。本地前端运行检查已可复现，但格式检查和完整的本地 Golden/Compose 矩阵仍未闭合。

校准报告更新（2026-09-24）：

- 已增加面向单独标注的终态 Run 的只读[评估校准报告工具](docs/evaluation-calibration.md)。工具从持久化 `RunEvent` 重建轨迹，输出逐维及总体的混淆矩阵计数、precision、recall、F1 和分歧 Run。
- 后端全量验证为 `184 passed, 2 skipped`；4 项聚焦测试覆盖计数、标签校验、持久化事件和无效 Run 状态。独立标注集尚未建立，因此仍没有可发布的评估准确率结果。

本地 Compose 验证更新（2026-09-24）：

- 隔离的 PostgreSQL 16、API 与当前源码 Rust Runner 栈通过两个注册 Golden 场景：均持久化失败基线轨迹、完成成功 Replay 并激活通过验证的 Policy。`scripts/recovery_e2e.py` 终止运行中的 Runner 后，验证 Lease 恢复使 Attempt 2 成功完成。
- 标准四服务 Compose 构建因 Docker Hub 超时，无法获取 Web 的 `node:22-alpine` 镜像元数据。三服务验证使用在 `rust:1.96-slim` 中离线编译的 Runner 二进制及当前 Python Agent 源码构建镜像。隔离项目及其卷已清理；Web 容器与 Recorded 浏览器 E2E 仍未完成本地验证。

P1 后续验证（2026-09-24）：

- CI 的 Compose 校验及 Golden/Recovery Job 现在使用临时随机 `RUNNER_TOKEN`；本地配置校验在提供 Token 时通过，缺少 Token 时仍会拒绝。使用 `make fmt` 整理 24 个前端文件后，frontend CI Job 已加入格式检查。
- Linux Node 22 下，前端格式、类型检查、27 个单测、构建及 2 个 Recorded 浏览器 E2E 均通过。临时本地 Node Web 覆盖启动了隔离的四服务 Compose 栈，Web 及其 API 代理可用，两个 Golden 场景和 Runner 恢复演练再次通过。Docker 守护进程仍无法拉取标准 Web Dockerfile 所需的 `node:22-alpine`；覆盖仅用于本地验证。
- 新增分析盲审[校准样本导出工具](docs/evaluation-calibration.md)，按场景、模式、Run 类型及终态选取当前已注册的终态 Run，且不输出分数或已保存的分析。四条 Golden fixture 样本只验证流程，不能证明准确率。现有 Phase 1 数据卷的只读副本仅有四条旧版未注册场景的 fixture Run，因此尚无独立标注集或可发布的 precision/recall/F1。后端全量验证为 `186 passed, 2 skipped`。

Review 更新（2026-09-25）：

- 独立复核通过后端全量测试（`186 passed, 2 skipped`）、PostgreSQL 16 并发与 retention 专项（`10 passed`）、Rust workspace（Runner 15 项测试）、前端格式/类型检查/27 项单测/构建，以及 2 项 Recorded 浏览器 E2E。Compose 在缺少 `RUNNER_TOKEN` 时拒绝，在提供临时 Token 时通过。前端检查在隔离副本中运行，因为工作树现有 `node_modules/.vite` 文件由 `root` 拥有；依赖安装没有改动源码树。
- 校准导出工具属于“分析盲审”，不是“结果盲审”：packet 仍包含 Scenario、执行模式、Run 类型、终态、Provider 错误存在性、限制、耗时和 Token 数等线索。独立标注、分歧仲裁、样本快照与评审者一致性记录完成前，不得发布准确率；baseline/replay 相关性和没有置信区间仍是方法学限制。
- `npm audit --omit=dev` 报告生产依赖 `react-router-dom`/`react-router` 7.18.0 存在两个 high 漏洞。任何可信内网部署前必须升级到已修复的兼容版本、重生成 lockfile 并重跑前端检查；这项工作列为 P1。
- 标准 Web Dockerfile 仍没有本地构建证据，因为 Docker 守护进程无法获取 `node:22-alpine`；临时 Web override 只能作为冒烟验证证据。

后续顺序：

1. 升级 React Router 生产依赖，重新执行 `npm ci`、audit、格式、类型、单测、构建和 Recorded E2E。
2. 为当前注册 Scenario 收集带数据库快照的样本，完成独立标注和分歧仲裁，再运行校准报告；在此门槛前不发布 precision/recall/F1。
3. Docker Hub 可用后构建标准 Web 镜像，并在不使用 override 的情况下重跑隔离四服务 Golden 与 Runner 恢复演练。
4. 重新 triage `.scratch/` 规划和工单，记录每个文件的替代路径与标签后，只删除确认已被 ROADMAP/CONTEXT 取代的文件；保留带日期 handoff、`.codegraph/` 和 `.cursor/`。

验收：每个终态都有确定性证据；分析器结果使用独立标注并发布指标；后端、前端、Rust 与 Golden 检查在本地和 CI 可复现。

### P2：明确清理项与延后项

- 过时的规划/分析文档移出 `docs/`，下一次仓库清理前仅保留在 `.scratch/archive/`；清理时只删除已确认被 ROADMAP/CONTEXT 取代的文档，保留带日期 handoff 以及本地 `.codegraph/`、`.cursor/` 工具目录。
- 重新 triage 过期 `.scratch/` 工单，将已取代内容标记为 `wontfix` 或 `ready-for-human`；只有记录标签和替代路径后才删除旧 issue 文件。
- 首批清理候选是旧版 `.scratch/agentops-platform-v0.1/`、`.scratch/agentops-platform-v0.3/`、`baseline-hardening/`、`closed-loop-architecture/`、`demo-packaging/` 和 `frontend-polish/` 跟踪目录。先检查引用和历史证据，再按单个文件完成 triage 后删除；不得整目录盲删。
- 在任何可信内网部署前，将 Compose 中用于演示的 PostgreSQL 凭据替换为显式 Secret 来源；演示默认值只能留在明确标注的本地 profile 中。
- MCP、向量记忆、有副作用工具、多租户和吞吐量优化，只有在出现可度量需求并通过新 ADR 后才提升优先级。

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
