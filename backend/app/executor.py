"""Abstract executor protocol for running tools.

Defines the ``Executor`` abstract base class and ``ExecutorResult`` dataclass
that all executor implementations must follow, along with a factory function
to select the active executor based on configuration.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from logging import getLogger
from typing import Any

from app.tool_registry import Tool

logger = getLogger(__name__)


@dataclass
class ExecutorResult:
    """Result of a tool execution.

    Attributes
    ----------
    status:
        ``"success"`` (exit code 0) or ``"failed"``.
    output:
        Combined stdout/stderr of the execution.
    latency_ms:
        Wall-clock execution time in milliseconds.
    execution_id:
        Implementation-specific execution identifier
        (e.g. Docker container ID, Kubernetes Job name).
    """

    status: str  # "success" | "failed"
    output: str
    latency_ms: int
    execution_id: str


class Executor(abc.ABC):
    """Abstract base for tool executors.

    Subclasses must implement ``execute()`` which runs a ``Tool`` with the given
    arguments and returns an ``ExecutorResult``.
    """

    @abc.abstractmethod
    async def execute(self, tool: Tool, arguments: dict[str, Any]) -> ExecutorResult:
        """Run *tool* with *arguments* and return the execution result."""
        ...


# ── Shared utilities ─────────────────────────────────────────────────────────

def inject_params(command: list[str], arguments: dict[str, Any]) -> list[str]:
    """Replace ``$VAR`` tokens in *command* elements with values from *arguments*.

    Only exact ``$NAME`` tokens (at the start of a list element) are replaced.
    This keeps construction array-based and prevents shell injection.
    """
    result: list[str] = []
    for part in command:
        if part.startswith("$") and part[1:] in arguments:
            result.append(str(arguments[part[1:]]))
        else:
            result.append(part)
    return result


# ── Factory ──────────────────────────────────────────────────────────────────


def create_executor(mode: str = "docker") -> Executor:
    """Return an executor implementation based on *mode*.

    Parameters
    ----------
    mode:
        ``"docker"`` (default) — returns ``DockerToolExecutor``.
        ``"k8s"`` — returns ``K8sJobExecutor`` (requires ``kubernetes`` package).
    """
    if mode == "k8s":
        try:
            from app.k8s_executor import K8sJobExecutor  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "K8s executor requires the 'kubernetes' package. "
                "Install with: uv sync --group k8s"
            ) from exc
        logger.info("Using K8sJobExecutor")
        return K8sJobExecutor()

    from app.docker_executor import DockerToolExecutor  # noqa: PLC0415

    logger.info("Using DockerToolExecutor")
    return DockerToolExecutor()
