# Phase 2 任务清单

状态日期：2026-11-01（本地；实际运行时间见 UTC 验收记录）。决策在 `docs/adr/0007`–`0014`。里程碑在 `ROADMAP.md`。
本文件是可执行任务的事实来源。`.scratch/` 只放本地证据，不放交接。

规范：`docs/agents/development-standards.md`。

阅读入口：`docs/learn/00-map.md`。

**12 周内必须完成：** H0（本文件所列剩余项）、H1、H2-1、H3、H6-1、H6-2。

**可以降级：** H2-2、H2-3、H2-5、整个 H4、H5-2 至少标注 20 条。

**当前集成状态：** H1-1..H1-6 的工程实现已接入生产 dispatch/completion；组件已分别审查，fixture/fake-Provider、隔离 PostgreSQL、标准 Runner 网络构建、实际 named-volume 替换、受影响回归和 exact-payload 文档收尾均有本地父证据。lock-path 与 exit-boundary 两个 P2 已修复并经只读独立复审。本轮已完成 H1-5：已有 Windows llama.cpp 端点直连，隔离 PostgreSQL 下 reference Agent 的 research/order 成功、checkout/fault 失败均保留，标准 Runner 镜像直连 research 也成功；不修改断言刷绿。976 项后端测试、Rust/前端/Recorded E2E 与原契约 gate 通过。H1 本地工程与真实模型集成边界完成；hosted CI 已在 `b32da25` 全部通过（含 `golden-e2e`，见 [system-acceptance.md](../operations/system-acceptance.md)）。这不等于发布、全模型兼容或准确率证明。H2-1/H2-4 已完成：ADR-0008 发现修订、pi 适配器、真实 llama.cpp 下 reference 与 pi 的四 Environment 对比和 `03` 章；可选 H2-2/H2-3/H2-5 未做。H0 剩余人工事项不阻塞 H1。

未开工的任务用 `docs/agents/triage-labels.md` 的词。`completed` 表示已经落地，不要重做。

---

## H0 — 门面与决策

### H0-1 LICENSE 与 GitHub About

- Status: `ready-for-human`（仓库内 LICENSE 已落地；GitHub 设置需人点一次）
- 已完成：根目录 `LICENSE`（MIT）。
- 尚未做，需仓库管理员在 GitHub 上改：
  - Description：`Self-hosted agent reliability harness: deterministic tool environments, pass^k, and rule-based failure classification for local models.`
  - Topics：`agent`、`evaluation`、`llm`、`rust`、`opentelemetry`、`mcp`
  - 确认 About 里不再写 training-data export。
- 验收：GitHub About 与 README 第一段一致。

### H0-2 对外名称

- Status: `ready-for-human`
- 产品名仍是 **AgentOps Workbench**。仓库目录不改。
- 2026-10-17 已查 GitHub 仓库搜索与 PyPI。下面三个名称两边都没有占用，且不含 "AgentOps"：
  1. **leasefence** — 对应租约恢复和 Attempt fencing。优先候选。
  2. **resetrial** — 对应可重置环境上的重复试验。
  3. **rerunnel** — 自造词，空名，含义不如前两个直接。
- 已排除：`gauntlet`、`provingground`、`passk`、`harnessd`（GitHub 或 PyPI 已有，其中 provingground 和 harnessd 已是 agent harness）。`trialrun`、`tooltrial` 近乎空，但已有同名或近名仓库。
- 选定后改 README 标题、`CONTEXT.md` 标题、GitHub 描述。未选定前不要改代码里的 crate 名。

### H0-3 决策与活文档

- Status: `completed`
- ADR-0007..0011 已接受。`CONTEXT.md`、`ROADMAP.md`、`README.md` 与中文对应文件已指向它们。
- 验收：活文档里的链接能打开。

### H0-4 校准文档重定义

- Status: `completed`
- `docs/evaluation-calibration.md` 已分成回归门与一致性研究。
- 明确不做：新的 digest、snapshot identity、strict-mode。

### H0-5 学习地图

- Status: `completed`
- 交付：`docs/learn/README.md`、`docs/learn/00-map.md`、`docs/learn/glossary.md`。

H0 剩余只有 H0-1 的 GitHub 点击和 H0-2 的命名。命名未定不阻塞 H1。

---

## H1 — Environment / Agent 拆分（第 2–4 周）

先读 ADR-0007、0008、0009，再读 `docs/learn/00-map.md` 里的 Phase 1 路径。
不要在 H1 里做 pass^k 或 OTel。

### H1 共用契约（已批准并接入当前集成）

- 遵循 [ADR-0012](../adr/0012-h1-canonical-cutover-and-control-plane-success.md)：一次性不兼容升级，三个规范 ID 为 `checkout-api-latency.v2`、`multi-step-research.v2`、`api-fault-orchestration.v2`；API 省略 ID、UI 默认、quickstart 均走规范 v2（checkout 默认 `.v2`）。退休对应三个 v1 执行注册与耦合 fixture/provider 循环，不保留双路径。`order-support.v1` 与通用语义版本身份规则不变。
- 不做旧 v1 兼容、迁移、别名、重评分、历史 replay 保留或跨版本可比性工作；保留准确的 Phase 1 历史说明。旧记录可丢弃不等于允许访问或删除现有数据：不做自动 reset、破坏性迁移、删表或数据库/文件/volume 删除。另行请求的运维 reset 独立处理；实现验证使用全新隔离数据。
- Python Control Plane 独占权威评估、Run 状态与数据库。成功必须同时满足：有效 submit、check 成功、必需可信证据充分有效、注册 suite aggregate >= 该版本固定的 1.0、没有异常执行失败。缺失/无效必需证据、check 失败、未有效 submit、Agent 异常/非零退出或终态完成前 Environment 意外失败均不能成功。有效 submit、check 成功并捕获必要证据后，Runner 请求终止健康 Environment（含 SIGTERM 后共享两秒宽限期届满再 SIGKILL）属于计划内生命周期，不是异常执行失败；不要求两个子进程都自然零退出。该例外不覆盖前述失败、取消、超时、output-limit 或 lease failure；终态事件与 CP completion 必须一致，不信任 Agent/check 自报分数或状态。
- 保留 ALL=min、ANY=max、WEIGHTED=加权均值与每条断言的 threshold；不得替换成通用 `all(item.passed)`，也不以含惩罚的 quality score 判定任务成功。人工 Policy 验证与激活规则不变。
- CP 轨迹断言读取最新 Attempt 持久化的所有 world attempts（含拒绝），排除保留控制调用。拒绝投影为 `rejected_tool`、status `failed`，不能满足请求工具名的 used/args/sequence 断言；Environment 仅有效调用的内部 history 不能覆盖它。
- 最终状态断言读取有界、可信、Environment 所有的 final-check 事实，由 CP 校验必需 operands 与注册 expectations；不采信 Agent 提供的期望值或分数。订单恰好一次的证明来自最终 ledger。usage 缺失保持 unknown，有报告也只是 Agent-reported。已审查 wire 为 Environment 所有的 `scenario_id` + `final_observation`，CP 使用注册 expectations；见 ADR-0012。
- H1-3 只负责监督与事实转发，不实现 CP 评分策略或 Rust 第二评分器；H1-5/集成负责证据与 CP completion 的接线。组件验收不替代集成 Golden、PostgreSQL 专项；真实模型验收和结果边界见 [H1-5 记录](../history/h1-live-model-acceptance-2026-11-01.md)。

### H1-1 Environment 协议

- Status: `completed`（协议抽取与规范 v2 已接入；H1 本地交付边界已收尾）
- 从 `backend/app/scenario_registry.py` 与 `backend/app/scenarios/` 抽出 ADR-0007 的 Environment 接口：`reset`、`tool_definitions`、`call`、`check`、`fault_schedule`。
- 四个当前 Environment 提供统一接口；三世界规范 v2、订单 v1，不原地改变旧 v1 语义。
- 生产路径已退休耦合世界循环；旧 demo/test helpers 仅用于历史回归，不是可选生产 fallback。
- 验收：现有 `phase1_tests` 与三个 Scenario 的断言测试仍绿；新接口有单测覆盖 `reset` 同 seed 同观察。

### H1-2 `app.env_server`

- Status: `completed`（Environment MCP、采集与可信 final-check 已集成并审查）
- 依赖：H1-1。
- `python -m app.env_server`：`mcp` 固定 2.3.x，`MCPServer`，`transport="streamable-http"`，只绑 `127.0.0.1`。
- middleware 把每次 `tools/call` 写成 JSONL `step_completed`。实现 `submit_result` 与 `report_usage`。
- stdout 先打一行 `env_ready`（无 token），见 ADR-0008。
- submit/check 只产生事实，不决定成功；后续可信 final-check 证据接线遵循 ADR-0012，不信任 check 自报分数。
- 模块 docstring 按 development-standards 的四段式来写。
- 验收：单测不依赖真实模型；伪造一次 tool call 能得到一行 JSONL；超 `max_steps` 的调用被拒绝并被记录。

### H1-3 Runner 双进程监督

- Status: `completed`（含 lock-path / exit-boundary 修复复审、受影响镜像与最终回归）
- 依赖：H1-2 的 `env_ready` 约定。
- 先写 `runner/crates/agentops-runner` 的测试，再改实现：先拉起 env，读到 `env_ready`，再拉起 agent；取消时进程组一起收到 SIGTERM，两秒后 SIGKILL。
- 协议 crate 增加本地监督消息 `env_ready`。它不是 RunEvent，持久化事件里不得出现原始 token。
- 只转发 Environment 可信事实；Agent stdout 不得升级为可信事件。监督启动/退出/取消/超时与 lease fencing；提交、check 标记、EOF 或 exit 0 均不是成功。Agent 异常/非零退出或终态完成前 Environment 意外失败不得变成成功；H1-3 必须将意外失败与有效 submit、check 成功、必要证据捕获后 Runner 请求对健康 Environment 的计划内清理区分，后者含 SIGTERM 后共享两秒宽限期届满再 SIGKILL，不算异常失败，也不要求两个子进程自然零退出。该例外不得放过缺失证据、check 失败、Agent 异常/非零退出、取消、超时、output-limit 或 lease failure。终态事件须与 CP completion 一致。CP 评分策略不在 H1-3 实现，Rust 不新增评分器；证据/completion 接线由 H1-5/集成负责。
- 验收：`cargo test` 覆盖“env 已死则 agent 不被留存”“取消杀两个子进程”。不要用真实 MCP 端口做 flaky 测试；用假子进程打印 `env_ready`。

### H1-4 `agent_id`、注册表、前端下拉

- Status: `completed`（Rust/Python registry 与前端选择已集成并审查）
- 依赖：H1-3 能根据 id 解析命令。
- 运维 TOML 注册表；`GET /api/agents` 只返回元数据；`EvaluationSpec.agent_id` 可选，缺省规则见 ADR-0009。
- 未知 id 在创建 Run 时返回与未知 `scenario_id` 相同风格的结构化错误。
- 前端只改 New Experiment：增加 agent 下拉。Trace 页不动。
- API 省略 `scenario_id` 与 UI 默认同步规范 v2；不为退休的三个 v1 ID 提供别名。
- 改 Pydantic 后跑 `make contracts`。
- 验收：省略 `agent_id` 仍按 execution_mode 选择 fixture/reference（不是保留旧 v1 world）；API 测试拒绝未知/退休 id，省略 scenario ID 选规范 v2；前端单测覆盖注册表下拉与规范默认值。

### H1-5 参考 agent 与本地模型 quickstart

- Status: `completed`（工程、fake-Provider、真实 llama.cpp 直连 trace/分析与可复现 runbook 均完成）
- 依赖：H1-2、H1-4。
- `reference-openai-compatible.v1`：独立进程，通过 MCP 发现工具，用现有 `AGENTOPS_PROVIDER_*` 调 `/chat/completions`。
- `fixture-agent.v1`：在规范 Environment 上跑确定性轨迹，不要求复现旧 v1 结果；退休三套旧注册和耦合 fixture/provider 循环，不留双路径。
- 本任务/集成负责 ADR-0012 的可信轨迹/final-check 证据与 Python CP completion 接线、aggregate >= 1.0 成功门和终态一致性。精确 wire 已审查并接入；测试覆盖无 submit、check 失败、必需证据无效/缺失、拒绝轨迹、Agent 异常/非零退出、Environment 意外失败与上述计划内清理的区别，以及 ANY/ALL/WEIGHTED 语义，不能用 quality score 或通用 all(passed) 替代。
- README 使用规范 v2，并链接 [local-model.md](../operations/local-model.md) 的直连配置和 `scripts/local_model_acceptance.py`。按 ADR-0003 修订提供四项可选、校验有界的 Runner-only 生成参数；未设置保持旧请求，不修补模型响应或工具参数。Compose/示例配置同步，API/浏览器不接收这些参数。
- 工程验收：fixture Golden、reference Agent + fake Provider 及新增配置/采集测试通过。真实 Windows llama.cpp + Swift + 隔离 PostgreSQL 下 research/order 成功、checkout/fault 按既定规则失败；标准镜像 host-network research 成功。[记录](../history/h1-live-model-acceptance-2026-11-01.md)保留模型、工具事实、submit/check、终态、分析、GPU/连通性范围。H1 不要求模型每题成功；不冒称 Ollama/vLLM、准确率或稳定通过率。无 GPU 的 CI 不跑 live 模型，安装/下载、Windows/防火墙变更不在本轮范围。

### H1-6 有状态环境 `order-support.v1`

- Status: `completed`（有状态世界、MCP Agent 与订单恰好一次断言已集成验证）
- 依赖：H1-1。
- 一个内存订单库，身份保持 `order-support.v1`。Outcome 断言至少包括“退款恰好一次”；证明来自最终 ledger，而不是工具请求次数。CP 按注册 expectations 验证必需的可信 final-check operands，不采信 Agent 提供的期望值/分数；wire 已按 ADR-0012 审查并接入。
- 故障注入沿用现有 Scenario 的做法。
- 验收：fixture agent 下 baseline 失败、replay 成功，与 checkout Golden 同一套闭环测试形状。

### H1 集成诊断修订（ADR-0013，已批准）

- 仅把共享分析器升为 `failure-analyzer.v3`：显式 `agent_reported` context 作为 Run 级诊断证据；仅在零世界调用且有 Provider error 时，不从缺失工具断言推断 planning。
- 校准高/低 context 样本通过真实 `report_usage` 控制调用采集；六组标签和 unknown 覆盖保持不变，不伪造步骤。
- 不改 CP 入口、断言/评分/终态门或校准绑定机制；不回写历史包、不重评分，不提前启动 H5 研究。
- 验证双断言入口、非空规划反例、usage 覆盖/Attempt 隔离及两类 digest 防篡改；新生成 v3 校准包并独立审查。

### H1-C 工程收尾（本轮唯一活跃工程任务）

- Status: `completed`（H1 本地工程与真实模型集成边界完成；不授权 H2）
- ADR-0008 先行修订后修复 capacity/per-Run lock symlink 与非普通文件；拒绝不改写目标。
- Reader-prefix 与 observed-Agent-exit barrier 证明重构恰好一次；覆盖残留半行、clean/fragment EOF、自然退出、超长输出、final upload/heartbeat 和 Runner 自主回收。
- 两个 P2 的 scoped 独立复审已通过；父执行 Rust 59 项 Runner 测试/Clippy、五次边界重复、完整后端/前端/Recorded E2E、重建标准镜像、全新 volume replacement 和最终 exact-payload 文档复审均完成。
- 已整理历史 handoff/旧规划引用，保留 ADR ID，并区分 accepted decision、当前实现、父证据与历史记录。规范任务继续只在此维护。
- 保留 `make check-contracts` 原门禁，已在提交后的 canonical v2 基线上通过；历史 dirty-index exit 2 不再是当前 blocker，没有生成 schema 差异。
- H1-5 真实模型直连验收现已完成，没有安装模型或改变 Windows/网络/防火墙。原 P2 scoped 复审不冒充本轮增量独立复审；新增增量有确定性测试与父 diff review。最新边界见 [system-acceptance.md](../operations/system-acceptance.md)。

### H1 学习交付

- `docs/learn/01-run-lifecycle.md`
- `docs/learn/02-environment-and-mcp.md`
- `docs/learn/04-runner-lease-fencing.md`
- 这三篇在 H1 最后一个任务合并前写完。内容用中文，指向实际文件与函数。

---

## H2 — 外部 agent（第 5–6 周）

2026-11-02 计划变更（[ADR-0009](../adr/0009-agent-registry-trust-boundary.md) H2 修订）：必须的外部 Agent 由 Claude Code 改为 pi（`@earendil-works/pi-coding-agent`）。pi 是最小 harness，可直连现有 OpenAI-compatible 本地端点；与参考 agent 用同一本地模型时，比较的只是 scaffold。Claude Code 是深度定制的 harness，默认依赖 Anthropic 模型与凭据，降为可选 H2-5。

### H2-1 pi 适配器（必须）

- Status: `completed`（2026-11-03：适配器、假 `pi` 确定性测试、同一本地模型下 reference 与 pi 四个 Environment 各一次的真实验收和 `03` 章均已完成；见[验收记录](../history/h2-pi-live-acceptance-2026-11-03.md)）
- 前置（已完成，ADR-0008 H2 修订）：`env_server` 的 `tools/list` 现在列出空参数 `submit_result`，`report_usage` 仍不可发现；`MCPAgentClient` 要求恰好一个 `submit_result` 并保留自己的 control 定义。pi 只能调用发现到的工具；不允许适配器在 pi 退出后代为 submit。
- 注册 `pi-coding-agent.v1`（kind `external`）。适配器是薄 Python 模块，为每个 Run 生成私有 `PI_CODING_AGENT_DIR`：
  - `mcp.json`：一个 streamable-HTTP server 指向 `AGENTOPS_ENV_MCP_URL`，`exposure: "direct"`，`Authorization` header 写成 `Bearer ${AGENTOPS_ENV_TOKEN}` 插值；token 不进 argv，也不落盘。
  - `models.json`：`api: "openai-completions"`，`baseUrl` 与模型取自 `AGENTOPS_PROVIDER_*`，`apiKey` 写成 `$AGENTOPS_PROVIDER_API_KEY` 插值。生成参数尽量与参考 agent 一致，映射不了的写明。
- 调用 `pi --mode json --no-session`，在空临时目录运行，隔离运维者个人配置：`-ne -e builtin:mcp`、`-ns`、`-np`、`-nc`、`--no-themes`、`-na`、`--offline`。`--tools 'mcp__<server>__*'` 只声明 Environment 工具，内置 read/bash/edit/write 全部关闭。任务说明与 Policy `instruction_patch` 走 `--append-system-prompt`。
- 适配器不评分，不发 `step_completed`。pi 的 JSONL 只是不可信进程输出，转发前不得含 token；退出码 0 不等于成功。
- pi 没有 turn 上限参数：步数靠 Environment `max_steps`，时间靠 Run timeout。pi 的 usage 不经 `report_usage` 进入 Environment 时保持 unknown。预期不需要扩展 Runner 子进程环境变量白名单；实测需要时另行修订 ADR-0009。
- 固定 pi 版本（运维主机当前为 1.3.0，要求 Node >= 22.19）。CI 不安装、不调用 `pi`；用假 `pi` 可执行文件连接真实 `env_server` 做确定性测试。
- 验收：同一 Environment、同一本地模型上，参考 agent 与 pi 各完成一次 Run，成败照实记录；`docs/learn/03-plug-in-your-agent.md` 写出失败类型分布的差异（哪一类只能是 unknown）。

### H2-2 OpenAI Agents SDK 适配器（可选）

- Status: `ready-for-agent`
- 依赖：H1-4。可以整项不做。
- `MCPServerStreamableHttp` + `OpenAIChatCompletionsModel`，base URL 指向 Ollama。约一个小脚本，不引入第二套评分。
- 可整项砍掉。

### H2-3 Codex CLI 适配器（可选）

- Status: `ready-for-agent`
- 依赖：H1-4。可以整项不做。
- `codex exec --json`，MCP 写在运行时生成的 config 的 `[mcp_servers.*]`。
- 可整项砍掉。H2-2、H2-3、H2-5 至少留下一个的前提是 H2-1 已完成且还有时间；否则只留 H2-1。

### H2-4 接入说明

- Status: `completed`（`docs/learn/03-plug-in-your-agent.md` 第 3 节，示例已用真实 `env_server` 跑通）
- 并入 H2-1 的学习交付：`docs/learn/03-plug-in-your-agent.md`，一页加一个约 20 行的自有循环示例。

### H2-5 Claude Code 适配器（可选）

- Status: `ready-for-agent`
- 2026-11-02 由必须降为可选。依赖：H2-1 前置的控制工具发现修订。可以整项不做。
- 注册 `claude-code.v1`。`claude -p --output-format stream-json`，`--strict-mcp-config` 加运行时生成的 `--mcp-config`。`--tools ""` 关掉内置工具；不要用 `--allowedTools` 充当白名单（它只是自动批准）。`--bare` 跳过个人 hooks、插件与 CLAUDE.md，但认证只认 `ANTHROPIC_API_KEY`。
- 接本地模型需要 Anthropic Messages 兼容端点；8192 context 可能放不下 Claude Code 的系统提示。2.1.312 的 `--help` 未列 `--max-turns`，使用前实测。
- CI 不安装、不调用 `claude`。

---

## H3 — Trial 与 pass^k（第 7–8 周）

### H3-1 数据模型与 API

- Status: `completed`（2026-11-03：迁移 `0010_trial_groups`，按 ADR-0010 H3-1 修订增加 `trial_groups` 行保存 `n`/`k`/seed；`POST /api/experiments/{id}/trials`；SQLite/PostgreSQL 迁移往返与 `test_trial_groups.py`）
- 依赖：H1 完成。Trial 挂在拆分后的 Run 上，不要在旧的单进程 agent 上先做。
- 新 Alembic revision：`trial_group_id`、`trial_index`。旧行视为不在组内。
- `POST /api/experiments/{id}/trials`，字段 `n` 与 `k`，语义见 ADR-0010。
- 验收：迁移 upgrade/downgrade；组内 seed 相同；每个 trial 仍是普通 Run。

### H3-2 聚合与策略验证

- Status: `completed`（2026-11-03：`app.trials` 估计量与读时聚合，组 replay 与组锁验证，见 ADR-0010 H3-2 修订；`test_trials_estimator.py`、`test_trial_groups.py`、PostgreSQL 并发测试。已知弱点：baseline pass^k 为 0 时一次成功即可验证，是否收紧另行决定）
- 实现 ADR-0010 的 `C(c,k)/C(n,k)`。组未全部终态时不发布 pass^k。
- `k = 1` 的策略验证保持 CONTEXT 不变量 7。`n > 1` 时要求 pass^k 不下降且平均分差为正。
- 验收：用脚本化的成功/失败组合测估计量，不调用模型。

### H3-3 Experiment 页展示

- Status: `ready-for-agent`
- 只改 Experiment 详情：pass^1、pass^k、每个 trial 的状态。
- Trace / Analysis / Improve 的信息架构不新增视图。在 README 写明 Trace UI 冻结。
- 验收：前端单测覆盖有组与无组两种 Experiment。

### H3 学习交付

- `docs/learn/06-pass-k.md`，含估计量公式和“温度设为 0 时 pass^k 会被高估”这句。

---

## H4 — OTel（第 9 周，可推迟）

### H4-1 映射与导出

- Status: `ready-for-agent`
- 依赖：H1-2 的事件仍是 `step_completed`。可整阶段推迟。
- `backend/app/otel_export.py`。默认关闭。`AGENTOPS_OTEL_EXPORT=1` 才导出。
- 映射见 ADR-0011。导出失败不改变 Run 状态。
- 验收：对一条 golden Run 的事件生成 span，单测检查属性名；导出关闭时测试不连网。

### H4-2 对端验证

- Status: `ready-for-human`（需要本地 Phoenix 或 Langfuse）
- 对 `:6006/v1/traces` 或 Langfuse `/api/public/otel/v1/traces` 打一条，截图放 `docs/`。
- `docs/learn/07-otel-mapping.md` 写明 `semantic-conventions-genai` 的 commit，并写 Status: Development。

---

## H5 — 校准重定义（第 10–11 周）

### H5-1 文档拆分（已完成正文）

- Status: `completed`
- 机制冻结。H1 的 ADR-0013 已将分析器升至 `failure-analyzer.v3`；H5 若发现新规则问题，须新 ADR/新 revision，并重跑六合同门。不要加新的绑定字段。

### H5-2 自标注

- Status: `ready-for-human`（标注必须人做；导出命令可以交给 agent）
- 用现有 `python -m app.calibration_review` 导出约 40 条终态 Run（时间不够则 20 条）：覆盖 3–4 个 Environment，含本地模型；有外部适配器时各抽几条。
- 作者在不看 `run_analyses` 的前提下标注。发布混淆矩阵、覆盖率、分歧和偏差，放在 `docs/`，并写明标签是作者自己标的。
- 验收：六合同 CI 门仍绿；文档里没有“等待独立标注员”作为阻塞项。

### H5 学习交付

- `docs/learn/05-analyzer-and-calibration.md`

---

## H6 — 打包（第 12 周）

### H6-1 README

- Status: `ready-for-agent`（等 H3 有真实数字后再改“结果”段；定位段已在 2026-10-17 改过）
- 补：Ollama 30 秒路径、架构图、截图或 GIF、带模型名和 `n`/`k` 的 pass^k、一段“为什么不是 Langfuse”。

### H6-2 Write-up

- Status: `ready-for-agent`
- `docs/writeup.md`：建了什么、2026-10 市场变成什么样、砍了什么、数字从哪来。

### H6-3 Recorded demo

- Status: `ready-for-agent`
- H1 已刷新真实 checkout v2 Recorded Preview（ADR-0014）；H6 只补后续实际交付能力的演示，不重复造数据。公网托管不是验收条件。

---

## 明确不做

Trace UI 新功能、annotation queue、数据集管理、多租户、Kubernetes、把 CLI agent 放进容器、LLM-as-judge、训练数据导出、新的校准绑定机制。

计划外工作：先改 ADR 和 `ROADMAP.md`，再写代码。
