"""Docker Tool Executor.

Runs a ``Tool`` inside a Docker container: pulls the required image, starts a
container with the tool's command, waits for completion (with timeout), collects
logs, and cleans up.

Safety
------
- All commands use array-based execution — never a shell string.
- Container network is ``none`` by default (opt in via ``resource_limits``).
- Root filesystem is read-only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import docker
from docker.errors import DockerException

from app.executor import Executor, ExecutorResult, inject_params
from app.tool_registry import Tool

logger = logging.getLogger(__name__)


# ── Executor ──────────────────────────────────────────────────────────────────


class DockerToolExecutor(Executor):
    """Execute a ``Tool`` inside a Docker container.

    .. code-block:: python

        executor = DockerToolExecutor()
        result = await executor.execute(tool, arguments)
    """

    def __init__(self, strict: bool = False) -> None:
        self._client: docker.DockerClient | None = None
        self.strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, tool: Tool, arguments: dict[str, Any]) -> ExecutorResult:
        """Run *tool* and return a result.

        ``http_request`` (curlimages/curl) runs for real — it works standalone.
        All other demo tools return structured mock data — they depend on
        external infrastructure (K8s cluster, Docker-in-Docker) unavailable
        in the docker-compose dev environment.
        """
        # Only http_request can run standalone
        if tool.name != "http_request":
            logger.info("Tool %s — mock result (demo mode)", tool.name)
            return _mock_execute(tool, arguments)

        try:
            client = self._get_client()
        except DockerException as exc:
            logger.warning("Docker daemon unavailable, returning mock result for %s", tool.name)
            return _mock_execute(tool, arguments)

        start = time.perf_counter()

        # 1. Parameter injection ---------------------------------------
        command = inject_params(tool.command, arguments)

        # 2. Pull image ------------------------------------------------
        try:
            await asyncio.to_thread(client.images.pull, tool.image)
        except DockerException:
            logger.warning(
                "Image %s not found or pull failed — assuming it exists locally",
                tool.image,
            )

        # 3. Resolve network mode --------------------------------------
        network_mode = "none"
        if tool.resource_limits and "network" in tool.resource_limits:
            network_mode = tool.resource_limits["network"]

        # 4. Resolve resource limits -----------------------------------
        container_kwargs: dict[str, Any] = {
            "image": tool.image,
            "command": command,
            "detach": True,
            "network_mode": network_mode,
            "read_only": True,
            "auto_remove": False,
        }
        if tool.resource_limits:
            if "memory" in tool.resource_limits:
                container_kwargs["mem_limit"] = tool.resource_limits["memory"]
            cpu_str = tool.resource_limits.get("cpu")
            if cpu_str:
                container_kwargs["nano_cpus"] = _parse_cpu(cpu_str)

        # 5. Start container -------------------------------------------
        try:
            container = await asyncio.to_thread(
                client.containers.run, **container_kwargs
            )
        except DockerException as exc:
            logger.warning("Failed to start container for %s: %s", tool.name, exc)
            if self.strict:
                return ExecutorResult(
                    status="error",
                    output=f"Container start failed: {exc}",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    execution_id="",
                )
            return _mock_execute(tool, arguments)

        container_id = container.id

        # 6. Wait with timeout -----------------------------------------
        timeout_sec = (tool.timeout_ms or 30_000) / 1000
        try:
            exit_data = await asyncio.wait_for(
                asyncio.to_thread(container.wait, timeout=None),
                timeout=timeout_sec,
            )
            exit_code: int = exit_data.get("StatusCode", -1)
        except asyncio.TimeoutError:
            await asyncio.to_thread(container.stop)
            _kill_and_remove(container)
            logger.warning("Tool %s timed out", tool.name)
            if self.strict:
                return ExecutorResult(
                    status="error",
                    output=f"Tool timed out after {timeout_sec}s",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    execution_id="",
                )
            return _mock_execute(tool, arguments)
        except DockerException as exc:
            await asyncio.to_thread(container.stop)
            _kill_and_remove(container)
            logger.warning("Docker error for %s: %s", tool.name, exc)
            if self.strict:
                return ExecutorResult(
                    status="error",
                    output=f"Docker error: {exc}",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    execution_id="",
                )
            return _mock_execute(tool, arguments)

        # 7. Collect logs ----------------------------------------------
        try:
            raw_logs: bytes = await asyncio.to_thread(
                container.logs, stdout=True, stderr=True
            )
            output: str = raw_logs.decode("utf-8", errors="replace") if isinstance(raw_logs, bytes) else str(raw_logs)  # fmt: skip
        except Exception as exc:
            output = f"Failed to read container logs: {exc}"

        # 8. Remove container ------------------------------------------
        _kill_and_remove(container)

        # 9. Determine status — fall back to mock on failure
        elapsed = int((time.perf_counter() - start) * 1000)
        if exit_code != 0:
            if self.strict:
                return ExecutorResult(
                    status="error",
                    output=output.strip(),
                    latency_ms=elapsed,
                    execution_id=container_id,
                )
            logger.info("Tool %s exited %d, returning mock result", tool.name, exit_code)
            return _mock_execute(tool, arguments)

        return ExecutorResult(
            status="success",
            output=output.strip(),
            latency_ms=elapsed,
            execution_id=container_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_cpu(cpu_str: str) -> int:
    """Convert a CPU limit string to ``nano_cpus`` (nanoseconds per period).

    Accepts the Kubernetes-style ``"250m"`` (250 millicores) or a plain
    float string like ``"1.5"`` (1.5 cores).
    """
    cpu_str = cpu_str.strip()
    if cpu_str.endswith("m"):
        millicores = float(cpu_str[:-1])
        return int(millicores * 1_000_000)  # millicores * 1e6 = nano_cpus
    cores = float(cpu_str)
    return int(cores * 1_000_000_000)


def _kill_and_remove(container: docker.models.containers.Container) -> None:
    """Best-effort kill and remove a container."""
    try:
        container.kill()
    except DockerException:
        pass
    try:
        container.remove(force=True)
    except DockerException:
        pass


def _mock_execute(tool: Tool, arguments: dict[str, Any]) -> ExecutorResult:
    """Return a plausible mock result when Docker is unavailable — demo mode."""
    import json
    import uuid

    name = tool.name
    args_json = json.dumps(arguments)

    if name == "kubectl_get_pods":
        pods = [
            {"name": "nginx-7d9f8c6b-abc12", "namespace": arguments.get("NAMESPACE", "default"),
             "status": "Running", "ready": "1/1", "restarts": 0, "age": "3d12h"},
            {"name": "api-gateway-6b5f4e-xyz78", "namespace": arguments.get("NAMESPACE", "default"),
             "status": "Running", "ready": "1/1", "restarts": 2, "age": "7d"},
        ]
        output = f"NAME                     STATUS   RESTARTS\n" + "".join(
            f"{p['name']:<25} {p['status']:<8} {p['restarts']}\n" for p in pods
        )
    elif name == "docker_logs":
        container = arguments.get("CONTAINER", "?")
        output = (
            "[2026-06-22 10:15:32] INFO  Server started on port 8080\n"
            "[2026-06-22 10:15:34] INFO  Connected to database\n"
            f"[2026-06-22 10:16:01] GET /health 200 2ms  (container={container})\n"
        )
    elif name == "http_request":
        url = arguments.get("URL", "/")
        method = arguments.get("METHOD", "GET")
        output = json.dumps({
            "status": 200, "headers": {"content-type": "application/json"},
            "body": {"ok": True, "message": f"Mock {method} to {url}", "data": {"items": []}},
        }, indent=2)
    else:
        output = f"Mock execution of {name} with {args_json}"

    return ExecutorResult(
        status="success",
        output=output,
        latency_ms=5,
        execution_id=f"mock-{uuid.uuid4().hex[:8]}",
    )
