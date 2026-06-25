# Handoff — AgentOps Platform v0.1 完成，准备 v0.2

## 项目状态

v0.1 MVP 全部交付并验证通过。10 个 issue 全部完成，核心链路跑通。

### 已完成的模块

| 模块 | 位置 | 说明 |
|------|------|------|
| 后端 API | `backend/app/` | FastAPI + SQLAlchemy + asyncpg |
| Agent Runtime | `backend/app/runtime.py` | ReAct 循环，async generator |
| LLM Adapter | `backend/app/llm.py` | OpenAI-compatible 格式 |
| Context Manager | `backend/app/context_manager.py` | tiktoken + sliding window |
| Docker 执行层 | `backend/app/docker_executor.py` | docker-py 动态容器 |
| Tool Registry | `backend/app/tool_registry.py` | 3 个 demo tool 预注册 |
| Trajectory 持久化 | `backend/app/trajectory_repo.py` | PostgreSQL CRUD |
| SSE 流式推送 | `backend/app/event_bus.py` | asyncio.Queue pub/sub |
| 框架适配器 | `backend/app/adapters/` | LangChain + OpenAI Agents SDK |
| 前端 | `frontend/src/` | React + Vite + Tailwind v4 + Redux Toolkit |
| K8s 部署 | `infra/k8s/` | 9 个 YAML（Python 校验通过） |
| Rust CLI | `sdk/agentops-analyze/` | 3 个子命令，已编译通过 |
| Docker Compose | `infra/docker/docker-compose.yml` | db + api + web 三服务 |

### 设计文档

| 文档 | 路径 |
|------|------|
| PRD | `.scratch/agentops-platform-v0.1/PRD.md` |
| Issue 列表 | `.scratch/agentops-platform-v0.1/issues/01-*.md` ~ `10-*.md` |
| 术语表 | `CONTEXT.md` |
| 设计系统 | PRD §14（配色、字体、组件、动效） |

### 技术栈总结

- **后端**：Python 3.12, FastAPI, SQLAlchemy (async), asyncpg, docker, tiktoken, httpx
- **前端**：React 19, TypeScript, Vite 6, Tailwind v4, Redux Toolkit, RTK Query, TanStack Table, framer-motion, Lucide React
- **数据库**：PostgreSQL 16-alpine
- **Infra**：Docker Compose（dev），Kubernetes YAML（prod）
- **包管理**：uv（Python），npm（Node）
- **LLM**：OpenAI-compatible 格式（当前用 DeepSeek）

### 启动方式

```bash
# 1. 配置 .env（从 .env.example 复制）
cp .env.example .env
# 编辑 LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

# 2. 启动
docker compose -f infra/docker/docker-compose.yml up -d

# 3. 访问
# 前端: http://localhost:5173
# API:  http://localhost:8000
# API 文档: http://localhost:8000/docs
```

### 已修复的 Bug（本次 session 中）

1. `docker-py` → `docker`（包名更正）
2. `lucide-react` 版本 `^0.450.0` → `^1.21.0`
3. `created_at`/`started_at` 时区问题（TIMESTAMP → TIMESTAMPTZ）
4. Dockerfile `uv sync --frozen` 缺 `uv.lock`
5. Dockerfile `# syntax=docker/dockerfile:1` 导致 Docker Hub 超时
6. Docker Compose 端口冲突（系统 postgres 占 5432 → 改 5433）
7. `agentops-db` 不在 Docker 网络上（首次端口冲突残留）
8. `GET /api/traces` 返回 `{trajectories, total}` 但前端期望数组
9. `TrajectorySummary.duration` 前端有但后端不返回
10. `Step.started_at` 类型 SSE（float）vs DB（ISO string）不一致

### 项目配置

- `.claude/settings.local.json`：`"model": "deepseek-v4-flash"`, 权限规则
- `.env.example`：所有环境变量模板
- Docker volume `pgdata` 持久化数据库

---

## v0.2 计划（PRD Out of Scope）

按 PRD 路线图，v0.2 应包含：

1. **Kubernetes Job 作为 tool 执行单元**（当前 Docker SDK，需支持 K8s Job 模式）
2. **完善 trace logging**（token 计数字段、更细粒度的上下文记录）
3. **前端增强**：token 用量可视化面板、context window 展示
4. **多轨迹对比** `/compare` 页面的基础框架

v0.3 才做 RL/GRPO-lite 优化循环。

---

## 下一个 session 建议

1. 从 `/grill-with-docs` 开始，对 v0.2 范围做需求澄清
2. 然后 `/to-prd` → `/to-issues` → `/implement`

## Suggested Skills

- `grilling` — v0.2 需求澄清
- `to-prd` — 生成 v0.2 PRD
- `to-issues` — 拆分 issue
- `implement` — 执行实现
