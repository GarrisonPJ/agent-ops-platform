<p align="center">
  <a href="README.zh-CN.md">中文</a> |
  <a href="README.md">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.4-blue?style=flat-square" alt="version">
  <img src="https://img.shields.io/badge/python-3.12-3776AB?style=flat-square&logo=python" alt="python">
  <img src="https://img.shields.io/badge/react-19-61DAFB?style=flat-square&logo=react" alt="react">
  <img src="https://img.shields.io/badge/tests-104_passed-2dd4bf?style=flat-square" alt="tests">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="license">
</p>

<h1 align="center">AgentOps Platform</h1>
<p align="center"><strong>AI Agent 基础设施平台</strong> — 任务执行 → 轨迹回放 → 闭环自优化 → 训练数据导出</p>
<p align="center">自驱动的 Agent 评测与改进系统，让 Agent 在运行中自动变得更聪明</p>

---

## 页面预览

<p align="center">
  <img src="docs/screenshots/traces.png" width="48%" alt="轨迹列表">
  <img src="docs/screenshots/compare.png" width="48%" alt="多轨迹对比">
</p>
<p align="center">
  <img src="docs/screenshots/eval-policies.png" width="48%" alt="策略时间线">
  <img src="docs/screenshots/eval-failures.png" width="48%" alt="故障分析">
</p>

---

## 闭环自优化（v0.4 核心能力）

平台自动发现失败模式、编写改进策略、A/B 验证、采纳或回滚——全程无需人工干预。

```
Task → Run(Policy vN) → Trajectory → Score
  ↓
Failure Analyzer   → 四维度故障分类（Planning / Execution / Context / Budget）
  ↓
Policy Compiler    → 规则引擎生成 PolicyPatch（prompt_suffix / max_steps / strategy / tool_bias）
  ↓
Policy Store       → 版本化存储（v1→v2→v3）+ 人工审核队列
  ↓
Auto-replay        → 新 Policy 下重跑失败轨迹 → 三段式判定（激活 / 回滚 / 待审）
  ↓
Policy Router      → 注入下一次 Agent 执行
  ↓
[循环]
```

触发条件：≥10 条新 trajectory 或距上次编译 ≥30 分钟。策略以 **prompt 级注入**（不修改模型权重），适配任何云端 LLM。

---

## 快速开始

```bash
cp .env.example .env   # 编辑 LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
docker compose -f infra/docker/docker-compose.yml up -d
open http://localhost:5173
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| **前端** | React 19, TypeScript, Vite 6, Tailwind v4, Redux Toolkit, RTK Query, TanStack Table, framer-motion, Phosphor Icons, Recharts |
| **后端** | Python 3.12, FastAPI, SQLAlchemy (async), asyncpg, docker-py, tiktoken, httpx |
| **数据库** | PostgreSQL 16-alpine |
| **LLM** | OpenAI-compatible（DeepSeek / OpenAI / 任何兼容 provider） |
| **执行器** | Docker SDK + Kubernetes Job（双模） |
| **测试** | pytest (96) + vitest (8) + Playwright E2E |
| **部署** | Docker Compose / kind K8s / Cloudflare Tunnel |

---

## 核心特性

### Agent 运行时
- **ReAct 循环** — think → act → observe，while 直到 final answer
- **SSE 流式推送** — 每步实时推送到前端
- **Policy 注入** — 活跃 Policy 的 suffix / tool_bias / context_strategy / max_steps 自动注入
- **多框架适配** — LangChain + OpenAI Agents SDK
- **DeepSeek 思考模式** — 完整支持 `reasoning_content` 往返传递

### 闭环自优化
- **Failure Analyzer** — 四维度多标签故障分类，纯规则引擎
- **Policy Compiler** — 差异化阈值 + 6 条组合规则 C1-C6
- **Policy Store** — UUID + 自增 display 双字段版本管理
- **Auto-replay** — 新 Policy 下重跑失败轨迹，≥10% 激活 / ≤-5% 回滚
- **自动触发** — Agent 完成即检查，事件/时间双驱动
- **人工审核** — 三维超标 → ReviewQueue → Approve / Reject

### 工具执行层
- Docker + K8s Job 双模执行器，资源隔离
- Tool Registry 统一注册，在线 enable/disable
- MCP Server — Tool Registry → MCP JSON-RPC stdio transport

### 可观测性
- **轨迹回放** — 播放/暂停/步进/倍速（0.5x~4x），键盘快捷键
- **Token 用量** — 每步 prompt/completion tokens + context window 进度条
- **多轨迹对比** — 独立列布局，工具差异琥珀色高亮

### 评测系统
- **四维评分** — success + cost + latency + tool_failure
- **Benchmark** — asyncio.gather 并发 N 次，自动排名
- **Failure 图表** — Recharts 雷达图 + 柱状图
- **Policy Timeline** — 时间轴可视化，绿/黄/红状态区分
- **训练数据导出** — OpenAI SFT / RLHF 对 / JSONL

### 设计系统
- Geist + Geist Mono 字体，暗色主题 (#030303)
- 钴蓝 (#4b8cf7) + 琥珀金 (#f5a623) 双 accent
- 状态管理：RTK Query → Redux → useState 三级约定
- motion 编排 + CSS timeline 动画 + 进度条探照灯

---

## 项目结构

```
agent-ops-platform/
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI 路由 + 应用入口
│   │   ├── orchestrator.py       # Agent 依赖图工厂
│   │   ├── runtime.py            # ReAct 循环 + Policy 注入
│   │   ├── agent_runner.py       # Agent 生命周期（评分/取消/闭环触发）
│   │   ├── failure_analyzer.py   # 故障分析器（四维度纯规则）
│   │   ├── policy_compiler.py    # Policy 编译器 + PolicyPatch
│   │   ├── policy_store.py       # Policy CRUD + Policy 类型
│   │   ├── policy_pipeline.py    # 闭环管道入口 run_closed_loop
│   │   ├── auto_replay.py        # 自动回放 + 三段式判定
│   │   ├── scoring.py            # 评分引擎（纯函数）
│   │   ├── benchmarks.py         # Benchmark 任务定义
│   │   ├── exporters.py          # 训练数据导出
│   │   ├── llm.py                # LLM Provider 适配
│   │   ├── context_manager.py    # 上下文窗口管理 + strategy
│   │   ├── trajectory_repo.py    # 轨迹 CRUD
│   │   ├── models.py             # ORM（trajectories/steps/policies）
│   │   ├── config.py             # 配置 + 闭环阈值常量
│   │   └── serializer.py         # 序列化（render_scoring_view）
│   └── tests/                    # 96 tests
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── RunPage.tsx           # Agent 执行
│   │   │   ├── TraceListPage.tsx     # 轨迹列表
│   │   │   ├── ComparePage.tsx       # 多轨迹对比
│   │   │   ├── EvalPage.tsx          # 评测面板外壳
│   │   │   └── eval/
│   │   │       ├── BenchmarkTab.tsx  # Benchmark + 排名
│   │   │       ├── FailuresTab.tsx   # Failure 分布图表
│   │   │       └── PoliciesTab.tsx   # Policy 时间线 + 审核
│   │   ├── components/              # 22+ UI 组件
│   │   ├── hooks/                   # usePolicyActions, useAgentStream...
│   │   └── services/                # RTK Query API
│   └── tests/                       # Playwright E2E
├── infra/                           # Docker Compose + K8s 配置
├── docs/                            # 设计文档 + 截图
└── .scratch/                        # PRD + Issue 追踪
```

---

## API

### Agent

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/agents/run` | 提交任务（可选 `policy_id`） |
| `GET` | `/api/agents/:id/stream` | SSE 实时推送 |
| `POST` | `/api/agents/:id/cancel` | 取消运行中任务 |

### 轨迹

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/traces` | 列表（`?status=&tool=`） |
| `GET` | `/api/traces/:id` | 详情 + score + breakdown |
| `POST` | `/api/compare` | 多轨迹并排对比 |

### 工具

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/tools` | 工具目录 |
| `PATCH` | `/api/tools/:name/toggle` | 在线启停 |

### 评测与分析

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/eval/score` | 评分（支持自定义权重） |
| `POST` | `/api/eval/analyze` | 故障维度分析 |
| `GET` | `/api/eval/analysis/summary` | 聚合故障分布 |
| `GET` | `/api/eval/benchmarks` | Benchmark 任务 |
| `POST` | `/api/eval/benchmark` | 并发跑 Benchmark |
| `GET` | `/api/eval/export` | 导出训练数据 |

### Policy 管理

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/eval/policies` | 版本列表 |
| `GET` | `/api/eval/policies/active` | 当前活跃 Policy |
| `GET` | `/api/eval/policies/:id` | Policy 详情 |
| `POST` | `/api/eval/policies/:id/approve` | 批准 |
| `POST` | `/api/eval/policies/:id/reject` | 驳回 |
| `POST` | `/api/eval/policies/compile` | 手动编译 |
| `POST` | `/api/eval/policies/:id/replay` | Auto-replay |
| `GET` | `/api/eval/policies/warmup-status` | 冷启动进度 |

---

## 测试

```bash
# 后端 — 96 tests
cd backend && uv run pytest tests/ -v

# 前端 — 8 unit tests
cd frontend && pnpm vitest run

# TypeScript
cd frontend && npx tsc -b

# E2E（需后端运行）
cd frontend && pnpm playwright test
```

---

## License

MIT
