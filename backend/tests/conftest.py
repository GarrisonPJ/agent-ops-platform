"""Shared fixtures for backend tests."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB


# Allow JSONB columns to work with SQLite (testing only).
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    """Render JSONB as JSON on SQLite."""
    return compiler.visit_JSON(element, **kw)


# Allow pgvector Vector columns to work with SQLite (testing only).
# The Vector type is a UserDefinedType; on SQLite we store as BLOB.
try:
    from pgvector.sqlalchemy import Vector

    @compiles(Vector, "sqlite")
    def _compile_vector_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
        return "BLOB"
except ImportError:
    pass


@pytest_asyncio.fixture
async def session() -> AsyncSession:  # type: ignore[misc]
    """Create a fresh in-memory SQLite database for each test."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
    )

    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with _async_session() as sess:
        yield sess

    await engine.dispose()


@pytest.fixture
def perfect_trajectory() -> dict[str, Any]:
    """A trajectory with all steps successful, minimal costs."""
    return {
        "steps": [
            {
                "action": {"name": "search", "arguments": {"q": "hello"}},
                "observation": "Found results",
                "latency_ms": 100,
            },
            {
                "action": {"name": "read", "arguments": {"url": "https://example.com"}},
                "observation": "Page content",
                "latency_ms": 200,
            },
            {
                "action": None,
                "observation": "Final answer: hello world",
                "latency_ms": 50,
            },
        ],
        "status": "success",
        "total_tokens": 500,
        "total_latency_ms": 350,
    }


@pytest.fixture
def failed_trajectory() -> dict[str, Any]:
    """All steps failed, status is failed."""
    return {
        "steps": [
            {
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Error: connection refused",
                "latency_ms": 1000,
            },
            {
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Timeout: no response",
                "latency_ms": 2000,
            },
        ],
        "status": "failed",
        "total_tokens": 2000,
        "total_latency_ms": 3000,
    }


@pytest.fixture
def no_tool_calls_trajectory() -> dict[str, Any]:
    """Trajectory with no tool calls — direct answer."""
    return {
        "steps": [
            {
                "action": None,
                "observation": "Direct answer: 42",
                "latency_ms": 100,
            },
        ],
        "status": "success",
        "total_tokens": 100,
        "total_latency_ms": 100,
    }


@pytest.fixture
def all_failed_tool_calls_trajectory() -> dict[str, Any]:
    """All tool calls returned failures."""
    return {
        "steps": [
            {
                "action": {"name": "search", "arguments": {"q": "x"}},
                "observation": "Error: something went wrong",
                "latency_ms": 500,
            },
            {
                "action": {"name": "read", "arguments": {"url": "x"}},
                "observation": "Failed to fetch",
                "latency_ms": 600,
            },
            {
                "action": None,
                "observation": "Could not complete task",
                "latency_ms": 50,
            },
        ],
        "status": "success",  # overall success despite tool failures
        "total_tokens": 800,
        "total_latency_ms": 1150,
    }
