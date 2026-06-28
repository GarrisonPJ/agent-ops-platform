# AgentOps Platform

AI Agent 基础设施平台——从任务提交到轨迹回放、评测排名、**闭环自优化**、训练数据导出的**完整工具链**。打通"外部工具 → Agent 执行 → 评测反馈 → 自动改进 → RL 训练体系"的链路。

## 架构

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             Frontend (React 19)                                   │
│  ┌─────────┐ ┌───────────┐ ┌────────┐ ┌────────────┐ ┌──────────────────────┐  │
│  │  /run   │ │  /traces  │ │ /tools │ │  /compare  │ │        /eval         │  │
│  │ Agent   │ │ 轨迹列表   │ │ 工具   │ │ 并排对比    │ │ Benchmark + Failures │  │
│  │ 执行页   │ │ + 回放    │ │ 目录   │ │            │ │ + Policy Timeline    │  │
│  └────┬────┘ └─────┬─────┘ └────────┘ └──────┬─────┘ └──────────┬───────────┘  │
│       │            │                         │                  │               │
│       │     SSE ◄──┼─────────────────────────┼──────────────────┘               │
│       │            │                         │                                  │
├───────┼────────────┼─────────────────────────┼──────────────────────────────────┤
│       ▼            ▼                         ▼                                  │
│                           Backend (FastAPI :8000)                                │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌───────────────────────────────┐   │
│  │ Runtime  │ │ Trajectory │ │   LLM    │ │       Closed-Loop Pipeline     │   │
│  │ ReAct    │ │   Repo     │ │ Adapter  │ │  Failure Analyzer → Compiler   │   │
│  │ 循环     │ │  持久化     │ │          │ │  → Policy Store → Auto-replay │   │
│  └────┬─────┘ └─────┬──────┘ └────┬─────┘ └───────────────┬───────────────┘   │
│       │             │             │                         │                   │
│  ┌────▼─────┐       │        ┌────▼─────┐                   │                   │
│  │ Executor │       │        │ Context  │                   │                   │
│  │ 工厂     │       │        │ Manager  │                   │                   │
│  ├──────────┤       │        │ tiktoken │                   │                   │
│  │ Docker   │       │        │ +sliding │                   │                   │
│  │ K8s Job  │       │        │ window   │                   │                   │
│  └────┬─────┘       │        └──────────┘                   │                   │
│       │             │                                       │                   │
├───────┼─────────────┼───────────────────────────────────────┼───────────────────┤
│       ▼             ▼                                       ▼                   │
│                          PostgreSQL 16                                            │
│    trajectories │ steps │ policy_versions │ trajectory_policy_map                 │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## 闭环自优化（v0.4）

```
Task → Run(Policy vN) → Trajectory → Score
  ↓
Failure Analyzer → 四维度故障分类 (Planning/Execution/Context/Budget)
  ↓
Policy Compiler → 规则引擎生成 PolicyPatch（system_prompt_suffix / max_steps_override / context_strategy / tool_priority_bias）
  ↓
Policy Store → 版本化存储 (v1 → v2 → v3)
  ↓
Auto-replay → 在新 Policy 下重跑失败轨迹，三段式判定 (激活/回滚/待审)
  ↓
Policy Router → 注入到下次 Run(Policy vN+1)
  ↓
[loop]
```

触发条件：≥10 条新 trajectory 或距上次编译 ≥30 分钟。策略补丁以 prompt 级注入（system_prompt_suffix + tool 排序调整 + 上下文策略 + 步数上限），不修改模型权重，适配任何云端 LLM。

## 技术栈

| 层 | 技术 |
|----|------|
| **前端** | React 19, TypeScript, Vite 6, Tailwind v4, Redux Toolkit, RTK Query, TanStack Table, framer-motion, Phosphor Icons, react-json-view-lite, react-resizable-panels, Recharts |
| **后端** | Python 3.12, FastAPI, SQLAlchemy (async), asyncpg, docker-py, tiktoken, httpx |
| **数据库** | PostgreSQL 16-alpine |
| **LLM** | OpenAI-compatible 格式（DeepSeek / OpenAI / 任何兼容 provider），环境变量切换 |
| **执行层** | Docker SDK + Kubernetes Job（双模，`EXECUTOR_MODE` 切换：[文档](infra/k8s/README.md)） |
| **基础设施** | Docker Compose（dev，一键 `up -d`），Kubernetes YAML（prod，`kubectl apply -k`） |
| **测试** | pytest (96 tests), vitest (8 tests), Playwright E2E |
| **工具链** | Rust CLI（`agentops-analyze`），uv（Python），pnpm（Node） |
| **部署** | kind（dev K8s），K3s / GKE / 任何 CNCF K8s（prod），Cloudflare Tunnel（公开 Demo） |

## 项目结构

```
agent-ops-platform/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 路由 + 应用入口
│   │   ├── orchestrator.py      # Agent 依赖图工厂（统一组装，消除重复）
│   │   ├── runtime.py           # ReAct Agent 循环 + Policy 注入
│   │   ├── agent_runner.py      # Agent 生命周期（持久化、评分、取消、闭环触发）
│   │   ├── scoring.py           # 评分引擎（纯函数，可单元测试）
│   │   ├── benchmarks.py        # 预定义 Benchmark 任务集
│   │   ├── exporters.py         # 训练数据导出（OpenAI SFT / RLHF / JSONL）
│   │   ├── serializer.py        # API 响应序列化（render_scoring_view + render_trajectory）
│   │   ├── llm.py               # LLM provider 适配（OpenAI-compatible）
│   │   ├── context_manager.py   # Context window 管理（tiktoken + sliding + strategy）
│   │   ├── executor.py          # 执行器抽象协议 + 工厂 + 共享工具函数
│   │   ├── docker_executor.py   # Docker 执行器
│   │   ├── k8s_executor.py      # K8s Job 执行器（懒加载 kubernetes）
│   │   ├── trajectory_repo.py   # 轨迹 CRUD
│   │   ├── tool_registry.py     # Tool 注册中心（支持 enable/disable）
│   │   ├── event_bus.py         # SSE 事件总线 + stream_events
│   │   ├── mcp_server.py        # MCP stdio Server（Tool Registry → MCP）
│   │   ├── config.py            # 配置管理（含闭环阈值常量）
│   │   ├── database.py          # 异步数据库连接 + Migration
│   │   ├── models.py            # SQLAlchemy ORM（trajectories + steps + policy_versions）
│   │   ├── failure_analyzer.py  # 故障分析器 — 四维度多标签分类（纯规则）
│   │   ├── policy_compiler.py   # Policy 编译器 — 规则引擎 + PolicyPatch dataclass
│   │   ├── policy_store.py      # Policy 存储 — CRUD + Policy 类型接口
│   │   ├── policy_pipeline.py   # 闭环管道 — run_closed_loop → compile → store → replay
│   │   ├── auto_replay.py       # 自动回放引擎 — 三段式判定（active/reverted/pending）
│   │   └── adapters/            # LangChain + OpenAI Agents SDK 适配器
│   ├── run_mcp.py               # MCP Server 独立入口
│   └── tests/                   # pytest（96 测试用例，含 E2E smoke + 闭环集成测试）
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── RunPage.tsx       # Agent 执行（居中→顶部形变交互）
│   │   │   ├── TraceListPage.tsx # 轨迹列表
│   │   │   ├── TraceDetailPage.tsx # 轨迹回放
│   │   │   ├── ToolsPage.tsx     # 工具目录
│   │   │   ├── ComparePage.tsx   # 多轨迹并排对比
│   │   │   ├── EvalPage.tsx      # 评测面板外壳（Tab 路由）
│   │   │   └── eval/             # Eval Tab 组件
│   │   │       ├── BenchmarkTab.tsx   # Benchmark 执行 + 排名
│   │   │       ├── FailuresTab.tsx    # Failure 分布图表（Recharts 雷达图）
│   │   │       └── PoliciesTab.tsx    # Policy 时间线 + 审核队列
│   │   ├── components/          # StatusBadge, StepCard, TokenDashboard, FailureDistributionChart, PolicyTimeline, PolicyDetailPanel, PolicyReviewQueue 等
│   │   ├── hooks/               # usePolicyActions, useAgentStream, usePlaybackEngine
│   │   ├── services/            # RTK Query API + SSE 客户端
│   │   ├── store/               # Redux Toolkit（playback 状态机）
│   │   └── types/               # TypeScript 类型定义
│   └── tests/                   # Playwright E2E
├── infra/
│   ├── docker/docker-compose.yml # 本地一键启动（db + api + web）
│   └── k8s/                      # K8s 部署 YAML + Executor RBAC
├── sdk/agentops-analyze/         # Rust CLI（离线分析 + tokio 并发 live-logs）
├── docs/                         # 设计文档 + ADR + handoff
└── .scratch/                     # PRD + Issue 追踪（closed-loop-architecture）
```

## 部署方案

### 方案一：Docker Compose（本地开发 / 快速验证）

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

# 2. 一键启动（db + api + web）
docker compose -f infra/docker/docker-compose.yml up -d

# 3. 访问
# 前端:  http://localhost:5173（Vite HMR）
# API:   http://localhost:8000
# 文档:  http://localhost:8000/docs
```

或分步启动以便热重载：

```bash
docker compose -f infra/docker/docker-compose.yml up db -d
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# 另一个终端
cd frontend && pnpm dev
```

### 方案二：kind 本地 K8s 集群

本项目在 **kind**（Kubernetes in Docker）集群上验证端到端部署，全套 K8s 资源配置开箱即用，支持 kind / K3s / GKE 等任何 CNCF 认证集群。

#### 集群架构

```
┌──────────────┐     ┌──────────────────────────────────────────────────┐
│  Cloudflare   │     │            kind K8s Cluster                      │
│  Tunnel       │────▶│  ┌──────────────────┐  ┌────────────────────┐    │
│  (public URL) │     │  │  agentops-web    │  │  agentops-api × 2  │    │
└──────────────┘     │  │  nginx :80        │──▶│  FastAPI :8000      │    │
                     │  │  SPA + /api proxy │  │  SA: executor       │    │
                     │  └──────────────────┘  └───────┬────────────┘    │
                     │                                  │                 │
                     │                          ┌───────▼────────────┐   │
                     │                          │  agentops-db       │   │
                     │                          │  PostgreSQL :5432  │   │
                     │                          └────────────────────┘   │
                     │  ┌────────────────────────────────────────────┐   │
                     │  │  K8s Job Executor (EXECUTOR_MODE=k8s)     │   │
                     │  │  每步 tool 调用 → 独立 K8s Job → 资源隔离  │   │
                     │  └────────────────────────────────────────────┘   │
                     └──────────────────────────────────────────────────┘
```

#### 一键部署

```bash
# 1. 创建集群
kind create cluster --name agentops

# 2. 构建镜像并载入
docker build -t agentops-api:latest -f backend/Dockerfile backend
docker build -t agentops-web:prod -f frontend/Dockerfile.prod frontend
kind load docker-image agentops-api:latest --name agentops
kind load docker-image agentops-web:prod --name agentops

# 3. 部署
kubectl apply -k infra/k8s/

# 4. 等待就绪
kubectl wait --for=condition=ready pod -l app=agentops -n agentops --timeout=120s
```

#### 公网 Demo 访问（Cloudflare Tunnel）

```bash
# 端口转发
kubectl port-forward -n agentops svc/agentops-web 8080:80 &

# 启动隧道，获取 trycloudflare.com 链接
cloudflared tunnel --url http://localhost:8080
```

#### 包含的 K8s 资源

| 文件 | 说明 |
|------|------|
| `namespace.yaml` | agentops 独立命名空间 |
| `configmap.yaml` | LLM 配置 / 执行器模式 / 日志级别 |
| `secret.yaml` | 数据库密码 / LLM API Key（占位符，部署时注入） |
| `deployment-api.yaml` | FastAPI 后端（2 副本，含存活/就绪探针） |
| `deployment-web.yaml` | nginx 前端静态服务 |
| `deployment-db.yaml` | PostgreSQL 16 |
| `service-api.yaml` | ClusterIP :8000 |
| `service-web.yaml` | ClusterIP :80 |
| `ingress.yaml` | nginx-ingress 路由（`/api` → API, `/` → Web） |
| `executor-rbac.yaml` | ServiceAccount + Role + RoleBinding（K8s Job 执行器权限） |
| `kustomization.yaml` | 一键 `kubectl apply -k` |

#### 执行器双模式

| 模式 | 设置 | 说明 |
|------|------|------|
| **Docker** | `EXECUTOR_MODE=docker` | 本地开发，通过 docker-py 创建容器 |
| **Kubernetes** | `EXECUTOR_MODE=k8s` | 生产环境，创建隔离的 K8s Job（需 RBAC） |

切换到 K8s 执行器：

```bash
kubectl edit configmap agentops-config -n agentops
# 改 EXECUTOR_MODE: "k8s"
kubectl rollout restart deployment agentops-api -n agentops
```

#### 注意事项

- **数据库持久化：** `deployment-db.yaml` 使用 `emptyDir`，Pod 重启数据丢失。生产环境替换为 `PersistentVolumeClaim`，或使用托管 PostgreSQL
- **Ingress：** 需要安装 nginx-ingress controller（`kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml`）
- **API Key：** 通过 `kubectl create secret generic` 或 sealed-secrets operator 注入，**不要将真实 key 提交到 secret.yaml**

## 核心特性

### Agent 运行时
- **ReAct 循环**：think → act → observe，while 循环直到 final answer
- **SSE 流式推送**：每一步实时推送到前端，RTK Query `onCacheEntryAdded` 消费
- **Policy 注入**：活跃 Policy 的 `system_prompt_suffix` / `tool_priority_bias` / `context_strategy` / `max_steps_override` 在每次 Agent 执行时注入
- **多框架适配**：LangChain + OpenAI Agents SDK 适配器，`AgentAdapter` 抽象协议
- **DeepSeek thinking mode**：完整支持 `reasoning_content` 往返传递

### 闭环自优化
- **Failure Analyzer**：四维度故障分类（Planning/Execution/Context/Budget），纯规则检测引擎
- **Policy Compiler**：差异化阈值 + 6 条组合规则 C1-C6 → 生成 PolicyPatch
- **Policy Store**：UUID 主键 + 自增 display 双字段版本管理
- **Auto-replay**：新 Policy 下重跑失败轨迹，三段式判定（≥10% 激活 / ≤-5% 回滚 / 中间待审）
- **自动触发**：事件驱动（≥10 条新 trajectory 或 ≥30 分钟），Agent 完成即检查
- **人工审核**：三维及以上同时超标 → PolicyReviewQueue → Approve / Reject

### 工具执行层
- **双模执行器**：Docker SDK + Kubernetes Job，`EXECUTOR_MODE` 环境变量切换
- **资源隔离**：每个 tool 调用在独立容器/Job 中执行
- **Tool Registry**：统一注册中心，支持 enable/disable 在线切换
- **MCP Server**：Tool Registry → MCP JSON-RPC stdio transport，轨迹写入 SQLite
- **Docker strict 模式**：可配置真实执行失败时是否 fallback mock 数据

### 可观测性
- **轨迹回放**：播放/暂停/步进/倍速（0.5x~4x），键盘快捷键（Space / ← →），连续进度条 + 迷你总览条 + 播放探照灯
- **逐步展示**：Step 卡片按序出现，播放中隐藏后续步骤，暂停可全局浏览
- **Token 用量**：每步 token_prompt + token_completion + context window 进度条（TokenDashboard）
- **多轨迹对比**：独立列布局，展开卡片不影响相邻 trace，工具差异 amber 高亮

### 评测系统
- **自动评分**：四维度——success_reward + cost_penalty + latency_penalty + tool_failure_penalty
- **Benchmark**：同一 task 并发跑 N 次（asyncio.gather），自动排名，best/worst 识别
- **Failure 分布图表**：Recharts 雷达图 + 柱状图，四维度可视化
- **Policy Timeline**：横向时间轴，绿色 active / 黄色 pending / 红色 reverted
- **训练数据导出**：OpenAI fine-tuning 格式、RLHF preference pair、JSONL 原始数据

### 设计系统
- Geist + Geist Mono 字体，暖黑底 (#030303) + 双 Accent 色彩（钴蓝 #4b8cf7 + 琥珀金 #f5a623）
- 蓝色管交互（按钮/链接/选中态/进度条），琥珀金管评判（Score/BEST/关键指标）
- 状态管理约定：服务端缓存 → RTK Query，跨路由 UI → Redux slice，页面内临时状态 → useState
- 浮空胶囊导航栏 + 环境光呼吸动画
- CSS timeline 流动动画 + framer-motion 编排 + 连续进度条 + 播放探照灯效果

## Rust CLI

```bash
# 离线分析
agentops-analyze top-tools          # 最频繁调用的 tool
agentops-analyze slowest-step       # 最慢 step 排名
agentops-analyze failure-pattern    # 失败模式分析

# 实时 Docker 日志聚合（tokio 并发）
agentops-analyze live-logs container1 container2 container3
```

## 评分公式

```
score = success_reward                        # 1.0 (success) | 0.0 (failed)
      - (total_tokens / 1000) × 0.01          # 每千 token 扣 0.01
      - (total_latency_ms / 1000) × 0.01      # 每秒扣 0.01
      - (failures / total_calls) × 0.5        # 全部 tool 调用失败 = 扣 0.5
```

## API

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/agents/run` | 提交 Agent 任务，返回 trajectory_id（支持可选 `policy_id`） |
| GET | `/api/agents/:id/stream` | SSE 端点，实时推送 step 事件 |
| GET | `/api/traces` | 轨迹列表（`?status=&tool=` 过滤） |
| GET | `/api/traces/:id` | 单条轨迹详情（含 score + breakdown） |
| GET | `/api/tools` | 已注册工具目录 |
| PATCH | `/api/tools/:name/toggle` | 在线启停工具 |
| POST | `/api/eval/score` | 对轨迹评分（支持自定义权重） |
| POST | `/api/eval/analyze` | 分析单条轨迹故障维度 |
| GET | `/api/eval/analysis/summary` | 最近 N 条轨迹聚合故障分布 |
| GET | `/api/eval/benchmarks` | 5 个预定义 benchmark 任务 |
| POST | `/api/eval/benchmark` | 批量运行 benchmark（并发 N 次） |
| GET | `/api/eval/export` | 导出训练数据（`?format=openai_sft\|rlhf_pair\|jsonl`） |
| POST | `/api/compare` | 多轨迹并排对比 |
| GET | `/api/eval/policies` | Policy 版本列表 |
| GET | `/api/eval/policies/active` | 当前活跃 Policy |
| GET | `/api/eval/policies/:id` | Policy 详情（含 patch + rationale） |
| POST | `/api/eval/policies/:id/approve` | 批准 Policy |
| POST | `/api/eval/policies/:id/reject` | 驳回 Policy |
| POST | `/api/eval/policies/compile` | 手动触发 Policy 编译 |
| POST | `/api/eval/policies/:id/replay` | 对 Policy 执行 auto-replay |
| GET | `/api/eval/policies/warmup-status` | 冷启动暖机进度 |

## 测试

```bash
# Backend — pytest（需 PostgreSQL 运行中）
cd backend && uv run pytest tests/ -v
# 96 tests: unit + DB integration + E2E smoke + closed-loop pipeline

# Frontend — vitest unit tests
cd frontend && pnpm vitest run
# 8 tests: usePolicyActions hook

# Frontend — TypeScript + Vite build
cd frontend && npx tsc -b && npx vite build

# Frontend — Playwright E2E（需后端 + 前端运行中）
cd frontend && pnpm playwright test
```

## License

MIT
