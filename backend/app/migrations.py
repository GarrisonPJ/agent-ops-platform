"""Helpers for comparing a live database with the application migration head."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def application_alembic_head() -> str:
    """Return the single Alembic head shipped with the application."""

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("path_separator", "os")
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("application has no Alembic head")
    return head
