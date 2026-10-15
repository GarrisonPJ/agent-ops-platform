from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("database_url", [
    "postgresql+asyncpg://operator:encoded%40password@localhost/unused",
    "postgresql+asyncpg://operator:encoded%25password@localhost/unused",
    "postgresql+asyncpg://operator:encoded%40and%25password@localhost/unused?application_name=encoded%25query",
])
def test_alembic_cli_accepts_encoded_url_in_offline_migrations(database_url: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    # Revision 0006 sanitizes persisted rows and needs a live database connection.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0005_runner_presence", "--sql"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "CREATE TABLE alembic_version" in result.stdout
    assert "0005_runner_presence" in result.stdout
    assert result.stdout.rstrip().endswith("COMMIT;")
    assert database_url not in result.stdout + result.stderr
