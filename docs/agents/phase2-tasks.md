# Phase 2 任务清单

状态日期：2026-10-24。决策在 `docs/adr/0007`–`0012`。里程碑在 `ROADMAP.md`。
本文件是可执行任务的事实来源。`.scratch/` 只放本地证据，不放交接。

规范：`docs/agents/development-standards.md`。

阅读入口：`docs/learn/00-map.md`。

**12 周内必须完成：** H0（本文件所列剩余项）、H1、H2-1、H3、H6-1、H6-2。

**可以降级：** H2-2、H2-3、整个 H4、H5-2 至少标注 20 条。

**基线状态：** H1-1 已在 `4ee7adc72ca4e3acce99acd0145659e5d34ba603` 接受。H1-2 的独立组件待审，H1-6 的独立组件已接受但 E2E 延后；两者均未并入本基线，不表示 H1 已交付。其他任务要等依赖落地后再领，一次只领一个。H0 剩下的两项是人工操作，不阻塞 H1。

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

### H1 共用契约（2026-10-24 已批准，待实现）

- 遵循 [ADR-0012](../adr/0012-h1-canonical-cutover-and-control-plane-success.md)：一次性不兼容升级，三个规范 ID 为 `checkout-api-latency.v2`、`multi-step-research.v2`、`api-fault-orchestration.v2`；API 省略 ID、UI 默认、quickstart 均走规范 v2（checkout 默认 `.v2`）。退休对应三个 v1 执行注册与耦合 fixture/provider 循环，不保留双路径。`order-support.v1` 与通用语义版本身份规则不变。
- 不做旧 v1 兼容、迁移、别名、重评分、历史 replay 保留或跨版本可比性工作；保留准确的 Phase 1 历史说明。旧记录可丢弃不等于允许访问或删除现有数据：不做自动 reset、破坏性迁移、删表或数据库/文件/volume 删除。另行请求的运维 reset 独立处理；实现验证使用全新隔离数据。
- Python Control Plane 独占权威评估、Run 状态与数据库。成功必须同时满足：有效 submit、check 成功、必需可信证据充分有效、注册 suite aggregate >= 该版本固定的 1.0、没有异常执行失败。缺失/无效必需证据、check 失败、未有效 submit、异常或非零退出均不能成功；取消、超时与 lease fencing 优先。终态事件与 CP completion 必须一致，不信任 Agent/check 自报分数或状态。
- 保留 ALL=min、ANY=max、WEIGHTED=加权均值与每条断言的 threshold；不得替换成通用 `all(item.passed)`，也不以含惩罚的 quality score 判定任务成功。人工 Policy 验证与激活规则不变。
- CP 轨迹断言读取最新 Attempt 持久化的所有 world attempts（含拒绝），排除保留控制调用。拒绝投影为 `rejected_tool`、status `failed`，不能满足请求工具名的 used/args/sequence 断言；Environment 仅有效调用的内部 history 不能覆盖它。
- 最终状态断言读取有界、可信、Environment 所有的 final-check 事实，由 CP 校验必需 operands 与注册 expectations；不采信 Agent 提供的期望值或分数。订单恰好一次的证明来自最终 ledger。usage 缺失保持 unknown，有报告也只是 Agent-reported。精确的 additive wire 字段/类型留给后续独立审查，不在此发明。
- H1-3 只负责监督与事实转发，不实现 CP 评分策略或 Rust 第二评分器；H1-5/集成负责证据与 CP completion 的接线。组件验收不替代集成 Golden、PostgreSQL 专项或本地模型验收。

### H1-1 Environment 协议

- Status: `completed`（抽取已在本基线接受；不是规范 v2 切换已完成）
- 从 `backend/app/scenario_registry.py` 与 `backend/app/scenarios/` 抽出 ADR-0007 的 Environment 接口：`reset`、`tool_definitions`、`call`、`check`、`fault_schedule`。
- 已完成抽取：三个现有 Scenario 提供 Environment 接口；本抽取阶段保留原 `scenario_id`。后续规范切换按 ADR-0012 使用新 v2 身份，不原地改变 v1 语义。
- 抽取阶段保留 `run_fixture`/`run_provider`；H1-5 接上独立 agent 后按共用契约退休耦合循环和对应 v1 执行注册，不要求保留旧执行结果。
- 验收：现有 `phase1_tests` 与三个 Scenario 的断言测试仍绿；新接口有单测覆盖 `reset` 同 seed 同观察。

### H1-2 `app.env_server`

- Status: `needs-triage`（独立组件实现待审，未并入本基线）
- 依赖：H1-1。
- `python -m app.env_server`：`mcp` 固定 2.3.x，`MCPServer`，`transport="streamable-http"`，只绑 `127.0.0.1`。
- middleware 把每次 `tools/call` 写成 JSONL `step_completed`。实现 `submit_result` 与 `report_usage`。
- stdout 先打一行 `env_ready`（无 token），见 ADR-0008。
- submit/check 只产生事实，不决定成功；后续可信 final-check 证据接线遵循 ADR-0012，不信任 check 自报分数。
- 模块 docstring 按 development-standards 的四段式来写。
- 验收：单测不依赖真实模型；伪造一次 tool call 能得到一行 JSONL；超 `max_steps` 的调用被拒绝并被记录。

### H1-3 Runner 双进程监督

- Status: `ready-for-agent`
- 依赖：H1-2 的 `env_ready` 约定。
- 先写 `runner/crates/agentops-runner` 的测试，再改实现：先拉起 env，读到 `env_ready`，再拉起 agent；取消时进程组一起收到 SIGTERM，两秒后 SIGKILL。
- 协议 crate 增加本地监督消息 `env_ready`。它不是 RunEvent，持久化事件里不得出现原始 token。
- 只转发 Environment 可信事实；Agent stdout 不得升级为可信事件。监督启动/退出/取消/超时与 lease fencing；提交、check 标记、EOF 或 exit 0 均不是成功。异常/非零退出不得变成成功，终态事件须与 CP completion 一致。CP 评分策略不在 H1-3 实现，Rust 不新增评分器；证据/completion 接线由 H1-5/集成负责。
- 验收：`cargo test` 覆盖“env 已死则 agent 不被留存”“取消杀两个子进程”。不要用真实 MCP 端口做 flaky 测试；用假子进程打印 `env_ready`。

### H1-4 `agent_id`、注册表、前端下拉

- Status: `ready-for-agent`
- 依赖：H1-3 能根据 id 解析命令。
- 运维 TOML 注册表；`GET /api/agents` 只返回元数据；`EvaluationSpec.agent_id` 可选，缺省规则见 ADR-0009。
- 未知 id 在创建 Run 时返回与未知 `scenario_id` 相同风格的结构化错误。
- 前端只改 New Experiment：增加 agent 下拉。Trace 页不动。
- API 省略 `scenario_id` 与 UI 默认同步规范 v2；不为退休的三个 v1 ID 提供别名。
- 改 Pydantic 后跑 `make contracts`。
- 验收：省略 `agent_id` 仍按 execution_mode 选择 fixture/reference（不是保留旧 v1 world）；API 测试拒绝未知/退休 id，省略 scenario ID 选规范 v2；前端单测覆盖注册表下拉与规范默认值。

### H1-5 参考 agent 与本地模型 quickstart

- Status: `ready-for-agent`
- 依赖：H1-2、H1-4。
- `reference-openai-compatible.v1`：独立进程，通过 MCP 发现工具，用现有 `AGENTOPS_PROVIDER_*` 调 `/chat/completions`。
- `fixture-agent.v1`：在规范 Environment 上跑确定性轨迹，不要求复现旧 v1 结果；退休三套旧注册和耦合 fixture/provider 循环，不留双路径。
- 本任务/集成负责 ADR-0012 的可信轨迹/final-check 证据与 Python CP completion 接线、aggregate >= 1.0 成功门和终态一致性。精确 additive wire 另行审查；测试覆盖无 submit、check 失败、必需证据无效/缺失、拒绝轨迹、异常/非零退出，以及 ANY/ALL/WEIGHTED 语义，不能用 quality score 或通用 all(passed) 替代。
- README quickstart 使用规范 v2（checkout 默认 `checkout-api-latency.v2`），增加 Ollama 小节：默认 `qwen3:8b`；`qwen3:14b` 标为 16 GB 上的可选项，不写“保证放得下”。vLLM 启动参数写 `--enable-auto-tool-choice --tool-call-parser hermes`（Qwen3-Coder 用 `qwen3_xml`，不要混用）。
- 验收：Golden E2E 用 fixture agent 全绿。本地有 Ollama 时，参考 agent 能跑出一条带分析的 trace；没有 GPU 的 CI 不跑这一条。

### H1-6 有状态环境 `order-support.v1`

- Status: `ready-for-agent`（独立组件已接受，未并入本基线；集成/E2E 待完成）
- 依赖：H1-1。
- 一个内存订单库，身份保持 `order-support.v1`。Outcome 断言至少包括“退款恰好一次”；证明来自最终 ledger，而不是工具请求次数。CP 按注册 expectations 验证必需的可信 final-check operands，不采信 Agent 提供的期望值/分数；具体 wire 在集成时审查。
- 故障注入沿用现有 Scenario 的做法。
- 验收：fixture agent 下 baseline 失败、replay 成功，与 checkout Golden 同一套闭环测试形状。

### H1 学习交付

- `docs/learn/01-run-lifecycle.md`
- `docs/learn/02-environment-and-mcp.md`
- `docs/learn/04-runner-lease-fencing.md`
- 这三篇在 H1 最后一个任务合并前写完。内容用中文，指向实际文件与函数。

---

## H2 — 外部 agent（第 5–6 周）

### H2-1 Claude Code 适配器（必须）

- Status: `ready-for-agent`（等 H1-4 注册表落地后再开工）
- 注册 `claude-code.v1`。适配脚本从环境变量生成 `--mcp-config`，调用 `claude -p`、`--output-format stream-json`、`--max-turns`。
- `--tools ""` 关掉内置工具；MCP 工具不受 `--tools` 影响。不要用 `--allowedTools` 充当白名单（它只是自动批准）。
- CI 不安装、不调用 `claude`。
- 验收：同一 Environment 上，参考 agent 与 Claude Code 各完成一次；`docs/learn/03-plug-in-your-agent.md` 写出失败类型分布的差异（哪一类只能是 unknown）。

### H2-2 OpenAI Agents SDK 适配器（可选）

- Status: `ready-for-agent`
- 依赖：H1-4。可以整项不做。
- `MCPServerStreamableHttp` + `OpenAIChatCompletionsModel`，base URL 指向 Ollama。约一个小脚本，不引入第二套评分。
- 可整项砍掉。

### H2-3 Codex CLI 适配器（可选）

- Status: `ready-for-agent`
- 依赖：H1-4。可以整项不做。
- `codex exec --json`，MCP 写在运行时生成的 config 的 `[mcp_servers.*]`。
- 可整项砍掉。H2-2 与 H2-3 至少留下一个的前提是 H2-1 已完成且还有时间；否则只留 H2-1。

### H2-4 接入说明

- Status: `ready-for-agent`
- 并入 H2-1 的学习交付：`docs/learn/03-plug-in-your-agent.md`，一页加一个约 20 行的自有循环示例。

---

## H3 — Trial 与 pass^k（第 7–8 周）

### H3-1 数据模型与 API

- Status: `ready-for-agent`
- 依赖：H1 完成。Trial 挂在拆分后的 Run 上，不要在旧的单进程 agent 上先做。
- 新 Alembic revision：`trial_group_id`、`trial_index`。旧行视为不在组内。
- `POST /api/experiments/{id}/trials`，字段 `n` 与 `k`，语义见 ADR-0010。
- 验收：迁移 upgrade/downgrade；组内 seed 相同；每个 trial 仍是普通 Run。

### H3-2 聚合与策略验证

- Status: `ready-for-agent`
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
- 机制冻结。若 H5-2 证明规则不够，只允许改分析器规则并升到 `failure-analyzer.v3`，然后重跑六合同门。不要加新的绑定字段。

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
- 更新 `VITE_MOCK_API` 的 fixture，覆盖新 Environment。公网托管仍然可选，不作为验收条件。

---

## 明确不做

Trace UI 新功能、annotation queue、数据集管理、多租户、Kubernetes、把 CLI agent 放进容器、LLM-as-judge、训练数据导出、新的校准绑定机制。

计划外工作：先改 ADR 和 `ROADMAP.md`，再写代码。
