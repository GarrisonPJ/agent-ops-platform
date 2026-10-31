# 一次 Run 的生命周期：从 claim 到终态

> 本章描述当前 H1 集成代码，不把组件测试、本地进程验证或镜像配置当作完整 H1 验收。
> 三个默认 Environment 已切到 v2；`order-support.v1` 保持不变。不做旧 ID alias、迁移或历史重评分。

## 本章的四个定位

1. **这是什么：** 一次 Run 如何排队、领取、执行、持久化并进入终态。
2. **它在一次 Run 的哪里：** 从 `create_baseline_run` 到 `complete_job`；replay 是重新执行，不是播放旧 Trace。
3. **不能破坏的边界：** Control Plane 独占 PostgreSQL、评分、分析、Run 状态和 Policy side effects；Runner 只监督与转发。
4. **下一步读什么：** [`02-environment-and-mcp.md`](02-environment-and-mcp.md) 解释工具事实，
   [`04-runner-lease-fencing.md`](04-runner-lease-fencing.md) 解释恢复与 capability。

## 1. 谁拥有事实

| 组件 | 拥有 | 不拥有 |
|---|---|---|
| Control Plane | Run/Job/Attempt、租约、事件持久化、评分、分析、终态及人工 Policy 门 | 子进程监督 |
| Runner | 注册命令解析、进程组、管道、deadline、心跳、重试和清理 | 数据库、工具规则、评分与成功裁决 |
| Environment | seed、工具、实例状态、预算、真实调用捕获与一次 check | Run 状态、租约、持久化和评分 |
| Agent-under-test | 工具决策、可选 usage 报告 | 可信 RunEvent、评分或成功声明 |

Agent stdout 即使包含合法 JSONL，也不能成为可信世界事实。`report_usage` 是可选的
Agent-reported 累计声明，不是测量；缺失字段保持 unknown。

## 2. 生产路径

1. API 创建 `Run(status=queued)`、不可变 `EvaluationSpec` 与 `RunnerJob`。
   CP 验证注册 `scenario_id` 和 `agent_id`；省略 Agent 按 execution mode 解析为 fixture/reference。
   请求只能选择 ID，不能提供 argv、Provider URL 或凭据。
2. Rust `Worker::run_forever` claim。CP 发放 15 秒 lease，返回当前 `attempt`、
   `next_sequence` 与 spec。恢复不重写已接受的序列。
3. `execute_claim` 从 operator TOML 解析 Agent，调用 `supervise_attempt`。
   外层 deadline 包含启动、spec 写入、readiness、执行和监督；不是等 Agent EOF 才计时。
   Runner 先上传 `run_started`，含 `attempt` 和 `source: "environment"`，再以 heartbeat fence
   已继承的取消或失效 lease。
4. Runner 启动 `app.env_server`，stdin 写一行 spec，以环境变量传入 capability。
   Environment 的真实 listener/lifespan ready 后输出不含 token 的本地 `env_ready`。
   Runner 严格校验 loopback URL，再启动注册 Agent，把 MCP URL、task 和同一 token 交给它。
5. Agent 通过 MCP 调工具。Environment 从真实调用产生 `step_completed`；Runner 分配全局
   contiguous sequence 与 attempt，再通过认证 API 上传。CP 先落库，再通知 SSE。
   重连使用 `after=<lastSequence>`，SSE 不是事实来源。
6. Environment stdout 有有界 JSONL framing。Agent stdout 和双边 stderr 持续 drain；
   四条流共享 live output 计数，cleanup drain 的超量也能清除 eligibility。
   Agent stdout 只计数/丢弃，不能提升为世界轨迹。心跳约每 2 秒续租并读取取消指令。
7. `submit_result` 让 Environment 只执行一次 check 并冻结。可信 final-check tuple 只有
   `scenario_id` 与实际 `final_observation`，不带 Agent 参数、expected、score 或 status。
   Controls 不计入世界步骤；拒绝的世界调用仍是步骤，但投影为 `rejected_tool/failed`，
   不能满足原工具名称断言。
8. Runner 区分意外失败与健康 Environment 的计划内 teardown：有效提交、check 成功、
   必需证据已捕获后，可请求 TERM，共享 2 秒 grace 后 KILL。清理不等于成功；
   Agent abnormal/nonzero、取消、timeout、超量、lease fencing 或 Environment 意外失败仍阻止成功。
9. Runner 只提交 `ExecutionFacts` 与完成请求，**不上传 terminal event**。
   CP 在 Job → Run 锁和 terminal CAS 下，用最新 Attempt 的持久化证据、注册断言 aggregate
   与执行失败门决定终态，同时写入一致的 terminal event、评分、分析及候选/回放效果。
10. Runner 有界接收 completion body，校验 wrapper 的 Run/Attempt、正 terminal sequence、
    内外 status 一致，再删除 per-Run capability record。HTTP 200、EOF、`eligible` 或本地
    建议 status 都不够。CP 可以权威拒绝本地 eligible Attempt；只要 wrapper 本身身份/状态一致，
    该失败终态 ACK 也允许清理。无效 ACK 保留 token，供恢复或受控处理。

旧 `supervise`/`finalize_terminal` 路径只在 `#[cfg(test)]` 下保留详细回归，不是生产 fallback。
`demo_agent` 也不是生产 Runner 的默认命令。

## 3. 成功、分数、分析是三件事

CP 成功门要求：有效提交、check 成功、充分的有效当前 Attempt 可信证据、注册 suite aggregate
达到该 Environment 版本固定的 `1.0`，且没有覆盖性的执行失败。

- 断言组合保持 `ALL=min`、`ANY=max`、`WEIGHTED=weighted mean`，每条 threshold 仍生效。
- 不用通用 `all(item.passed)` 替代注册 aggregate，也不把扣除成本/延迟后的 quality score 当成功门。
- 订单 replay 可因故障观察扣分，仍满足 outcome aggregate；恰好一次退款由最终 ledger 证明，
  不是由退款调用次数证明。
- analyzer-v3 只做诊断：显式 Agent-reported 高 context 是 Run 级证据；零世界调用 Provider
  failure 不从缺失工具断言推断 planning。它不修改断言结果、score 或终态。见 ADR-0013。

## 4. Deadline、取消和输出

| 情况 | 当前 split 行为 |
|---|---|
| 慢启动/阻塞 stdin/readiness | 纳入 startup-inclusive outer deadline；Environment 另有自身 admission deadline |
| 取消或 lease 拒绝 | 不继续执行；两子进程共享清理，旧 owner 不能写下一 Attempt |
| timeout | 映射 timed_out；不能由 submit、exit 0 或 planned teardown 覆盖 |
| 输出超限 | 四流有界计数与 post-drain 检查；Environment 单行上限 65,536 bytes |
| Agent 非零/异常退出 | 执行失败，不因世界看似完成而成功 |
| Environment 意外退出 | 与主动请求的健康 teardown 区分，不能成功 |
| completion body 无效/过大/超时 | 不把 HTTP success 当 terminal ACK，不清理 capability |

输出 bytes 不是模型 tokens；optional usage 不是 measured telemetry。`tokio::select!` 的同时就绪
不构成所有平台的完整排序证明。退出边界测试现在用 reader-consumed-prefix 与 observed-Agent-exit barrier 验证重构恰好一次、clean/fragment EOF、自然退出、超长输出及自主回收；残留半行在 planned teardown 前 fail closed。lock path 使用 no-follow/非普通文件拒绝；两个 scoped P2 复审已通过，不代表所有并发交错均获证明。

## 5. 包装、恢复与验证边界

镜像配置包含独立 MCP Agent 依赖、显式 registry、Environment/Agent workdir 和 uid 10001。
Compose 用 named `phase1-token-handoff` volume 保存 `/run/agentops/token-handoff`，不再是 tmpfs。
镜像创建 mode 0700 目录；实际标准镜像/named-volume 初始化与不同容器替换已单独验证 uid 10001、record 0600 和 ACK 后清理，不能把 Dockerfile 静态配置本身当运行证据。

本地生产 Runner/API/新建 PostgreSQL 验证覆盖了三个 v2 Golden、订单 fixture/reference + fake Provider、
Runner kill/replacement、同 capability 保留与权威 ACK 清理、旧 lease 三个写入口拒绝。
标准网络构建与 named-volume 替换另有本地验证；这些均不代表 hosted CI、cold-cache、Windows 或整个 H1 发布验收。真实模型 trace/分析由操作者明确延期，fake Provider 不代替它。

恢复门使用专用 `config/agents.recovery.toml`，API 和 Runner 配置同一组 ID/命令，显式选择
`recovery-fixture.v1`。正常 registry 仍只有 fixture/reference；不是 H2 新产品 adapter。
`AGENTOPS_AGENT_PROGRAM/ARGS` 仅为省略 wire Agent ID 的运维 break-glass，不能覆盖显式 ID；
CP 通常已把默认 Agent 解析成显式 ID，因此不能靠该覆盖变量驱动生产恢复门。

继续阅读：[`04-runner-lease-fencing.md`](04-runner-lease-fencing.md)、
[`ADR-0008`](../adr/0008-environment-mcp-server-and-env-side-capture.md)、
[`ADR-0012`](../adr/0012-h1-canonical-cutover-and-control-plane-success.md)。
