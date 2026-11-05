"""Deterministic tests for H1's optional operator-only generation settings.

Exercise the low-level client used by the independent reference Agent.
No real endpoint, credentials, world rewrites or model-dependent expectations are allowed.
Read test_independent_agents.py for MCP Agent integration coverage.
"""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.phase1_provider import OpenAICompatibleProvider, ProviderError, ProviderSettings

OPTIONS = {
    "MAX_TOKENS": "256",
    "TEMPERATURE": "0",
    "REASONING_EFFORT": "none",
    "PARALLEL_TOOL_CALLS": "false",
}
SEED = "AGENTOPS_PROVIDER_SEED"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for suffix in OPTIONS:
        monkeypatch.delenv("AGENTOPS_PROVIDER_" + suffix, raising=False)
    monkeypatch.delenv(SEED, raising=False)
    monkeypatch.setenv("AGENTOPS_PROVIDER_BASE_URL", "http://provider.invalid/v1")
    monkeypatch.setenv("AGENTOPS_PROVIDER_API_KEY", "PRIVATE_TEST_KEY")
    monkeypatch.setenv("AGENTOPS_PROVIDER_MODEL", "fake-model")
    monkeypatch.setenv("AGENTOPS_PROVIDER_TIMEOUT_MS", "1000")
    monkeypatch.setenv("AGENTOPS_PROVIDER_MAX_RETRIES", "0")


def test_unspecified_options_preserve_legacy_constructor_and_defaults():
    settings = ProviderSettings.from_env()
    assert settings.request_options() == {}
    assert ProviderSettings("http://fake", "key", "model", 1000, 0).request_options() == {}


def test_blank_options_are_omitted(monkeypatch):
    for suffix in OPTIONS:
        monkeypatch.setenv("AGENTOPS_PROVIDER_" + suffix, " \t")
    assert ProviderSettings.from_env().request_options() == {}


@pytest.mark.parametrize(("suffix", "value"), [
    ("MAX_TOKENS", "0"), ("MAX_TOKENS", "32769"), ("MAX_TOKENS", "1.5"),
    ("MAX_TOKENS", "true"), ("MAX_TOKENS", "PRIVATE_BAD_OPTION"),
    ("TEMPERATURE", "-0.1"), ("TEMPERATURE", "2.01"), ("TEMPERATURE", "NaN"),
    ("TEMPERATURE", "inf"), ("TEMPERATURE", "-inf"), ("TEMPERATURE", "PRIVATE_BAD_OPTION"),
    ("REASONING_EFFORT", "auto"), ("REASONING_EFFORT", "NONE"),
    ("PARALLEL_TOOL_CALLS", "0"), ("PARALLEL_TOOL_CALLS", "False"),
    ("PARALLEL_TOOL_CALLS", "PRIVATE_BAD_OPTION"),
])
def test_invalid_options_fail_closed_with_safe_configuration_error(monkeypatch, suffix, value):
    monkeypatch.setenv("AGENTOPS_PROVIDER_" + suffix, value)
    with pytest.raises(ProviderError) as captured:
        ProviderSettings.from_env()
    error = captured.value
    assert error.code == "PROVIDER_CONFIGURATION_ERROR"
    assert error.attempts == 0 and not error.retryable
    assert "PRIVATE" not in str(error) + json.dumps(error.payload())


@pytest.mark.parametrize(("suffix", "raw", "field", "expected"), [
    ("MAX_TOKENS", "1", "max_tokens", 1),
    ("MAX_TOKENS", "32768", "max_tokens", 32768),
    ("TEMPERATURE", "0", "temperature", 0.0),
    ("TEMPERATURE", "2", "temperature", 2.0),
    ("TEMPERATURE", " 0.25 ", "temperature", 0.25),
    *[("REASONING_EFFORT", v, "reasoning_effort", v) for v in ("none", "minimal", "low", "medium", "high", "xhigh")],
    ("PARALLEL_TOOL_CALLS", "true", "parallel_tool_calls", True),
    ("PARALLEL_TOOL_CALLS", "false", "parallel_tool_calls", False),
])
def test_valid_options_are_typed_and_independent(monkeypatch, suffix, raw, field, expected):
    monkeypatch.setenv("AGENTOPS_PROVIDER_" + suffix, raw)
    actual = ProviderSettings.from_env().request_options()
    assert actual == {field: expected}
    assert type(actual[field]) is type(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_exact_request_and_unmodified_response(monkeypatch, configured):
    if configured:
        for suffix, value in OPTIONS.items():
            monkeypatch.setenv("AGENTOPS_PROVIDER_" + suffix, value)
    settings = ProviderSettings.from_env()
    messages = [{"role": "user", "content": "Task"}]
    tools = [{"type": "function", "function": {
        "name": "world_tool", "parameters": {"type": "object", "properties": {}},
    }}]
    original_messages = json.loads(json.dumps(messages))
    original_tools = json.loads(json.dumps(tools))
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"id": "call-1", "type": "function", "function": {
                    "name": "world_tool", "arguments": "{}",
                }}],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await OpenAICompatibleProvider(settings, client=client).chat(messages, tools)
    assert len(calls) == 1
    assert str(calls[0].url) == "http://provider.invalid/v1/chat/completions"
    assert calls[0].headers["Authorization"] == "Bearer PRIVATE_TEST_KEY"
    expected = {"model": "fake-model", "messages": messages, "tools": tools, "tool_choice": "auto"}
    if configured:
        expected.update(max_tokens=256, temperature=0.0, reasoning_effort="none", parallel_tool_calls=False)
    assert json.loads(calls[0].content) == expected
    assert "PRIVATE_TEST_KEY" not in calls[0].content.decode()
    assert messages == original_messages and tools == original_tools
    # Invalid/empty world arguments remain the model's output; no automatic repair.
    assert response.tool_calls[0].arguments == {}
    assert response.tool_calls[0].name == "world_tool"
    assert response.usage.prompt_tokens == 10


@pytest.mark.asyncio
async def test_unsupported_options_do_not_trigger_semantic_fallback(monkeypatch):
    monkeypatch.setenv("AGENTOPS_PROVIDER_REASONING_EFFORT", "none")
    calls = []

    def reject(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "PRIVATE_UPSTREAM_BODY"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
        with pytest.raises(ProviderError) as captured:
            await OpenAICompatibleProvider(ProviderSettings.from_env(), client=client).chat([], [])
    assert len(calls) == 1 and calls[0]["reasoning_effort"] == "none"
    assert "PRIVATE_UPSTREAM_BODY" not in str(captured.value)


@pytest.mark.parametrize("raw", ["-1", "2147483648", "abc", "PER-RUN", "1.5", "1_0", "+1"])
def test_invalid_seed_fails_closed(monkeypatch, raw):
    monkeypatch.setenv(SEED, raw)
    with pytest.raises(ProviderError) as captured:
        ProviderSettings.from_env()
    assert captured.value.code == "PROVIDER_CONFIGURATION_ERROR"
    assert captured.value.attempts == 0 and not captured.value.retryable


@pytest.mark.parametrize(("raw", "expected"), [("0", 0), (" 42 ", 42), ("2147483647", 2147483647)])
def test_integer_seed_is_sent_unchanged(monkeypatch, raw, expected):
    monkeypatch.setenv(SEED, raw)
    settings = ProviderSettings.from_env()
    assert settings.request_options() == {"seed": expected}
    assert settings.for_run("run-a").request_options() == {"seed": expected}


def test_blank_seed_is_omitted(monkeypatch):
    monkeypatch.setenv(SEED, "  ")
    assert "seed" not in ProviderSettings.from_env().for_run("run-a").request_options()


def test_per_run_seed_follows_the_run_id_sha256_rule(monkeypatch):
    monkeypatch.setenv(SEED, "per-run")
    settings = ProviderSettings.from_env()
    with pytest.raises(RuntimeError):
        settings.request_options()
    seed = settings.for_run("run-a").request_options()["seed"]
    expected = int.from_bytes(hashlib.sha256(b"run-a").digest()[:4], "big") & 0x7FFFFFFF
    assert seed == expected and 0 <= seed <= 2147483647
    assert settings.for_run("run-a").request_options()["seed"] == seed
    assert settings.for_run("run-b").request_options()["seed"] != seed


@pytest.mark.asyncio
async def test_seed_appears_in_the_chat_request_body(monkeypatch):
    monkeypatch.setenv(SEED, "per-run")
    settings = ProviderSettings.from_env().for_run("run-a")
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await OpenAICompatibleProvider(settings, client=client).chat([], [])
    assert bodies[0]["seed"] == settings.seed
