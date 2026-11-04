# OTel GenAI 映射：把一个 Run 投影成 trace

决策见 [ADR-0011](../adr/0011-otel-genai-export.md)；打开方式见
[otel-export.md](../operations/otel-export.md)。代码在 `backend/app/otel_export.py`。

## 1. 依据的版本

- 规范仓库：`open-telemetry/semantic-conventions-genai`，核对到 commit
  `633cbb060b16b1487ea2dcd23c4307edc1a03f01`（2026-11-04）。该仓库没有任何 release tag。
- **Status: Development。** 下面用到的每个 `gen_ai.*` 属性在该 commit 的
  `model/gen-ai/registry.yaml` 里都是 `stability: development`（`gen_ai.conversation.id`
  未标稳定性），属性名还可能改。改名时只改映射代码和本文，不迁移 RunEvent。
- span 名在 `model/gen-ai/spans.yaml` 里规定：`gen_ai.invoke_agent.*` 的 span 名
  SHOULD 是 `invoke_agent {gen_ai.agent.name}`，`gen_ai.execute_tool.internal` 的 span 名
  SHOULD 是 `execute_tool {gen_ai.tool.name}`。两种 span 我们都用 SDK 默认的 INTERNAL kind。

## 2. 映射表

一个终态 Run 是一条 trace：

| 来源 | span | 属性 |
|---|---|---|
| Run | `invoke_agent {Agent 名}`（根） | `gen_ai.operation.name=invoke_agent`、`gen_ai.agent.name`（注册表里的名字）、`gen_ai.agent.id`（`agent_id`）、`gen_ai.conversation.id`（`run_id`） |
| 每个 `step_completed` | `execute_tool {工具名}`（子） | `gen_ai.operation.name=execute_tool`、`gen_ai.tool.name`、`gen_ai.tool.call.id`（事件 sequence） |

`agentops.*` 是我们自己的属性，不属于规范：`agentops.run.status`、`agentops.run.kind`、
`agentops.run.score`、`agentops.experiment.id`、`agentops.scenario.id`、trial 的
`agentops.trial_group.id`/`agentops.trial.index`，以及工具 span 上的
`agentops.environment.kind`（`world`/`control`）和 `agentops.environment.result`。
Run 不是 `succeeded`、或 Environment 结果不是 `ok` 时，span status 为 ERROR。

## 3. 为什么是这样投影

- **只读持久化事实。** 映射函数 `build_run_spans` 是纯函数，输入是 Run 和它的 RunEvent，
  在终态事务提交之后由 `_schedule_otel_export` 调用。导出是有损投影，从不读回；RunEvent
  仍是唯一事实源（CONTEXT 不变量 28）。
- **时间。** 工具 span 的结束时间是该事件的 `occurred_at`，开始时间是前一个事件的时间。
  Runner 和 CP 的时钟可能不一致，所以根 span 的起止取 Run 时间戳和所有子 span 的并集，
  保证根 span 包住所有子 span。
- **控制调用也是工具调用。** `submit_result` 和 `report_usage` 经 MCP 调用，所以同样是
  `execute_tool` span，用 `agentops.environment.kind=control` 区分；事件里没有 `tool_call`
  名时退回 `environment.control`。
- **不导出的内容。** 工具参数、观察、`decision_summary`、token、Provider URL、凭据都不导出。
  规范里有 opt-in 的 `gen_ai.tool.call.arguments`/`gen_ai.tool.call.result`，我们没有打开。
- **一个 Run 一个请求。** span 先收进内存再一次性导出（`emit`），不会每个 span 发一次 HTTP。
  只支持 `http/protobuf`；导出失败只记固定日志，不改变 Run。

## 4. 测试怎么证明

`backend/phase1_tests/test_otel_export.py`：

- golden 成功 Run 的根与子 span 名、属性键集合、`gen_ai.tool.call.id` 等于事件 sequence；
- 失败 Run 和被拒工具是 ERROR span；
- 关闭时连导出器都不构造；打开时所有 span 同一个 trace、子 span 的 parent 是根；
- 导出器抛错时 Run 仍是 `succeeded`，日志里没有异常文本；
- 默认导出器把一个 Run 以一次 `application/x-protobuf` POST 发到本地假 collector 的
  `/v1/traces`，请求体可解析为 `ExportTraceServiceRequest`，且不含 Runner token。

## 5. 尚未完成

H4-2 要求的对端验证（向真实 Phoenix `:6006/v1/traces` 或 Langfuse 发一条并截图）还没有做。
