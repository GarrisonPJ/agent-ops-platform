"""Regress the independent-Agent image dependency admission check.

CI invokes the same verifier against locked metadata, without package installs.
Missing transitive/extra pins must fail even when all direct roots are present.
Read scripts/verify_runner_requirements.py for the fixed image target profile.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("runner_requirements", ROOT / "scripts/verify_runner_requirements.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inputs():
    return (ROOT / "runner-requirements.lock").read_text(), tomllib.loads((ROOT / "backend/uv.lock").read_text())


def test_packaged_dependency_closure_matches_upstream_lock():
    requirements, lock = inputs()
    closure = MODULE.verify(requirements, lock)
    assert {"mcp", "httpx", "httpx2", "cryptography", "cffi", "pycparser"} <= closure
    assert "pywin32" not in closure and "httpx2-jsfetch" not in closure


@pytest.mark.parametrize("dependency", ["pycparser", "cryptography", "mcp-types"])
def test_missing_transitive_and_extra_dependencies_fail(dependency):
    requirements, lock = inputs()
    requirements = "\n".join(line for line in requirements.splitlines() if not line.lower().startswith(dependency + "=="))
    with pytest.raises(ValueError, match="missing or divergent"):
        MODULE.verify(requirements, lock)


@pytest.mark.parametrize("pin", ["mcp==0.0.1", "mcp>=2.3.0", "mcp==2.3.0", "not-a-runtime-dependency==1.0.0"])
def test_divergent_unpinned_duplicate_or_unrelated_requirement_fails(pin):
    requirements, lock = inputs()
    with pytest.raises(ValueError):
        MODULE.verify(requirements + "\n" + pin, lock)


def test_platform_markers_do_not_follow_verifier_host(monkeypatch):
    requirements, lock = inputs()
    monkeypatch.setattr(MODULE, "default_environment", lambda: {
        "sys_platform": "win32", "python_full_version": "3.13.0", "os_name": "nt",
    })
    assert MODULE.verify(requirements, lock) == MODULE.verify(*inputs())
