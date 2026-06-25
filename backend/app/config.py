"""Application configuration via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (walks up from this file's directory)
_env_file = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_file)


@dataclass(frozen=True)
class Settings:
    # Database
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://agentops:agentops@db:5432/agentops",
        )
    )

    # LLM
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o")
    )
    llm_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "4096"))
    )
    llm_max_steps: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_STEPS", "15"))
    )

    # App
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "*").split(",")
    )
    api_host: str = field(
        default_factory=lambda: os.getenv("API_HOST", "0.0.0.0")
    )
    api_port: int = field(
        default_factory=lambda: int(os.getenv("API_PORT", "8000"))
    )
    debug: bool = field(
        default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true"
    )

    # Context window
    context_window_limit: int = field(
        default_factory=lambda: int(os.getenv("CONTEXT_WINDOW_LIMIT", "128000"))
    )

    # Executor
    executor_mode: str = field(
        default_factory=lambda: os.getenv("EXECUTOR_MODE", "docker")
    )


settings = Settings()


def get_settings() -> Settings:
    """FastAPI-compatible dependency that returns the application ``Settings``.

    Usage in a route handler::

        from app.config import get_settings, Settings
        from fastapi import Depends

        @app.get("/api/foo")
        async def foo(settings: Settings = Depends(get_settings)):
            ...

    The module-level ``settings`` singleton is preserved for backward
    compatibility (non-route code imports it directly).
    """
    return settings
