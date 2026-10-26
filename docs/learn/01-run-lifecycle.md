# 一次 Run 的生命周期：从 claim 到终态

> **状态：Draft / H1-3 组件交接。** 本章描述本 worktree 中已经存在的 Phase 1 基线代码，并把 H1-3 的双进程监督标成“计划中的接线”。H1-2 的 `app.env_server` 已有独立实现，但尚未接入当前 Runner；H1 尚未集成、验收或发布。本章不声称 H1 acceptance。

## 本章的四个定位

1. **这是什么：** 学习一次 `Run` 如何由 Control Plane 排队、被 Runner 领取、产生事件并进入终态。
2. **它在一次 Run 的哪里：** 从 `create_baseline_run` 写入 `queued` 开始，经过租约、Attempt、子进程监督，直到 `complete`；回放仍是重新执行，不是播放旧 Trace。
3. **不能破坏的边界：** Control Plane 是唯一的 PostgreSQL 读写者、评分者和 Run 状态拥有者；Runner 只监督/转发，不访问数据库、不自行评分。
4. **下一步读什么：** 先读 [`00-map.md`](00-map.md) 的 Phase 1 入口，再读 [`02-environment-and-mcp.md`](02-environment-and-mcp.md) 了解已抽出的 Environment；租约围栏与恢复见 [`04-runner-lease-fencing.md`](04-runner-lease-fencing.md)。

## 0. 先分清“已落地”与“H1 目标形状”

| 部分 | 本 worktree 的事实 | H1-3 状态 |
|---|---|---|
| Control Plane | `phase1_service.py` 创建 Run、领取/恢复租约、持久化事件、完成 Run，并调用评分与分析。 | 继续是唯一权威；H1-3 不改变数据库所有权。 |
| Environment | Phase 1 的 `ScenarioHandler` 仍把世界和决策循环放在一起；`app.env_server` 可独立启动一个 Environment MCP Server。 | H1-2 的服务器未由当前 Runner 拉起；H1-3 负责双进程监督，但 H1-5/集成才替换旧循环并接最终可信证据。 |
| Agent-under-test | 当前默认子进程是 `python -m app.demo_agent`，由 `spawn_agent` 拉起。 | H1 计划从注册表解析 Agent；H2 外部适配器不在本章范围。 |
| Runner | 当前 `Worker::execute_claim` 只有一个被监督的 Agent 子进程；它解析 Agent stdout 的 JSONL。 | H1-3 计划先拉 Environment，等 `env_ready` 后再拉 Agent，并分离两条事实来源。 |
| 结果集成 | Phase 1 有既有 Golden/数据库/Provider 路径。 | 没有 H1-3 双进程的集成 Golden、PostgreSQL 或 live-model acceptance；组件文档不能把它们混为 H1 已交付。 |

ADR-0007、ADR-0008、ADR-0009 和 ADR-0012 是目标契约，不是对当前基线代码的描述。特别是 H1 的三个规范 v2 身份是一次计划中的不兼容切换；不做 v1 alias、迁移、重评分或历史 replay 兼容，也不在 H1-3 偷做 H2。

## 1. 谁拥有哪一种事实

一次 Run 中有三种容易混淆的东西：

- **控制事实：** Run 状态、租约、事件序列、评分、分析、Policy side effects。这些只能由 Python Control Plane 依据持久化数据决定。
- **环境事实：** Environment 实际观察到的工具调用、有限观察、预算结果以及受约束的 check/final-check 事实。H1 目标中由 Environment 的输出产生，不能由 Agent 自报。
- **Agent 信号：** Agent 的 stdout、退出码、模型报告的 usage 或 status。它们可以是诊断或受限的 Agent-reported usage，但不是成功裁决。

当前基线仍把第一类和部分第二类混在一个 Python Agent/Scenario 循环里：Runner 会把 Agent stdout 中合法的 `step_completed` / `process_output` 包成 `EventEnvelope` 上传。H1-3 要改变的是监督和事实来源，不是把 Rust 变成第二个评分器。

| 组件 | 可以做什么 | 不可以做什么 |
|---|---|---|
| Control Plane (`phase1_main.py` / `phase1_service.py`) | 读写 PostgreSQL；验证 lease；持久化有序事件；完成 Run；评分、分析和 Policy 决策。事件提交后才通知 SSE。 | 不负责子进程监督。 |
| Runner (`runner/.../agentops-runner/src/lib.rs`) | claim、启动进程、读写管道、心跳、重试事件、取消/超时清理、complete。 | 不碰 PostgreSQL；不信任 Agent/check 自报 score/status；不拥有评分规则。 |
| Environment (`app.env_server`, H1 目标) | 工具、确定性状态、种子、故障安排、预算、check；从真实 `tools/call` 捕获事实。 | 不拥有 Run 生命周期、租约或最终评分。 |
| Agent-under-test (H1 目标) | 决定调用哪个工具。 | 不写 Run、不记分、不发可信 `step_completed`。 |

H1 的成功门仍属于 Control Plane：有效提交、成功 check、必需的可信证据、注册 assertion suite aggregate 达到该 Environment 版本固定的 `1.0`，并且没有异常执行失败。Agent 退出零、EOF、ready 或 submission/check marker 单独都不能建立成功；这一完整成功契约由 H1-5/集成接线，H1-3 只负责监督和转发事实。

## 2. 当前 Phase 1 的一次 Run

下面是当前代码实际走的路径，入口也见 [`00-map.md`](00-map.md)。

1. **创建：** API 调用 `create_baseline_run`，写入 `Run(status="queued")`、不可变 `EvaluationSpec` 和 `RunnerJob(attempt=1)`。
2. **领取：** Runner `POST /api/internal/runner/jobs/claim`。Control Plane 记录 Runner presence，惰性回收已过期 lease，然后把一个 queued Run CAS 到 `claimed`，赋予 `runner_id`、随机 `lease_id` 和 15 秒 lease。
3. **拉起一个 Agent：** 当前 `Worker::spawn_agent` 按 Runner 配置执行默认 `python -m app.demo_agent`（或运维配置的 argv），把完整 `EvaluationSpec` JSON 加换行写入 stdin，随后关闭该 stdin。Unix 上 Agent 自成 process group。
4. **开始监督：** Agent 成功启动且 `run_started` 上传之后，当前 `supervise` 才设置外层 deadline。它读取 Agent stdout 的有界 JSONL；合法 child event 由 Runner 分配连续的 RunEvent `sequence` 并上传。stderr 由独立 drain 持续读取并计数，以免子进程因管道填满而阻塞；stderr 没有 live byte cap，也不变成可信 Trace 内容。stdout 做 live 行长/字节 enforcement；child 与 stderr drain 完成后才检查 combined stdout+stderr，且 final check 不覆盖 cancellation/timeout，因此 live stderr-limit gap 仍是当前 residual gap。
5. **心跳与事件：** Runner 每约 2 秒 heartbeat。Control Plane 首次 heartbeat 会把 `claimed` 推进 `running`，刷新 lease，并返回 `continue` 或 `cancel`。事件上传在 Control Plane 先落库，再通知 SSE；重复的同序列同 payload 可收敛，跳序或不同 payload 是错误。终态 Run 拒绝新的 event append 和 heartbeat，但已经接受事件的完全相同 retry 仍幂等确认，不同 type/payload 的 retry 仍是冲突。
6. **结束：** Agent EOF 后，Runner 等子进程退出；零退出映射为当前 Runner 的 `succeeded`，非零退出映射为 `agent_exit`，结构化 Provider error 优先映射为 `provider_failure`。然后 Runner 上传 terminal event，并调用 `/complete`。Control Plane 用 lease 和 Run 状态 CAS，完成评分、分析和回放/候选 Policy side effects。

这里的“当前 Runner 成功”只是 Runner 的 terminal request；它不等于 H1 目标的 Control Plane 任务成功。当前基线也没有 Environment `env_ready` 消息、双进程共享清理或 Environment-side trusted capture。

## 3. H1-3 计划中的一次 Run

下列顺序是 ADR-0008、ADR-0012 和 H1-3 任务要求的目标形状；在当前 HEAD 尚未接线。

1. **从 claim 开始计时：** 领取成功后马上开始一次 Run 的外层 wall-clock deadline，覆盖 stdin/spec 写入、Environment 启动、ASGI listener/readiness、Agent 启动、执行、drain 和 teardown；不能等 `env_ready` 或写完 stdin 才开始。
2. **启动 Environment：** Runner 以 stdin 写一行有界 `EvaluationSpec`，以 `AGENTOPS_ENV_TOKEN` 传入每个 logical Run 的 capability。每个 recovery Attempt 都重新启动 Environment 并获得新 endpoint，但同一 logical Run 继续使用 ADR-0008 规定的同一个 token；只有新的 logical Run 生成新 token，除非先修改 ADR。`app.env_server` 只消费一份 spec，绑定 loopback 的 MCP Streamable HTTP `/mcp`，真实 listener 和 lifespan 成功后才打印一次本地监督消息：

   ```json
   {"type":"env_ready","payload":{"mcp_url":"http://127.0.0.1:<port>/mcp"}}
   ```

   `env_ready` 不是 RunEvent，不得含 token，也不能仅凭 readiness 推断成功。Environment 内部从初始化起有自己的 `timeout_ms` deadline；这不能替代 Runner 的启动-inclusive 外层 deadline。
3. **再启动 Agent：** Runner 验证 `mcp_url` 是 loopback，使用同一 Run 的 capability 和 task prompt 通过允许的环境变量交给注册的 Agent。Agent 通过 MCP 调 Environment；API 请求从不提供 shell command、Provider URL、model 或 secret。具体 Agent stdin/argv 由 operator registry 决定，不应被浏览器输入改变。
4. **分流 stdout：** Environment stdout 只解析本地监督 JSONL 和 Environment-owned facts；Agent stdout 不能升级为可信 `step_completed`。两边的 stderr 都要持续 drain/count、没有 live byte cap；任意一边的日志、异常文本和 token 都不能直接写入 durable events。stdout 有 live 行长/字节 enforcement；child 与 drain 完成后检查 combined stdout+stderr，当前 final check 不覆盖 cancellation/timeout。H1-3 需要为 Agent 诊断保留受限 `process_output` 边界，而不是把任意 stdout 当成世界轨迹。
5. **Environment-side capture：** `tools/call` 的成功、invalid、unknown、over-budget、post-submit 尝试由 Environment 顺序捕获。`submit_result` 只让 Environment 执行一次 `check()` 并冻结世界；`report_usage` 是可选的 Agent-reported 累计 usage，缺失维度保持 unknown。二者都不是最终 Run status 或 score。按 ADR-0012，Control Plane 使用最新 Attempt 的持久化 all-world-attempt projection，包含 refusals/rejections、排除 reserved controls；rejection 投影为 `rejected_tool`、status `failed`，不能满足 named tool assertions。Valid-only Environment history 不能覆盖这条投影；后续只留下 additive trusted final-check evidence 与 Control Plane completion wiring 这两个 seam。
6. **健康终止与异常终止分开：** 在有效 submit、check 成功、必要可信证据捕获后，Runner 请求对健康 Environment 做计划内 teardown；共享进程组先 `SIGTERM`，共享 2 秒 grace 后仍未退出再 `SIGKILL`。这是正常生命周期，不要求 Environment 或 Agent 都自然零退出。相反，终态完成前 Environment 意外失败、Agent abnormal/nonzero、缺证据、check 失败、取消、timeout、output-limit 或 lease fencing 都不能被这条清理例外掩盖。
7. **由 Control Plane 完成：** Runner 只把带 Attempt/source 的事实和明确 terminal outcome relay 给 Control Plane。终态事件与 `/complete` 结果必须一致；Control Plane 才决定 authoritative score/status。

当前 H1-2 的输入读取、认证、工具 capture 和 deadline 细节见 [`02-environment-and-mcp.md`](02-environment-and-mcp.md)。

## 4. Deadline、取消和输出上限

| 情况 | 当前基线行为 | H1-3 / H1 集成要求 |
|---|---|---|
| 启动慢 | `spawn_agent` 和 `run_started` 上传发生在 `supervise` deadline 之前；启动尚未纳入同一个 Runner wall clock。 | 从 claim 后开始，包含 stdin、`env_ready`、Agent spawn 和 teardown；这是当前需要补的监督语义。 |
| Environment deadline | H1-2 的 `EnvironmentSession` 在初始化时按 `timeout_ms` 计算 deadline，并在 deadline 停止服务；但当前 Runner 没有启动它。 | 保留 Environment 自己的边界，同时由 Runner 监督更外层的整个 Run。 |
| 用户取消 | 当前 heartbeat 收到 `cancel` 后杀当前 Agent，terminal status 为 `cancelled`；Control Plane 的 `cancelling`/cancel intent 会在 completion 时强制 cancelled。 | 同时终止 Environment 与 Agent；取消优先于把一次部分轨迹解释成成功。 |
| timeout | 当前 Runner 到 deadline 后终止 Agent，映射 `timed_out`。 | 覆盖双进程全生命周期；timeout 优先于计划内 teardown。 |
| 输出过大 | 当前单行上限是 `MAX_LINE_BYTES = 65,536`；stdout 超过 Run 的 `max_output_bytes` 立即失败并做 live enforcement，stderr 持续 drain/count 但没有 live byte cap；child 与 drain 完成后检查 combined stdout+stderr，final check 不覆盖 cancelled/timed_out。live stderr-limit gap 仍是 residual gap。 | 双子进程都要有有界 framing、持续 drain 和明确的 total-output accounting；不能用日志成功覆盖 output-limit failure。 |
| 同时发生的失败 | 当前 `tokio::select!` 的竞争由当时哪个分支先被选中决定，最终还会做 stdout+stderr 检查。 | 批准的 H1 合约明确 cancellation、timeout、output-limit 和 lease fencing 不能被健康 teardown 或成功解释覆盖；退出边界的精确 framing 仍是 P2 测试 seam。 |

“输出字节数”不是“token 数”。当前 Environment 的 `report_usage` 也是 Agent-reported、可缺失的 usage，不是测量到的 Provider telemetry。H1-3 不应在 Rust 中发明 token 计费或第二评分器。

## 5. 生命周期/失败速查表

| 观察 | 当前基线的结果 | H1-3 文档边界 |
|---|---|---|
| `queued` 被 Runner claim | `claimed`，创建 lease；首次 heartbeat/event 时进入 `running`。 | 同样受 lease fencing 保护；双进程共享一个 Run/Attempt。 |
| Agent 输出合法 child event | Runner 给它分配 sequence、上传；CP 落库后才 SSE。 | 只有 Environment pipe 的事实可成为可信 world step；Agent stdout 不能伪造它。 |
| Agent 输出坏 JSON、坏 event、上传失败 | 当前为 `internal_failure` 并清理进程组。 | 双进程 supervision failure；不得误报成功。 |
| Agent 非零退出 | 当前为 `failed / agent_exit`；结构化 Provider error 可优先为 `provider_failure`。 | Agent abnormal/nonzero 退出不能成功，即使轨迹看似完整。 |
| Agent 零退出、EOF、仅有 submission/check marker | 当前零退出可映射 `succeeded`，EOF 本身结束读取。 | 都不是 H1 authoritative success；还需 CP 的可信证据与 assertion aggregate。 |
| 有效 submit + check 成功 + 必要证据，健康 Environment | 当前路径没有双进程该事实。 | Runner 请求计划内 teardown；TERM + 共享 2 秒 grace + KILL 不算异常 Environment failure。 |
| 终态前 Environment 意外退出 | 当前没有独立 Environment 进程可判定。 | 记录为异常执行失败；不能被 submit、EOF 或 Agent exit 0 盖过。 |
| CP 返回 `cancel` 或 lease 失效 | 当前取消会清理 Agent；lease 失效会使 heartbeat/event/complete 被拒绝。 | 同时清理两子进程；旧 lease/Attempt 绝不能写新 Attempt。 |

这张表是学习用的生命周期摘要，不是新的 wire contract。latest-Attempt persisted all-world-attempt projection（含 refusals/rejections、排除 reserved controls）已经由 ADR-0012 决定；精确的 additive trusted final-check evidence 字段和 Control Plane completion wiring 留给 H1-5/集成审查，H1-3 不在文档里预先发明其他字段。

## 6. 一个有界的事件例子

下面把“本地监督消息”和“持久化 RunEvent”分开写；它不是可复制的完整协议 fixture：

```text
Environment stdout（本地监督/事实来源，不含 token）
{"type":"env_ready","payload":{"mcp_url":"http://127.0.0.1:43127/mcp"}}
{"type":"step_completed","payload":{"index":0,"environment":{"kind":"world","result":"ok"}}}

Runner → Control Plane（未来 H1-3 需要的事件语义）
run_started  payload: {"attempt":1,"source":"environment"}
step_completed payload: {"attempt":1,"environment":{"kind":"world","result":"ok"}}
run_completed / run_failed / run_cancelled
```

- `env_ready` 不占用 durable `sequence`，也不能从 Agent stdout 接受同名消息。
- RunEvent 的 sequence 仍由 Runner 从 Control Plane 返回的 `next_sequence` 开始，连续递增。
- `source: "environment"`、trusted final-check facts 和终态 success gate 是 H1 接线的一部分；当前 Runner 的 `run_started` 只有 `attempt`，所以不能把上例写成当前已存在的 event fixture。

## 7. 已知缺口与验收边界

- H1-3 双进程 Runner、`env_ready` protocol type、共享 process group 和 Environment/Agent source separation 在当前 HEAD 尚未集成；当前 Rust 测试覆盖的是单 Agent supervision，不是 H1 acceptance。
- output accounting 目前主要是 Runner 的 stdout/stderr 字节统计：stdout 做 live 行长/字节 enforcement，stderr 持续 drain/count 但没有 live byte cap，child 与 drain 完成后才检查 combined stdout+stderr；final check 不覆盖 cancellation/timeout，live stderr-limit gap 仍是 residual gap。Environment 的 token/context 只可能是缺失或 Agent-reported，不能宣称已测量。
- Agent 退出后的 post-exit proof（Environment 是否仍健康、final-check 必需事实是否已捕获、终态是否一致）当前尚未实现；H1-3 不能把 exit zero 当证明。后续只接 additive trusted final-check evidence 与 Control Plane completion wiring。
- JSONL framing 的 **exit-boundary P2**（EOF、partial final line、terminal event 和子进程退出的边界排序）仍需独立的协议/监督测试；本章不把现有 `read_bounded_line` 解释成完整双进程证明。
- 当前没有 H1 integrated Golden、PostgreSQL-specific 或 live-model acceptance。Phase 1 的既有 CI 结果不替代这些证据。
- 不做旧 v1 alias，也不在本章加入 H2 外部 Agent 适配器；canonical v2 cutover 的 defaults/retirement 由 H1-4/H1-5/集成按 ADR-0012 处理。

继续阅读：[`04-runner-lease-fencing.md`](04-runner-lease-fencing.md)、[`ADR-0008`](../adr/0008-environment-mcp-server-and-env-side-capture.md)、[`ADR-0012`](../adr/0012-h1-canonical-cutover-and-control-plane-success.md)。
