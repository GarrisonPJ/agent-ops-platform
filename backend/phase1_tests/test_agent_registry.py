"""Deterministic tests for the operator-owned agent registry.

These tests exercise the parser and API boundary without starting a Runner,
using temporary TOML files and the isolated API fixture.  The invariant is
that requests select registered identities but never receive trusted argv.
Read ``app.agent_registry`` next for the parser implementation.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_registry import AgentRegistryError, MAX_REGISTRY_BYTES, load_registry
from app.phase1_models import Policy, Run
from app.phase1_schemas import EvaluationSpec


ROOT = Path(__file__).parents[1]


def _write_registry(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _registry_body(**overrides: object) -> str:
    entry = {
        "id": "fixture-agent.v1",
        "name": "Fixture",
        "kind": "fixture",
        "description": "Deterministic fixture",
        "command": ["python", "-m", "app.fixture_agent"],
    }
    entry.update(overrides)
    return (
        "[[agents]]\n"
        + "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in entry.items())
        + "\n"
    )


@pytest.mark.parametrize(
    ("identifier", "valid"),
    [
        ("ab.v1", True),
        ("01.v0", True),
        ("a-.v123", True),
        ("a" * 61 + ".v1", True),
        ("a" * 62 + ".v1", False),
        ("a.v1", False),
        (".v1", False),
        ("-a.v1", False),
        ("Ab.v1", False),
        ("ab_cd.v1", False),
        ("éa.v1", False),
        ("ab.v١", False),
        ("ab.v", False),
        ("ab.v-1", False),
        ("ab.v1.extra", False),
    ],
)
def test_registry_and_protocol_agent_id_grammar(
    tmp_path: Path, identifier: str, valid: bool
) -> None:
    path = _write_registry(tmp_path / "agents.toml", _registry_body(id=identifier))
    spec = {"run_id": "r", "experiment_id": "e", "task": "task", "agent_id": identifier}
    if valid:
        assert load_registry(path).agents[0].metadata.id == identifier
        assert EvaluationSpec.model_validate(spec).agent_id == identifier
    else:
        with pytest.raises(AgentRegistryError):
            load_registry(path)
        with pytest.raises(ValidationError):
            EvaluationSpec.model_validate(spec)


@pytest.mark.parametrize(("field", "maximum"), [("name", 200), ("description", 1_000)])
@pytest.mark.parametrize("character", ["x", "界", "😀"])
def test_registry_text_bounds_count_characters(
    tmp_path: Path, field: str, maximum: int, character: str
) -> None:
    path = tmp_path / "agents.toml"
    registry = load_registry(_write_registry(path, _registry_body(**{field: character * maximum})))
    assert getattr(registry.agents[0].metadata, field) == character * maximum
    for invalid in ["", character * (maximum + 1)]:
        with pytest.raises(AgentRegistryError):
            load_registry(_write_registry(path, _registry_body(**{field: invalid})))


@pytest.mark.parametrize("field", ["name", "description"])
def test_registry_rejects_escaped_nul_metadata(tmp_path: Path, field: str) -> None:
    body = _registry_body(**{field: "before\x00after"})
    assert "\\u0000" in body
    with pytest.raises(AgentRegistryError, match="NUL"):
        load_registry(_write_registry(tmp_path / "agents.toml", body))


def test_registry_preserves_literal_argv(tmp_path: Path) -> None:
    argv = ["program with spaces", "quoted arg; $HOME", "$(touch never)", "", "雪\\path"]
    # Empty argv entries are invalid, even though other shell-like text is literal.
    with pytest.raises(AgentRegistryError):
        load_registry(_write_registry(tmp_path / "agents.toml", _registry_body(command=argv)))
    argv.remove("")
    registry = load_registry(_write_registry(tmp_path / "agents.toml", _registry_body(command=argv)))
    assert registry.agents[0].command == tuple(argv)


@pytest.mark.parametrize(
    "argv",
    [[], [" "], ["python", ""], ["python", "\x00"], ["x" * 1_025], ["x"] * 33],
)
def test_registry_rejects_invalid_argv_bounds(tmp_path: Path, argv: list[str]) -> None:
    with pytest.raises(AgentRegistryError):
        load_registry(_write_registry(tmp_path / "agents.toml", _registry_body(command=argv)))


@pytest.mark.parametrize("character", ["x", "界", "😀"])
def test_registry_command_entry_bounds_count_characters(tmp_path: Path, character: str) -> None:
    path = tmp_path / "agents.toml"
    argv = ["python", character * 1_024]
    registry = load_registry(_write_registry(path, _registry_body(command=argv)))
    assert registry.agents[0].command == tuple(argv)
    with pytest.raises(AgentRegistryError):
        load_registry(_write_registry(path, _registry_body(command=["python", character * 1_025])))


def test_registry_accepts_exact_entry_and_argv_bounds(tmp_path: Path) -> None:
    argv = ["x" * 1_024] * 32
    path = tmp_path / "agents.toml"
    registry = load_registry(_write_registry(path, _registry_body(command=argv)))
    assert registry.agents[0].command == tuple(argv)
    body = "".join(_registry_body(id=f"agent-{index}.v1") for index in range(32))
    assert len(load_registry(_write_registry(path, body)).agents) == 32
    for body in ["agents = []", "", _registry_body() + "unexpected = 'field'\n"]:
        with pytest.raises(AgentRegistryError):
            load_registry(_write_registry(path, body))


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_registry_file_byte_limit(tmp_path: Path, extra_bytes: int) -> None:
    content = _registry_body(name="雪").encode("utf-8")
    content += b"#" + b"x" * (MAX_REGISTRY_BYTES - len(content) - 1 + extra_bytes)
    path = tmp_path / "agents.toml"
    path.write_bytes(content)
    assert path.stat().st_size == MAX_REGISTRY_BYTES + extra_bytes
    if extra_bytes:
        with pytest.raises(AgentRegistryError, match="exceeds"):
            load_registry(path)
    else:
        assert load_registry(path).agents[0].metadata.name == "雪"


@pytest.mark.parametrize("oversized", [False, True])
def test_registry_uses_one_bounded_binary_read(monkeypatch, oversized: bool) -> None:
    content = b"x" * (MAX_REGISTRY_BYTES + 100) if oversized else _registry_body().encode("utf-8")
    reads = []
    opens = []

    class RecordingFile(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            assert size == MAX_REGISTRY_BYTES + 1
            return super().read(size)

    def open_registry(path: Path, mode: str):
        opens.append((path, mode))
        assert mode == "rb"
        return RecordingFile(content)

    monkeypatch.setattr(Path, "open", open_registry)
    path = Path("registry.toml")
    if oversized:
        with pytest.raises(AgentRegistryError, match="exceeds"):
            load_registry(path)
    else:
        assert load_registry(path).agents[0].metadata.id == "fixture-agent.v1"
    assert opens == [(path, "rb")]
    assert reads == [MAX_REGISTRY_BYTES + 1]


def test_registry_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "agents.toml"
    path.write_bytes(_registry_body().encode("utf-8") + b"#\xff")
    with pytest.raises(AgentRegistryError, match="invalid agent registry"):
        load_registry(path)


def test_default_registry_public_metadata_has_no_command() -> None:
    registry = load_registry()
    assert [item.public_dict() for item in registry.metadata()] == [
        {
            "id": "fixture-agent.v1",
            "name": "Deterministic fixture agent",
            "kind": "fixture",
            "description": "Deterministic MCP agent for local Golden validation.",
        },
        {
            "id": "reference-openai-compatible.v1",
            "name": "OpenAI-compatible reference agent",
            "kind": "reference",
            "description": "MCP reference loop using Runner-side provider configuration.",
        },
    ]
    assert all("command" not in item.public_dict() for item in registry.metadata())


@pytest.mark.parametrize(
    "body",
    [
        "[[agents]]\nid='fixture-agent.v1'\nname='x'\nkind='fixture'\ndescription='x'\ncommand=['python']\nunknown='no'\n",
        "[[agents]]\nid='fixture-agent.v1'\nname='x'\nkind='fixture'\ndescription='x'\ncommand=['python']\n\n[[agents]]\nid='fixture-agent.v1'\nname='y'\nkind='fixture'\ndescription='y'\ncommand=['python']\n",
        "[[agents]]\nid='bad id'\nname='x'\nkind='fixture'\ndescription='x'\ncommand=['python']\n",
        "[[agents]\nid='fixture-agent.v1'\n",
    ],
)
def test_invalid_registry_fails_closed(tmp_path: Path, body: str) -> None:
    with pytest.raises(AgentRegistryError):
        load_registry(_write_registry(tmp_path / "agents.toml", body))


def test_registry_bounds_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "agents.toml"
    too_many = "\n".join(
        "[[agents]]\nid='agent-%d.v1'\nname='x'\nkind='fixture'\ndescription='x'\ncommand=['python']" % index
        for index in range(33)
    )
    with pytest.raises(AgentRegistryError):
        load_registry(_write_registry(path, too_many))
    path.write_bytes(b"x" * 65_537)
    with pytest.raises(AgentRegistryError):
        load_registry(path)


def test_agent_id_schema_rejects_malformed_and_accepts_versioned() -> None:
    with pytest.raises(ValidationError):
        EvaluationSpec(
            run_id="r",
            experiment_id="e",
            task="task",
            agent_id="not registered",
        )
    spec = EvaluationSpec(
        run_id="r",
        experiment_id="e",
        task="task",
        agent_id="fixture-agent.v1",
    )
    assert spec.agent_id == "fixture-agent.v1"


@pytest.mark.asyncio
async def test_api_rejects_malformed_id_and_invalid_operator_registry(api, monkeypatch, tmp_path: Path) -> None:
    client, _factory = api
    malformed = await client.post(
        "/api/experiments",
        json={"name": "Malformed", "task": "task", "agent_id": "not-an-id"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "VALIDATION_ERROR"
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("not = valid for this registry", encoding="utf-8")
    monkeypatch.setenv("AGENTOPS_AGENT_REGISTRY", str(invalid))
    unavailable = await client.get("/api/agents")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "AGENT_REGISTRY_INVALID"


@pytest.mark.asyncio
async def test_api_metadata_and_agent_selection_are_safe(api, monkeypatch) -> None:
    client, _factory = api
    monkeypatch.delenv("AGENTOPS_AGENT_REGISTRY", raising=False)
    metadata = await client.get("/api/agents")
    assert metadata.status_code == 200
    assert set(metadata.json()[0]) == {"id", "name", "kind", "description"}
    assert "command" not in metadata.text

    unknown = await client.post(
        "/api/experiments",
        json={"name": "Unknown", "task": "task", "agent_id": "missing-agent.v1"},
    )
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "AGENT_NOT_REGISTERED"
    assert unknown.json()["details"] == {"agent_id": "missing-agent.v1"}

    fixture = await client.post(
        "/api/experiments",
        json={"name": "Fixture", "task": "task"},
    )
    assert fixture.status_code == 201
    fixture_run = await client.post(f"/api/experiments/{fixture.json()['id']}/runs", json={})
    assert fixture_run.json()["evaluation_spec"]["agent_id"] == "fixture-agent.v1"

    provider = await client.post(
        "/api/experiments",
        json={"name": "Provider", "task": "task", "execution_mode": "provider"},
    )
    assert provider.status_code == 201
    provider_run = await client.post(f"/api/experiments/{provider.json()['id']}/runs", json={})
    assert provider_run.json()["evaluation_spec"]["agent_id"] == "reference-openai-compatible.v1"


@pytest.mark.asyncio
async def test_replay_preserves_selected_agent_id(api) -> None:
    client, factory = api
    experiment = await client.post(
        "/api/experiments",
        json={"name": "Replay identity", "task": "task", "agent_id": "fixture-agent.v1"},
    )
    run = await client.post(f"/api/experiments/{experiment.json()['id']}/runs", json={})
    async with factory() as session:
        session.add(
            Policy(
                id="policy-registry-test",
                experiment_id=experiment.json()["id"],
                source_run_id=run.json()["id"],
                status="candidate",
                patch={"instruction_patch": [], "tool_priority": {}, "max_steps": 6},
                rationale="registry test",
            )
        )
        await session.commit()
    replay = await client.post("/api/policies/policy-registry-test/replay")
    assert replay.status_code == 201
    async with factory() as session:
        replay_run = await session.get(Run, replay.json()["replay_run_id"])
        assert replay_run is not None
        assert replay_run.evaluation_spec["agent_id"] == "fixture-agent.v1"


def test_migration_upgrade_and_downgrade_fresh_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite3"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0009_experiment_agent_id"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(experiments)")}
        assert "agent_id" in columns
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0008_regression_cases"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(experiments)")}
        assert "agent_id" not in columns


def test_migration_preserves_populated_pre_0009_experiment(tmp_path: Path) -> None:
    database = tmp_path / "legacy-registry.sqlite3"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}

    def upgrade(revision: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    upgrade("0008_regression_cases")
    legacy_spec = {
        "run_id": "legacy-run",
        "experiment_id": "legacy-experiment",
        "task": "Preserve the legacy task",
        "execution_mode": "fixture",
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO experiments "
            "(id, name, task, scenario_id, scenario_params, execution_mode, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-experiment", "Legacy experiment", legacy_spec["task"],
                "checkout-api-latency.v2", '{"region": "local"}', "fixture",
                "2026-01-01 00:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO runs "
            "(id, experiment_id, kind, status, metrics, evaluation_spec, queued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-run", "legacy-experiment", "baseline", "succeeded", '{}',
                json.dumps(legacy_spec), "2026-01-01 00:00:00",
            ),
        )
        before = connection.execute("SELECT * FROM experiments").fetchone()
        before_run = connection.execute("SELECT * FROM runs").fetchone()

    upgrade("0009_experiment_agent_id")
    with sqlite3.connect(database) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(experiments)")}
        assert columns["agent_id"][2] == "VARCHAR(64)"
        assert columns["agent_id"][3] == 0  # Existing rows need no backfill.
        after = connection.execute("SELECT * FROM experiments").fetchone()
        assert after[:-1] == before
        assert after[-1] is None
        assert connection.execute("SELECT * FROM runs").fetchone() == before_run
        stored_spec = json.loads(connection.execute("SELECT evaluation_spec FROM runs").fetchone()[0])
        assert stored_spec == legacy_spec
        parsed = EvaluationSpec.model_validate(stored_spec)
        assert parsed.agent_id is None
        assert load_registry().resolve(parsed.agent_id, parsed.execution_mode.value) == "fixture-agent.v1"
