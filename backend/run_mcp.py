#!/usr/bin/env python3
"""Standalone entry-point for the AgentOps MCP Server.  Listens on stdio."""
from __future__ import annotations

import asyncio
import logging

from app.executor import create_executor
from app.mcp_server import run_server
from app.tool_registry import ToolRegistry

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("mcp").setLevel(logging.WARNING)


async def main() -> None:
    registry = ToolRegistry.get_instance()
    registry.register_demo_tools()
    executor = create_executor()
    await run_server(registry, executor)


if __name__ == "__main__":
    asyncio.run(main())
