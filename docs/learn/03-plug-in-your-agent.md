# 接入你自己的 Agent：契约、pi 适配器与失败分布

本章讲一个外部 Agent-under-test 要满足什么契约才能被评估，以 H2-1 的
`pi-coding-agent.v1` 为例，并对比同一本地模型下 reference loop 与 pi 的失败分布。
决策见 [ADR-0009](../adr/0009-agent-registry-trust-boundary.md) 与
[ADR-0008](../adr/0008-environment-mcp-server-and-env-side-capture.md) 的 H2 修订；
真实运行记录见 [H2-1 验收](../history/h2-pi-live-acceptance-2026-11-03.md)。

## 1. 一个 Agent 进程收到什么

Runner 按注册表里的 argv 启动 Agent（`config/agents*.toml`，见
[agent-registry.md](../operations/agent-registry.md)），API 只传 `agent_id`，从不传命令。
Agent 进程能用的输入只有这些：

- **stdin**：一行 `EvaluationSpec` JSON。`app.fixture_agent.read_spec` 是现成的严格解析器，
  它还检查 `AGENTOPS_TASK_PROMPT` 与 spec 里的 `task` 一致。
- **环境变量**：Runner 先 `env_clear()`，再只放回 `PATH`、全部 `AGENTOPS_PROVIDER_*`、
  `AGENTOPS_ENV_MCP_URL`、`AGENTOPS_ENV_TOKEN`、`AGENTOPS_TASK_PROMPT`
  （`runner/crates/agentops-runner/src/lib.rs` 的 `spawn_supervised_child`）。没有 `HOME`。
- **工具**：`AGENTOPS_ENV_MCP_URL` 是 loopback Streamable HTTP MCP，请求必须带
  `Authorization: Bearer <AGENTOPS_ENV_TOKEN>`。`tools/list` 返回世界工具加上
  `submit_result`；`report_usage` 不在 discovery 里，但可以直接调用。

Agent 的输出都不可信：stdout/stderr 只被 Runner 读走并计入 `max_output_bytes`，
不会变成 RunEvent。可信事实只来自 Environment 的 `step_completed`。

## 2. Agent 必须做、不能做什么

必须：

1. 只通过 MCP 调 Environment 的工具。
2. 任务完成时**自己**调用一次空参数的 `submit_result`。没有有效提交，CP 一定判失败。
3. 在 Run timeout 内退出。

不能：评分、发 `step_completed`、把 token 写进 argv/文件/输出、用退出码 0 冒充成功。
成功只由 CP 根据可信证据和注册 aggregate >= 1.0 判定（ADR-0012）。

可选：拿得到真实 Provider usage 的 Agent 可以调 `report_usage`（累计 totals，见 02 章）。
不调用时，context/token 维度保持 unknown。

## 3. 约 20 行的自有循环

下面的 loop 复用仓库里的 `MCPAgentClient` 和 `OpenAICompatibleProvider`，每轮让模型选
一个工具，直到它提交。已用真实 `app.env_server` 跑通 checkout。把它放进
`AGENTOPS_AGENT_WORKDIR`（通常是 `backend/`），用 `command = ["python", "-m", "my_loop"]`
注册即可；`kind` 写 `external`。

```python
import asyncio, os
from app.fixture_agent import read_spec
from app.mcp_agent_client import MCPAgentClient
from app.phase1_provider import OpenAICompatibleProvider, ProviderSettings

async def main() -> int:
    spec = read_spec()  # one EvaluationSpec line on stdin
    model = OpenAICompatibleProvider(ProviderSettings.from_env())
    async with MCPAgentClient(os.environ["AGENTOPS_ENV_MCP_URL"], os.environ["AGENTOPS_ENV_TOKEN"]) as env:
        messages = [{"role": "system", "content": "Use the tools, then call submit_result."},
                    {"role": "user", "content": spec.task}]
        for _ in range(spec.max_steps + 1):
            reply = await model.chat(messages, env.tools)
            if not reply.tool_calls:
                return 1  # no submission: the Control Plane fails the Run
            call = reply.tool_calls[0]
            result = await env.call_tool(call.name, call.arguments)
            if call.name == "submit_result":
                return 0 if not result.is_error else 1
            messages += [{"role": "assistant", "content": reply.content or "", "tool_calls": [call.raw]},
                         {"role": "tool", "tool_call_id": call.id, "content": result.text}]
    return 1

raise SystemExit(asyncio.run(main()))
```

`MCPAgentClient` 要求 discovery 里恰好一个空参数 `submit_result`，并在 `env.tools` 里
附上自己的 control 定义（包括 `report_usage`）。不用仓库代码时，任何能连 Streamable
HTTP MCP、能带 bearer header 的客户端都可以，例如 pi。

## 4. pi 适配器怎样满足契约

pi 不认识这套契约，所以 [`backend/app/pi_agent.py`](../../backend/app/pi_agent.py)
做一层薄翻译：

- `mcp_config`：只有一个 MCP server `env`，`exposure: direct`，header 写成
  `Bearer ${AGENTOPS_ENV_TOKEN}`，由 pi 自己从环境变量插值；token 不进文件和 argv。
- `models_config`：把 `AGENTOPS_PROVIDER_*` 映射成 pi 的 `openai-completions` provider，
  `apiKey` 写成 `$AGENTOPS_PROVIDER_API_KEY`；`max_tokens`、`temperature`、
  `reasoning_effort`、`parallel_tool_calls` 与 reference 对齐。
- `pi_argv`：`--no-extensions -e builtin:mcp`、`--no-skills`、`--no-context-files`、
  `--no-approve`、`--offline` 等，加上私有 `PI_CODING_AGENT_DIR`、空工作目录，
  把运维者个人的 pi 配置挡在外面；`--tools 'mcp__env__*'` 关掉 read/bash/edit/write。
- 系统指令与 reference 共用 `app.reference_agent.system_prompt`，以文件路径传入。

spike 时踩到的四个坑决定了 `run` 的写法：

1. pi 会把管道 stdin 读到 EOF 拼进 prompt；继承来的 stdin 会让它永远等着。
2. 以 `@` 开头的参数会被当成文件读。任务来自 API，所以任务写进 pi 的 stdin，不放 argv。
3. Provider 不可达时 pi 重试三次后仍然退出 0。`PiOutcome` 从 JSONL 的最后一个
   assistant `stopReason` 得出固定码（`PI_PROVIDER_ERROR` 等），不信退出码。
4. pi 的 JSONL 很大，`_read_events` 只解析、不转发；看到被接受的 `submit_result`
   后就停掉 pi，和 reference loop 提交后立即返回一致。

## 5. 同一模型、两个 scaffold 的失败分布

同一 Windows llama.cpp（`swift-1.5-27b-iq3xxs`，context 8192，temperature 0），每格一次 Run：

| Environment | reference | pi |
|---|---|---|
| research | 成功 | 成功 |
| checkout | 失败：`loop_no_progress`；4 次被拒的空参数 logs 调用留在轨迹里 | 超时：`loop_no_progress` + `timeout`；轨迹里只有 2 次调用 |
| fault | 失败：首步 `invoke_service` 违反顺序断言，`loop_no_progress` | 成功 |
| order | 成功（`execution` 诊断 0.667） | 成功（同样的诊断） |

差异来自 Environment 侧采集能看到什么：

- **被 Agent 自己拦下的调用看不到。** pi 先按 `inputSchema` 校验参数，空参数的
  `fetch_service_logs` 在 pi 内部被拒了约 20 次，从未到达 Environment。所以
  `max_steps` 没触发，Run 一直耗到 timeout；`rejected_tool` 一类的轨迹证据也不存在。
  reference 把同样的调用转发给 Environment，拒绝被记录，16.7 秒就因步数用完结束。
- **context 与 token 预算只能是 unknown。** pi 不调用 `report_usage`，所以 pi Run 没有
  token 总数、没有 `budget` 维度、也没有 context 证据。分析器不会、也不该替它推断
  （ADR-0007 的 unknown 维度）。这一类在 pi 上只能是 unknown。
- **质量分不能跨 scaffold 比。** pi 超时的 checkout 质量分 0.867，高于 reference 失败的
  0.390，因为 pi 的失败尝试和 token 用量都不可见。比较时看 status 和成功门。

一格只有一次 Run，fault 上的差异分不清是 scaffold 本身还是对 prompt 敏感；要看稳定性
得等 H3 的 trial group 和 pass^k。

## 6. 剩余边界

- pi 只在宿主机 Runner 上跑：Runner 镜像没有 pi，默认注册表也不含它
  （`config/agents.external.toml` 是可选注册表）。
- CI 不安装、不调用 pi；`backend/phase1_tests/test_pi_agent.py` 用假 `pi` 可执行文件，
  其中一条连真实 `env_server` 走完提交。
- pi 版本固定为 1.3.0；换版本或换模型都要重新验收。可选的 Claude Code、Agents SDK、
  Codex 适配器（H2-5、H2-2、H2-3）未做。
