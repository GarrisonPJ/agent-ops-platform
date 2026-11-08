# H3 pi 在 per-Run seed 下的 checkout 与 order — 2026-11-08

这是一份带日期的本地记录，不是任务看板，不是通过率声明，也不是准确率研究。
它补充 [h3-pass-k-seed-2026-11-07.md](h3-pass-k-seed-2026-11-07.md)：那份记录里
pi 只有 research 和 fault 两组是 2026-11-05 在固定默认 seed 下跑的，checkout
与 order 没有用 per-Run seed 跑过。本记录补上 pi 的这两组。当前任务状态：
[phase2-tasks.md](../agents/phase2-tasks.md)。决策：
[ADR-0010](../adr/0010-trial-groups-and-pass-k.md)。阅读指南：
[06-pass-k.md](../learn/06-pass-k.md)。

## 被验证的边界

- 功能：`AGENTOPS_PROVIDER_SEED=per-run`（commit `f00ef7d`）。pi 适配器把
  `AGENTOPS_PROVIDER_*` 映射进 pi 的 `models.json`；本记录没有单独抓取 pi
  发出的请求体来确认 `seed` 字段，只依据适配器与 Runner 配置推断它被带上。
- 设置与 11-07 记录相同：Windows 上已安装的 llama.cpp 服务，别名
  `swift-1.5-27b-iq3xxs`，context 8192；`temperature=1`、`max_tokens=256`、
  `reasoning_effort=none`、`parallel_tool_calls=false`，超时 60000 ms、重试 1；
  宿主机控制面（`app.phase1_main`）与 Rust Runner（`runner/target/debug/agentops`，
  `cargo build --locked` 显示已是最新）；pi 来自 `config/agents.external.toml`
  （pi 1.3.0）；全新 PostgreSQL 数据库（复用容器 `h3-seed-pg` 并新建库）；每个
  cell 是一个 trial group，`n = 5`、`k = 5`、Environment seed 42。
- 命令：`scripts/local_model_acceptance.py --agent-id pi-coding-agent.v1
  --scenario <id> --trials 5 --k 5 --timeout-seconds 300`。
- 源码：运行时 HEAD 为 `eb389aa` 之上的工作区（其他 agent 同时在改文档与测试，
  产品代码未改）；写本记录时 HEAD 为 `ec56d29`。

## 结果（pi，n = 5，k = 5，temperature 1，per-Run seed）

| Environment | 成功 | pass^1 | pass^5 | 不同轨迹数 | 耗时 | group id 前缀 |
|---|---|---|---|---|---|---|
| checkout（`checkout-api-latency.v2`） | 0/5 | 0.0 | 0.0 | 2（只算 world 调用则为 1） | 284 s | `f292eba5` |
| order（`order-support.v1`） | 5/5 | 1.0 | 1.0 | 2 | 103 s | `88d03a17` |

两组都在 25 分钟预算内，所以保持 n = 5，没有降到 3。

checkout 逐次：4 次 `timed_out`（world 调用都只有
`check_service_health`、`query_service_metrics`，没有被接受的提交），1 次
（trial 4）在相同的两次 world 调用之后调用了 `submit_result`（被接受），随后
`run_failed`（`internal_failure`，"Agent execution failed"）。五次分数都是
0.8667。轨迹按工具调用序列（含 `submit_result`）计为 2 种。

order 逐次：5 次都 `succeeded`。world 调用序列有两种：trial 2 是
`inspect_order → authorize_payment → ship_order → request_refund → inspect_order`，
其余四次在 `inspect_order` 之前多一次 `request_refund`。分数 0.667 / 0.7 / 0.667 / 0.667 / 0.667。

没有观察到任何 `PI_*` 错误码：在保存的 summary、事件、分析、诊断文件中检索，
结果为空。

## 这说明了什么

- **order 在独立采样下稳定成功，但不是一条轨迹。** 5/5 成功，pass^5 为 1.0；
  序列有细微差别（多一次 `request_refund`）。11-05 固定 seed 下 pi 的 order 是
  3/5、两种轨迹，这次是 5/5，差别只来自这一次 n = 5，不构成趋势。
- **checkout 对 pi 仍是系统性失败。** 0/5，和 11-05 固定 seed 下的 0/5（全部
  `timed_out`）以及 11-03 的单次结果一致：pi 只做了两次 world 调用就停住。
  per-Run seed 没有让它成功。与 H2-1 记录一致的解释是 pi 在自己内部按
  `inputSchema` 拒绝了模型的非法调用，Environment 看不到这些尝试；本次没有
  抓取 pi 的 JSONL 去验证，所以只是与之相符，不是已证实的原因。
- **per-Run seed 对 pi 产生了可观察的分叉，但很小。** 两个 group 各有 2 种
  轨迹，分叉点分别是一次额外的 `request_refund` 和一次最终的 `submit_result`。
  这支持 per-Run seed 在 pi 路径上也让采样独立的说法，但样本太小，不能当作
  对采样独立性的定量证据。

## 观察到但未解决的问题

Runner 日志里，所有 10 个 Run 完成后都出现同一条错误：`completion response is
not an authoritative wrapper: unknown field trial_group_id`。Runner 的
`runner/` 目录自 2026-11-02 起没有变化，而 trial group 的 `trial_group_id`
字段是之后加入的，所以 Runner 解析完成响应的结构体不认识它。控制面里的状态
是 Environment 终结时确定的：order 的 5 个 Run 都是 `succeeded`，这条 Runner
错误也出现在它们身上，所以它没有改变 order 的结果。但我没有证明它与 checkout
trial 4 的 `run_failed`（`internal_failure`）无关，那个失败发生在被接受的提交
之后。这一点需要单独的 issue 去查，本记录没有修改产品代码。

**后续（同日）：已修复。** 根因是 H3-1 给后端 `RunResponse` 加了
`trial_group_id` 和 `trial_index`，而 Runner 协议 crate 里的 `RunResponse` 带
`deny_unknown_fields`，没有同步这两个字段。修复给 Rust 结构加上两个
`#[serde(default)]` 字段，并增加 `test_runner_protocol_contract.py`，让后端模型和
Rust 结构的字段集合不一致时测试失败。修复后没有重跑 checkout，所以 trial 4 的
`internal_failure` 是否由它造成仍然未知。

## 没有显示的内容

- 没有 pi 的 research 与 fault 的 per-Run seed 结果（11-07 表中仍是 11-05 的固定
  seed 数据）。
- 没有抓取 pi 的请求体或 JSONL，因此没有直接证明 `seed` 到达了模型端，也没有
  证明 checkout 失败的原因。
- 轨迹只按 Environment 看得到的工具调用区分；pi 内部被拒的调用看不到
  （H2-1 已记录），pi 也不上报 usage。
- 一个模型、一个端点、一个 Environment seed、`n = 5`、每个 cell 只跑一次。
  不是通过率，不是其他模型的证据，也不是分析器准确率（H5）。
- 证据文件（`summary.json`、每个 trial 的事件和分析）只保存在本地 `/tmp` 与被
  git 忽略的 `.scratch/h3pi/`，不被跟踪文档引用。

## 清理

收尾时已停止 Runner、控制面、llama.cpp 服务和 `h3-seed-pg`。容器没有删除，
`h2-pi-pg` 未启动。

更早的记录：[温度 0](h3-pass-k-live-2026-11-04.md)、
[温度 1](h3-pass-k-temp1-2026-11-05.md)、
[per-Run seed（reference）](h3-pass-k-seed-2026-11-07.md)。
