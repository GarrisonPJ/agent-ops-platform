# AgentOps Platform — Context

> 领域知识、术语表、ADR 索引。由 `/grill-with-docs` 和 `/domain-modeling` 维护。

## 术语表

| 术语 | 定义 |
|------|------|
| Trajectory | Agent 单次执行的全步骤记录，包含 task、steps 数组、status、created_at |
| Step | 轨迹中的单步，包含 thought、action（ToolCall 或 null）、observation、latency_ms |
| Tool Call | Agent 对某个 tool 的单次调用，包含 id、name、arguments |
| Tool Registry | 已注册工具的统一目录，每个 tool 同时承载 LLM schema 和 Docker 执行配置 |
| ReAct | Agent 循环模型：think → act → observe，while 循环直到 final answer |
| Context Manager | 自管 context window 的模块，tiktoken 估算 + sliding window 策略 |
| Agent Adapter | 多框架适配协议，将平台 tool schema 翻译为特定框架的 tool 对象 |
| GRPO-lite | 轻量级 Group Ranking Policy Optimization（v0.3 计划） |
| SSE | Server-Sent Events，Agent 执行时后端推 step 到前端的流式协议 |
| Failure Dimension | Agent 失败的根因分类维度：Planning / Execution / Context / Budget，一个 Step 可归属多个维度（多标签），failure_rate = 该维度失败步数 / 总步数 |
| FailureReport | Failure Analyzer 输出结构，含四维度计数+evidence、dominant_failure_type、overall_failure_rate |
| PolicyPatch | Policy Compiler 输出的可执行策略补丁，含 system_prompt_suffix、tool_priority_bias、context_strategy、max_steps_override |
| Failure Detection | 四维度检测规则：Planning=同名tool≥3次无差异；Execution=observation含error/exception/timeout或latency>60s；Context=used/limit>0.95或含truncated信号；Budget=step_count==max_steps且status≠success |
| Policy Version ID | 双字段策略：version_id=UUID(PK，内部引用)，version_display=自增v1/v2/v3(人类可读)，parent_version_id=FK指向version_id |
| Policy Compile Rules | 单维度独立阈值(Exec>0.25/Budget>0.20/Planning>0.35/Context>0.40) + 6条两两组合规则C1-C6 + 三维以上标记needs_human_review不自动编译 |
| max_steps_override | effective = min(settings.llm_max_steps + delta, settings.llm_max_steps × 1.5, 20)，防止连续编译无限膨胀 |
| Auto-replay | 触发条件：score_delta ≥ 10%（相对提升率）。首版(parent=None)默认replay。仅重跑source_trajectories。并发控制复用Semaphore(3) |
| Policy Rollback | score_delta ≥ +10% → 激活vN；-5% ~ +10% → pending_review不自动激活；≤ -5% → 自动回滚到vN-1。policy_versions加status字段(active/pending_review/reverted)，同时仅一条active |
| tool_priority_bias | 在runtime.py层做weight注入（非llm.py），按bias权重重排tool list传给LLM，保持LLM层通用性 |
| Frontend Chart | Recharts（纯SVG、声明式React API、暗色token无缝映射）。Policy Timeline手写（flexbox+Tailwind横向时间轴卡片节点） |
| Context Strategy | ContextManager.manage()新增strategy参数：default(不变)/increase_recent_weight(预算×0.7)/aggressive_eviction(预算×0.5 + observation>500字符截断)。Policy Router透传 |
| Closed-Loop Concurrency | (1)Policy快照隔离：_execute_agent()启动时获取policy一次，全程不重查；(2)Semaphore分池：benchmark和replay独立Semaphore；(3)编译前取DB快照 |
| Human Review Queue | 复用policy_versions.status='pending_review'作为队列。API: GET /api/eval/policy/review-queue, POST .../approve, POST .../reject。前端Timeline黄色节点+Approve/Reject按钮。v1不做通知系统 |
| Closed-Loop API | 12个新endpoint按P0-P4分布：Failure Analyzer(2)、Policy Compiler(1)、Policy Store(5)、Review Queue(3)、Auto-replay(2) |
| Bootstrapping | 冷启动：trajectory≥10条后自动触发首次编译。parent_version=null，source为全部已完成trajectory。首版自动replay验证。暖机中UI显示进度条 |
| Loop Trigger | 事件驱动+批量：每次Agent完成后检查——(新增trajectory≥10 OR 距上次编译≥30min)→触发闭环流水线。在_execute_agent()完成处挂_maybe_trigger_loop() hook。v1不引入独立调度器 |
| Prompt Injection | system_prompt_suffix通过\n\n追加到_REACT_SYSTEM_PROMPT末尾，在runtime.py构造self._history时注入。ContextManager无需修改 |
| Pipeline Error Handling | Step1-2(analyze/compile)失败→静默放弃本轮；Step4(replay)失败→标记pending_review；永不阻塞Agent执行。闭环流水线是独立后台任务 |
| DB Migration | ORM模型(create_all自动建表) + _MIGRATIONS显式IF NOT EXISTS双保险。version_display在policy_store.create_policy()事务内通过COUNT(*)+1生成 |
| Testing Strategy | Failure Analyzer+Policy Compiler=纯函数测试(复用conftest fixture)；Policy Store=DB集成测试；Policy Router+Auto-replay=集成测试；全链路=E2E。P0-P2覆盖率≥85%。v1不Mock LLM |

## ADR 索引

| 编号 | 标题 | 状态 |
|------|------|------|
| — | — | — |
