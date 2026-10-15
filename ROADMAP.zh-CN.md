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

历史跟踪器：`.scratch/archive/completed-trackers-20261005/phase1.4-scenario-onboarding/PRD.md`。架构边界：[ADR-0006](docs/adr/0006-scenario-registry-boundary.md)。

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
- 至少一个额外场景在 CI 中无需外部 API 即可完成完整闭环（`golden_e2e.py` 默认跑 checkout、multi-step-research 与 API fault orchestration）。
- README、CONTEXT 与 ADR-0006 描述注册表、版本化身份与断言语义；CI 具备全部验收证据后，ROADMAP 将本里程碑标为 **已完成**。

## 闭环深化后续

历史跟踪器：`.scratch/archive/completed-trackers-20261005/closed-loop-depth/PRD.md`。

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

依赖与 CI 收口（2026-09-29）：

- 将 `react-router-dom` 从 `^7.0.0` 升级到最小兼容修复版本 `^7.18.2`；lockfile 现在将 `react-router-dom` 及其 `react-router` 依赖都固定为 `7.18.2`。OSV 将 `7.18.2` 标为此前高危 RSC CSRF 通告（影响 `7.12.0` 至 `7.18.1`）的修复版本。
- 在 frontend CI 的 `npm ci` 之后新增 `npm audit --omit=dev`，并保持其后已有的格式、类型检查、测试、构建和 Recorded E2E 门禁不变。
- 当前 WSL 仍将 `npm` 解析为 Windows shim，且没有 Linux Node 运行时，因此没有宣称本地 `npm ci`、audit 或前端矩阵重跑通过。已直接核对 lock/package 元数据和 CI wiring；完整前端矩阵应在 CI 或 Linux Node 22 环境中重跑。
- 标准构建命令 `docker build --pull -f frontend/Dockerfile -t agentops-web-standard-verify frontend` 仍在构建步骤之前解析 `docker.io/library/node:22-alpine` 时失败（`dial tcp 173.236.212.42:443: i/o timeout`）。随后 Docker Hub 间歇恢复时曾成功执行标准构建（见 2026-10-05 证据），但最新重跑再次遇到 registry 超时；因此只能确认有成功构建证据，不能称为本机可重复恢复。

校准样本与 Docker 后续核查（2026-09-29）：

- 以只读方式复制并检查了 `agentops_phase1-pgdata`，Alembic revision 为 `0005_runner_presence`。其中有 33 条 `RunEvent`，仅包含旧版未注册 `checkout-api-latency` Scenario 的 4 个终态 Run（3 个 failed baseline、1 个 succeeded replay）；当前注册的版本化 Scenario 样本数为 0。
- 因没有合格的当前样本，未生成 review packet、labels、校准报告或准确率指标。这保持了独立标注门槛；校准聚焦套件通过 `22 passed`（其中 review/report 子集为 `6 passed`）。
- 当前源码 API 镜像使用缓存依赖构建成功；当前源码 Runner 构建因 crates.io index 网络不可用而停止。临时快照容器、卷、网络及验证镜像已清理，原有数据卷和本地工具均保留。

代理恢复后的验证与样本采集（2026-10-05）：

- Docker Hub 访问已间歇性恢复。标准 Web 镜像从 `frontend/Dockerfile` 构建成功，基础镜像 digest 为 `sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402`；容器启动并提供页面。由于本机 buildx 引用目录只读，隔离 Compose 构建使用了 `DOCKER_BUILDKIT=0`；这是主机 builder workaround，不是源码或 Dockerfile 修改。
- 针对标准 Web 镜像的 Node 22 隔离验证成功执行 `npm ci`、`npm audit --omit=dev`（`found 0 vulnerabilities`）、格式检查、类型检查、27 个单测、生产构建及 2 个 Recorded 浏览器 E2E。完整依赖安装仍报告 dev-only audit 项，但生产依赖 CI 门禁干净。
- 隔离的标准四服务 Compose 构建成功构建 API、Web 和 Rust Runner，并通过两个 `scripts/golden_e2e.py` 场景（失败 baseline、成功 replay、Policy 激活）及 `scripts/recovery_e2e.py`（kill 前为 `running`，替代 Runner 后为 `succeeded`）。临时项目、卷、网络和容器已清理。
- 已记录该栈的新数据库快照：`sha256:dbd3734309ff439e6bcee38b133774e58470bf23b3abcdf4d9db2ad391f72427`。analysis-blind packet 保留在被忽略的 `.scratch/evaluation-calibration/review-packet-20261005.json`，含 5 个合格终态 Run：checkout（2 baseline、1 replay）和 multi-step research（1 baseline、1 replay），不含分数或已保存分析。独立标注、仲裁和准确率报告仍待完成。
- 当前已生成被忽略的 demo 临时标签草稿 `.scratch/evaluation-calibration/demo-temp-labels-20261005.json`：两个重复 6 步且失败的 baseline 暂标为 `planning` + `budget`，三个成功/恢复 Run 暂为空集。Recovery harness 现在使用 checkout Scenario 的默认 task，并在替代 Runner 后发出其成功工具序列，因此没有将基于断言的 `planning` 预测机械复制进草稿。该草稿明确是 agent 生成、analysis-blind 但暴露终态的 demo 数据，不能作为独立标注或可发布指标；人工必须修改或替换，并记录评审者、provenance 与仲裁后才能使用指标。
- 2026-10-05 packet 早于 recovery 合约修复，继续作为历史 demo 样本保留。下方修复版 packet 是当前 demo 样本；只有采集新的标准 Compose 样本时才需重新导出。不得静默复用旧临时标签作为修复后的准确率证据。
- 修复后的隔离标准栈产生 logical snapshot digest `sha256:2ad97aa9c22112dd55b2d55e669d605478facf260b3e138f6ea3853b0131f30c`，新 packet 为 `.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`。其中 5 个终态 Run 的 recovery baseline 已包含完整三步 checkout 轨迹，analyzer 维度为空。对应 demo 草稿为 `.scratch/evaluation-calibration/demo-temp-labels-20261005-recovery-fixed.json`，仍是 agent 生成，不能作为准确率证据。
- hosted GitHub Actions run `36029244922`（`fbe51c5`）已成功完成 `backend`、`database-recovery`、`frontend`、`rust`、`compose` 和 `golden-e2e` 六个 Job，依赖/CI 批次的 hosted 验证已闭合；本地 classic-builder workaround 仍是环境特例。

校准 mechanics 后续核查（2026-10-06）：

- 已核对修复版 packet 与对应草稿标签，5 个 Run ID 全部匹配。在 Alembic head `0008_regression_cases` 的全新临时 PostgreSQL 数据库中重建 packet 的 `Experiment`、`Run` 和 `RunEvent` 事实后，`app.calibration_report` 成功运行，5 个 case 没有计算出的分歧。
- mechanics 输出保存在被忽略的 `.scratch/evaluation-calibration/demo-calibration-report-20261006.json`；它显示 planning/budget 与 micro 的表面指标为 `1.0`，execution/context 因没有正例而为 `null`。这些是与 agent-generated labels 循环一致的 mechanics 结果，不是可发布准确率。
- reviewer/provenance/仲裁信息记录在被忽略的 `.scratch/evaluation-calibration/review-record-20261006-demo.json`：评审者明确为 agent-generated provisional，独立人工复核仍待完成，尚无仲裁，`publishable_accuracy=false`。报告完成后已删除临时数据库。
- P1 calibration binding 已在 `backend/app/calibration_report.py` 实现：绑定模式要求 packet 文件 digest、labels 文件 digest、source snapshot、完整 Run ID、scenario/mode/kind/task、终态、数据库 Experiment/Run 身份、由 `calibration_review.py` 导出的 canonical trajectory digest、完整事件流 digest，以及源码显式维护的 analyzer revision `failure-analyzer.v1`。外部 snapshot 真实性仍在 packet contract 之外。
- 固定的 5-case fixture packet 已通过临时内部 mechanics gate；agent 按文档规则逐条复核标签，没有发现需要修正的 case。该 gate 允许继续开发 analyzer、trajectory、Scenario、recovery、exporter、schema 和 report mechanics，但独立人工准确率 gate 仍为 pending。旁路记录在被忽略的 `.scratch/evaluation-calibration/provisional-internal-gate-20261006.json`。
- 校准聚焦验证（`-k calibration`）为 `31 passed, 2 skipped`；后端全套为 `199 passed, 2 skipped`。历史 v1 sidecar 继续保留；旧严格绑定派生 packet 仍可作为历史数据解析，但早于 analyzer revision 字段，新的严格报告前必须用当前 exporter 重新导出。agent-generated review 仍不可发布。

Provider 与 binding 加固后续（2026-10-07）：

- Provider 现在会拒绝不符合注册 Scenario 合约的模型 tool arguments，并把模型实际选择的 arguments 写入 `step_completed`；checkout 工具 schema 也明确声明了必需参数，不再静默接受 `{}`。
- durable event normalization 现在保留 fault-orchestration Scenario 使用的有界整数 `attempts`。Provider 与 durable-event 聚焦测试为 `28 passed`，后端全套保持 `198 passed, 2 skipped`。
- 历史 packet 保留在 `.scratch/evaluation-calibration/review-packet-20261005-recovery-fixed.json`。严格绑定派生 packet `.scratch/evaluation-calibration/review-packet-20261007-recovery-fixed-bound.json` 与 sidecar `.scratch/evaluation-calibration/review-record-v1-20261007-provisional-bound.json` 早于 analyzer revision 字段；其 digest 继续作为历史证据保留，但新的严格报告前必须导出新 packet，并单独准备匹配的 labels 与 sidecar。它们仍不可发布，也不代表准确率成立。
- recovery E2E harness 现在会等待明确的 `running` 后再注入故障，并校验连续 SSE sequence、Attempt 1/2、Attempt 2 的完整三步轨迹和 Attempt 2 完成事件。本轮只完成单元验证，没有把新的真实 Compose kill/recreate 运行写成已验证证据。

校准事件流保真后续（2026-10-12）：

- 每个 analysis-blind packet case 现在包含 `event_stream_sha256`，它基于每条持久化 `RunEvent` 的 sequence、event type、规范化 UTC 时间戳和 payload 计算 canonical digest。packet 只保存摘要，不写入原始 event payload。
- 严格 `calibration_report --require-binding` 现在拒绝缺失或格式错误的事件流摘要，并从当前数据库重建摘要后再报告。即使 sanitized trajectory 投影不变，event payload、event type、时间戳或 sequence 的变化也会被检测。
- 严格 binding 现在要求 packet 与 sidecar 的 `source_snapshot` 使用 canonical `sha256:<64hex>` 格式。这可以捕获格式错误或含义不明确的标识，同时保留历史 packet 的解析兼容；它不能证明外部备份或数据库快照确实产生了该 digest。
- 历史 packet JSON 仍可解析检查；严格 binding 要求新字段及当前 packet/sidecar 身份。packet 仍是 analysis-blind，终态暴露边界不变。

Golden 覆盖与校准兼容性后续（2026-10-13）：

- canonical snapshot 格式仅在 `--require-binding` 下强制校验；匹配的历史字符串标识仍可用于非严格 mechanics 报告。回归测试基于持久化 Run 验证两条路径。
- `golden_e2e.py` 新增 `api-fault-orchestration.v1`：fixture baseline 六次调用返回 503 后耗尽预算；replay 执行 `retry_call → degrade_route → invoke_service`，满足场景断言且评分提升。CI 现有默认调用会自动覆盖全部三个场景。
- 故障场景 Golden 现在在激活前要求 baseline 六步、replay 三步且断言 aggregate 为 `1.0`。单纯正分差可能接受不完整 replay；回归用例覆盖该反例并确认不会调用激活接口。清除回环代理后的后端验证通过 `208 passed, 2 skipped`。
- [校准流程](docs/evaluation-calibration.md) 改为从保留的真实备份计算 digest，并使用匹配的新 packet/labels/sidecar 文件名。exporter 只生成 packet，临时 reviewer 元数据和 labels 需单独准备。
- 隔离四服务栈通过全部三个 Golden 场景、Runner kill/replacement recovery，以及增强断言后的故障场景复跑。API 和 Web 使用标准 Dockerfile 与缓存层；标准 Runner 构建停在依赖安装，功能验收改用缓存 Runner 镜像并只读挂载当前 Python 源码，其 Rust binary SHA-256 与当前构建阶段一致。这是明确的 override 运行证据，不代表标准 Runner 构建完成或新的 hosted CI 结果。
- 冻结后的数据库生成了保留的真实逻辑备份及新版九个 Run、67 条事件的 packet。严格报告为 `bound`、`provisional_mechanics`、`publishable_accuracy=false`；reviewer 信息记录新编写的 agent 标签、逐 case 证据/不确定性及待仲裁状态。样本含重复/相关 fixture 对，且没有 context 阳性。证据索引位于被忽略的 `.scratch/evaluation-calibration/20261013-compose-refresh-110352Z/README.md`。本轮容器/网络已清理，新旧数据卷均保留。

受控覆盖与本地工程收口（2026-10-15）：

- 修复 Provider 在零工具步骤时发生错误或超时的漏判：已有执行证据现在同时投影到 `execution` 维度和 dominant。语义 revision 升至 `failure-analyzer.v2`；旧 v1 packet 保留，但不再满足当前严格绑定。
- 新增 `backend/scripts/controlled_calibration_capture.py`，预先固定 context 高压力、低压力、测量未知及 Fake Provider 成功、超时、无效响应六个合同。通过认证 ASGI Runner API 持久化事件，生成真实备份、packet、单独保存的合同标签、review sidecar、严格报告与摘要；合同不一致时保留产物并失败。它不覆盖 Rust supervision 或外部模型。
- 早先二元标签批次在临时库采集六个 Run、30 条事件，严格报告为 `bound`、`provisional_mechanics`、`publishable_accuracy=false`，合同无分歧。`.scratch/roadmap-closeout-20261015/controlled-capture-20261015/` 原样保留为历史机制证据：该报告把未测量 context 计为阴性，不能证明当前 unknown 评估覆盖率。2026-10-13 旧受控采集对 Provider 故障使用空标签，存在语义缺陷，保留原件但停止用于 gate。
- 标准 Runner Dockerfile 已锁定 Python 依赖闭包，使用 Cargo locked build，并在应用修改时保留依赖缓存。完成标准构建及最终源码刷新后，隔离标准四服务栈通过三个 Golden 场景与 Runner 恢复，没有源码挂载，Python 源码摘要与该次验收记录的源码快照一致。基础镜像和 API/Web 层使用 Docker 缓存；这是本地证据，不是新的 hosted CI。证据：`.scratch/evaluation-calibration/20261015-standard-runner-closeout/final/`。
- 此前收口时后端全套为 `225 passed`，包含独立 PostgreSQL 测试，无跳过；协议 schema 导出检查通过。Linux Node 22 下锁文件安装、生产依赖 audit（0 漏洞）、格式、类型检查、27 项前端单测、构建和 2 项 Recorded E2E 均通过；这些有日期的验收记录位于 `.scratch/roadmap-closeout-20261015/`。
- 已完成剩余旧 tracker 的逐文件 triage：24 个 issue 与 4 份规划标为 `wontfix`，原文和替代路径保存在 `.scratch/archive/superseded-trackers-20261013/README.md`。旧验收项不等于全部实现，不能重新把已移除架构或公网托管要求列为当前任务。

unknown 校准后续（2026-10-15）：

- 标签新增可选 `unknown_dimensions` 集合，与阳性 `dimensions` 不得重叠；省略时保留历史二元标签行为。逐维及 micro 混淆计数、指标和分歧只统计已评估的 Run/维度组合。既有 metrics 字典结构保留，顶层新增 `evaluation_scope="assessed_run_dimension_pairs"` 以及逐维和 micro coverage（`known_count`、`unknown_count`、`total_count`、`ratio`）。
- 六个 Run、30 条事件已通过严格绑定，无合同分歧：context 已知 2 / 未知 4、总数 6（`tp=1`、`tn=1`），micro 已知 20 / 未知 4、总数 24（`tp=3`、`tn=17`）。此前工程 gate 保留在 `.scratch/roadmap-closeout-20261015/controlled-capture-unknown-20261015/`；该检查点的后端全套通过 `228 passed`，包含独立 PostgreSQL 检查，无跳过。
- analyzer 仍为 `failure-analyzer.v2`，packet 与 v1 sidecar schema 不变，现有 labels 原字节 digest 会绑定 unknown 修改。人工评审、仲裁、绑定与 `accuracy_status` gate 保持不变；合法 unknown 本身不禁止 publishable，但 publishable 也不证明完整覆盖或样本代表性。本批 agent 标签仍为 `publishable_accuracy=false`。

受控校准集成后续（2026-10-15）：

- 新增 `scripts/controlled_calibration_e2e.py`：创建独占 PostgreSQL 采集库与恢复库，运行六合同、恢复真实 dump、核对其 digest 与源库/恢复库 manifest，仅规范化两个 binding 文件 digest 的前缀后要求严格报告全部字段一致。两份原始报告保持不变，检查包含 unknown 覆盖率与 `publishable_accuracy=false`，保留诊断且只清理本次创建的数据库。可复现命令和 digest 规范化规则见[校准流程](docs/evaluation-calibration.md)。
- 此前本地工程检查点已通过，产物位于 `.scratch/roadmap-closeout-20261015/postgres-calibration-e2e/capture-run-verified/`：六个 Run、30 条事件、schema `0008_regression_cases`，实际恢复的备份为 `sha256:85f81cb1aadd5f7cd3df5c3da2d68783ddccf91c2ede7a6892af885bb835439b`，源库/恢复库 manifest 与报告匹配，context 已知 2 / 未知 4、micro 已知 20 / 未知 4，状态为 `bound`、`provisional_mechanics`、`publishable_accuracy=false`。父目录的 `local-verification.json` 记录 harness UUID 数据库剩余 0，29 个产物文件的凭据匹配数为 0。历史批次及首次比较失败的产物继续保留。
- 该检查点的后端全套通过 `256 passed in 31.89s`，包含 PostgreSQL 检查，无跳过（同一证据目录的 `backend-full.log`）。既有 backup/restore 演练也已通过；`legacy-recovery.log` 与 `legacy-cleanup.json` 记录其独占库清理及既有数据库保留。这些宿主机检查覆盖该检查点，此前标准镜像验收仍只对应当时记录的源码快照。
- `database-recovery` CI job 已在 retention 检查后接入该 gate，步骤限时五分钟，产物保留七天。这补齐现有可靠性/评估工作的手动集成缺口；这些修改的 hosted CI 仍待后续获准推送后运行。历史快照继续保留，不新增 Phase 1.5。

编码凭据与 CI 步骤后续（2026-10-15）：

- 修复 Alembic 经 ConfigParser 传递已编码异步数据库 URL 时的百分号处理，并在脱敏摘要中将报告验证后的清理失败标为 `cleanup_restore` 或 `cleanup_source`。CLI 回归覆盖编码 URL 及清理失败。
- 最终后端全套通过 `262 passed in 42.72s`，包含 PostgreSQL 检查，无跳过。当前 gate 使用含编码凭据的非超级 `LOGIN CREATEDB` 角色，通过完整迁移及真实采集/恢复，dump 为 `sha256:57feb2fc0d0e7d679b1d9600860bdc56e3f74913cc8e3a549aa62dc84cb1d48a`。临时库及角色已删除、配置指定的数据库保留，产物与解压 dump 的已知凭据匹配数为 0。证据位于 `.scratch/roadmap-closeout-20261015/ci-sequence-followup/`，索引见[校准后续](docs/evaluation-calibration.md#encoded-credentials-and-ci-step-follow-up-2026-10-15)。
- 分别完成本地 CI 命令复现：此前 backend job 通过 schema 导出/diff、`246 passed, 2 skipped` 及 migration up/down/up，其中两个 PostgreSQL 模块未启用；此前 recovery job 启用全部十项 PostgreSQL 测试，并依序通过迁移、既有 backup/restore 和受控采集/恢复。这些本地检查不等于 hosted CI 成功，也不更新此前镜像验收，临时准确率 gate 不变。

当前版本整体验收（2026-10-15）：

- **本地工程验收通过**，一项非阻断 UI 不足记录在[验收矩阵](docs/operations/system-acceptance.md)。代码修复为本地提交 `3f10485` 和 `5a65d4a`，没有推送。
- 修复真实 PostgreSQL 取消/完成死锁，让取消和领取统一采用 Job → Run 锁顺序；确定性 PostgreSQL 回归也能拒绝“只修取消、将死锁转移到 queued claim”的不完整方案。修复原生命名 SSE 的接收与监听清理，真实浏览器已能显示此前为空的六步 Trace。
- 最终通过 **265 项后端测试、无跳过**、协议导出/diff、**34 项前端单测**、类型/构建/格式检查、**2 项 Recorded E2E** 以及 Rust fmt/Clippy/workspace 测试。刷新后的标准镜像通过三个 Golden 闭环、真实 checkout UI 与 RegressionCase 激活门禁、真实请求中断后 `after=1` 续接、取消、Runner kill/replacement、非终态旧 Lease 写入拒绝和应用备份恢复。147 个源码文件的摘要与已提交代码一致，应用容器没有源码挂载。允许 Docker 缓存，这些提交的 hosted CI 仍未验证。
- 新建 PostgreSQL 库中的 migration up/down/up、retention/并发及六合同校准采集/恢复通过。agent 合同标签仍为 `provisional_mechanics`、`publishable_accuracy=false`，独立标注与仲裁继续 pending。本地产物位于 `.scratch/system-acceptance-20261015/`。
- 非阻断后续：RegressionCase Replay 完成后，卡片状态有时需导航或刷新才更新，持久化激活门禁仍正确。将其作为有界 UI 维护处理，不新增产品里程碑。

后续顺序：

1. 后续获得推送授权后运行 hosted CI，验证本地提交和新接入的 PostgreSQL 校准采集/恢复 gate；本轮仅本地 commit，不把 2026-10-05 的 CI 结果当作当前提交结果。
2. 维护 unknown-aware v2 六合同工程 gate 和自动 PostgreSQL 采集/恢复检查；扩展 analyzer、trajectory、Scenario、recovery、exporter 或报告时，维护相应回归及 revision/digest 绑定。当前没有必须新增的产品功能，不启动推测性 Phase 1.5。
3. 条件允许时再由至少两名独立评审者标注代表性样本，记录证据与不确定性，经指定仲裁者处理分歧后重新导出/报告。人工 gate 未完成前不发布 precision/recall/F1；这不阻塞工程开发，外部 snapshot 来源真实性仍不由 report 自动证明。
4. 只有确实启动可信内网部署时，才推进数据库凭据与访问边界整改；只有出现实际需求并通过 ADR，才提升吞吐量、协作等暂缓项。

验收分开记录：确定性工程机制、本地可重复验证与文档清理单独收口；独立标注及准确率发布 gate 仍为 pending，不能用 agent 合同替代。

### P2：明确清理项与延后项

- 过时的规划/分析文档移出 `docs/`，下一次仓库清理前仅保留在 `.scratch/archive/`；清理时只删除已确认被 ROADMAP/CONTEXT 取代的文档，保留带日期 handoff 以及本地 `.codegraph/`、`.cursor/` 工具目录。
- 重新 triage 过期 `.scratch/` 工单，将已取代内容标记为 `wontfix` 或 `ready-for-human`；只有记录标签和替代路径后才删除旧 issue 文件。
- 2026-10-05 归档的 legacy/completed tracker 与本地 `.bak` 副本继续保留，替代路径见各目录 README。2026-10-13 已将 `baseline-hardening/`、`closed-loop-architecture/`、`demo-packaging/` 的剩余 28 份文件逐一标记 `wontfix` 并归档到 `.scratch/archive/superseded-trackers-20261013/`；不再有待实现旧条目。这些被忽略的本地归档不是源码或回滚输入，原文、日期 handoff、`.codegraph/` 和 `.cursor/` 保留。
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
