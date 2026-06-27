"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLogger

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runner import run_benchmark_task
from app.benchmarks import BENCHMARK_TASKS, get_benchmark_task
from app.config import settings
from app.database import get_db
from app.event_bus import event_bus, stream_events
from app.failure_analyzer import analyze_trajectory
from app.orchestrator import AgentOrchestrator
from app.policy_store import PolicyStore
from app.policy_compiler import Policy, compile_policy
from app.serializer import (
    render_scoring_view,
    render_step,
    render_trajectory,
)
from app.trajectory_repo import TrajectoryRepository

logger = getLogger(__name__)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize resources on startup and clean them up on shutdown."""
    # ── startup ─────────────────────────────────────────
    from app.database import engine, init_db

    await init_db()
    logger.info("Connected to PostgreSQL")

    from app.tool_registry import ToolRegistry

    registry = ToolRegistry.get_instance()
    registry.register_demo_tools()
    logger.info("ToolRegistry initialized with %d tools", len(registry.list_all()))

    yield

    # ── shutdown ────────────────────────────────────────
    await engine.dispose()


app = FastAPI(
    title="AgentOps Platform API",
    version="0.1.0",
    description="Backend API for the AgentOps observability platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tools")
async def list_tools() -> list[dict[str, object]]:
    """Return all registered tools (name, description, parameters, enabled)."""
    from app.tool_registry import ToolRegistry

    registry = ToolRegistry.get_instance()
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "enabled": t.enabled,
        }
        for t in registry.list_all()
    ]


@app.patch("/api/tools/{name}/toggle")
async def toggle_tool(name: str):
    """Toggle a tool's enabled state."""
    from app.tool_registry import ToolRegistry

    registry = ToolRegistry.get_instance()
    new_state = registry.toggle(name)
    if new_state is None:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    return {"name": name, "enabled": new_state}


@app.post("/api/agents/run")
async def run_agent(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Launch an agent run in the background and return immediately.

    Request body: ``{"task": "...", "policy_id": "..."}`` (policy_id optional)

    The agent executes asynchronously — steps are streamed to the SSE endpoint
    at ``GET /api/agents/{trajectory_id}/stream`` as they are produced.
    """
    from fastapi import HTTPException

    task: str = body["task"]
    policy_id: str | None = body.get("policy_id")

    orchestrator = AgentOrchestrator(settings)
    trajectory_id, stream_url = await orchestrator.run_background(
        task, db, policy_id=policy_id,
    )

    return {
        "trajectory_id": trajectory_id,
        "stream_url": stream_url,
        "status": "running",
    }


@app.post("/api/agents/{trajectory_id}/cancel")
async def cancel_trajectory(trajectory_id: str):
    """Cancel a running agent trajectory.

    The cancellation is best-effort: the background task checks for a
    cancellation flag between steps and stops gracefully.  No-op if the
    trajectory is already completed or was never running.
    """
    from app.agent_runner import cancel_trajectory as _cancel

    newly_cancelled = _cancel(trajectory_id)
    return {
        "trajectory_id": trajectory_id,
        "cancelled": newly_cancelled,
        "status": "cancelling" if newly_cancelled else "already_cancelled_or_completed",
    }


@app.get("/api/agents/{trajectory_id}/stream")
async def stream_trajectory(
    trajectory_id: str,
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint that streams agent steps as they are produced.

    Response headers are set for Server-Sent Events.  Each event is a single
    JSON line prefixed with ``data: `` and terminated by ``\n\n``.

    Terminal events:
      ``{"type": "done", "trajectory_id": "..."}``
      ``{"type": "error", "message": "..."}``

    If the trajectory is already completed, a ``done`` event is sent
    immediately — this handles the race between the background task finishing
    and the SSE subscriber connecting.
    """
    # Check if trajectory already completed before subscribing
    repo = TrajectoryRepository(db)
    trajectory = await repo.get_trajectory(trajectory_id)

    if trajectory and trajectory.status in ("success", "failed"):
        async def _immediate_done() -> AsyncGenerator[bytes, None]:
            yield f"data: {json.dumps({'type': 'done', 'trajectory_id': trajectory_id})}\n\n".encode()
        return StreamingResponse(
            _immediate_done(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    return StreamingResponse(
        stream_events(trajectory_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/compare")
async def compare_trajectories(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Compare 2-5 trajectories side by side.

    Request body: ``{"trajectory_ids": ["id1", "id2", ...]}``

    Returns aligned step data with diff markers for tool-usage differences.
    """
    from fastapi import HTTPException

    trajectory_ids: list[str] = body.get("trajectory_ids", [])

    if len(trajectory_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 trajectories required")
    if len(trajectory_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 trajectories allowed")

    repo = TrajectoryRepository(db)
    trajectories: list = []
    for tid in trajectory_ids:
        traj = await repo.get_trajectory(tid)
        if traj is None:
            raise HTTPException(status_code=404, detail=f"Trajectory {tid} not found")
        trajectories.append(traj)

    # Determine max steps across all trajectories
    max_steps = max((len(t.steps) for t in trajectories), default=0)

    # Build aligned steps and diff info
    aligned_steps: list[dict[str, object]] = []
    for i in range(max_steps):
        step_group: list[dict[str, object] | None] = []
        tool_names: list[str | None] = []
        for t in trajectories:
            if i < len(t.steps):
                s = t.steps[i]
                step_group.append(render_step(s, view="compare"))
                tool_names.append(s.action["name"] if s.action else None)
            else:
                step_group.append(None)
                tool_names.append(None)

        # Diff: check if tools differ (excluding None)
        active_tools = [n for n in tool_names if n is not None]
        tools_differ = len(set(active_tools)) > 1 if active_tools else False

        aligned_steps.append(
            {
                "step_index": i,
                "trajectories": step_group,
                "tools_differ": tools_differ,
                "tool_names": tool_names,
            }
        )

    # Build trajectory metadata
    traj_meta: list[dict[str, object]] = []
    for t in trajectories:
        total_latency = sum(s.latency_ms for s in t.steps)
        traj_meta.append(
            {
                "id": t.id,
                "task": t.task,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
                "total_steps": len(t.steps),
                "total_latency_ms": total_latency,
            }
        )

    return {
        "trajectories": traj_meta,
        "aligned_steps": aligned_steps,
        "max_steps": max_steps,
    }


@app.get("/api/traces")
async def list_traces(
    status: str | None = Query(None),
    tool: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return a paginated list of trajectories with optional filters.

    Supports filtering by ``status`` (running/success/failed) and by ``tool``
    name (checks the ``action->>'name'`` JSONB field on steps).
    """
    repo = TrajectoryRepository(db)
    trajectories, total, step_counts = await repo.list_trajectories(
        status=status,
        tool=tool,
        limit=limit,
        offset=offset,
    )

    return {
        "trajectories": [
            {
                "id": t.id,
                "task": t.task,
                "status": t.status,
                "step_count": step_counts.get(t.id, 0),
                "created_at": t.created_at,
                "score": t.score,
            }
            for t in trajectories
        ],
        "total": total,
    }


@app.get("/api/traces/{trajectory_id}")
async def get_trace(
    trajectory_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return a single trajectory with all its steps."""
    from fastapi import HTTPException

    repo = TrajectoryRepository(db)
    trajectory = await repo.get_trajectory(trajectory_id)

    if trajectory is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")

    return render_trajectory(trajectory)


# ---------------------------------------------------------------------------
# Eval routes
# ---------------------------------------------------------------------------


@app.post("/api/eval/score")
async def eval_score(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Re-compute a trajectory score with optional custom weights.

    Request body::

        {
            "trajectory_id": "...",
            "weights": {"cost": 0.01, "latency": 0.01, "tool_failure": 0.5}
        }

    The ``weights`` field is optional; defaults are used when omitted.
    The result is persisted to the database.
    """
    from fastapi import HTTPException

    from app.scoring import compute_score

    trajectory_id: str = body.get("trajectory_id", "")
    if not trajectory_id:
        raise HTTPException(status_code=422, detail="trajectory_id is required")

    weights: dict | None = body.get("weights")

    # Validate weights if provided
    if weights is not None:
        allowed = {"cost", "latency", "tool_failure"}
        extra = set(weights) - allowed
        if extra:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown weight keys: {', '.join(sorted(extra))}",
            )
        for key in allowed:
            if key in weights:
                val = weights[key]
                if not isinstance(val, (int, float)):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Weight '{key}' must be a number",
                    )
                if val < 0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Weight '{key}' must be non-negative",
                    )

    repo = TrajectoryRepository(db)
    trajectory = await repo.get_trajectory(trajectory_id)

    if trajectory is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")

    traj_dict = render_scoring_view(trajectory, settings.llm_max_steps)

    result = compute_score(traj_dict, weights)
    await repo.set_score(trajectory_id, result["score"], result["breakdown"])
    await db.commit()

    return {
        "trajectory_id": trajectory_id,
        "score": result["score"],
        "breakdown": result["breakdown"],
    }


@app.post("/api/eval/analyze")
async def eval_analyze(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Analyze a single trajectory for failure root causes.

    Request body::

        {"trajectory_id": "..."}

    Returns a ``FailureReport`` with per-dimension rates, evidence list,
    dominant dimension, and a ``needs_human_review`` flag.
    """
    from fastapi import HTTPException

    from app.failure_analyzer import analyze_trajectory

    trajectory_id: str = body.get("trajectory_id", "")
    if not trajectory_id:
        raise HTTPException(status_code=422, detail="trajectory_id is required")

    repo = TrajectoryRepository(db)
    trajectory = await repo.get_trajectory(trajectory_id)

    if trajectory is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")

    from app.config import settings

    traj_dict = render_scoring_view(trajectory, settings.llm_max_steps)

    report = analyze_trajectory(traj_dict)

    return {
        "trajectory_id": trajectory_id,
        "dimensions": report.dimensions,
        "dominant": report.dominant,
        "evidence": [
            {
                "dimension": ev.dimension,
                "step_index": ev.step_index,
                "reason": ev.reason,
                "severity": ev.severity,
                "details": ev.details,
            }
            for ev in report.evidence
        ],
        "needs_human_review": report.needs_human_review,
    }


@app.get("/api/eval/analysis/summary")
async def eval_analysis_summary(
    last_n: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Aggregate failure analysis across the most recent completed trajectories.

    Query params:
        ``last_n``: number of recent completed trajectories to analyze (1-500).

    Returns per-dimension average failure rates across all selected trajectories.
    """
    from app.failure_analyzer import analyze_trajectories
    from app.config import settings

    repo = TrajectoryRepository(db)
    # Fetch last_n trajectories with terminal status (not "running")
    trajectories, _, _ = await repo.list_trajectories(
        limit=last_n,
        offset=0,
    )
    # Filter to completed trajectories only
    completed = [t for t in trajectories if t.status in ("success", "failed")]

    traj_dicts: list[dict] = []
    for t in completed:
        td = render_trajectory(t)
        td["max_steps"] = settings.llm_max_steps
        traj_dicts.append(td)

    result = analyze_trajectories(traj_dicts)

    return {
        "trajectories_analyzed": len(completed),
        "dimension_rates": result,
    }


# ---------------------------------------------------------------------------
# Export routes
# ---------------------------------------------------------------------------


@app.get("/api/eval/export")
async def eval_export(
    task_name: str | None = Query(None),
    trajectory_id: str | None = Query(None),
    format: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export trajectory data in one of three training-data formats.

    Query parameters
    ----------------
    task_name:
        Filter by task name (exact match).  Takes precedence over
        ``trajectory_id`` when both are provided.
    trajectory_id:
        Export a single trajectory by its ID.
    format:
        One of ``openai_sft``, ``rlhf_pair``, or ``jsonl``.

    Returns
    -------
    Response
        ``application/x-ndjson`` for ``openai_sft`` and ``jsonl``;
        ``application/json`` for ``rlhf_pair``.  A ``Content-Disposition``
        header is set so the response is treated as a file download.
    """
    from fastapi import HTTPException

    from app.exporters import build_jsonl, build_openai_sft, build_rlhf_pair

    VALID_FORMATS = {"openai_sft", "rlhf_pair", "jsonl"}

    # -- validate params -------------------------------------------------------
    if not task_name and not trajectory_id:
        raise HTTPException(
            status_code=422,
            detail="Either task_name or trajectory_id is required",
        )

    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    repo = TrajectoryRepository(db)

    # ------------------------------------------------------------------
    # task_name branch — find all matching trajectories, pick by score
    # ------------------------------------------------------------------
    if task_name:
        trajectories = await repo.get_trajectories_by_task(task_name)
        if not trajectories:
            raise HTTPException(
                status_code=404,
                detail=f"No trajectories found for task: {task_name}",
            )

        if format == "openai_sft":
            detail = render_trajectory(trajectories[0])
            result = build_openai_sft(detail)
            content = json.dumps(result, default=str, ensure_ascii=False) + "\n"
            return Response(
                content=content,
                media_type="application/x-ndjson",
                headers={"Content-Disposition": 'attachment; filename="export.jsonl"'},
            )

        elif format == "rlhf_pair":
            scored = [t for t in trajectories if t.score is not None]
            if len(scored) < 2:
                raise HTTPException(
                    status_code=400,
                    detail="Need at least 2 trajectories for RLHF pair export",
                )
            best_detail = render_trajectory(scored[0])
            worst_detail = render_trajectory(scored[-1])
            result = build_rlhf_pair(best_detail, worst_detail)
            content = json.dumps(result, default=str, ensure_ascii=False)
            return Response(
                content=content,
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="export.json"'},
            )

        elif format == "jsonl":
            details = [render_trajectory(t) for t in trajectories]
            content = build_jsonl(details)
            return Response(
                content=content,
                media_type="application/x-ndjson",
                headers={"Content-Disposition": 'attachment; filename="export.jsonl"'},
            )

    # ------------------------------------------------------------------
    # trajectory_id branch — single trajectory
    # ------------------------------------------------------------------
    trajectory = await repo.get_trajectory(trajectory_id)
    if trajectory is None:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory {trajectory_id} not found",
        )

    detail = render_trajectory(trajectory)

    if format == "openai_sft":
        result = build_openai_sft(detail)
        content = json.dumps(result, default=str, ensure_ascii=False) + "\n"
        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="export.jsonl"'},
        )

    elif format == "rlhf_pair":
        raise HTTPException(
            status_code=400,
            detail="RLHF pair export requires task_name (need at least 2 trajectories)",
        )

    elif format == "jsonl":
        content = build_jsonl([detail])
        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="export.jsonl"'},
        )

    # Should never reach here (format is validated above), but keep mypy happy.
    raise HTTPException(status_code=422, detail=f"Unhandled format: {format}")


# ---------------------------------------------------------------------------
# Benchmark routes
# ---------------------------------------------------------------------------


@app.get("/api/eval/benchmarks")
async def list_benchmarks() -> list[dict[str, str]]:
    """Return the list of predefined benchmark tasks."""
    return [
        {"name": t.name, "task": t.task, "description": t.description}
        for t in BENCHMARK_TASKS
    ]


@app.post("/api/eval/benchmark")
async def run_benchmark(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Run a benchmark task N times concurrently and return rankings.

    Request body::

        {
            "task_name": "bench_01_http_request",   # OR
            "task": "custom task description",       # (mutually exclusive)
            "n_runs": 5                               # 1-10, default 5
        }

    Uses ``asyncio.gather`` to run all N agents concurrently.  After all runs
    finish, scores are collected from the database, ranked using **dense
    ranking** (same score = same rank), and returned along with best/worst
    references.
    """
    from fastapi import HTTPException

    task_name: str | None = body.get("task_name")
    custom_task: str | None = body.get("task")
    n_runs: int = body.get("n_runs", 5)

    # -- validate task_name / task mutual exclusivity ---------------------------
    if task_name and custom_task:
        raise HTTPException(
            status_code=422,
            detail="Provide either task_name or task, not both",
        )
    if not task_name and not custom_task:
        raise HTTPException(
            status_code=422,
            detail="Either task_name or task is required",
        )

    # -- validate n_runs ---------------------------------------------------------
    if not isinstance(n_runs, int) or n_runs < 1 or n_runs > 10:
        raise HTTPException(
            status_code=422,
            detail="n_runs must be an integer between 1 and 10",
        )

    # -- resolve task string -----------------------------------------------------
    if task_name:
        task_obj = get_benchmark_task(task_name)
        if task_obj is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown benchmark task: {task_name}",
            )
        task_str: str = task_obj.task
    else:
        task_str = custom_task  # type: ignore[assignment]

    # -- launch all runs concurrently (max 3 at a time) -------------------------
    semaphore = asyncio.Semaphore(3)

    async def _run_with_semaphore(task_str: str) -> str:
        async with semaphore:
            return await run_benchmark_task(task_str)

    results = await asyncio.gather(
        *[_run_with_semaphore(task_str) for _ in range(n_runs)],
        return_exceptions=True,
    )

    # Collect successful trajectory IDs, log failures
    trajectory_ids: list[str] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Benchmark run failed: %s", r)
        else:
            trajectory_ids.append(r)

    # -- fetch scores and build rankings ----------------------------------------
    repo = TrajectoryRepository(db)
    rankings_data: list[dict[str, object]] = []
    for tid in trajectory_ids:
        traj = await repo.get_trajectory(tid)
        if traj is not None and traj.score is not None:
            rankings_data.append({
                "trajectory_id": tid,
                "score": traj.score,
                "status": traj.status,
            })

    # Sort by score descending
    rankings_data.sort(key=lambda x: x["score"], reverse=True)  # type: ignore

    # Dense ranking: same score = same rank, no gaps
    rankings: list[dict[str, object]] = []
    current_rank = 1
    for i, item in enumerate(rankings_data):
        if i > 0 and item["score"] < rankings_data[i - 1]["score"]:
            current_rank += 1
        rankings.append({
            "trajectory_id": item["trajectory_id"],
            "rank": current_rank,
            "score": item["score"],
            "status": item["status"],
        })

    best = rankings[0] if rankings else None
    worst = rankings[-1] if rankings else None

    return {
        "task": task_str,
        "n_runs": n_runs,
        "completed": len(trajectory_ids),
        "rankings": rankings,
        "best": {
            "trajectory_id": best["trajectory_id"],
            "score": best["score"],
        }
        if best
        else None,
        "worst": {
            "trajectory_id": worst["trajectory_id"],
            "score": worst["score"],
        }
        if worst
        else None,
    }


# ---------------------------------------------------------------------------
# Policy routes
# ---------------------------------------------------------------------------


@app.get("/api/eval/policies")
async def list_policies(
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List policy versions, optionally filtered by status."""
    store = PolicyStore(db)
    policies = await store.list_policies(status=status)
    return [p.to_dict() for p in policies]


@app.get("/api/eval/policies/active")
async def get_active_policy(
    db: AsyncSession = Depends(get_db),
) -> dict | None:
    """Return the currently active policy, or null."""
    store = PolicyStore(db)
    policy = await store.get_active_policy()
    return policy.to_dict() if policy else None


@app.get("/api/eval/policies/warmup-status")
async def warmup_status(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the warmup progress toward the first auto-compile threshold."""
    store = PolicyStore(db)
    return await store.get_warmup_status()


@app.get("/api/eval/policies/{version_id}")
async def get_policy(
    version_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return a single policy version by ID."""
    from fastapi import HTTPException

    store = PolicyStore(db)
    policy = await store.get_policy(version_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy.to_dict()


@app.post("/api/eval/policies/{version_id}/approve")
async def approve_policy(
    version_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve a pending_review policy — archive the old active, activate this one."""
    from fastapi import HTTPException

    store = PolicyStore(db)
    policy = await store.get_policy(version_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    if policy.status not in ("pending_review", "active"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve policy with status '{policy.status}'",
        )

    await store.deactivate_active_policy()
    result = await store.update_policy_status(version_id, "active")
    await db.commit()
    return result.to_dict() if result else {"status": "ok"}


@app.post("/api/eval/policies/{version_id}/reject")
async def reject_policy(
    version_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reject a pending_review policy with an optional reason."""
    from fastapi import HTTPException

    store = PolicyStore(db)
    policy = await store.get_policy(version_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")

    reason = body.get("reason", "")
    result = await store.update_policy_status(
        version_id, "reverted", reject_reason=reason
    )
    await db.commit()
    return result.to_dict() if result else {"status": "ok"}



@app.post("/api/eval/policies/compile")
async def compile_and_store_policy(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Analyze a trajectory, compile a policy, and persist it.

    Request body::

        {"trajectory_id": "..."}

    Returns ``{"compiled": true, "policy": {...}}`` on success, or
    ``{"compiled": false, "reason": "..."}`` when no compilation is needed.
    """
    from fastapi import HTTPException

    from app.failure_analyzer import FailureReport

    trajectory_id: str = body.get("trajectory_id", "")
    if not trajectory_id:
        raise HTTPException(status_code=422, detail="trajectory_id is required")

    repo = TrajectoryRepository(db)
    trajectory = await repo.get_trajectory(trajectory_id)
    if trajectory is None:
        raise HTTPException(status_code=404, detail="Trajectory not found")

    traj_dict = render_scoring_view(trajectory, settings.llm_max_steps)

    report: FailureReport = analyze_trajectory(traj_dict)

    # Compile policy
    patch = compile_policy(report, [trajectory_id])
    if patch is None:
        reason = "no_policy_needed"
        if report.needs_human_review:
            reason = "needs_human_review"
        return {"compiled": False, "reason": reason}

    # Persist
    store = PolicyStore(db)
    version_display = await store.next_version_display()
    policy = await store.create_policy(
        version_display=version_display,
        parent_version=None,
        patch=patch.patch,
        rationale=patch.rationale,
        expected_impact=patch.expected_impact,
        confidence=patch.confidence,
        source_trajectories=patch.source_trajectories,
    )

    # Auto-approve if confidence is high
    if patch.confidence == "high":
        await store.deactivate_active_policy()
        await store.update_policy_status(policy.version_id, "active")

    await db.commit()

    # Re-fetch to get the persisted state
    persisted = await store.get_policy(policy.version_id)
    return {"compiled": True, "policy": persisted.to_dict() if persisted else None}
