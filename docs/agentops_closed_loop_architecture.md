# AgentOps Closed-Loop Architecture

## 1. Problem

Current system is **open-loop**: Task → Agent Run → Trajectory → Score → Export → [END].

Goal: **closed-loop**. Score → Failure Analysis → Policy Improvement → Next Run. Model and harness co-evolve automatically.

```
OPEN LOOP (v0.1–v0.3, current)
  Task → Run(config) → Trajectory → Score → Export → RL Pipeline (external)

CLOSED LOOP (v0.4, this plan)
  Task → Run(Policy vN) → Trajectory → Score
    ↓
  Failure Analyzer → classify failure dimensions
    ↓
  Policy Compiler → generate policy patch
    ↓
  Policy Store → version + persist
    ↓
  Policy Router → inject into next Run(Policy vN+1)
    ↓
  [loop]
```

## 2. What Already Exists (Ground Truth)

These modules are implemented and tested — closed-loop builds on top of them:

| Module | File | What it does |
|---|---|---|
| Scoring Engine | `scoring.py` | `compute_score()` → 4-dimension breakdown (success, cost, latency, tool_failure) |
| Trajectory Repo | `trajectory_repo.py` | Full CRUD for trajectories + steps |
| Agent Runner | `agent_runner.py` | `run_agent_background()`, `run_benchmark_task()`, cancellation |
| Exporters | `exporters.py` | `build_openai_sft()`, `build_rlhf_pair()`, `build_jsonl()` |
| Benchmark | `benchmarks.py` | Concurrent N-run with `asyncio.gather`, auto-ranking |
| ORM | `models.py` | `Trajectory` + `Step` tables (PostgreSQL) |

## 3. New Modules (To Build)

### 3.1 Failure Analyzer

**File**: `backend/app/failure_analyzer.py`
**Status**: 📐 To implement

Takes scored trajectory, classifies failure root causes into 4 dimensions:

| Dimension | Signals | Example |
|---|---|---|
| **Planning** | Repeated tool calls, wrong tool selection, circular reasoning | Agent calls `search` 5x with same query |
| **Execution** | Empty observation, error/exception/timeout in output | Tool returns `"error: container timeout"` |
| **Context** | Token truncation, lost earlier information | `context_window.used / limit > 0.95` before failure |
| **Budget** | Hit `max_steps`, context window alert fired | Step count == max_steps and no final answer |

**Interface**:
```python
def analyze_trajectory(trajectory: dict) -> FailureReport:
    """Classify failures from scored trajectory.
    
    Returns:
        FailureReport with per-dimension counts, 
        failure_rate per dimension, dominant_failure_type,
        and raw evidence list.
    """
```

**Data source**: Reuses existing `scoring.py` breakdown + `steps[].observation` text matching + `context_window` fields already tracked per step.

**API endpoints**:
- `POST /api/eval/analyze` — analyze single trajectory
- `GET /api/eval/analysis/summary?last_n=50` — aggregated failure distribution across recent runs

---

### 3.2 Policy Compiler

**File**: `backend/app/policy_compiler.py`
**Status**: 📐 To implement

Consumes `FailureReport`, produces executable policy patch:

```python
def compile_policy(report: FailureReport) -> PolicyPatch:
    """Generate actionable policy adjustments from failure analysis."""
```

**Output schema**:
```json
{
  "version": "v2",
  "parent_version": "v1",
  "patch": {
    "system_prompt_suffix": "Avoid repeating tool calls. Verify args before execution.",
    "tool_priority_bias": {"search": -0.1, "code_exec": +0.05},
    "context_strategy": "increase_recent_weight",
    "max_steps_override": 12
  },
  "rationale": "Execution failures at 45%, primarily timeout. Planning loops detected in 3/10 runs.",
  "expected_impact": {"execution_failure_rate": "-15%", "planning_loop_rate": "-20%"},
  "confidence": "medium",
  "source_trajectories": ["traj_abc", "traj_def"]
}
```

**Rules-based first, LLM-assisted later**:
- v1: Deterministic rules (if execution_failure_rate > 30% → add retry hint to prompt)
- v2 (future): LLM summarizes failure patterns into natural-language prompt patches

---

### 3.3 Policy Store & Router

**File**: Extends `backend/app/agent_runner.py` + new `backend/app/policy_store.py`
**Status**: 📐 To implement

**Store** — 2 new SQL tables:

```sql
-- Policy versions
CREATE TABLE policy_versions (
  id UUID PRIMARY KEY,
  version TEXT UNIQUE NOT NULL,        -- "v1", "v2", ...
  parent_version TEXT,
  patch JSONB NOT NULL,
  rationale TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Link trajectories to the policy they ran under
CREATE TABLE trajectory_policy_map (
  trajectory_id UUID REFERENCES trajectories(id),
  policy_id UUID REFERENCES policy_versions(id),
  PRIMARY KEY (trajectory_id, policy_id)
);
```

**Router** — injects policy into agent config before `_execute_agent()`:

```python
async def get_active_policy() -> PolicyPatch | None:
    """Fetch latest policy version from store."""

async def run_agent_with_policy(task: str, policy: PolicyPatch | None):
    """Merge policy.patch into agent_config, then delegate to existing run flow."""
```

**Auto-replay**: When new policy created and score delta > threshold, auto-replay failed trajectories from `source_trajectories` list.

---

## 4. Frontend Integration

Extend existing `/eval` page with new tabs/panels:

| Component | What it shows |
|---|---|
| Failure Distribution Chart | Radar or bar chart: Planning / Execution / Context / Budget breakdown |
| Policy Version Timeline | Horizontal timeline showing policy evolution (v1 → v2 → v3) |
| Before/After Comparison | Reuse existing `/compare` infrastructure to diff runs under different policies |

---

## 5. Roadmap

| Priority | Item | Status | Depends on |
|---|---|---|---|
| P0 | Failure Analyzer module + 2 API routes + tests | 📐 To build | Existing scoring.py |
| P1 | Policy Compiler (rules-based v1) + API + tests | 📐 To build | P0 |
| P2 | Policy Store (SQL tables + CRUD) | 📐 To build | P1 |
| P3 | Policy Router (inject into agent_runner) | 📐 To build | P2 |
| P4 | Auto-replay failed trajectories with new policy | 📐 To build | P3 |
| P5 | Frontend: failure chart + policy timeline | 📐 To build | P0, P2 |
| P6 | LLM-assisted policy generation (v2 compiler) | 🔮 Future | P1 working |

## 6. JD Alignment

| JD Requirement | How this addresses it |
|---|---|
| "将 Agent 工具集成到 RL 基础设施" | Policy patches feed directly into RL training loop via existing export layer |
| "搭建 Agent 评测平台，轨迹查看、调试分析" | Failure Analyzer = structured debugging beyond raw scores |
| "推动 Agent 系统可靠性与可扩展性" | Closed-loop = self-improving reliability without manual intervention |
| "对接 RL 训练框架" | Policy Store versioning maps 1:1 to RL policy checkpoints |

## 7. Vision

```
Before:  Evaluation Dashboard (score → human reads → human tweaks prompt → retry)
After:   Self-improving Agent Runtime (score → auto-analyze → auto-patch → auto-retry)
```
