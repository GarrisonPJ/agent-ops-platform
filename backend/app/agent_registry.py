"""Operator-owned agent registry parsing and resolution.

This module loads the API host's TOML registry for the HTTP control plane,
which calls it before persisting an Experiment or Run.  It must not expose
trusted commands, paths, environment, or secrets through public metadata.
Read ``app.phase1_schemas`` next for the wire-level agent identifier rules.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MAX_REGISTRY_BYTES: Final = 65_536
MAX_AGENTS: Final = 32
MAX_AGENT_ID_LENGTH: Final = 64
MAX_AGENT_NAME_LENGTH: Final = 200
MAX_AGENT_DESCRIPTION_LENGTH: Final = 1_000
MAX_COMMAND_ENTRIES: Final = 32
MAX_COMMAND_ENTRY_LENGTH: Final = 1_024
AGENT_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]+\.v[0-9]+$")
_ALLOWED_KEYS: Final = frozenset({"id", "name", "kind", "description", "command"})
_ALLOWED_KINDS: Final = frozenset({"fixture", "reference", "external"})


class AgentRegistryError(ValueError):
    """Raised when the operator registry is absent or fails closed validation."""


@dataclass(frozen=True)
class AgentMetadata:
    """Public, non-executable representation of a registered agent."""

    id: str
    name: str
    kind: str
    description: str

    def public_dict(self) -> dict[str, str]:
        """Return precisely the fields safe for API consumers."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
        }


@dataclass(frozen=True)
class RegisteredAgent:
    """Validated private registration, including the trusted argv."""

    metadata: AgentMetadata
    command: tuple[str, ...]


@dataclass(frozen=True)
class AgentRegistry:
    """Immutable validated registry indexed by versioned agent id."""

    agents: tuple[RegisteredAgent, ...]

    @property
    def by_id(self) -> dict[str, RegisteredAgent]:
        return {agent.metadata.id: agent for agent in self.agents}

    def metadata(self) -> list[AgentMetadata]:
        return [agent.metadata for agent in self.agents]

    def resolve(self, agent_id: str | None, execution_mode: str) -> str:
        selected = agent_id or (
            "fixture-agent.v1"
            if execution_mode == "fixture"
            else "reference-openai-compatible.v1"
        )
        if selected not in self.by_id:
            raise AgentRegistryError(f"Agent is not registered: {selected}")
        return selected


def registry_path(*, environ: dict[str, str] | None = None) -> Path:
    """Resolve the configured registry or the repository-relative default."""
    values = os.environ if environ is None else environ
    configured = values.get("AGENTOPS_AGENT_REGISTRY", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "config" / "agents.toml"


def _bounded_string(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise AgentRegistryError(f"{field} must be 1 to {maximum} characters")
    if "\x00" in value:
        raise AgentRegistryError(f"{field} must not contain NUL")
    return value


def _parse_entry(raw: object, index: int) -> RegisteredAgent:
    if not isinstance(raw, dict):
        raise AgentRegistryError(f"agents[{index}] must be a table")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise AgentRegistryError(f"agents[{index}] has unknown keys: {', '.join(sorted(unknown))}")
    missing = _ALLOWED_KEYS - set(raw)
    if missing:
        raise AgentRegistryError(f"agents[{index}] is missing keys: {', '.join(sorted(missing))}")
    identifier = _bounded_string(raw["id"], field="agent id", maximum=MAX_AGENT_ID_LENGTH)
    if not identifier.isascii() or not AGENT_ID_PATTERN.fullmatch(identifier):
        raise AgentRegistryError("agent id must match ^[a-z0-9][a-z0-9-]+\\.v[0-9]+$")
    name = _bounded_string(raw["name"], field="agent name", maximum=MAX_AGENT_NAME_LENGTH)
    description = _bounded_string(
        raw["description"], field="agent description", maximum=MAX_AGENT_DESCRIPTION_LENGTH
    )
    kind = _bounded_string(raw["kind"], field="agent kind", maximum=32)
    if kind not in _ALLOWED_KINDS:
        raise AgentRegistryError("agent kind must be fixture or reference")
    command = raw["command"]
    if not isinstance(command, list) or not 1 <= len(command) <= MAX_COMMAND_ENTRIES:
        raise AgentRegistryError(f"agent command must contain 1 to {MAX_COMMAND_ENTRIES} entries")
    argv: list[str] = []
    for value in command:
        item = _bounded_string(value, field="agent command entry", maximum=MAX_COMMAND_ENTRY_LENGTH)
        argv.append(item)
    if not argv[0].strip():
        raise AgentRegistryError("agent command program must not be whitespace-only")
    return RegisteredAgent(AgentMetadata(identifier, name, kind, description), tuple(argv))


def load_registry(path: Path | None = None) -> AgentRegistry:
    """Read and validate the complete registry without partial acceptance."""
    selected = path or registry_path()
    try:
        with selected.open("rb") as registry_file:
            content = registry_file.read(MAX_REGISTRY_BYTES + 1)
        if len(content) > MAX_REGISTRY_BYTES:
            raise AgentRegistryError(f"registry file exceeds {MAX_REGISTRY_BYTES} bytes")
        raw = tomllib.loads(content.decode("utf-8"))
    except AgentRegistryError:
        raise
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise AgentRegistryError(f"invalid agent registry: {exc}") from exc
    if set(raw) != {"agents"} or not isinstance(raw.get("agents"), list):
        raise AgentRegistryError("registry must contain only an agents array")
    entries = raw["agents"]
    if not 1 <= len(entries) <= MAX_AGENTS:
        raise AgentRegistryError(f"registry must contain 1 to {MAX_AGENTS} agents")
    parsed = tuple(_parse_entry(item, index) for index, item in enumerate(entries))
    ids = [item.metadata.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise AgentRegistryError("registry agent ids must be unique")
    return AgentRegistry(parsed)


def effective_agent_id(agent_id: str | None, execution_mode: str) -> str:
    """Resolve an explicit id or stable mode-specific H1 default."""
    return load_registry().resolve(agent_id, execution_mode)
