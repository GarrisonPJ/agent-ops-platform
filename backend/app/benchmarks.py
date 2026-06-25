"""Predefined benchmark task definitions for the eval benchmark system.

Each task has a unique ``name``, a ``task`` prompt that is sent to the agent, and
a human-readable ``description``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class BenchmarkTask:
    """A single predefined benchmark task."""

    name: str
    task: str
    description: str


BENCHMARK_TASKS: List[BenchmarkTask] = [
    BenchmarkTask(
        name="bench_01_http_request",
        task="使用 http_request 发送 GET 请求到 https://httpbin.org/json 获取数据",
        description="HTTP GET request to httpbin",
    ),
    BenchmarkTask(
        name="bench_02_kubectl_get_pods",
        task="列出 default namespace 中的所有 pod",
        description="List pods in default namespace",
    ),
    BenchmarkTask(
        name="bench_03_docker_logs",
        task="获取 docker 容器 agentops-api 的日志",
        description="Get logs from docker container",
    ),
    BenchmarkTask(
        name="bench_04_multi_step",
        task="先获取 pod 列表，然后尽可能多地收集集群信息",
        description="Multi-step: get pods and cluster info",
    ),
    BenchmarkTask(
        name="bench_05_no_matching_tool",
        task="使用 WinDbg 分析系统 crash dump 文件",
        description="No matching tool available",
    ),
]


def get_benchmark_task(name: str) -> BenchmarkTask | None:
    """Look up a benchmark task by name.

    Returns the matching ``BenchmarkTask`` or ``None`` when not found.
    """
    for t in BENCHMARK_TASKS:
        if t.name == name:
            return t
    return None
