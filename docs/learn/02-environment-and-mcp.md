# Environment 与 MCP：一个 Run 的工具边界

当前生产 `execute_claim` 使用独立的 `app.env_server` 与注册 fixture/reference Agent，
通过 MCP 调工具、由 Environment 捕获事实、CP 持久化并独占评分/终态。镜像配置包含
MCP 2.3/httpx2 closure 和 operator registry；配置不等于运行证据。Host/PostgreSQL
闭环、标准 Runner 网络构建与实际 named-volume 替换均有分开的本地验证；最终
exact-payload 审查与本地交付验证保持各自边界；H1-5 已完成真实 llama.cpp 直连 trace/分析（research/order 成功，checkout/fault 失败保留），不是模型准确率证明。三个默认世界使用 v2，旧三个 v1 execution ID 不提供兼容。

## 从输入到 ready

入口在 [`backend/app/env_server.py`](../../backend/app/env_server.py) 的 `main`：

1. Runner 通过 stdin 写一行 `EvaluationSpec` JSON（UTF-8，最多 65,536 bytes，
   包含换行），不需要关闭 stdin。这里只消费一个 spec，不提供文件、网络或命令输入。
2. `AGENTOPS_ENV_TOKEN` 是独立的 bearer capability，只通过环境变量传递，
   不放在 URL、spec 或 ready 消息中。边界要求 32..512 个 printable non-space ASCII
   字符；Rust Runner 用 32 bytes CSPRNG 生成 64 个 hex 字符。受控 Python 捕获用
   `secrets.token_hex(32)`，都不使用可预测 UUID 代替 capability。
3. `EnvironmentSession` 只调用一次 `create_environment(spec)`；factory 已执行
   `reset(spec.seed)`。世界不缓存，不随 `execution_mode` 或 policy 切换行为。
4. 使用官方 `mcp>=2.3.0,<2.4.0` 的 `MCPServer` 和 Streamable HTTP app。Runner
镜像用 `runner-requirements.lock` 固定 `mcp==2.3.0`、`httpx2==2.16.0` 及其完整 Linux
闭包；`httpx==0.28.1` 只供低层 OpenAI-compatible Provider 使用。
   Uvicorn 只绑定 `127.0.0.1:0`，路径 `/mcp`，不启动 OAuth、stdio MCP 或
   legacy HTTP+SSE transport。Streamable HTTP 自己的 streaming 能力不是旧 SSE transport。
5. `ReadyServer.startup` 等 ASGI lifespan 和 listener 成功，才向 stdout 写一次：

   ```json
   {"type":"env_ready","payload":{"mcp_url":"http://127.0.0.1:<port>/mcp"}}
   ```

   这是本地监督消息，不是 RunEvent。启动失败不打印 ready。stdout 只用于监督 JSONL；
   库日志禁用，其他 Python stdout 重定向到 stderr。异常只输出固定错误文本，不输出输入。

HTTP 最外层 `BearerCapability` 用 constant-time comparison 检查每个请求；缺失、错误或
重复 Authorization 返回 401，query string 返回 400，不接受 URL token。SDK 保留
Host/Origin 检查，非 loopback Host/Origin 被拒绝；单个 body 最多 16,384 bytes。
这是可信宿主机上的 capability 隔离，不是对同用户恶意进程的 sandbox。

## 工具与一次捕获

`EnvironmentMCPServer.list_tools` 列出 `Environment.tool_definitions()`，末尾再加
`SUBMIT_TOOL`（`submit_result`，空参数 schema）。外部 Agent 只能调用 discovery 里出现的工具，
所以 [ADR-0008](../adr/0008-environment-mcp-server-and-env-side-capture.md) H2 修订让它可见；
`report_usage` 仍不出现在 discovery 中，免得模型凭空编造 usage。`MCPAgentClient` 要求恰好一个
空参数 `submit_result`、拒绝出现 `report_usage`，并继续使用自己的 control 定义，因此
fixture/reference Agent 交给模型的工具集不变。两个 reserved controls 都由认证后的 MCP
`tools/call` 调用：

- `submit_result`：arguments 必须为空对象。世界只执行一次 `check()` 并冻结；
  不把 check 的 evaluation、score、expected values 或 Run status 持久化为裁决。
  重复提交返回 `duplicate_submit`，后续世界调用返回 `finalized`，都不再改变世界。
- `report_usage`：可选的 Agent-reported 累计 totals。字段是 `token_prompt`、
  `token_completion`（各 0..1,000,000,000）和 `context_window`（`used`/`limit`
  各 0..10,000,000）。省略或 null 表示 unknown；空对象是有效的 unknown report。
  最新有效 report 替换先前 report，不相加；无效 report 不覆盖。它不是测量得到的 provider telemetry。

[ADR-0013](../adr/0013-h1-analyzer-v3-canonical-diagnostics.md) 将共享诊断分析器升为
`failure-analyzer.v3`：显式 Agent-reported context 超过 95% 时，证据属于整个 Run，
`step_index=null`，details 保留 `source=agent_reported`，不能伪装成每步测量。低值、
缺失和无效报告不产生该正信号；最新有效报告替换与 Attempt 隔离规则不变。
零世界调用且有 Provider error 时，不从缺失工具断言推断 planning；执行诊断和
实际断言失败仍保留。评分和终态门不变，校准必须生成新 v3 包，不回写历史证据。

保留名或重复世界工具名会使启动失败。预算是 `policy.max_steps` 或 6。世界调用尝试
（有效、invalid、unknown、over-budget、post-submit）都记账；controls 不消耗世界预算，
因此预算耗尽后仍可提交。超预算调用返回错误而不调用 `Environment.call`。
世界 admission、mutation、usage 和 check 在同一个 async lock 内串行，避免竞争超额。
Environment 从初始化起执行 `timeout_ms` admission deadline，并在 deadline 停止服务；
Runner 仍须监督包括 startup 在内的外层 wall-clock timeout。

`EnvironmentSession.capture` 使用 SDK 2.3 的 provisional、pre-validation middleware
拦截所有 `tools/call`。每次只输出一条 `step_completed`，包括 unknown 或非法 arguments。
不信任 Agent 的 `_meta`、事件、decision summary、score、expected 或 status。
世界工具名/arguments 经过已有 durable allowlist；unknown 原名、原始 arguments 和错误
exception text 不写入事件。已知 token 在任何可变事实文本中先被移除。

示例（不是 Agent 生成）：

```json
{"type":"step_completed","payload":{"index":0,"tool_call":{"name":"check_service_health","arguments":{"service":"checkout-api"}},"observation":"Service is healthy but checkout latency is elevated.","environment":{"kind":"world","result":"ok","finalized":false,"check_called":false}}}
```

`index` 是所有调用的本地顺序（包括 controls），不是 durable sequence。
Runner 才分配 RunEvent 的全局 contiguous sequence 和 attempt。

## 持久化后谁评分

[`durable_events.py`](../../backend/app/durable_events.py) 增加的是可选、bounded provenance，
没有新事件类型：`environment.kind`、controlled `result`、`control`、`finalized`、
`check_called` 和 sanitized `usage`。`run_started` 还允许 `source: "environment"`。
H1-3 必须在任意 child fact 前发这个 marker，包括零调用就失败的 Attempt，才能把缺失 usage
重建为 unknown，而不是旧 Phase 1 的零值。旧的无 marker 事件兼容不变。

[`phase1_service._trajectory_from_events`](../../backend/app/phase1_service.py) 只重建最新
Attempt：

- controls 不进入世界轨迹，不影响 step-count、tool sequence、failure ratio 或最后 observation；
- rejected 世界调用仍进入世界 step-count，但 action 使用内部 `rejected_tool` sentinel，
  `status: failed`，保留 controlled rejection category。它不是注册工具，不能满足原工具的断言；
- usage 缺失维度在 metrics 中保留 null，`agent_usage.source` 为 `agent_reported`。
  旧 scorer 对 unknown tokens 不扣费用，这不等于测得零成本；
- `finalized` 和 `check_called` 只是事实，不决定 Run success；Control Plane 仍从 registered
  assertion suite 和持久化世界轨迹计算 score，并走原来的 lease/CAS completion；
- 旧 Attempt 的 check/usage 不会满足新 Attempt。

三个 v2 世界主要使用 trajectory assertions；订单世界另有 outcome assertions。
Environment 在同一 submit 控制事实中输出有界 `final_check`（`scenario_id` 与实际
`final_observation`），CP 验证同事件 check/submission 与当前 Attempt 后持久化、求值。
这是世界证据，不是第二个 scorer。已有 lossy argument allowlist 不扩成任意参数。

## H1-3 接线必须保留的边界

- Environment stdout 和 Agent stdout 分开；只把前者解析成可信事实。
  即使 Agent 输出格式完美的 `step_completed` 或 `env_ready`，也不能提升为事实。
- `env_ready` 只启动一次 Agent，不上传 PostgreSQL。验证 loopback URL，不接受 token/query。
- `run_started` 带 `attempt` 和 `source: "environment"`；每个 recovery Attempt 都重新启动一个新的
  Environment 并获得新的 loopback endpoint，但同一 logical Run 继续使用按 Run 生成的单个
  capability token；只有新的 logical Run 才生成新 token，除非先修改 ADR-0008。相同 seed/spec
  不意味着可以把旧 Attempt 的事实接到新 Attempt；旧事实不跨 Attempt。
- submit 会先返回 MCP response，Environment 保持冻结服务直到 Runner teardown 或 deadline；
  不以 server EOF/exit 0/`check_called` 推断 terminal success。H1-2 没有改变全局 terminal rule。
- Runner 负责 deadline/cancel/lease 失效与两进程清理；token handoff 目录是镜像内
  `/run/agentops/token-handoff`，Compose 使用持久 named volume；镜像预建 uid 10001、
  mode 0700 的目录，实际挂载初始化/替换仍需独立验证。只有身份/状态一致的 CP terminal
  wrapper ACK 后才清除 per-Run token record；不能因 HTTP 200、submit、check、EOF 或 exit 0 清理。
  stdin 读取本身等待一行输入，Runner 必须监督写入与 startup deadline，不能等写完才开始计时。

验证入口：`test_env_server.py` 覆盖调用、预算、HTTP auth/Origin 和真实 loopback readiness；
`test_env_server_persistence.py` 用隔离 SQLite API 验证 normalize → persist → reconstruct →
Control Plane score/status。测试不需要模型、GPU、外网、Rust Runner、应用数据库或 `.env`。

## 有状态订单组件：最终状态不等于调用次数

以下用有状态订单解释世界接口与最终可信证据。生产路径已连接独立 Agent/MCP、
final-check 和 CP completion；这不修改历史 Run、重放旧 Trace 或表示整个 H1 已验收。

## 从注册到一个独立世界

从 [`scenario_registry.create_environment()`](../../backend/app/scenario_registry.py)
开始读。它校验 `scenario_id` 和 `scenario_params`，把新的规范化参数字典交给
注册的 factory，再调用 `reset(spec.seed)`。factory 不接收 Policy、执行模式或
Run 身份。这些属于 agent 的输入，不应改变世界规则。

[`Environment`](../../backend/app/environment.py) 的五个入口是：

- `reset(seed)`：重建状态和历史；同 seed、同动作得到同观察和检查证据。
- `tool_definitions()`：返回调用方可拥有的工具定义，修改返回值不能污染其他实例。
- `call(name, args)`：只执行白名单内工具；参数不合法时原子拒绝。
- `check()`：只读地返回 trajectory 与 outcome 断言证据，不决定 Run 终态或分数。
- `fault_schedule`：世界声明故障条件，不由 agent 任意选择。

三个 canonical Environment 已按 ADR-0012 注册为 v2，旧三个 v1 execution ID 被拒绝，
没有 alias、迁移或历史重评分。`ScenarioHandler` 的旧 fixture/provider 入口仅保留代码
回归，不是生产 fallback；`order-support.v1` 是独立的有状态身份。

## 为什么调用退款工具不代表退款成功

读 [`order_support.OrderSupportEnvironment`](../../backend/app/scenarios/order_support.py)。
它有一个实例内的 `_orders`，没有应用数据库或外部支付服务。`reset()` 重建
`order-1001`：金额 `4200` cents，支付和发货为 `pending`，退款 ledger 为空。
当前版本是固定世界：seed 不改变订单内容；它仍保证相同 seed 和动作可重现。

严格参数只有固定 `order_id`，退款还要 `reason="shipping_failed"`。
工具依次是 `inspect_order`、`authorize_payment`、`ship_order`、`request_refund`。
参数错误抛异常且不改变历史或状态；参数正确但前置条件不满足，返回未生效的
JSON 观察。例如还没有授权时调用退款，不能让 outcome 通过。

授权后第一次发货在实际派送前失败。第一次合格退款会先写入一条固定 receipt，
然后返回 `refund_timeout_after_commit`。这是确定性的故障观察，不是真的网络
超时，也不是异常回滚。再次退款返回 `refund_already_recorded` 和同一条 receipt。
重复授权/发货也不能让已退款订单重新扣款或发货。重置会清除这些故障进度和历史。

`check()` 从当前 ledger 计算 `refund_count` 和退款总额，输出排序键、紧凑 JSON
的 `final_observation`，超过 durable 观察的 `4000` 字符上限会拒绝而非截断 JSON。
它只有世界生成的固定值，不接受 agent 提交的结果来覆盖订单状态。返回的是
不可变字符串；重复 `check()` 不会消费故障或修改订单，重复退款不改变该快照。

Outcome 使用已有 `json-match`：退款数为 `1`，支付为 `refunded`，发货为
`failed`，金额全额且 ledger 恰好等于一条 receipt。局部 adapter 将快照交给
现有断言求值器，不向历史插入假的工具事件。只有这个新场景声明 `ALL`；
其他场景的组合方式保持原样，也没有第二套评分引擎。

## 证据怎样回到 Control Plane

生产 `fixture_agent` 通过 MCP 执行工具脚本。baseline 重复检查六次，留下未完成状态；
replay 检查、授权、发货、退款、重试确认、最后检查，共六次世界调用。每次调用观察
携带当时的 ledger 快照，submit 的 final-check 再给出最终冻结快照。保留的旧 handler
也使用新实例，但不参与生产 dispatch。

Environment-owned capture 保持有界事件结构；
[`durable_events._normalize_tool_call()`](../../backend/app/durable_events.py)
仅投影允许的工具和固定参数，原九个工具的分支不变，并保留空参数的
`submit_result` control tool-call 作为 provenance。这个 control 仍不进入世界轨迹和评分。
Recorded Preview 由真实 v2 Golden 执行重新生成；recorder 只归一化稳定 ID 和生命周期区间内的确定性时间戳，
不改写 observation、score、assertion、usage、final-check 或 Environment provenance。Rust 只传输 JSON，
不负责订单规则或评分。Control Plane 的
[`_trajectory_from_events()` / `_analyze_and_score()`](../../backend/app/phase1_service.py)
从当前 Attempt 的持久化 all-world-attempt projection 与同事件 accepted final-check
求值注册断言，并拥有最终分数和状态。世界调用次数或 Agent 自报不能替代最终 ledger。

[`test_order_support.py`](../../backend/phase1_tests/test_order_support.py)
覆盖重置、实例隔离、参数原子拒绝、故障后幂等退款、真实 ledger 损坏检测、
JSONL fixture 和内存 SQLite 的 baseline → analysis → candidate → replay →
comparison → human activate 闭环。它不是实际 PostgreSQL/Runner/MCP Golden E2E。
默认三场景 Golden 已使用 v2。独立 reference Agent 走真实 MCP；本地 fake Provider
进程验证覆盖六次世界调用、七次 Provider 请求与恰好一笔退款。真实模型另有有限的
H1-5 记录；两类证据不混用，也不把有限样本写成准确率或稳定通过率。

## 独立 Agent 与剩余边界

loopback MCP 服务调用同一 `call()`，在 Environment 侧捕获真实工具事实，
在 `submit_result` 时调用一次 `check()` 并传递有界最终 ledger；不能信任 Agent 自报退款成功。
`report_usage` 不提供时相关维度仍是 unknown。

fixture agent 已从独立 MCP 客户端发现并调用工具；reference agent 走同一客户端并把
工具定义交给 Runner 配置的 OpenAI-compatible Provider。不能用旧 provider 的“只按工具名
拿预制观察”的 adapter 代替有状态执行。CI 只选择 fixture，不安装 GPU、不调用 live model；
reference 只有 operator 显式提供 Provider 配置时才运行。
现有场景的晋升门、Run 生命周期、租约、评分归属与人工激活门都不因此改变。
