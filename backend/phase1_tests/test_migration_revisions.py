from __future__ import annotations

import ast
from pathlib import Path


MAX_ALEMBIC_REVISION_LENGTH = 32
MIGRATIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"


def _revision_id(path: Path) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in module.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        if statement.target.id != "revision" or not isinstance(statement.value, ast.Constant):
            continue
        if isinstance(statement.value.value, str):
            return statement.value.value
    raise AssertionError(f"migration {path.name} must declare a string revision")


def test_alembic_revisions_fit_the_version_table() -> None:
    revisions = {path.name: _revision_id(path) for path in MIGRATIONS_DIR.glob("*.py")}
    too_long = {
        name: revision
        for name, revision in revisions.items()
        if len(revision) > MAX_ALEMBIC_REVISION_LENGTH
    }
    assert not too_long, f"Alembic version table accepts at most 32 characters: {too_long}"
