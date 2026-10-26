# Runner 的 Lease、Attempt 与 fencing

> **状态：Draft / H1-3 组件交接。** 代码引用以本 worktree 当前 HEAD 为准；H1-3 双进程监督仍是计划中的接线，不是已发布功能，也不声称 H1 acceptance。

## 本章的四个定位

1. **这是什么：** 解释 Control Plane 如何用 `runner_id + lease_id + run_id` 认证和围栏一个 Runner 的写权限，并用 RunEvent 的 `attempt` 做恢复关联，以及 lease 过期后如何恢复。
2. **它在一次 Run 的哪里：** 从 `claim_job` 发放 lease，经过 heartbeat、事件持久化和 completion，直到 lease 失效、Run 终态或新的 Attempt。
3. **不能破坏的边界：** 过期 lease 不能写事件、续租或完成一个已经交给下一 Attempt 的 Run；Runner 不能用本地状态绕过 Control Plane。
4. **下一步读什么：** 先对照 [`01-run-lifecycle.md`](01-run-lifecycle.md) 的全局顺序，再读当前实现 [`phase1_service.py`](../../backend/app/phase1_service.py) 的 `claim_job`、`heartbeat`、`persist_events`、`complete_job` 和 Rust [`lib.rs`](../../runner/crates/agentops-runner/src/lib.rs) 的 `supervise` / `terminate_child`。

## 1. Lease 不是“拥有 Run”，而是有期限的写能力

`RunnerJob` 与 `Run` 是 Control Plane 的持久化事实。当前 Python 代码在 `claim_job` 中：

1. 记录 Runner presence；
2. 惰性扫描 `claimed`、`running`、`cancelling` 且 `lease_expires_at <= now` 的 job；
3. 从最早排队的 Run 中用数据库条件更新把 `queued` 变成 `claimed`；
4. 写入新的随机 `lease_id`、`runner_id` 和 `lease_expires_at = now + 15s`；
5. 返回 `attempt`、`next_sequence`、`recovery_reason` 与 `EvaluationSpec`。

不能把所有 API 写成必须携带 `attempt`。更准确地说：

```text
heartbeat: lease_id from URL + runner_id from authenticated body
event: run_id from URL + lease_id + runner_id
complete: lease_id from URL + runner_id
```

Runner 生成的 RunEvent payload 会带当前 `attempt` 用于关联（包括 `run_started`、child event 和 terminal event）；但 **attempt 不是认证凭证，也不是每个 API write 当前都要求的字段**。heartbeat、event 和 completion 的认证来自 Control Plane 当前数据库中的 lease/runner identity（event 还要通过 run、sequence 和 payload 校验）。RunEvent 的 sequence 也不是 lease：它负责同一 Run 内的有序、连续和幂等，而 lease 负责“谁现在可以写”。

数据库状态的权威关系如下：

```text
queued → claimed → running
queued → cancelled
claimed | running → cancelling → cancelled
claimed | running → succeeded | failed | cancelled | timed_out
```

终态 Run 会拒绝新的 event append 和 heartbeat；但对已经接受事件的完全相同 retry，仍会幂等确认，不会因为终态改成普通拒绝；同一 sequence 的冲突 retry 仍返回 `EVENT_CONFLICT`。API 路由在 [`phase1_main.py`](../../backend/app/phase1_main.py) 中只做认证、解析和调用；状态、锁和 fencing 仍在 `phase1_service.py`。

### 当前常量与边界

- 单次 lease：15 秒；heartbeat 每次刷新另一个 15 秒窗口。
- Runner availability 观察窗：15 秒；这是运维状态，不等于某个 Run 的 lease。
- 最多 3 个 Attempt。过期恢复发生在下一次认证 Runner claim 时，不是另起一个 scheduler。
- `next_sequence` 从当前 Run 的最大已落库序列加一开始；恢复不重写已经接受的事件。

## 2. 每个写入口如何 fence

### Heartbeat

`heartbeat` 通过 `_leased_job(..., for_update=True)` 锁住 job，并要求：

- `lease_id` 存在；
- `runner_id` 与 job 相同；
- lease 尚未过期；
- Run 尚未 terminal。

第一次 heartbeat 可以把 `claimed` CAS 到 `running`；之后刷新 expiry。若 Run 已是 `cancelling`，或 job 有 `cancel_requested_at`，返回 `cancel`，Runner 随即清理子进程并以 cancelled 结束。lease 不属于该 Runner、已过期或 Run 已 terminal 时，Control Plane 返回结构化拒绝；Runner 不能把它解释成 continue。

### Event upload

`persist_events` 先校验事件目标 Run、序列递增和 payload 的有界规范化，然后在插入前重新取得并锁住 Job。锁顺序是 Job → Run；这样 lease recovery 不能在事件事务提交中间清空 lease。它同时保证：

- 同序列、同类型、同规范化 payload 的重试可以收敛；
- 不同 payload 是 `EVENT_CONFLICT`；
- 跳过下一个序列是 `EVENT_SEQUENCE_GAP`；
- lease 不对或过期时不能插入新事件；终态 Run 拒绝新的 event append，但已经接受事件的相同 retry 仍幂等确认，不同 type/payload 的 retry 仍是 `EVENT_CONFLICT`；
- 事务提交后才发布给 SSE，SSE 不是事实来源。

因此“我在本地已经读到一行 JSONL”不等于“这行已成为 RunEvent”。只有 Control Plane 接受并落库，它才进入 durable Trace。

### Completion

`complete_job` 在 Job 锁内先确认 lease 身份，再显式检查 expiry；`allow_expired=True` 只允许它在已锁定的路径上读取并返回明确的 `LEASE_EXPIRED`，不是允许过期 lease 完成 Run。它用 `WHERE Run.status == observed_status` 做 terminal CAS：竞争完成者只有一个能推进状态，失败者重读或看到 terminal；终态评分、分析和 Policy side effects 只由胜者运行。

取消 intent 具有更高的领域优先级：如果观察到 `cancelling` 或 `cancel_requested_at`，即使 Runner 请求 `succeeded`，effective status 也会被强制为 `cancelled`。这不是 Runner 的本地判断，而是 Control Plane 的终态裁决。

## 3. Lease 过期后的 Attempt recovery

`_recover_expired_jobs` 在下一次 authenticated `claim` 的同一事务路径中工作：

```text
Attempt 1: claimed/running/cancelling + lease expired
        ↓
record RunnerAttempt(history)
clear old lease_id/runner_id/expiry
        ↓ (attempt < 3)
Run → queued, job.attempt += 1
        ↓
next claim → fresh lease, next_sequence, new Attempt marker
```

如果已经达到第 3 个 Attempt：

- 没有取消 intent → Run `failed`；
- 有取消 intent → Run `cancelled`；
- 记录 terminal recovery error 和普通 terminal effects，不再发放第 4 个 Attempt。

恢复时保留 Run identity 和既有事件序列，清掉旧 lease，递增 attempt，并为旧 lease 写入 `RunnerAttempt` 历史。新的 Runner 从 `next_sequence` 继续，不能覆盖已接受的事件。分析器从最新 `run_started` marker 开始重建最新 Attempt，旧 Attempt 仍可审计但不重复计分。

### 一个 stale Runner 为什么写不进去

假设 Runner A 的 lease 过期，Runner B claim 触发 recovery：

1. A 手里仍有旧 `lease_id=A`，但数据库已清空它；
2. B 获得新 `lease_id=B`、新 attempt 和新的 expiry；
3. A 的 heartbeat、event 或 complete 进入 `_leased_job` 时找不到属于 A 的当前 lease，得到 `INVALID_LEASE` 或 `LEASE_EXPIRED`；
4. A 不能凭旧 attempt、旧 sequence 或本地 retry 绕过检查；B 才能继续写新 Attempt。

这就是 fencing：恢复不是“让两个 Runner 都继续”，而是让旧 owner 的所有后续写入失效。

## 4. Runner 侧监督：stdin、stdout、stderr 与 process group

当前 Rust Runner 入口在 [`runner/crates/agentops-runner/src/lib.rs`](../../runner/crates/agentops-runner/src/lib.rs)：

- `spawn_agent` 按受信 Runner 配置的 program/args 启动子进程，不接受 API 请求传入的命令；
- stdin 是 piped，完整 spec 序列化为一行 JSON，写入 newline 后 `shutdown`；写入失败会清理子进程并返回内部失败；
- stdout 被 `BufReader` 读取，每行最多 `MAX_LINE_BYTES = 65,536`，坏 JSON/坏 child event 不能变成 durable 事实；
- stderr 由独立 `drain_stderr` 任务持续读取，并按实际读取字节累计；它没有 live byte cap，也不进入 event payload；
- Unix 上 `pre_exec` 让 Agent 自己成为 process group leader；`terminate_child` 对 group 发 `SIGTERM`，等待共享的 2 秒 grace，仍未退出再发 `SIGKILL`，最后 wait 回收 child。

`stderr` drain 是正确性约束，不只是日志美化：如果没人读 stderr，Agent 的 pipe buffer 可能满，Agent 停在写日志上，Runner 就可能既读不到 stdout 又无法让它自然退出。当前 stdout 对 JSONL 行长和 stdout 字节数做 live enforcement；stderr 则持续 drain/count、没有 live byte cap。子进程和 drain 都完成后，Runner 才检查 combined stdout+stderr 字节数；这个 final check 不会覆盖已经确定的 cancellation/timeout，因此 stderr 超量不能在写入过程中及时触发 output-limit，是当前仍需明确的 live stderr-limit residual gap。

### H1-3 的双进程变化

当前 HEAD 只有一个 Runner-supervised Agent。H1-3 计划把监督顺序变为：

```text
claim → start outer deadline
  → spawn Environment, write one spec/token line
  → read one env_ready (real listener/lifespan ready)
  → spawn registered Agent with MCP URL/token/task
  → drain both stderr and separate stdout sources
  → relay only Environment-owned facts
  → cancel/timeout/lease failure: terminate shared process group
```

Environment 与 Agent 必须属于同一个 shared process group，取消或 timeout 时一起收到 `SIGTERM`，共享 2 秒 grace 到期再一起 `SIGKILL`。这条双进程实现、`env_ready` protocol message 和假子进程测试在当前 HEAD 还没有；不要把当前 `terminate_child` 的单 Agent 语义写成已交付 H1。

Environment 的 stdin 读取本身只需要一行 spec，并不要求 Runner 关闭 stdin 才返回；Runner 仍必须监督写入过程与启动 deadline。Agent 的现有 stdin 写入则在写完后关闭。无论哪一侧，启动/写入/读取都不得无限等待。

## 5. Deadline 与失败 precedence

### Deadline 必须覆盖 startup

当前 `Worker::supervise` 在 Agent 已 spawn、`run_started` 已上传后才以 `Instant::now() + timeout_ms` 设 deadline。因此当前基线的 startup、写 stdin、首个 event upload 并没有完全落在同一个外层 wall-clock budget 内。

H1-3 的修复方向是：在 claim 后立即建立 Run 的 outer deadline，覆盖：

- Environment/Agent 的 spawn；
- stdin/spec 写入与 readiness；
- Environment listener/lifespan；
- Agent 执行、stdout/stderr drain；
- 终止和 completion relay 的监督窗口。

`EnvironmentSession` 同时从初始化计算自己的 `timeout_ms` deadline，并在超时后停止服务。这是 Environment 内部边界，不替代 Runner outer deadline；两者都不能等 Agent 自然 EOF 才开始计时。

### 不要让 planned teardown 盖掉失败

ADR-0012 的规则是“失败门先于健康清理例外”：

- cancellation；
- outer timeout 或 Environment timeout；
- output-limit failure；
- lease fencing / completion rejected；
- Agent abnormal/nonzero exit；
- 终态完成前 Environment unexpected failure；
- 缺失或无效的必需可信 evidence、check 失败或无有效 submit。

只有在有效 submit、成功 check、必要证据捕获且 Environment 仍健康时，Runner 请求的 Environment teardown 才可标记为**计划内生命周期**，不算异常 Environment failure。即便如此，它也不使 Agent/check 自报的 score/status 变成权威；Control Plane 仍必须独立检查注册 assertion suite aggregate >= 1.0。

当前实现的可见映射如下：

| 触发 | 当前 Rust 的主要结果 | fencing/优先级含义 |
|---|---|---|
| heartbeat 返回 `cancel` | `cancelled`，TERM → 2 秒 → KILL | CP 的 `cancelling`/cancel intent 在 completion 强制 cancelled。 |
| outer deadline 到期 | `timed_out` | 不能被 EOF、exit 0 或 planned teardown 解释成成功。 |
| stdout 行过长或 stdout live output 超限；child/drain 完成后 combined output 超限 | `failed / output_limit_exceeded` | stdout 有 live 行长/字节 enforcement；stderr 持续 drain/count 但无 live byte cap。child 与 drain 完成后检查 stdout+stderr 合计；final check 不覆盖 `cancelled`/`timed_out`，所以 live stderr-limit 仍是 residual gap。 |
| Agent 非零退出 | `failed / agent_exit` | 当前结构化 Provider failure signal 可优先映射 `provider_failure`；H1 成功门仍不接受 Agent abnormal。 |
| lease 过期/不属于 runner | API 拒绝 heartbeat/event/complete | 不是“再试一次同 lease”；等待下一 Attempt recovery。 |
| healthy Environment planned teardown | 当前不存在双进程路径 | H1 目标中是清理动作，不是 failure；但不能覆盖上面任一失败门。 |

当前 `tokio::select!` 中 EOF、heartbeat、deadline、child exit 和 output-limit 可能在边界同时就绪；当前代码的局部竞争顺序不能当作跨平台的完整 precedence proof。**exit-boundary framing P2** 仍需明确 partial final line、terminal event、child exit 和 teardown 的排序。H1 文档能确定的是：失败门不得被 planned teardown 盖掉，旧 lease 不得完成；不能替实现凭空指定尚未审查的 wire 字段或新的失败排序。

## 6. Environment/Agent source separation

H1-3 需要特别防止一个看似合理但错误的 shortcut：让 Agent 自己输出一行漂亮的 `step_completed`，再把它当作世界事实。正确的来源是：

```text
Agent -- MCP tools/call --> Environment
                           │
                           └─ middleware 观察真实调用并输出 Environment-owned JSONL
Environment stdout ────────> Runner ──(lease + sequence)──> Control Plane
Agent stdout ──────────────> 诊断/受限 relay；不升级为 trusted world fact
```

`env_ready` 只是本地监督消息，不是 RunEvent。它不占 durable sequence、不能含 raw token、不能建立成功。Environment 的 `submit_result` 会使世界只执行一次 `check()` 并冻结；`report_usage` 的值是 Agent-reported，null/省略仍是 unknown。Reserved controls 不进入世界轨迹。Control Plane 已按 ADR-0012 决定：评分使用最新 Attempt 的持久化 all-world-attempt projection，包含 refusals/rejections，排除 reserved controls；rejection 投影为 `rejected_tool`、status `failed`，不能满足 named tool assertions。Valid-only Environment history 不能覆盖这条投影；后续只留下 additive trusted final-check evidence 与 Control Plane completion wiring 这两个 H1-5/集成 seam，Rust 不自行评分。

每个 recovery Attempt 都应获得新的 Environment 和 endpoint，但同一 logical Run 按 ADR-0008 只生成一个 capability token，并由该 Run 的 recovery Attempts 继续使用；只有新的 logical Run 才生成新 token，除非先修改 ADR。相同 seed/spec 不意味着可以把旧 Attempt 的事实接到新 Attempt。RunEvent 的 durable `attempt` 与 `run_started` marker 让 Control Plane 能选择最新 Attempt；当前 Runner 的 marker 还只有 `attempt`，所以 H1 的 `source: "environment"` 尚未是现有代码事实。

## 7. 健康 teardown 与 unexpected failure 的分界

这个分界比“某个子进程有没有 exit 0”更精确：

| Environment 状态 | Agent 状态 | 结论 |
|---|---|---|
| 已有效 submit、check 成功、必需证据已捕获，Environment 健康，Runner 请求结束 | Agent 可自然退出或被共享组 TERM/KILL | H1 计划内 teardown；不把 Environment 清理本身当异常。CP 仍独立裁决 success。 |
| 尚未满足上述条件就 Environment EOF/崩溃/启动失败 | 任意 | unexpected Environment failure，不能成功。 |
| Environment 健康但 Agent nonzero/abnormal | 任意 | Agent abnormal failure，不能成功。 |
| 取消、timeout、output-limit 或 lease 已失效 | 任意 | 对应失败/取消路径优先；planned teardown 例外不适用。 |
| 只有 Agent 自报 score/status、exit 0、EOF、ready 或 check marker | 任意 | 证据不足；不是 authoritative success。 |

当前 HEAD 没有可证明“Agent exit 之后 Environment 健康且最终 evidence 已完整捕获”的 post-Agent-exit proof。H1-3 不能通过一个 `wait()` 或 exit code 补造该证明；后续 seam 仅是 additive trusted final-check evidence 与 Control Plane completion wiring。

## 8. 学习用 recovery 例子

设 Run 的 sequence 目前到 4，Attempt 1 的 lease 已过期：

```text
旧 Attempt 1:
  run_started(seq=1, attempt=1)
  ... seq=4
  lease=A expires

下一次 claim（Control Plane）:
  RunnerAttempt(attempt=1, outcome=recovered)
  Run=queued; job.attempt=2
  lease_id/runner_id/expiry 清空

新 Attempt 2:
  claim -> lease=B, next_sequence=5
  run_started(seq=5, attempt=2)
  ...
```

- Attempt 1 的 seq=1..4 仍是审计历史；新的评分轨迹从 seq=5 的最新 marker 起算。
- Attempt 1 如果之后重试 seq=5，API 不会把它当成合法 owner；它会被 `INVALID_LEASE` / `LEASE_EXPIRED` 挡住。
- Attempt 2 同样必须按 contiguous sequence 上传；不能从 1 重播或改写旧事件。
- 若 Attempt 3 也过期，下一次 claim 不再重试，Control Plane 依据 cancel intent 选择 failed 或 cancelled。

## 9. 已知缺口与测试边界

- 本章的 lease/attempt/fencing 描述来自当前 Phase 1 Control Plane，已实现；H1-3 的双进程 supervision、shared process group、Environment stdout source separation 和 `env_ready` 接线尚未在当前 HEAD 集成。
- 当前 output accounting 是 stdout/stderr 字节统计，不是 token accounting：stdout 对行长和 stdout 字节数做 live enforcement；stderr 持续 drain/count 但没有 live byte cap；child 与 stderr drain 完成后才检查 combined stdout+stderr，且 final check 不覆盖 cancellation/timeout。live stderr-limit gap 仍是 residual gap。Environment usage 缺失时保持 unknown，Agent 报告也不是 measured truth。
- post-exit proof 尚未实现；不能声称 Agent exit 之后 Environment final-check、trusted evidence 与 CP completion 已一致。
- JSONL framing 的 exit-boundary P2 仍未封闭，尤其是 partial final line、EOF 与 terminal event 的先后；`read_bounded_line` 的有界性不等于完整终态证明。
- H1 integrated Golden、PostgreSQL-specific validation、live-model/reference-agent acceptance 尚未由 H1-3 组件文档提供；当前单 Agent Rust tests 不能替代这些验收。
- 不写 v1 alias，不把 H2 外部 Agent registry/adapter 扩进 H1-3；canonical v2 defaults 和旧 v1 execution retirement 由 H1-4/H1-5/集成按 ADR-0012 处理。

继续阅读：[`01-run-lifecycle.md`](01-run-lifecycle.md)、[`02-environment-and-mcp.md`](02-environment-and-mcp.md)、[`ADR-0002`](../adr/0002-runner-recovery.md)、[`ADR-0008`](../adr/0008-environment-mcp-server-and-env-side-capture.md)。
