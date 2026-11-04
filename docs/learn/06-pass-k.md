# Trial group 与 pass^k：一次成功不等于可靠

决策见 [ADR-0010](../adr/0010-trial-groups-and-pass-k.md) 及其 H3-1、H3-2 修订。
真实运行记录见 [H3 验收](../history/h3-pass-k-live-2026-11-04.md)。

## 1. 为什么要重复试验

fixture Agent 是确定性的，一次 replay 成功就能说明问题；本地模型会采样，同样的输入
可能这次成功、下次失败。不变量 7（一次 replay 成功且分数提升就算验证）对它会误判。
所以 H3 让同一个任务在同一个 Environment 上重复跑 `n` 次，再报告 pass^k。

## 2. 一个 trial group 是什么

- `POST /api/experiments/{id}/trials`，body 是 `n`（1..20）、`k`（1..n）和可选 `seed`。
  入口在 `backend/app/phase1_service.py` 的 `create_trial_group`。
- 一行 `trial_groups`（`backend/app/phase1_models.py` 的 `TrialGroup`）记下 `n`、`k`、seed；
  成员是普通 Run，带 `trial_group_id` 和从 1 开始的 `trial_index`，各有自己的 RunnerJob。
  迁移是 `backend/alembic/versions/0010_trial_groups.py`。
- 成员的 `EvaluationSpec` 除 `run_id` 外完全相同，协议 v1 不变。Environment seed 固定，
  所以工具观察在各 trial 之间一致；模型采样不强制确定。Agent 看不出自己是不是 trial。

## 3. 估计量

`n` 次 trial 里有 `c` 次成功时（`backend/app/trials.py` 的 `pass_hat_k`）：

```text
pass^k = C(c, k) / C(n, k)
```

它等于"从 n 次 trial 里任取 k 次，全部成功的比例"，是"k 次独立尝试全部成功"概率的
无偏估计（τ-bench，Yao et al., arXiv:2406.12045）。几个性质：

- `k = 1` 时就是成功率 `c / n`。
- `c < k` 时为 0：任取 k 次不可能全成功。
- 固定 `c` 时随 `k` 增大不增：要求越多次全成功，越难。

`backend/phase1_tests/test_trials_estimator.py` 对 n ≤ 7 穷举所有 k 子集，验证公式与
"全成功子集占比"相等。成功只认 terminal status `succeeded`，它已经要求注册 aggregate
>= 1.0（ADR-0012），不另看质量分。

## 4. 什么时候发布

聚合在读的时候算（`aggregate`），不存库，所以不会和成员 Run 不一致：

- 有成员还没到终态：`state = running`，不发布 pass^k。
- 有成员被取消：`state = cancelled`，也不发布。取消是运维动作，不是 Agent 的一次失败。
- 全部终态且没有取消：`state = complete`，给出 `successes`、pass^1、pass^k、平均分、平均步数。
  平均分里失败按 0 计（不是用失败 Run 的质量分）。

Experiment 页（`frontend/src/components/TrialGroupsSection.tsx`）只展示这些值；Trace UI 冻结。

## 5. 组级策略验证

- 候选策略的来源 Run 属于某个组时，`replay_policy` 建一个候选组，`n`、`k`、seed 与 baseline
  组相同，第 i 个候选成员 replay 第 i 个 baseline 成员。baseline 组未发布前返回
  `409 BASELINE_GROUP_NOT_PUBLISHED`。一个 baseline 组只产生一个候选策略。
- 候选组最后一个成员结束时判定：`n = 1` 沿用不变量 7；`n > 1` 要求平均分差为正，并且
  候选 pass^k **严格高于** baseline 组；baseline 已经是 1 时无法再高，候选也须为 1。
  严格提升意味着候选至少成功 `k` 次且成功次数多于 baseline：`n = k = 5` 对 0/5 的 baseline，
  只有 5/5 能通过。实现在 `backend/app/trials.py` 的 `candidate_group_validates`。
  激活仍然必须人工操作。
- 并发：每个成员的终态事务按 Job → Run → TrialGroup 的顺序加锁，拿到组锁之后才读兄弟成员。
  没有组锁时，两个成员同时结束会互相看到对方"还没完"，策略永远停在 `replaying`；
  `test_state_invariants_postgres.py` 里的并发测试去掉锁就会失败。
- 为什么不用"不下降"：ADR-0010 原文写的是 pass^k 不低于 baseline。可 baseline 的 pass^k 为 0 时
  这条恒成立，候选组 n 次里只要 1 次成功、平均分就会提高，于是通过验证，和"一次侥幸成功
  不能验证策略"相矛盾。2026-11-04 按运维决定收紧为严格提升（ADR-0010 H3-2 修订）。

## 6. 温度为 0 时 pass^k 会被高估

同一个 prompt、同一个 seed、温度 0，几次 trial 几乎是同一条轨迹的复制，彼此不独立。
这时 pass^k 会被高估：它更接近"这一条轨迹成不成功"，而不是"k 次独立尝试都成功的概率"。
发布数字时必须写明模型、Environment 版本、`n`、`k` 和温度。

## 7. 真实数据

同一 Windows llama.cpp（`swift-1.5-27b-iq3xxs`，context 8192），温度 0，每格一个
`n = 5`、`k = 5` 的组（[记录](../history/h3-pass-k-live-2026-11-04.md)）：

| Agent | Environment | 成功 | pass^1 | pass^5 | 5 次 trial 的不同轨迹数 |
|---|---|---|---|---|---|
| reference | research v2 | 5/5 | 1.0 | 1.0 | 1 |
| reference | fault v2 | 0/5 | 0.0 | 0.0 | 1 |
| pi | research v2 | 5/5 | 1.0 | 1.0 | 1 |
| pi | fault v2 | 5/5 | 1.0 | 1.0 | 1 |

每组 5 次 trial 的世界调用序列和分数完全相同，pass^5 恒等于 pass^1。这就是第 6 节说的高估：
数字看起来是"5 次都成功的概率"，实际只是"这一条轨迹成不成功"。要看到分布，需要温度大于 0
或改变 seed；温度 1.0 的对比组尚未运行。

## 8. 怎么复现

在已配置的本地栈上（见 [local-model.md](../operations/local-model.md)）：

```sh
python3 scripts/local_model_acceptance.py --api-url http://127.0.0.1:8000 \
  --agent-id reference-openai-compatible.v1 --scenario multi-step-research.v2 \
  --trials 5 --k 5 --output-directory /absolute/absent/evidence-directory
```

脚本建一个 trial group，逐个采集成员的 trace 与分析，最后保存组的聚合结果
`trial_group.json`，并核对 `successes` 与采集到的成员状态一致。
