# Runner 的 Lease、Attempt 与 fencing

> 当前生产 `execute_claim` 使用 `supervise_attempt`，CP 独占 terminal event 和完成裁决。本地进程/PostgreSQL 验证与组件验收不等于整个 H1、标准镜像或 named volume 验收。

## 本章的四个定位

1. **这是什么：** 解释 Control Plane 如何用 `runner_id + lease_id + run_id` 认证和围栏一个 Runner 的写权限，并用 RunEvent 的 `attempt` 做恢复关联，以及 lease 过期后如何恢复。
2. **它在一次 Run 的哪里：** 从 `claim_job` 发放 lease，经过 heartbeat、事件持久化和 completion，直到 lease 失效、Run 终态或新的 Attempt。
3. **不能破坏的边界：** 过期 lease 不能写事件、续租或完成一个已经交给下一 Attempt 的 Run；Runner 不能用本地状态绕过 Control Plane。
4. **下一步读什么：** 先对照 [`01-run-lifecycle.md`](01-run-lifecycle.md) 的全局顺序，再读当前实现 [`phase1_service.py`](../../backend/app/phase1_service.py) 的 `claim_job`、`heartbeat`、`persist_events`、`complete_job` 和 Rust [`lib.rs`](../../runner/crates/agentops-runner/src/lib.rs) 的 `supervise_attempt` / `complete_request`。

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
complete: lease_id from URL + runner_id + execution.run_id/attempt
```

Runner 上传的非终态 RunEvent payload 带当前 `attempt`，terminal event 由 CP 生成。**attempt 不是认证凭证**；completion 的 `ExecutionFacts` 必须精确匹配 Run/Attempt，heartbeat 仍不要求该字段。写权限来自 CP 当前数据库中的 lease/runner identity；event 还要验证原始 provenance/attempt，再做规范化与序列检查。RunEvent 的 sequence 也不是 lease：它负责同一 Run 内的有序、连续和幂等，而 lease 负责“谁现在可以写”。

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

生产入口是 [`lib.rs`](../../runner/crates/agentops-runner/src/lib.rs) 的 `execute_claim` → `supervise_attempt`：

- operator TOML 解析 Agent argv，不接受 API 命令；显式 ID 优先，unknown 不 fallback；
- Environment/Agent stdin 都只写一行有界 spec，写入和 readiness 纳入外层 deadline；
- Environment stdout 逐行解析，单行最多 65,536 bytes；Agent stdout 不成为可信事件；
- 四条 stdout/stderr 持续 drain，共享 live bounded output total，cleanup 后再检查 overflow；
- Unix 上 Environment leader 与 Agent 加入共享进程组，TERM → 共享两秒 grace → KILL，随后 wait 回收。

旧 `spawn_agent`、`supervise`、`drain_stderr` 的耦合实现仅在 `#[cfg(test)]` 下保留历史回归，
不能把它的 stderr-limit 缺口写成当前生产行为，也不能因保留测试而恢复该 fallback。

### 当前双进程顺序

```text
claim → start outer deadline
  → spawn Environment, write one spec/token line
  → read one env_ready (real listener/lifespan ready)
  → spawn registered Agent with MCP URL/token/task
  → drain both stderr and separate stdout sources
  → relay only Environment-owned facts
  → cancel/timeout/lease failure: terminate shared process group
```

Environment 与 Agent 必须属于同一个 shared process group，取消或 timeout 时一起收到 `SIGTERM`，共享 2 秒 grace 到期再一起 `SIGKILL`。`supervise_attempt` 已用于生产 claim/completion，并有本地实际进程验证；这些证据仍不代表整个 H1 或跨平台验收。

Environment 的 stdin 读取本身只需要一行 spec，并不要求 Runner 关闭 stdin 才返回；Runner 仍必须监督写入过程与启动 deadline。Agent 的现有 stdin 写入则在写完后关闭。无论哪一侧，启动/写入/读取都不得无限等待。

## 5. Deadline 与失败 precedence

### Deadline 必须覆盖 startup

生产 `supervise_attempt` 在开始监督时建立 outer deadline，先于 spec 写入、启动和 readiness。
与仅测试用的旧 `supervise` 不同，它覆盖：

- Environment/Agent 的 spawn；
- stdin/spec 写入与 readiness；
- Environment listener/lifespan；
- Agent 执行、stdout/stderr drain；
- 输出与终止监督；清理有共享两秒 grace，completion 请求另有有界接收 deadline，不是无限等待。

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
| Environment 行过长或四流 live/post-drain output 超限 | `failed / output_limit_exceeded` | 字节超量不能提升为成功；usage 与 output bytes 分开。 |
| Agent 非零退出 | `failed / agent_exit` | 不因世界轨迹完整而成功；Agent stdout 不能提供可信 Provider/world 事实。 |
| lease 过期/不属于 runner | API 拒绝 heartbeat/event/complete | 不是“再试一次同 lease”；等待下一 Attempt recovery。 |
| healthy Environment planned teardown | 直接 Environment leader signal 与共享组清理 | 是清理动作，不是 authoritative success；不能覆盖失败门。 |

当前 `tokio::select!` 的 EOF、heartbeat、deadline、child exit 与 output-limit 可能同时就绪；局部竞争顺序不是跨平台完整 precedence proof。closeout 用 reader-side prefix-consumed 与 supervisor-observed Agent exit 两个 barrier 后才释放 suffix，验证重构事件恰好一次与连续序列。clean/fragment EOF、自然退出、超长尾行和已 submit 后残留半行均有独立反例；残留 partial 在 planned teardown 前 fail closed。测试还在兜底清理前快照两 child 的 `/proc` 消失，防止测试自己杀进程掩盖 Runner 泄漏。这证明命名边界，不发明统一失败优先级或让 Runner 发 terminal event。

## 6. Environment/Agent source separation

H1-3 需要特别防止一个看似合理但错误的 shortcut：让 Agent 自己输出一行漂亮的 `step_completed`，再把它当作世界事实。正确的来源是：

```text
Agent -- MCP tools/call --> Environment
                           │
                           └─ middleware 观察真实调用并输出 Environment-owned JSONL
Environment stdout ────────> Runner ──(lease + sequence)──> Control Plane
Agent stdout ──────────────> 诊断/受限 relay；不升级为 trusted world fact
```

`env_ready` 只是本地监督消息，不是 RunEvent。它不占 durable sequence、不能含 raw token、不能建立成功。Environment 的 `submit_result` 会使世界只执行一次 `check()` 并冻结；`report_usage` 的值是 Agent-reported，null/省略仍是 unknown。Reserved controls 不进入世界轨迹。Control Plane 已按 ADR-0012 决定：评分使用最新 Attempt 的持久化 all-world-attempt projection，包含 refusals/rejections，排除 reserved controls；rejection 投影为 `rejected_tool`、status `failed`，不能满足 named tool assertions。Valid-only Environment history 不能覆盖这条投影。可信 final-check tuple 已接入 CP 投影，Rust 不自行评分。

每个 recovery Attempt 都应获得新的 Environment 和 endpoint，但同一 logical Run 按 ADR-0008 只生成一个 capability token，并由该 Run 的 recovery Attempts 继续使用；只有新的 logical Run 才生成新 token，除非先修改 ADR。`supervise_attempt` 即使返回 `eligible` 也必须保留 protected handoff record；eligibility 不是 Control Plane terminal completion acknowledgment。只有已经获得该权威确认的调用方才可调用 `cleanup_environment_token_after_terminal_ack`；该 API 在 per-run lock 内校验并删除 record，缺失时幂等 no-op，不创建新 token，也不自行验证或推断远端确认。当前 Compose 使用 named `phase1-token-handoff` volume；镜像内目录 uid 10001、mode 0700，已有不安全目录 fail closed。实际标准镜像/named-volume 新建与不同 Runner 容器替换已验证 uid 10001、0700/0600、相同 capability digest 和 ACK 后删除；这与宿主机测试及离线派生镜像证据分开。terminal ACK 前保留 record，ACK 后才清理。相同 seed/spec 不意味着可以把旧 Attempt 的事实接到新 Attempt。RunEvent 的 durable `attempt` 与 `run_started` marker 让 Control Plane 能选择最新 Attempt；生产 marker 同时含 `attempt` 与 `source: "environment"`。

## 7. 健康 teardown 与 unexpected failure 的分界

这个分界比“某个子进程有没有 exit 0”更精确：

| Environment 状态 | Agent 状态 | 结论 |
|---|---|---|
| 已有效 submit、check 成功、必需证据已捕获，Environment 健康，Runner 请求结束 | Agent 可自然退出或被共享组 TERM/KILL | H1 计划内 teardown；不把 Environment 清理本身当异常。CP 仍独立裁决 success。 |
| 尚未满足上述条件就 Environment EOF/崩溃/启动失败 | 任意 | unexpected Environment failure，不能成功。 |
| Environment 健康但 Agent nonzero/abnormal | 任意 | Agent abnormal failure，不能成功。 |
| 取消、timeout、output-limit 或 lease 已失效 | 任意 | 对应失败/取消路径优先；planned teardown 例外不适用。 |
| 只有 Agent 自报 score/status、exit 0、EOF、ready 或 check marker | 任意 | 证据不足；不是 authoritative success。 |

生产路径在 Agent exit 后继续有界读取与 heartbeat/upload 监督，并在直接向 Environment leader 发 teardown signal 前检查失败门。CP 从持久化 trusted final-check 和 ExecutionFacts 独立裁决；Runner 有界验证身份/状态一致的 completion wrapper 后才清理 token，HTTP 200 或本地 eligible 都不够。

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

- `supervise_attempt` 已用于生产 dispatch；组件与本地真实进程验证涵盖 post-Agent-exit、fencing、final-check 和 ACK 清理，但不等于整个 H1 验收。
- 四流 live bounded total 及 post-drain overflow 只统计 bytes，不是 Provider token accounting；usage 缺失保持 unknown，Agent 报告不是 measured truth。
- Host recovery 验证旧 lease 三入口拒绝；实际标准 Runner 镜像/named-volume 的不同容器替换另行验证相同 capability、uid 10001、0700/0600 与有效 ACK 后删除。两类证据不混用。
- exit-boundary 与 lock-path 两个 P2 已修复并经 scoped 独立复审。具体用例在 `exit_boundary_tests.rs` 与 `lib.rs` lock tests；测试不穷举所有调度交错。
- `open_locked_file` 对 capacity/per-Run locks 使用 `O_NOFOLLOW | O_NONBLOCK`，校验打开 fd 的 regular-file/owner/mode，拒绝 symlink、dangling、FIFO/device，不截断目标。可信目录仍是边界，不隔离恶意同用户进程。
- CI 保留 Golden、recovery、PostgreSQL/calibration gates，只用 deterministic fixture/fake-provider。恢复门显式选择专用 registry 的 `recovery-fixture.v1`，不靠无效的 override 覆盖 CP 显式 Agent ID。
- 不写 v1 alias、不启动 H2、不访问或重评分历史数据。标准镜像/volume、host、hosted CI、cold-cache、完整 exact-payload 各自是独立声明；真实 llama.cpp + PostgreSQL 的 H1-5 验收已另行完成，实际成功/失败见 [记录](../history/h1-live-model-acceptance-2026-11-01.md)。历史 Runner daemon bounded stop exit 137 不是 child-group cleanup 失败证据，也不能声称优雅 daemon shutdown。

继续阅读：[`01-run-lifecycle.md`](01-run-lifecycle.md)、[`02-environment-and-mcp.md`](02-environment-and-mcp.md)、[`ADR-0002`](../adr/0002-runner-recovery.md)、[`ADR-0008`](../adr/0008-environment-mcp-server-and-env-side-capture.md)。
