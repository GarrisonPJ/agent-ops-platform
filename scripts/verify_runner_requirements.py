"""Verify the packaged Runner's exact Linux/Python 3.12 dependency closure.

CI calls this before building independent MCP Agents from runner-requirements.lock.
Resolve only locked metadata with packaging's PEP 508 parser; never install code.
Read runner/Dockerfile for installation and backend/uv.lock for upstream pins.
"""

from __future__ import annotations

import re
import tomllib
from collections import deque
from pathlib import Path

from packaging.markers import Marker, default_environment
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
ROOT_PACKAGES = ("mcp", "httpx")
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)")


def verify(requirements_text: str, lock: dict) -> set[str]:
    """Fail closed on missing, unpinned, ambiguous, or extraneous packages."""
    requirements: dict[str, str] = {}
    for line in requirements_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if match is None:
            raise ValueError("Runner requirements must contain exact pins only")
        name, version = canonicalize_name(match[1]), match[2]
        if name in requirements:
            raise ValueError(f"duplicate Runner requirement: {name}")
        requirements[name] = version

    packages = {}
    for package in lock["package"]:
        name = canonicalize_name(package["name"])
        if name in packages:
            raise ValueError(f"ambiguous locked package: {name}")
        packages[name] = package

    # The Docker image is CPython 3.12 on Linux. Never use the verifier host's
    # Windows/macOS/Python branches to decide which image dependencies to omit.
    environment = {
        **default_environment(),
        "sys_platform": "linux",
        "os_name": "posix",
        "platform_system": "Linux",
        "platform_machine": "x86_64",
        "platform_python_implementation": "CPython",
        "python_version": "3.12",
        "python_full_version": "3.12.0",
        "implementation_name": "cpython",
        "implementation_version": "3.12.0",
        "extra": "",
    }
    extras: dict[str, set[str]] = {}
    visited: set[str] = set()
    pending = deque((name, ()) for name in ROOT_PACKAGES)
    while pending:
        name, requested_extras = pending.popleft()
        name = canonicalize_name(name)
        selected = extras.setdefault(name, set())
        new_extra = not set(requested_extras) <= selected
        selected.update(requested_extras)
        if name in visited and not new_extra:
            continue
        visited.add(name)
        if name not in packages:
            raise ValueError(f"dependency absent from backend lock: {name}")
        package = packages[name]
        if requirements.get(name) != package["version"]:
            raise ValueError(f"missing or divergent Runner pin: {name}")
        dependencies = list(package.get("dependencies", []))
        for extra in selected:
            optional = package.get("optional-dependencies", {})
            if extra not in optional:
                raise ValueError(f"locked extra is unavailable: {name}[{extra}]")
            dependencies.extend(optional[extra])
        for dependency in dependencies:
            marker = dependency.get("marker")
            if marker is not None and not Marker(marker).evaluate(environment):
                continue
            pending.append((dependency["name"], tuple(dependency.get("extra", ()))))

    if set(requirements) != visited:
        raise ValueError("Runner requirements include packages outside the runtime closure")
    return visited


def main() -> None:
    closure = verify(
        (ROOT / "runner-requirements.lock").read_text(),
        tomllib.loads((ROOT / "backend/uv.lock").read_text()),
    )
    print(f"Runner dependency closure verified: {len(closure)} exact pins (Linux CPython 3.12)")


if __name__ == "__main__":
    main()
