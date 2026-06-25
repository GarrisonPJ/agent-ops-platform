"""Kubernetes Job Tool Executor.

Runs a ``Tool`` inside a Kubernetes Job: creates a Job with the tool's image
and command, waits for completion (with timeout), collects logs from the
underlying Pod, and deletes the Job.

Requires the ``kubernetes`` Python package (optional dependency group ``k8s``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any, TYPE_CHECKING

from app.executor import Executor, ExecutorResult, inject_params
from app.tool_registry import Tool

if TYPE_CHECKING:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config



class K8sJobExecutor(Executor):
    """Execute a ``Tool`` inside a Kubernetes Job.

    Each invocation creates a unique Job, waits for it to complete, collects
    logs from the underlying Pod, and deletes the Job.

    .. code-block:: python

        executor = K8sJobExecutor()
        result = await executor.execute(tool, arguments)
    """

    def __init__(self) -> None:
        self._initialized = False
        self._batch_api: Any = None  # kubernetes.client.BatchV1Api
        self._core_api: Any = None   # kubernetes.client.CoreV1Api
        self._ApiException: Any = Exception  # kubernetes.client.rest.ApiException

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, tool: Tool, arguments: dict[str, Any]) -> ExecutorResult:
        """Run *tool* in a Kubernetes Job.

        Steps
        -----
        1. Initialize the Kubernetes client (in-cluster or kubeconfig).
        2. Substitute ``$VAR`` tokens in *tool.command* from *arguments*.
        3. Generate a unique Job name.
        4. Create the Job.
        5. Wait for it to finish (enforcing ``tool.timeout_ms``).
        6. Collect stdout + stderr logs.
        7. Delete the Job.
        8. Return an ``ExecutorResult``.
        """
        start = time.perf_counter()

        # 1. Initialize K8s client ----------------------------------------
        try:
            await asyncio.to_thread(self._init_client)
        except Exception as exc:
            logger.error("Kubernetes client initialization failed: %s", exc)
            return ExecutorResult(
                status="failed",
                output=f"Failed to initialize Kubernetes client: {exc}",
                latency_ms=0,
                execution_id="",
            )

        # 2. Parameter injection -------------------------------------------
        command = inject_params(tool.command, arguments)

        # 3. Generate unique job name --------------------------------------
        job_name = _make_job_name(tool.name)

        # 4. Create Job ----------------------------------------------------
        namespace = self._resolve_namespace()
        job_spec = _build_job_spec(job_name, tool, command)

        logger.info(
            "Creating K8s Job %s in namespace %s (image=%s)",
            job_name,
            namespace,
            tool.image,
        )
        try:
            await asyncio.to_thread(
                self._batch_api.create_namespaced_job,
                namespace=namespace,
                body=job_spec,
            )
        except self._ApiException as exc:
            logger.error("Failed to create K8s Job %s: %s", job_name, exc)
            return ExecutorResult(
                status="failed",
                output=f"Failed to create Kubernetes Job: {exc}",
                latency_ms=int((time.perf_counter() - start) * 1000),
                execution_id=job_name,
            )

        # 5. Wait with timeout ---------------------------------------------
        timeout_sec = (tool.timeout_ms or 30_000) / 1000
        try:
            exit_code = await asyncio.wait_for(
                self._wait_for_job(job_name, namespace),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.warning("K8s Job %s timed out after %sms", job_name, tool.timeout_ms)
            await asyncio.to_thread(self._delete_job, job_name, namespace)
            elapsed = int((time.perf_counter() - start) * 1000)
            return ExecutorResult(
                status="failed",
                output=f"Tool execution timed out after {tool.timeout_ms}ms",
                latency_ms=elapsed,
                execution_id=job_name,
            )
        except self._ApiException as exc:
            logger.error("K8s Job %s error: %s", job_name, exc)
            await asyncio.to_thread(self._delete_job, job_name, namespace)
            elapsed = int((time.perf_counter() - start) * 1000)
            return ExecutorResult(
                status="failed",
                output=f"Kubernetes Job error: {exc}",
                latency_ms=elapsed,
                execution_id=job_name,
            )

        # 6. Collect logs --------------------------------------------------
        try:
            output = await self._get_pod_logs(job_name, namespace)
        except Exception as exc:
            output = f"Failed to read pod logs: {exc}"

        # 7. Delete Job ----------------------------------------------------
        await asyncio.to_thread(self._delete_job, job_name, namespace)

        # 8. Determine status ----------------------------------------------
        status = "success" if exit_code == 0 else "failed"
        if exit_code != 0 and not output.strip():
            output = f"Job exited with code {exit_code}"
        elif exit_code != 0:
            output = f"Exit code {exit_code}\n{output}"

        elapsed = int((time.perf_counter() - start) * 1000)
        return ExecutorResult(
            status=status,
            output=output.strip(),
            latency_ms=elapsed,
            execution_id=job_name,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_client(self) -> None:
        """Initialize the Kubernetes API clients (idempotent)."""
        if self._initialized:
            return

        from kubernetes import client, config  # noqa: PLC0415
        from kubernetes.client.rest import ApiException  # noqa: PLC0415

        self._ApiException = ApiException

        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Loaded kubeconfig")

        self._batch_api = client.BatchV1Api()
        self._core_api = client.CoreV1Api()
        self._initialized = True

    def _resolve_namespace(self) -> str:
        """Detect the current Kubernetes namespace from the service account.

        Falls back to ``"default"`` when not running inside a cluster.
        """
        try:
            with open(
                "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
                encoding="utf-8",
            ) as f:
                return f.read().strip()
        except FileNotFoundError:
            return "default"

    async def _wait_for_job(self, name: str, namespace: str) -> int:
        """Poll the Job status until it completes.

        Returns the container exit code (0 for success, nonzero for failure).
        """
        while True:
            job = await asyncio.to_thread(
                self._batch_api.read_namespaced_job_status,
                name=name,
                namespace=namespace,
            )

            status = job.status

            # Job succeeded
            if status.succeeded is not None and status.succeeded >= 1:
                return 0

            # Job failed — fetch the exit code from the pod
            if status.failed is not None and status.failed >= 1:
                exit_code = await self._get_pod_exit_code(name, namespace)
                return exit_code if exit_code is not None else -1

            # Still running
            await asyncio.sleep(0.5)

    async def _get_pod_exit_code(
        self, job_name: str, namespace: str
    ) -> int | None:
        """Retrieve the container exit code from the Pod created by the Job."""
        pods = await asyncio.to_thread(
            self._core_api.list_namespaced_pod,
            namespace=namespace,
            label_selector=f"job-name={job_name}",
        )

        if not pods.items:
            return None

        pod = pods.items[0]
        for container_status in pod.status.container_statuses or []:
            if container_status.state.terminated is not None:
                return container_status.state.terminated.exit_code
        return None

    async def _get_pod_logs(self, job_name: str, namespace: str) -> str:
        """Fetch logs from the Pod created by the Job."""
        # Wait briefly for the pod to appear (up to ~10s)
        pods = None
        for _ in range(20):
            pods = await asyncio.to_thread(
                self._core_api.list_namespaced_pod,
                namespace=namespace,
                label_selector=f"job-name={job_name}",
            )
            if pods.items:
                break
            await asyncio.sleep(0.5)

        if not pods or not pods.items:
            return "No pod found for job"

        pod_name = pods.items[0].metadata.name

        try:
            logs: str = await asyncio.to_thread(
                self._core_api.read_namespaced_pod_log,
                name=pod_name,
                namespace=namespace,
            )
            return logs or ""
        except self._ApiException as exc:
            return f"Failed to read pod logs: {exc}"

    def _delete_job(self, name: str, namespace: str) -> None:
        """Best-effort delete a Job and its associated Pods."""
        try:
            self._batch_api.delete_namespaced_job(
                name=name,
                namespace=namespace,
                propagation_policy="Background",
            )
        except self._ApiException:
            logger.debug("Job %s already deleted or not found", name)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _make_job_name(tool_name: str) -> str:
    """Generate a unique, K8s-compatible Job name.

    Result is a DNS-1123 label: ``[a-z0-9]([-a-z0-9]*[a-z0-9])?``
    """
    suffix = uuid.uuid4().hex[:8]
    safe_name = re.sub(r"[^a-z0-9-]", "-", tool_name.lower())
    safe_name = safe_name.strip("-")
    # K8s names max 253 chars; truncate to leave room for prefix + suffix + dashes
    max_name_len = 253 - len("agentops-tool--") - len(suffix)
    safe_name = safe_name[:max_name_len].rstrip("-")
    return f"agentops-tool-{safe_name}-{suffix}"


def _build_job_spec(
    name: str, tool: Tool, command: list[str]
) -> dict[str, object]:
    """Build a Kubernetes Job body dict for the given tool."""
    container: dict[str, object] = {
        "name": "tool",
        "image": tool.image,
        "command": command,
    }

    # Map resource_limits to K8s resources
    if tool.resource_limits:
        limits: dict[str, str] = {}
        if "memory" in tool.resource_limits:
            limits["memory"] = tool.resource_limits["memory"]
        if "cpu" in tool.resource_limits:
            limits["cpu"] = tool.resource_limits["cpu"]
        if limits:
            container["resources"] = {
                "limits": limits,
                "requests": limits,
            }

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/managed-by": "agentops",
                "app.kubernetes.io/component": "executor",
            },
        },
        "spec": {
            "backoffLimit": 0,  # never retry
            "ttlSecondsAfterFinished": 300,  # auto-cleanup after 5 minutes
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [container],
                },
            },
        },
    }
