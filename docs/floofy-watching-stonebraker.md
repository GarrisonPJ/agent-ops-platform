# AgentOps 闭环架构实现方案

## Context

当前系统是**开环**：Task → Agent Run → Trajectory → Score → Export → END。需要升级为**闭环**：Score → Failure Analysis → Policy Improvement → Next Run，实现 Agent 的自驱动改进。

架构文档：`docs/agentops_closed_loop_architecture.md`
领域模型：`CONTEXT.md`（已通过 19 项 grilling 决策完善）

## 设计决策摘要（19 项已达成共识）

详见 `CONTEXT.md` 术语表。关键决策：
- **Failure Analyzer**：4 维度多标签分类（Planning/Execution/Context/Budget），纯规则检测
- **Policy Compiler**：v1 规则引擎，差异化阈值 + 6 条组合规则 C1-C6，三维超标走人工审核
- **Policy Store**：UUID 主键 + 自增 display 双字段版本号
- **Policy Router**：runtime.py 注入 system_prompt_suffix/context_strategy/tool_priority_bias/max_steps
- **Auto-replay**：score_delta ≥ 10% 激活，≤ -5% 回滚
- **前端**：Recharts（图表）+ 手写 PolicyTimeline
- **触发**：事件驱动（≥10 条新 trajectory OR ≥30min），在 `run_agent_background()` 返回后挂钩
- **冷启动**：≥10 条 trajectory 自动触发首次编译，首版默认 replay

## P0：Failure Analyzer（2-3 人天）

### 创建文件
- `backend/app/failure_analyzer.py` — `analyze_trajectory(dict) → FailureReport`
  - `_detect_planning_failures()` — 同名 tool ≥3 次且参数无差异
  - `_detect_execution_failures()` — observation 含 error/exception/timeout 关键词，或 latency > 60s
  - `_detect_context_failures()` — context_window used/limit > 0.95，或 truncated 信号
  - `_detect_budget_failures()` — step_count == max_steps 且 status ≠ success
  - `FailureReport` dataclass：dimensions/dict, dominant/str, evidence/list, needs_human_review/bool
- `backend/tests/test_failure_analyzer.py` — 纯函数测试（复用 conftest.py 现有 fixture）

### 修改文件
- `backend/app/main.py` — 新增 `POST /api/eval/analyze` + `GET /api/eval/analysis/summary`
- `backend/tests/conftest.py` — 新增 `planning_loop_trajectory`, `context_overflow_trajectory` fixture

### 验证
```bash
cd backend && pytest tests/test_failure_analyzer.py -v
# 目标：≥6 条测试覆盖正常/边界/异常路径
```

## P1：Policy Compiler v1（2-3 人天）

### 创建文件
- `backend/app/policy_compiler.py` — `compile_policy(report, current_version, max_steps) → PolicyPatch | None`
  - 单维度阈值（定义在 `config.py`，不从环境变量读取）：
    - `POLICY_THRESHOLD_EXECUTION = 0.25`
    - `POLICY_THRESHOLD_BUDGET = 0.20`
    - `POLICY_THRESHOLD_PLANNING = 0.35`
    - `POLICY_THRESHOLD_CONTEXT = 0.40`
  - 6 条组合规则 C1-C6（Exec+Budget, Exec+Planning, Exec+Context, Budget+Planning, Budget+Context, Planning+Context）
  - 三维以上 → `needs_human_review=True`，不自动编译
  - `max_steps_override`：`effective = min(settings.llm_max_steps + delta, int(settings.llm_max_steps * 1.5), 20)`
    - `delta` 默认值 0，当 Budget 维度 >0.20 时 `delta = 3`，当 Execution 维度 >0.40 时 `delta = max(delta, 2)`
  - confidence = f(高严重度维度数, 总 failure_rate 和)
- `backend/tests/test_policy_compiler.py` — 纯函数测试（Mock FailureReport 输入）

### 修改文件
- `backend/app/main.py` — 新增 `POST /api/eval/policy/compile`
- `backend/app/config.py` — 新增编译阈值常量（`POLICY_THRESHOLD_*`）+ 回滚阈值常量（`POLICY_ROLLBACK_ACTIVATE = 0.10`, `POLICY_ROLLBACK_REVERT = -0.05`）

### 验证
```bash
cd backend && pytest tests/test_policy_compiler.py -v
# 目标：≥6 条测试，覆盖全部 6 条组合规则 + 边界阈值
```

## P2：Policy Store（2-3 人天）

### 创建文件
- `backend/app/policy_store.py` — `PolicyStore` CRUD 类
  - `get_active_policy() → PolicyVersion | None`
  - `create_policy(patch_dict) → PolicyVersion`
    - **并发策略**：依赖 PostgreSQL 事务隔离 + `version_display` 列的 `UNIQUE` 约束。`create_policy()` 在事务内执行 `SELECT COUNT(*) FROM policy_versions` → 计算 display → `INSERT`，若 `UNIQUE` 冲突则重试一次（`IntegrityError` catch + retry）。不使用 asyncio.Semaphore。
  - `update_policy_status(version_id, status, score_delta) → bool`
  - `list_policies(status, limit, offset) → list[PolicyVersion]`
- `backend/tests/test_policy_store.py` — DB 集成测试

### 修改文件
- `backend/app/models.py` — 新增 `PolicyVersion`（version_id UUID PK, version_display UNIQUE, parent_version, patch JSONB, status, score_delta, created_at）+ `TrajectoryPolicyMap`（trajectory_id + policy_version_id 联合 PK）
- `backend/app/database.py` — `_MIGRATIONS` 追加 2 条 `CREATE TABLE IF NOT EXISTS`
- `backend/app/main.py` — 新增 7 个 endpoint：`GET /api/eval/policies`, `GET /api/eval/policies/active`, `GET /api/eval/policies/{id}`, `POST /api/eval/policies/{id}/status`, `GET /api/eval/policies/trajectory/{tid}`, `POST /api/eval/policies/compile-and-store`

### 验证
```bash
cd backend && pytest tests/test_policy_store.py -v
# 目标：≥4 条测试覆盖 CRUD + UNIQUE 冲突重试
```

## P3：Policy Router + 闭环触发（3-4 人天）

### 注入点概览

```
AgentOrchestrator.run_background() / run_benchmark()
  ├── PolicyStore.get_active_policy()  ← 获取当前 policy
  ├── run_agent_background(policy=...)  ← 传入
  │   └── _execute_agent(policy=...)
  │       ├── runtime._policy_patch = policy.patch  ← 存入 runtime
  │       ├── context_manager.manage(strategy=...)   ← 透传
  │       └── runtime.run() 内部:
  │           ├── system_content += policy.suffix   ← Prompt 注入
  │           └── self._sort_tools_by_priority()     ← Tool 排序
  └── await _maybe_trigger_closed_loop()  ← 生命周期边界挂钩
```

### 修改文件

- `backend/app/runtime.py` — 3 处注入
  - `AgentRuntime.__init__()` 新增 `_policy_patch`, `_context_strategy`, `_tool_priority_bias` 属性
  - `run()` 中 `self._history` 构造时：`system_content += "\n\n[Policy] " + suffix`
  - `run()` 中 `context_manager.manage(..., strategy=...)` 透传
  - 新增 `_sort_tools_by_priority()` 方法

- `backend/app/context_manager.py` — `manage()` 新增 `strategy` 参数
  - `default` → 不变
  - `increase_recent_weight` → `effective_max = max_tokens × 0.7`
  - `aggressive_eviction` → `effective_max = max_tokens × 0.5` + observation > 500 字符截断

- `backend/app/agent_runner.py` — 3 处修改
  - `_execute_agent()` 签名新增 `policy: dict | None` 参数
  - `_execute_agent()` 内部注入 policy patch 到 runtime（suffix/strategy/bias/max_steps）
  - `run_agent_background()` 在 `await _execute_agent(...)` **返回后**，挂 `await _maybe_trigger_closed_loop(repo, store)` 作为尾调用（sync await，非 fire-and-forget）。理由是 `run_agent_background()` 本身就是 `asyncio.create_task()` 的后台任务，尾调用不阻塞主循环；异常有完整 traceback，不会静默吞噬。
  - 新增 `_maybe_trigger_closed_loop()` 函数：检查冷启动 + 事件触发条件，调用 analyze→compile→store 流水线

- `backend/app/orchestrator.py` — **P3 一次性完成全部 policy-aware 改造，P4 不再修改此文件**
  - `run_background()` 中 `PolicyStore.get_active_policy()` → `run_agent_background(policy=...)`
  - `run_benchmark()` 同理
  - `run_agent_background()` 签名新增 `policy` 参数
  - 新增 `run_agent_with_policy(task, policy, trajectory_id)` 方法供 P4 auto-replay 直接调用（透传到 `_execute_agent(policy=...)`）

- `backend/app/main.py` — `POST /api/agents/run` 支持可选 `policy_id` 字段

### 验证
```bash
cd backend && pytest tests/test_policy_router.py -v
# 目标：验证 policy patch 注入到 runtime._history[0].content、strategy 透传、tool 排序
```

## P4：Auto-replay + 回滚（2-3 人天）

### 创建文件
- `backend/app/auto_replay.py`
  - `replay_trajectories(session, policy, trajectory_ids) → dict` — 调用 `orchestrator.run_agent_with_policy()` 在新 policy 下重跑失败轨迹
  - `evaluate_policy_effectiveness(session, replay_results, policy_id) → str` — 三段式判定（阈值来自 `config.py`）
    - avg_delta ≥ `POLICY_ROLLBACK_ACTIVATE` (0.10) → active
    - min_delta ≤ `POLICY_ROLLBACK_REVERT` (-0.05) → rollback
    - 中间 → pending_review
  - `trigger_auto_replay(session, policy_id) → dict` — 全流程编排

### 修改文件
- `backend/app/agent_runner.py` — `_maybe_trigger_closed_loop()` 末尾：若新 policy 创建成功且为首版（v1），调用 `trigger_auto_replay()`
- `backend/app/main.py` — 新增 `POST /api/eval/policies/{id}/replay`
- **不修改 `orchestrator.py`**（已在 P3 完成全部改造）

### 验证
```bash
cd backend && pytest tests/test_auto_replay.py -v
# 目标：覆盖 activate/rollback/pending_review 三条路径
```

## P5：前端组件（3-4 人天）

### 路由策略
**不作为新路由，作为 `/eval` 页面内新 Tab。** 当前 `EvalPage.tsx` 已有 Benchmark/Results/Export 的 tab 式 conditional render 模式。新增两个 tab：
- **"Failures"** tab → `FailureDistributionChart` + failure summary
- **"Policies"** tab → `PolicyTimeline` + `PolicyDetailPanel`

通过 URL search param `?view=failures` / `?view=policies` 驱动，无需修改路由表。

### 修改文件
- `frontend/src/types/index.ts` — 新增 `FailureEvidence`, `FailureAnalysisResponse`, `FailureAnalysisSummary`, `PolicySummary`, `PolicyDetail`, `PolicyPatch`
- `frontend/src/services/api.ts` — 新增 RTK Query endpoints（analyze/summary/policies CRUD/replay/compile-and-store）
- `frontend/src/pages/EvalPage.tsx` — 新增 "Failures" / "Policies" 两个 tab，纳入已有 tab 切换逻辑

### 创建文件
- `frontend/src/components/FailureDistributionChart.tsx` — Recharts `<RadarChart>` + `<BarChart>`，4 维度分布，含 loading/error/empty 3 态
- `frontend/src/components/PolicyTimeline.tsx` — 手写 flexbox 横向时间轴，每节点显示 version/status/confidence/delta，绿色 active / 黄色 pending / 红色 reverted
- `frontend/src/components/PolicyDetailPanel.tsx` — 选中 policy 的 patch diff 展示
- `frontend/src/components/PolicyReviewQueue.tsx` — pending_review 策略列表，Approve/Reject 按钮

### 验证
```bash
cd frontend && npx tsc --noEmit
cd frontend && npx playwright test tests/eval.spec.ts
```

## P6：LLM-Assisted Policy Generation（2-3 人天）⚠️ 未详细规格化

> **状态：占位。** 本阶段仅在 P1 规则引擎稳定运行后启动。以下为方向性描述，不作为精确估时依据。实现前需独立规格评审。

### 修改文件
- `backend/app/policy_compiler.py` — 新增 `compile_policy_llm()`，LLM 失败时 fallback 到规则引擎

## 文件总览

| 优先级 | 新建（8 个） | 修改（7 个） |
|--------|-------------|-------------|
| P0 | `failure_analyzer.py`, `test_failure_analyzer.py` | `main.py`, `conftest.py` |
| P1 | `policy_compiler.py`, `test_policy_compiler.py` | `main.py`, `config.py` |
| P2 | `policy_store.py`, `test_policy_store.py` | `models.py`, `database.py`, `main.py` |
| P3 | `test_policy_router.py` | `agent_runner.py`, `runtime.py`, `context_manager.py`, `orchestrator.py`, `main.py` |
| P4 | `auto_replay.py`, `test_auto_replay.py` | `agent_runner.py`, `main.py` |
| P5 | `FailureDistributionChart.tsx`, `PolicyTimeline.tsx`, `PolicyDetailPanel.tsx`, `PolicyReviewQueue.tsx` | `types/index.ts`, `api.ts`, `EvalPage.tsx` |
| P6 | — | `policy_compiler.py` |

### P3/P4 边界明确
- **P3** 完成 `orchestrator.py` 全部 policy 改造（`run_background(policy=...)` + `run_agent_with_policy()`）
- **P4** 只调用 `orchestrator.run_agent_with_policy()`，**不修改 orchestrator.py**
- 闭环钩子位于 `run_agent_background()` 尾调用（sync await），不在 `_execute_agent()` 内部

## 执行顺序与依赖

```
P0 (Failure Analyzer)
  ↓
P1 (Policy Compiler)
  ↓
P2 (Policy Store)
  ↓
P3 (Policy Router + 闭环触发 + orchestrator 全量改造)
  ↓
P4 (Auto-replay + 回滚)
  ↓
P5 (前端) ← 可与 P3-P4 并行
P6 (LLM v2) ← P1 稳定后，需独立规格评审
```

**预估总工作量**：14-20 人天（P6 不含在内，需独立评估）

## 验证

```bash
# 后端单元+集成测试
cd backend && pytest tests/test_failure_analyzer.py tests/test_policy_compiler.py tests/test_policy_store.py tests/test_policy_router.py tests/test_auto_replay.py -v --cov=app

# 前端类型检查
cd frontend && npx tsc --noEmit

# 前端 E2E
cd frontend && npx playwright test tests/eval.spec.ts

# 全链路 E2E（需要运行中的 FastAPI）
cd backend && pytest tests/ -v --cov=app
```
