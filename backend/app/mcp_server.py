"""MCP Server exposing AgentOps Tool Registry via Model Context Protocol stdio."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool as MCPTool

from app.tool_registry import Tool, ToolRegistry

logger = logging.getLogger("agentops.mcp")

_SQLITE_PATH: str | None = None


def _db_path() -> str:
    global _SQLITE_PATH
    if _SQLITE_PATH is None:
        d = Path.home() / ".agentops"
        d.mkdir(parents=True, exist_ok=True)
        _SQLITE_PATH = str(d / "mcp_trajectories.db")
    return _SQLITE_PATH


async def _init_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(_db_path())
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE IF NOT EXISTS mcp_trajectories ("
        "  id TEXT PRIMARY KEY, tool_name TEXT NOT NULL, arguments TEXT NOT NULL,"
        "  result TEXT NOT NULL, status TEXT DEFAULT 'ok',"
        "  started_at REAL NOT NULL, elapsed_ms INTEGER NOT NULL"
        ")"
    )
    await db.commit()
    return db


_MOCK_PODS = {
    "pods": [
        {"name": "nginx-7d9f-abc12", "namespace": "default", "status": "Running", "restarts": 0},
        {"name": "api-6b5f-xyz78", "namespace": "default", "status": "Running", "restarts": 2},
    ]
}

_MOCK_LOGS = {
    "logs": "[INFO] Server started\n[DEBUG] Config loaded\n[INFO] GET /health 200",
}

_MOCK_HTTP = {"status_code": 200, "body": '{"ok":true}', "headers": {"content-type": "application/json"}}


def _mock_result(tool: Tool, arguments: dict) -> dict:
    name = tool.name
    if name == "kubectl_get_pods":
        return {"tool": name, "arguments": arguments, "pods": _MOCK_PODS["pods"],
                "namespace": arguments.get("NAMESPACE", "default")}
    if name == "docker_logs":
        return {"tool": name, "arguments": arguments, "logs": _MOCK_LOGS["logs"],
                "container": arguments.get("CONTAINER", "?")}
    if name == "http_request":
        return {"tool": name, "arguments": arguments, **_MOCK_HTTP}
    return {"tool": name, "arguments": arguments, "error": "unknown tool"}


def _tool_to_mcp(t: Tool) -> MCPTool:
    return MCPTool(name=t.name, description=t.description, inputSchema=t.parameters)


def create_app(registry: ToolRegistry | None = None) -> Server:
    reg = registry or ToolRegistry.get_instance()
    app = Server("agentops-mcp")

    @app.list_tools()
    async def list_tools() -> list[MCPTool]:
        return [_tool_to_mcp(t) for t in reg.list_all()]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        tool = reg.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        args = arguments or {}
        started_at = time.time()
        t0 = time.perf_counter()
        result = _mock_result(tool, args)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        try:
            db = await _init_db()
            await db.execute(
                "INSERT INTO mcp_trajectories (id,tool_name,arguments,result,status,started_at,elapsed_ms) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), name, json.dumps(args), json.dumps(result),
                 "ok", started_at, elapsed_ms),
            )
            await db.commit()
            await db.close()
        except Exception:
            logger.warning("Failed to log MCP trajectory", exc_info=True)

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return app


async def run_server(registry: ToolRegistry | None = None) -> None:
    app = create_app(registry)
    async with stdio_server() as (read, write):
        await app.run(read, write)
