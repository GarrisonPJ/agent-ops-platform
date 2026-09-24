from __future__ import annotations

import pytest

from app.phase1_main import create_app


@pytest.mark.asyncio
async def test_startup_fails_fast_without_runner_token_outside_explicit_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNNER_TOKEN", raising=False)
    monkeypatch.delenv("AGENTOPS_ENV", raising=False)
    application = create_app(initialize_database=False)

    with pytest.raises(RuntimeError, match="RUNNER_TOKEN must be set"):
        async with application.router.lifespan_context(application):
            pass


@pytest.mark.asyncio
async def test_explicit_development_allows_local_runner_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNNER_TOKEN", raising=False)
    monkeypatch.setenv("AGENTOPS_ENV", "development")
    application = create_app(initialize_database=False)

    async with application.router.lifespan_context(application):
        assert application.state.runner_token == "development-runner-token"


@pytest.mark.asyncio
async def test_startup_and_auth_use_one_runner_token_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNNER_TOKEN", raising=False)
    monkeypatch.delenv("AGENTOPS_ENV", raising=False)
    application = create_app(initialize_database=False)

    # Changing the environment after app construction must not make startup
    # validate one token while request authentication uses another.
    monkeypatch.setenv("RUNNER_TOKEN", "injected-too-late")
    with pytest.raises(RuntimeError, match="RUNNER_TOKEN must be set"):
        async with application.router.lifespan_context(application):
            pass
