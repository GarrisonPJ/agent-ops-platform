# 仓库地图与一次 Run

日期：2026-10-17。描述的是已落地的 Phase 1 代码，不是 ADR 里的目标结构。

## 目录

| 路径 | 角色 |
|---|---|
| `backend/app/phase1_main.py` | HTTP 路由，前缀 `/api`。 |
| `backend/app/phase1_service.py` | 状态机：创建 Run、领取、完成、评分、策略。 |
| `backend/app/phase1_models.py` | PostgreSQL 表。 |
| `backend/app/phase1_schemas.py` | 协议模型。JSON Schema 由它生成到 `contracts/v1/`。 |
| `backend/app/scenario_registry.py` | 环境与 agent 循环目前耦合处。`ScenarioHandler`。 |
| `backend/app/scenarios/` | 三个内置环境。 |
| `backend/app/demo_agent.py` | Runner 默认拉起的子进程。 |
| `backend/app/phase1_provider.py` | 可选的 `/chat/completions` 客户端。 |
| `backend/app/failure_analyzer.py` | 规则分类。`analyze_trajectory`。 |
| `backend/app/calibration_*.py` | 离线校准 CLI，没有对应页面。 |
| `runner/crates/agentops-runner/src/lib.rs` | 领取、`spawn_agent`、心跳、上传事件。 |
| `runner/crates/agentops-protocol/` | Rust 侧的协议类型。 |
| `frontend/src/Phase1App.tsx` | 四条路由。 |
| `frontend/src/pages/RunDetailPage.tsx` | Trace / Analysis / Improve。 |
| `docs/adr/` | 已接受的边界。0007–0011 尚未写进代码。 |
| `docs/history/` | 带日期的验收索引，不是当前设计。 |

## 一次 Golden Run 怎么走

以 fixture 模式创建 checkout 实验并启动基线为例。

```text
浏览器
  → POST /api/experiments/{id}/runs
  → phase1_service.create_baseline_run     写入 Run + RunnerJob，状态 queued
  → Rust Runner POST /api/internal/runner/jobs/claim
  → phase1_service.claim_job               状态 claimed，发下 EvaluationSpec
  → lib.rs spawn_agent                     默认 python -m app.demo_agent
       stdin：一行 EvaluationSpec JSON
       stdout：JSONL（step_completed 等）
  → Runner 包成 EventEnvelope，POST .../events
  → 控制面先落库，再经 SSE 推给浏览器
  → Runner POST .../complete
  → 控制面评分，并调用 failure_analyzer.analyze_trajectory
```

对应代码入口：

- 创建：`create_baseline_run`（`phase1_service.py`）
- 领取：`claim_job`（同文件）
- 拉起子进程：`spawn_agent`（`runner/crates/agentops-runner/src/lib.rs`）
- 子进程：`demo_agent.py` → `scenario_registry.run_scenario`
- 分类：`analyze_trajectory`（`failure_analyzer.py`）

浏览器重连用 `after=<上次 sequence>`。事件以数据库为准，SSE 只是通知。

## 三个环境

| scenario_id | 在练什么 |
|---|---|
| `checkout-api-latency.v1` | Golden 路径。基线重复拉日志，耗尽步数。 |
| `multi-step-research.v1` | 搜索、抓取、回答，基线会空转。 |
| `api-fault-orchestration.v1` | 上游 503，要重试或降级。 |

Provider 模式仍走这三份工具定义。模型只能调用环境给出的工具，工具结果是 Python 里写死的观察，不是真实外网。密钥只出现在 Runner 的环境变量 `AGENTOPS_PROVIDER_*`。

## Phase 2 会拆开的那一处

现在 `ScenarioHandler.run_fixture` / `run_provider` 同时是“世界”和“决策循环”。H1 之后世界变成 `app.env_server`（本机 MCP），决策循环变成注册表里的另一个进程。事件改由环境侧记录。租赁、围栏、人工激活不改。见 ADR-0007 与 ADR-0008。

## 建议的第二遍

1. 打开 `scenario_registry.py` 里的 `ScenarioHandler`，对照 ADR-0007 的方法列表，看哪些已经有、哪些还没有。
2. 打开 `lib.rs` 的 `spawn_agent`，看默认命令和环境变量覆盖。
3. 打开 `failure_analyzer.py` 顶部的 `FailureClassification`，再读 `docs/evaluation-calibration.md` 开头的两段：回归门与一致性研究不是一回事。
