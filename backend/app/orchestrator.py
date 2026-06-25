"""Orchestrator — single assembly point for the agent dependency graph.

Eliminates duplication of LLM + ContextManager + Executor + Runtime construction
that was previously repeated across ``main.py`` and ``agent_runner.py``.
"""

from __future__ import annotations

import asyncio
from logging import getLogger

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runner import _execute_agent, run_agent_background
from app.config import Settings
from app.context_manager import ContextManager
from app.executor import create_executor
from app.llm import OpenAICompatibleProvider
from app.runtime import AgentRuntime
from app.tool_registry import ToolRegistry, tool_to_schema
from app.trajectory_repo import TrajectoryRepository

logger = getLogger(__name__)


class AgentOrchestrator:
    """Assembles and runs the agent dependency graph.

    Usage::

        orchestrator = AgentOrchestrator(settings)
        tid, url = await orchestrator.run_background(task, db)
    """

    def __init__(self, settings: Settings) -> None:
        registry = ToolRegistry.get_instance()
        self.tool_schemas = [tool_to_schema(t) for t in registry.list_all()]

        self.llm = OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
        self.cm = ContextManager(context_limit=settings.context_window_limit)
        self.executor = create_executor(settings.executor_mode)
        self.runtime = AgentRuntime(
            tool_executor=self.executor,
            tool_registry=registry,
        )

    async def run_background(
        self, task: str, session: AsyncSession
    ) -> tuple[str, str]:
        """Start an agent in a background task with SSE streaming.

        Returns ``(trajectory_id, stream_url)``.
        """
        repo = TrajectoryRepository(session)
        trajectory = await repo.create_trajectory(task)
        await session.commit()

        asyncio.create_task(
            run_agent_background(
                task=task,
                tool_schemas=self.tool_schemas,
                llm=self.llm,
                context_manager=self.cm,
                runtime=self.runtime,
                trajectory_id=trajectory.id,
            )
        )

        logger.info("Started agent %s for task: %.60s", trajectory.id, task)
        return trajectory.id, f"/api/agents/{trajectory.id}/stream"

    async def run_benchmark(self, task: str) -> str:
        """Run an agent synchronously (awaited, no SSE). Used for benchmarks.

        Returns the ``trajectory_id``.
        """
        from app.database import async_session

        async with async_session() as session:
            repo = TrajectoryRepository(session)
            trajectory = await repo.create_trajectory(task)
            await session.commit()

        await _execute_agent(
            task=task,
            tool_schemas=self.tool_schemas,
            llm=self.llm,
            context_manager=self.cm,
            runtime=self.runtime,
            trajectory_id=trajectory.id,
            publish_sse=False,
        )

        return trajectory.id
