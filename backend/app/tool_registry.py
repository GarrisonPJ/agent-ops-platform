"""In-memory Tool Registry.

Provides a ``Tool`` dataclass (matching the schema from PRD §13) and a
``ToolRegistry`` singleton that manages tool registration and lookup.

Pre-registered demo tools
-------------------------
- ``kubectl_get_pods`` — Query pods from a Kubernetes namespace.
- ``docker_logs``      — Fetch logs from a Docker container.
- ``http_request``     — Make an arbitrary HTTP request via curl.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Tool:
    """Schema for a runnable tool (PRD §13).

    Fields
    ------
    name           Tool identifier (unique).
    description    Human-readable description of what the tool does.
    parameters     OpenAI function-calling JSON Schema describing inputs.
    image          OCI container image that implements the tool.
    command        Shell command / entry-point arguments (list of strings).
    timeout_ms     Maximum execution time in milliseconds.
    resource_limits
                   Optional dict of resource constraints (CPU, memory, …).
    """

    name: str
    description: str
    parameters: dict
    image: str
    command: list[str]
    timeout_ms: int | None = None
    resource_limits: dict | None = None
    enabled: bool = True
    trigger_condition: str | None = None


# ── Conversion helpers ────────────────────────────────────────────────────


def tool_to_schema(tool: Tool) -> dict:
    """Convert a ``Tool`` dataclass to an OpenAI-compatible ``ToolSchema`` dict.

    The result can be passed directly to ``LLMProvider.chat(tools=[...])``.
    """
    description = tool.description
    if tool.trigger_condition is not None:
        description = f"{description}\n\nWhen to use: {tool.trigger_condition}"

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": description,
            "parameters": tool.parameters,
        },
    }


class ToolRegistry:
    """Registry of available tools.

    This is a **module-level singleton** — call ``ToolRegistry.get_instance()``
    to retrieve the shared instance.
    """

    _instance: ToolRegistry | None = None

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool (replaces any existing tool with the same name)."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name, or ``None`` if not found."""
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        """Return a copy of all registered tools."""
        return list(self._tools.values())

    def toggle(self, name: str) -> bool | None:
        """Toggle a tool's enabled state. Returns new state or None if not found."""
        tool = self._tools.get(name)
        if tool is None:
            return None
        tool.enabled = not tool.enabled
        return tool.enabled

    # ------------------------------------------------------------------
    # Demo seed
    # ------------------------------------------------------------------

    def register_demo_tools(self) -> None:
        """Pre-register the three demo tools defined by issue #02."""
        tools = [
            Tool(
                name="kubectl_get_pods",
                description=(
                    "Retrieve a list of pods from a Kubernetes namespace "
                    "in JSON format."
                ),
                trigger_condition=(
                    "Use when you need to inspect running pods in a "
                    "Kubernetes cluster, such as checking pod status, "
                    "finding pod names, or debugging deployment issues."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "NAMESPACE": {
                            "type": "string",
                            "description": "Target Kubernetes namespace",
                        },
                    },
                    "required": ["NAMESPACE"],
                },
                image="bitnami/kubectl:latest",
                command=["get", "pods", "-n", "$NAMESPACE", "-o", "json"],
                timeout_ms=30_000,
                resource_limits={"memory": "256m", "cpu": "250m"},
            ),
            Tool(
                name="docker_logs",
                description="Fetch logs from a running Docker container.",
                trigger_condition=(
                    "Use when you need to examine log output from a "
                    "Docker container, such as debugging runtime errors "
                    "or monitoring application output."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "CONTAINER": {
                            "type": "string",
                            "description": "Container name or ID",
                        },
                    },
                    "required": ["CONTAINER"],
                },
                image="docker:cli",
                command=["logs", "$CONTAINER"],
                timeout_ms=30_000,
                resource_limits={"memory": "128m", "cpu": "100m"},
            ),
            Tool(
                name="http_request",
                description="Send an HTTP request using curl.",
                trigger_condition=(
                    "Use when you need to query an external API or "
                    "retrieve web content via HTTP, such as fetching "
                    "data from REST endpoints or checking service health."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "METHOD": {
                            "type": "string",
                            "description": "HTTP method (GET, POST, PUT, DELETE, …)",
                        },
                        "URL": {
                            "type": "string",
                            "description": "Request target URL",
                        },
                    },
                    "required": ["METHOD", "URL"],
                },
                image="curlimages/curl:latest",
                command=["curl", "-s", "-X", "$METHOD", "$URL"],
                timeout_ms=30_000,
                resource_limits={"memory": "64m", "cpu": "100m"},
            ),
        ]
        for t in tools:
            self.register(t)
