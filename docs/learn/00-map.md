# 仓库地图与一次 Run

描述当前 H1 集成代码：生产 `execute_claim` 已使用 canonical split，CP 独占终态。
本地验证不等于整个 H1、镜像或真实模型验收；边界见 ADR-0012/0013。

## 目录

| 路径 | 角色 |
|---|---|
| `backend/app/phase1_main.py` | HTTP 路由，前缀 `/api`。 |
| `backend/app/phase1_service.py` | 状态机：创建 Run、领取、完成、评分、策略。 |
| `backend/app/phase1_models.py` | PostgreSQL 表。 |
| `backend/app/phase1_schemas.py` | 协议模型。JSON Schema 由它生成到 `contracts/v1/`。 |
| `backend/app/scenario_registry.py` | 显式 Environment factory、metadata 与断言注册；旧 handler 仅保留回归。 |
| `backend/app/scenarios/` | 三个 canonical v2 世界与 `order-support.v1`。 |
| `backend/app/env_server.py` | 独立 Environment MCP Server；Runner H1 split seam 的 Environment 子进程。 |
| `backend/app/fixture_agent.py` | 无模型的确定性 MCP Agent；Runner/CI 的注册命令。 |
| `backend/app/reference_agent.py` | OpenAI-compatible MCP Agent；只在显式 Provider 配置下运行。 |
| `backend/app/mcp_agent_client.py` | Agent 侧 MCP 2.3 Streamable HTTP 客户端与边界校验。 |
| `backend/app/demo_agent.py` | 保留的 Phase 1 旧耦合 Agent；不作为生产 Runner 镜像命令。 |
| `backend/app/phase1_provider.py` | 可选的 `/chat/completions` 客户端。 |
| `backend/app/failure_analyzer.py` | 规则分类。`analyze_trajectory`。 |
| `backend/app/calibration_*.py` | 离线校准 CLI，没有对应页面。 |
| `runner/crates/agentops-runner/src/lib.rs` | 领取、`supervise_attempt`、心跳、事件上传与权威 ACK 校验。 |
| `runner/crates/agentops-protocol/` | Rust 侧的协议类型。 |
| `frontend/src/Phase1App.tsx` | 四条路由。 |
| `frontend/src/pages/RunDetailPage.tsx` | Trace / Analysis / Improve。 |
| `config/agents.toml` | 运维拥有的 Agent argv 注册表；API 与 Runner 镜像各自显式加载。 |
| `docs/adr/` | 已接受的边界，包括 H1 canonical cutover、CP 成功门和 token ACK。 |
| `docs/history/` | 带日期的验收索引，不是当前设计。 |

## 一次 Golden Run 怎么走

以 fixture 模式创建 checkout 实验并启动基线为例。

```text
当前 canonical split 路径：

浏览器
  → POST /api/experiments/{id}/runs
  → Control Plane 写入 Run + RunnerJob，状态 queued
  → Rust Runner claim，启动 Environment 与注册 Agent 的 split seam
  → Environment：python -m app.env_server（loopback MCP，Environment-owned facts）
  → Agent：python -m app.fixture_agent（CI）或 app.reference_agent（显式 Provider）
  → Agent -- MCP tools/call + capability token --> Environment
  → Runner 只转发 Environment-owned facts，并携带 lease/Attempt
  → Control Plane 先落库，再评分、分析并决定终态

Runner 只提交 ExecutionFacts，CP 创建一致的 terminal event 与完成响应。
校验有界 completion wrapper 后才能删除 token record，HTTP 200 不够。
镜像配置独立 MCP 依赖、显式 registry/workdir；Compose 使用持久 named token-handoff volume。
这些包装配置仍需实际镜像/volume 验证。demo_agent 不是生产默认命令。
```

对应代码入口：

- 创建：`create_baseline_run`（`phase1_service.py`）
- 领取：`claim_job`（同文件）
- 监督：`execute_claim` → `supervise_attempt`（`runner/crates/agentops-runner/src/lib.rs`）
- 子进程：`env_server.py` 与 registry 选择的 `fixture_agent.py` / `reference_agent.py`
- 分类：`analyze_trajectory`（`failure_analyzer.py`）

浏览器重连用 `after=<上次 sequence>`。事件以数据库为准，SSE 只是通知。

## 内置环境

| scenario_id | 在练什么 |
|---|---|
| `checkout-api-latency.v2` | canonical H1 Environment；fixture 基线重复拉日志，耗尽步数。 |
| `multi-step-research.v2` | canonical H1 Environment；fixture 基线空转。 |
| `api-fault-orchestration.v2` | canonical H1 Environment；fixture 基线耗尽步数。 |
| `order-support.v1` | H1 有状态 Environment；不是旧三场景的 v2 alias。 |

Provider 模式仍走这三份工具定义。模型只能调用环境给出的工具，工具结果是 Python 里写死的观察，不是真实外网。密钥只出现在 Runner 的环境变量 `AGENTOPS_PROVIDER_*`。

## H1 拆开的那一处

生产世界在 `app.env_server`（本机 MCP），决策循环在 registry 选择的另一个进程。
旧 `ScenarioHandler.run_fixture` / `run_provider` 只保留兼容代码回归，不是生产 fallback。
事件由 Environment 侧记录；租约、围栏、人工激活和评分仍归 CP。见 ADR-0007/0008/0012。

## 建议的第二遍

1. 打开 `scenario_registry.py` 的 `create_environment`，再读 `environment.py` 的接口。
2. 打开 `lib.rs` 的 `execute_claim`、`resolve_agent_command` 与 `complete_request`，看注册解析与 ACK 清理门。
3. 打开 `failure_analyzer.py` 顶部的 `FailureClassification`，再读 `docs/evaluation-calibration.md` 开头的两段：回归门与一致性研究不是一回事。
