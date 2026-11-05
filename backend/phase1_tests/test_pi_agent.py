"""pi adapter tests with a fake `pi` executable; CI never installs or calls pi."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import pi_agent
from app.fixture_agent import AgentSpec
from app.phase1_provider import ProviderSettings
from app.reference_agent import SYSTEM_INSTRUCTIONS, system_prompt

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "c" * 64
API_KEY = "provider-secret-key"
PLAN = [
    ("check_service_health", {"service": "checkout-api"}),
    ("query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}),
    ("fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}),
]

FAKE_PI = '''#!{python}
import asyncio, json, os, sys, time
sys.path.insert(0, {backend!r})
MODE, MARKER, PLAN = {mode!r}, {marker!r}, {plan!r}

def emit(event):
    print(json.dumps(event), flush=True)

task = sys.stdin.read()
config = os.environ["PI_CODING_AGENT_DIR"]
record = {{
    "argv": sys.argv[1:], "task": task, "config": config, "cwd": os.getcwd(),
    "cwd_files": os.listdir("."), "env": dict(os.environ),
    "files": {{name: open(os.path.join(config, name)).read() for name in sorted(os.listdir(config))}},
}}
with open(MARKER, "w") as handle:
    json.dump(record, handle)

def assistant_end(reason):
    emit({{"type": "message_end", "message": {{"role": "assistant", "stopReason": reason}}}})

if MODE == "world":
    from app.mcp_agent_client import MCPAgentClient
    server = json.loads(record["files"]["mcp.json"])["mcpServers"]["env"]
    header = server["headers"]["Authorization"].replace("${{AGENTOPS_ENV_TOKEN}}", os.environ["AGENTOPS_ENV_TOKEN"])
    async def play():
        async with MCPAgentClient(server["url"], header.removeprefix("Bearer ")) as client:
            for name, args in PLAN + [("submit_result", {{}})]:
                result = await client.call_tool(name, args)
                emit({{"type": "tool_execution_end", "toolName": "mcp__env__" + name, "isError": result.is_error}})
    asyncio.run(play())
    time.sleep(30)  # the adapter must stop pi at the accepted submission
elif MODE == "flood":
    for index in range(1500):
        emit({{"type": "message_update", "delta": "x" * 2048, "index": index}})
    print("y" * (5 * 1024 * 1024), flush=True)
    emit({{"type": "tool_execution_end", "toolName": "mcp__env__submit_result", "isError": False}})
elif MODE == "provider_error":
    assistant_end("error")
elif MODE == "stop":
    assistant_end("stop")
elif MODE == "rejected_submit":
    emit({{"type": "tool_execution_end", "toolName": "mcp__env__submit_result", "isError": True}})
    assistant_end("stop")
elif MODE == "crash":
    sys.exit(3)
elif MODE == "hang":
    time.sleep(60)
'''


def write_fake_pi(directory: Path, mode: str) -> Path:
    marker = directory / "record.json"
    bin_dir = directory / "bin"
    bin_dir.mkdir()
    script = bin_dir / "pi"
    script.write_text(FAKE_PI.format(python=sys.executable, backend=str(ROOT), mode=mode,
                                     marker=str(marker), plan=PLAN))
    script.chmod(0o755)
    return marker


def spec_line(task: str = "Investigate checkout latency.", timeout_ms: int = 20_000, **extra) -> bytes:
    payload = {"schema_version": 1, "run_id": "run-pi", "experiment_id": "experiment-pi",
               "scenario_id": "checkout-api-latency.v2", "task": task,
               "execution_mode": "provider", "limits": {"timeout_ms": timeout_ms}} | extra
    return (json.dumps(payload) + "\n").encode()


def adapter_env(tmp_path: Path, endpoint: str = "http://127.0.0.1:9/mcp", **extra) -> dict[str, str]:
    return {
        "PATH": str(tmp_path / "bin"),
        "AGENTOPS_ENV_MCP_URL": endpoint,
        "AGENTOPS_ENV_TOKEN": TOKEN,
        "AGENTOPS_PROVIDER_BASE_URL": "http://127.0.0.1:8080/v1",
        "AGENTOPS_PROVIDER_MODEL": "qwen3:8b",
        "AGENTOPS_PROVIDER_API_KEY": API_KEY,
        **extra,
    }


def run_adapter(tmp_path: Path, mode: str, *, task: str = "Investigate checkout latency.",
                timeout_ms: int = 20_000, endpoint: str = "http://127.0.0.1:9/mcp", **extra):
    marker = write_fake_pi(tmp_path, mode)
    result = subprocess.run(
        [sys.executable, "-m", "app.pi_agent"],
        input=spec_line(task, timeout_ms), capture_output=True, cwd=ROOT, timeout=60,
        env=adapter_env(tmp_path, endpoint, AGENTOPS_TASK_PROMPT=task, **extra),
    )
    record = json.loads(marker.read_text()) if marker.exists() else None
    return result, record


def settings(**overrides) -> ProviderSettings:
    values = {"base_url": "http://127.0.0.1:8080/v1", "api_key": API_KEY, "model": "qwen3:8b",
              "timeout_ms": 60_000, "max_retries": 1} | overrides
    return ProviderSettings(**values)


def test_system_prompt_is_shared_with_reference_agent() -> None:
    plain = AgentSpec("t", "checkout-api-latency.v2", 6, 1000, False, {})
    patched = AgentSpec("t", "checkout-api-latency.v2", 6, 1000, True, {}, ("Check health first.",))
    assert system_prompt(plain) == SYSTEM_INSTRUCTIONS
    assert system_prompt(patched) == SYSTEM_INSTRUCTIONS + "\nPolicy instructions:\nCheck health first."


def test_config_interpolates_secrets_and_mirrors_provider_settings() -> None:
    mcp = pi_agent.mcp_config("http://127.0.0.1:4321/mcp", 60_000)
    server = mcp["mcpServers"]["env"]
    assert server == {"url": "http://127.0.0.1:4321/mcp",
                      "headers": {"Authorization": "Bearer ${AGENTOPS_ENV_TOKEN}"},
                      "exposure": "direct", "timeout": 60}
    assert mcp["autoEnableCodemode"] is False
    full = pi_agent.models_config(settings(max_tokens=256, temperature=0.0, reasoning_effort="none",
                                           parallel_tool_calls=False))
    provider = full["providers"]["agentops"]
    assert provider["apiKey"] == "$AGENTOPS_PROVIDER_API_KEY"
    assert provider["api"] == "openai-completions"
    assert provider["baseUrl"] == "http://127.0.0.1:8080/v1"
    assert provider["models"] == [{
        "id": "qwen3:8b", "reasoning": False, "maxTokens": 256,
        "compat": {"maxTokensField": "max_tokens", "supportsStore": False},
        "samplingParams": {"temperature": 0.0, "reasoning_effort": "none", "parallel_tool_calls": False},
    }]
    unset = pi_agent.models_config(settings())["providers"]["agentops"]["models"][0]
    assert "maxTokens" not in unset and "samplingParams" not in unset
    seeded = pi_agent.models_config(settings(seed=7))["providers"]["agentops"]["models"][0]
    assert seeded["samplingParams"] == {"seed": 7}
    per_run = settings(seed="per-run").for_run("run-a")
    assert pi_agent.models_config(per_run)["providers"]["agentops"]["models"][0]["samplingParams"] == {
        "seed": per_run.seed}
    assert API_KEY not in json.dumps(full) + json.dumps(mcp)


def test_argv_isolates_pi_and_never_carries_the_task() -> None:
    argv = pi_agent.pi_argv("/opt/pi", "qwen3:8b", Path("/run/agent/system.md"))
    assert argv[:3] == ["/opt/pi", "--mode", "json"]
    for flag in ("--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates",
                 "--no-context-files", "--no-themes", "--no-approve", "--offline"):
        assert flag in argv
    assert argv[argv.index("--extension") + 1] == "builtin:mcp"
    assert argv[argv.index("--tools") + 1] == "mcp__env__*"
    assert argv[argv.index("--append-system-prompt") + 1] == "/run/agent/system.md"
    assert argv[-2:] == ["--append-system-prompt", "/run/agent/system.md"]


@pytest.mark.parametrize(
    ("events", "returncode", "code"),
    [
        ([{"type": "tool_execution_end", "toolName": "mcp__env__submit_result", "isError": False}], 0, None),
        ([{"type": "tool_execution_end", "toolName": "mcp__env__submit_result", "isError": True}], 0,
         "PI_SUBMIT_REQUIRED"),
        ([{"type": "tool_execution_end", "toolName": "mcp__other__submit_result", "isError": False}], 0,
         "PI_SUBMIT_REQUIRED"),
        ([{"type": "message_end", "message": {"role": "assistant", "stopReason": "error"}}], 0,
         "PI_PROVIDER_ERROR"),
        ([{"type": "message_end", "message": {"role": "assistant", "stopReason": "aborted"}}], 0, "PI_ABORTED"),
        ([{"type": "message_end", "message": {"role": "assistant", "stopReason": "length"}}], 0,
         "PI_STOP_LENGTH"),
        ([{"type": "message_end", "message": {"role": "assistant", "stopReason": "error"}},
          {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop"}}], 0,
         "PI_SUBMIT_REQUIRED"),
        ([], 3, "PI_EXIT_NONZERO"),
        ([], 0, "PI_SUBMIT_REQUIRED"),
    ],
)
def test_outcome_codes(events, returncode, code) -> None:
    outcome = pi_agent.PiOutcome()
    for event in events:
        outcome.observe(json.dumps(event).encode())
    outcome.observe(b"not json")
    outcome.observe(b"[1, 2]")
    assert outcome.code(returncode) == code


SUBMIT_EVENT = json.dumps({"type": "tool_execution_end", "toolName": "mcp__env__submit_result",
                           "isError": False}).encode()


@pytest.mark.asyncio
async def test_event_reader_ignores_the_whole_oversized_line() -> None:
    reader = asyncio.StreamReader()
    # A submit event inside an oversized line must not count.
    reader.feed_data(b"z" * (pi_agent.MAX_EVENT_LINE_BYTES + 10) + SUBMIT_EVENT + b"\n")
    reader.feed_data(b'{"type": "message_end", "message": {"role": "assistant", "stopReason": "error"}}\n')
    reader.feed_eof()
    outcome = pi_agent.PiOutcome()
    await pi_agent._read_events(reader, outcome)
    assert (outcome.submitted, outcome.last_stop) == (False, "error")


@pytest.mark.asyncio
async def test_event_reader_returns_at_submit_without_waiting_for_eof() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(SUBMIT_EVENT + b"\n")
    outcome = pi_agent.PiOutcome()
    await asyncio.wait_for(pi_agent._read_events(reader, outcome), timeout=1)
    assert outcome.submitted


@pytest.mark.asyncio
async def test_fake_pi_drives_real_environment_and_stops_at_submit(tmp_path: Path) -> None:
    spec = {"schema_version": 1, "run_id": "run-pi", "experiment_id": "experiment-pi",
            "scenario_id": "checkout-api-latency.v2", "task": "@/etc/hostname investigate",
            "execution_mode": "provider", "limits": {"timeout_ms": 20_000}}
    environment = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.env_server", cwd=ROOT,
        env={"PATH": os.environ["PATH"], "AGENTOPS_ENV_TOKEN": TOKEN},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        environment.stdin.write((json.dumps(spec) + "\n").encode())
        await environment.stdin.drain()
        ready = json.loads(await asyncio.wait_for(environment.stdout.readline(), timeout=10))
        started = time.monotonic()
        result, record = await asyncio.to_thread(
            run_adapter, tmp_path, "world", task=spec["task"], endpoint=ready["payload"]["mcp_url"])
        elapsed = time.monotonic() - started
    finally:
        environment.terminate()
        stdout, _ = await asyncio.wait_for(environment.communicate(), timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    assert elapsed < 20  # terminated after submit, not after the fake's 30 s sleep
    # Task reaches pi literally on stdin, never as an `@file` argument.
    assert record["task"] == "@/etc/hostname investigate"
    assert not any("investigate" in item for item in record["argv"])
    assert record["cwd_files"] == []
    env = record["env"]
    assert set(env) >= {"PATH", "HOME", "TMPDIR", "PI_CODING_AGENT_DIR", "PI_OFFLINE",
                        "PI_SKIP_VERSION_CHECK", "PI_TELEMETRY"}
    assert not any(key.startswith("AGENTOPS_PROVIDER_") and key != "AGENTOPS_PROVIDER_API_KEY" for key in env)
    files = record["files"]
    assert sorted(files) == ["mcp.json", "models.json", "system.md"]
    assert files["system.md"] == SYSTEM_INSTRUCTIONS
    on_disk = "".join(files.values()) + json.dumps(record["argv"])
    assert TOKEN not in on_disk and API_KEY not in on_disk
    assert not Path(record["config"]).exists()
    events = [json.loads(line) for line in stdout.splitlines()]
    steps = [event["payload"] for event in events if event["type"] == "step_completed"]
    assert [(s["tool_call"]["name"], s["environment"]["kind"], s["environment"]["result"]) for s in steps] == [
        ("check_service_health", "world", "ok"),
        ("query_service_metrics", "world", "ok"),
        ("fetch_service_logs", "world", "ok"),
        ("submit_result", "control", "ok"),
    ]
    assert "final_check" in steps[-1]["environment"]
    assert TOKEN.encode() not in stdout


@pytest.mark.parametrize(
    ("mode", "code"),
    [("provider_error", b"PI_PROVIDER_ERROR\n"), ("stop", b"PI_SUBMIT_REQUIRED\n"),
     ("rejected_submit", b"PI_SUBMIT_REQUIRED\n"), ("crash", b"PI_EXIT_NONZERO\n")],
)
def test_fake_pi_failures_map_to_fixed_codes(tmp_path: Path, mode: str, code: bytes) -> None:
    result, record = run_adapter(tmp_path, mode)
    assert (result.returncode, result.stdout, result.stderr) == (1, b"", code)
    assert not Path(record["config"]).exists()


def test_flooded_pi_output_is_not_forwarded(tmp_path: Path) -> None:
    result, _ = run_adapter(tmp_path, "flood")
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


def test_missing_pi_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    result = subprocess.run([sys.executable, "-m", "app.pi_agent"], input=spec_line(), capture_output=True,
                            cwd=ROOT, timeout=30, env=adapter_env(tmp_path))
    assert (result.returncode, result.stdout, result.stderr) == (1, b"", b"PI_NOT_FOUND\n")


@pytest.mark.parametrize(
    ("override", "code"),
    [({"AGENTOPS_PROVIDER_MODEL": ""}, b"PROVIDER_NOT_CONFIGURED\n"),
     ({"AGENTOPS_ENV_MCP_URL": "http://example.com/mcp"}, b"MCP_ENDPOINT_INVALID\n"),
     ({"AGENTOPS_ENV_TOKEN": "short"}, b"MCP_TOKEN_INVALID\n")],
)
def test_invalid_runner_inputs_fail_before_pi(tmp_path: Path, override, code) -> None:
    marker = write_fake_pi(tmp_path, "stop")
    result = subprocess.run([sys.executable, "-m", "app.pi_agent"], input=spec_line(), capture_output=True,
                            cwd=ROOT, timeout=30, env=adapter_env(tmp_path) | override)
    assert (result.returncode, result.stdout, result.stderr) == (1, b"", code)
    assert not marker.exists()


def test_timeout_kills_pi_and_removes_config(tmp_path: Path) -> None:
    started = time.monotonic()
    result, record = run_adapter(tmp_path, "hang", timeout_ms=1_000)
    assert time.monotonic() - started < 15
    assert (result.returncode, result.stdout, result.stderr) == (1, b"", b"AGENT_TIMEOUT\n")
    assert not Path(record["config"]).exists()


def test_sigterm_reaps_pi_and_removes_config(tmp_path: Path) -> None:
    marker = write_fake_pi(tmp_path, "hang")
    task = "Investigate checkout latency."
    adapter = subprocess.Popen([sys.executable, "-m", "app.pi_agent"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
                               env=adapter_env(tmp_path, AGENTOPS_TASK_PROMPT=task))
    adapter.stdin.write(spec_line(task))
    adapter.stdin.close()
    deadline = time.monotonic() + 15
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    adapter.send_signal(signal.SIGTERM)
    # Not communicate(): Python 3.12 flushes the already-closed stdin there.
    adapter.wait(timeout=15)
    stdout, stderr = adapter.stdout.read(), adapter.stderr.read()
    assert (adapter.returncode, stdout, stderr) == (1, b"", b"AGENT_CANCELLED\n")
    assert not Path(json.loads(marker.read_text())["config"]).exists()
