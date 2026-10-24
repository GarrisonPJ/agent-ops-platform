# Environment 与 MCP：一个 Run 的工具边界

H1-2 已实现独立的 `python -m app.env_server`，但它**尚未接入旧 Runner**。
H1-3 才负责双进程监督，H1-5 才替换 fixture/provider agent；现有 Phase 1
路由、Scenario IDs、provider 行为和评分路径没有切换到新世界。

## 从输入到 ready

入口在 [`backend/app/env_server.py`](../../backend/app/env_server.py) 的 `main`：

1. Runner 通过 stdin 写一行 `EvaluationSpec` JSON（UTF-8，最多 65,536 bytes，
   包含换行），不需要关闭 stdin。这里只消费一个 spec，不提供文件、网络或命令输入。
2. `AGENTOPS_ENV_TOKEN` 是独立的 bearer capability，只通过环境变量传递，
   不放在 URL、spec 或 ready 消息中。边界要求 32..512 个 printable non-space ASCII
   字符；H1-3 用 `secrets.token_hex(32)` 生成 64 个 hex 字符。
3. `EnvironmentSession` 只调用一次 `create_environment(spec)`；factory 已执行
   `reset(spec.seed)`。世界不缓存，不随 `execution_mode` 或 policy 切换行为。
4. 使用官方 `mcp>=2.3.0,<2.4.0` 的 `MCPServer` 和 Streamable HTTP app。
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

`EnvironmentMCPServer.list_tools` 只列出 `Environment.tool_definitions()`。
两个 reserved controls 不出现在正常 discovery 中，但认证后的 MCP `tools/call` 可以调用：

- `submit_result`：arguments 必须为空对象。世界只执行一次 `check()` 并冻结；
  不把 check 的 evaluation、score、expected values 或 Run status 持久化为裁决。
  重复提交返回 `duplicate_submit`，后续世界调用返回 `finalized`，都不再改变世界。
- `report_usage`：可选的 Agent-reported 累计 totals。字段是 `token_prompt`、
  `token_completion`（各 0..1,000,000,000）和 `context_window`（`used`/`limit`
  各 0..10,000,000）。省略或 null 表示 unknown；空对象是有效的 unknown report。
  最新有效 report 替换先前 report，不相加；无效 report 不覆盖。它不是测量得到的 provider telemetry。

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

现有三个世界只有 trajectory assertions。H1-2 没有添加 final-state operands 或第二个 scorer；
未来 outcome assertions 需要单独明确契约。已有 lossy argument allowlist 也没有扩成任意参数。

## H1-3 接线必须保留的边界

- Environment stdout 和 Agent stdout 分开；只把前者解析成可信事实。
  即使 Agent 输出格式完美的 `step_completed` 或 `env_ready`，也不能提升为事实。
- `env_ready` 只启动一次 Agent，不上传 PostgreSQL。验证 loopback URL，不接受 token/query。
- `run_started` 带 `attempt` 和 `source: "environment"`；重启 Attempt 要新世界、新 endpoint、
  新 capability，同 seed/spec，不让旧事实跨 Attempt。
- submit 会先返回 MCP response，Environment 保持冻结服务直到 Runner teardown 或 deadline；
  不以 server EOF/exit 0/`check_called` 推断 terminal success。H1-2 没有改变全局 terminal rule。
- Runner 负责 deadline/cancel/lease 失效与两进程清理；本章不实现 H1-3 的 process group。
  stdin 读取本身等待一行输入，Runner 必须监督写入与 startup deadline，不能等写完才开始计时。

验证入口：`test_env_server.py` 覆盖调用、预算、HTTP auth/Origin 和真实 loopback readiness；
`test_env_server_persistence.py` 用隔离 SQLite API 验证 normalize → persist → reconstruct →
Control Plane score/status。测试不需要模型、GPU、外网、Rust Runner、应用数据库或 `.env`。
