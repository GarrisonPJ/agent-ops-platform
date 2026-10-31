# 术语表（学习用）

英文定义以仓库根目录 `CONTEXT.md` 为准。若两边用词冲突，改本文件。

| 中文 | 英文 | 现在指什么 |
|---|---|---|
| 控制面 | Control Plane | FastAPI。只有它读写 PostgreSQL。 |
| 执行面 | Runner | Rust 进程。负责领任务、看管子进程、心跳、上传事件。不碰数据库。 |
| 实验 | Experiment | 一次评测配置：哪个环境、哪种执行模式、以后还有哪个 agent。 |
| 运行 | Run | 一次具体执行。有生命周期、分数、不可变的 EvaluationSpec。 |
| 基线 | Baseline | 不带候选策略的 Run。 |
| 回放 | Replay | 从基线派生、带一份 PolicyPatch、其余输入相同的 Run。是重新执行，不是播放录像。 |
| 租约 | Lease | Runner 领取任务后的占用凭证。过期就不能再写这条 Run。 |
| 尝试 | Attempt | 同一次 Run 上的第几次领取。崩溃后续跑会换新的 Attempt，旧租约作废。 |
| 事件 | RunEvent | 落库的有序事实。主键是 `run_id + sequence`。 |
| 轨迹 | Trace | 界面上按顺序展示的事件，不是另一份真相。 |
| 环境 | Environment | 任务、工具、确定性状态、种子、故障注入、终态断言；H1 由 `app.env_server` 提供 loopback MCP。协议字段仍叫 `scenario_id`。 |
| 场景 | Scenario | Environment 的 Phase 1/协议历史名字。canonical H1 三个迁移为 `.v2`，`order-support.v1` 保持自己的身份。 |
| 被测 Agent | Agent-under-test | 独立进程，只通过 MCP 决定调用哪个 Environment 工具。不负责记分、不写可信事件。 |
| 参考 Agent | reference agent | 我们自己的 MCP 工具调用循环，接 Runner 上显式配置的 OpenAI-compatible 端点；不在 CI 调 live model。 |
| 夹具 Agent | fixture agent | 写死的 MCP 轨迹，给 CI/Golden 用，不调用模型。 |
| Agent 注册表 | operator agent registry | `config/agents.toml` 中的受信 argv；API 只接受已登记的 `agent_id`，请求不能提交命令。 |
| capability token | Environment capability | 每个 logical Run 的 loopback MCP bearer token；只经 Runner 注入 Agent/Environment，terminal ACK 前保留，ACK 后清理。 |
| 试验组 | Trial Group | 同一规格重复 n 次。环境种子固定，模型采样不固定。 |
| pass^k | pass^k | k 次独立试验全部成功的概率。估计量见 ADR-0010。 |
| 策略 | Policy | 从失败基线得出的有限补丁：提示修改、工具优先级、最大步数。 |
| 回归用例 | RegressionCase | 把一条失败 Run 留下来，激活前必须再跑过。 |
| 分析器 | Failure analyzer | 只看已落库事件的规则分类，不调用模型。 |
| 校准门 | Calibration gate | CI 里的六个受控合同，防止分析器改坏。不是准确率声明。 |
| 一致性研究 | Agreement study | 人（目前是作者本人）标注真实 Run，再和分析器对比。Phase 2 H5。 |
| 记录预览 | Recorded Preview | 前端用录好的事件开发 UI，不实现后端规则。 |

H1 还必须区分 Agent 的正常退出与 Control Plane 的终态裁决：submit、check、完整可信证据、assertion aggregate 和无异常失败全部满足后，CP 才能决定成功。Environment 健康 teardown 是计划内清理，不是 Agent 自报成功；Runner 只监督/转发，不自行评分。terminal ACK 前不能删除 logical Run 的 token handoff 记录，ACK 后才由 Runner 清理。Phase 2 计划中的不变量（23–28）在对应阶段落地前，还不是代码契约。见 `CONTEXT.md` 文末。
