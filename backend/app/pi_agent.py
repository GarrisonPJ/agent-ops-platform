"""pi coding agent adapter: the registered external Agent-under-test for H2.

The Runner starts this process like any registered Agent: an EvaluationSpec on
stdin, the loopback Environment MCP endpoint and capability token in the
environment. It writes a private per-Run pi configuration, runs
``pi --mode json`` against that Environment and the Runner's
``AGENTOPS_PROVIDER_*`` endpoint, and reports only fixed codes.

The adapter never scores and never submits on pi's behalf. It never puts the
capability token or API key in argv, files or output. Exit 0 is not success:
the Control Plane decides from Environment facts.

Read ``app.reference_agent`` next for the in-repo loop pi is compared with, and
ADR-0009's H2 amendment for why each pi flag is set.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.fixture_agent import AgentInputError, AgentSpec, read_spec
from app.mcp_agent_client import MCPAgentError, validate_endpoint, validate_token
from app.phase1_provider import ProviderError, ProviderSettings
from app.reference_agent import system_prompt

PI_PROGRAM = "pi"
MCP_SERVER = "env"
PROVIDER = "agentops"
SUBMIT_TOOL = f"mcp__{MCP_SERVER}__submit_result"
MAX_EVENT_LINE_BYTES = 4 * 1024 * 1024
TERMINATE_GRACE_SECONDS = 2.0
# Keep pi away from the operator's own configuration: no extensions except
# built-in MCP, no skills, templates, context files, themes, project trust,
# sessions or startup network traffic.
ISOLATION_FLAGS = (
    "--no-session",
    "--no-extensions",
    "--extension",
    "builtin:mcp",
    "--no-skills",
    "--no-prompt-templates",
    "--no-context-files",
    "--no-themes",
    "--no-approve",
    "--offline",
)
STOP_CODES = {"error": "PI_PROVIDER_ERROR", "aborted": "PI_ABORTED", "length": "PI_STOP_LENGTH"}


def mcp_config(endpoint: str, timeout_ms: int) -> dict[str, object]:
    """The only MCP server pi sees; the token is interpolated from pi's environment."""
    return {
        "mcpServers": {
            MCP_SERVER: {
                "url": endpoint,
                "headers": {"Authorization": "Bearer ${AGENTOPS_ENV_TOKEN}"},
                "exposure": "direct",
                "timeout": max(1, min(math.ceil(timeout_ms / 1000), 300)),
            }
        },
        "autoEnableCodemode": False,
    }


def models_config(settings: ProviderSettings) -> dict[str, object]:
    """Mirror the reference agent's Runner-only Provider settings where pi can."""
    sampling = {
        name: value
        for name in ("temperature", "reasoning_effort", "parallel_tool_calls")
        if (value := getattr(settings, name)) is not None
    }
    model: dict[str, object] = {
        "id": settings.model,
        "reasoning": False,
        # Send max_tokens like the reference agent and omit OpenAI's `store`.
        "compat": {"maxTokensField": "max_tokens", "supportsStore": False},
    }
    if settings.max_tokens is not None:
        model["maxTokens"] = settings.max_tokens
    if sampling:
        model["samplingParams"] = sampling
    return {
        "providers": {
            PROVIDER: {
                "baseUrl": settings.base_url,
                "api": "openai-completions",
                "apiKey": "$AGENTOPS_PROVIDER_API_KEY",
                "models": [model],
            }
        }
    }


def pi_argv(program: str, model: str, system_prompt_path: Path) -> list[str]:
    # The task goes on stdin: a message argument starting with `@` would make
    # pi read a local file into the prompt.
    return [
        program,
        "--mode",
        "json",
        *ISOLATION_FLAGS,
        "--provider",
        PROVIDER,
        "--model",
        model,
        "--tools",
        f"mcp__{MCP_SERVER}__*",
        "--append-system-prompt",
        str(system_prompt_path),
    ]


@dataclass
class PiOutcome:
    submitted: bool = False
    last_stop: str | None = None

    def observe(self, line: bytes) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "tool_execution_end":
            if event.get("toolName") == SUBMIT_TOOL and event.get("isError") is False:
                self.submitted = True
        elif kind == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                reason = message.get("stopReason")
                self.last_stop = reason if isinstance(reason, str) else None

    def code(self, returncode: int | None) -> str | None:
        """Return None for an accepted submission, else one fixed failure code."""
        if self.submitted:
            return None
        if self.last_stop in STOP_CODES:
            return STOP_CODES[self.last_stop]
        if returncode not in (0, None):
            return "PI_EXIT_NONZERO"
        return "PI_SUBMIT_REQUIRED"


async def _read_events(stream: asyncio.StreamReader, outcome: PiOutcome) -> None:
    # pi's JSONL is large and untrusted. Parse bounded lines only and stop at
    # an accepted submission. Nothing is forwarded to this process's stdout,
    # which the Runner counts against max_output_bytes.
    pending = b""
    oversized = False
    while chunk := await stream.read(65_536):
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            if not oversized:
                outcome.observe(line)
            oversized = False
            if outcome.submitted:
                return
        if len(pending) > MAX_EVENT_LINE_BYTES:
            pending, oversized = b"", True


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), TERMINATE_GRACE_SECONDS)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


async def run(spec: AgentSpec) -> int:
    try:
        settings = ProviderSettings.from_env()
        endpoint = validate_endpoint(os.environ.get("AGENTOPS_ENV_MCP_URL", ""))
        token = validate_token(os.environ.get("AGENTOPS_ENV_TOKEN", ""))
    except (ProviderError, MCPAgentError) as error:
        _error(error.code)
        return 1
    program = shutil.which(PI_PROGRAM)
    if program is None:
        _error("PI_NOT_FOUND")
        return 1
    root = Path(tempfile.mkdtemp(prefix="agentops-pi-"))
    process = None
    try:
        agent_dir, home, work, tmp = (root / name for name in ("agent", "home", "work", "tmp"))
        for directory in (agent_dir, home, work, tmp):
            directory.mkdir(mode=0o700)
        _write_private(agent_dir / "mcp.json", json.dumps(mcp_config(endpoint, spec.timeout_ms)))
        _write_private(agent_dir / "models.json", json.dumps(models_config(settings)))
        # A path, not inline text: pi treats an argument naming a file as that file.
        _write_private(agent_dir / "system.md", system_prompt(spec))
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "TMPDIR": str(tmp),
            "PI_CODING_AGENT_DIR": str(agent_dir),
            "PI_OFFLINE": "1",
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY": "0",
            "AGENTOPS_ENV_TOKEN": token,
            "AGENTOPS_PROVIDER_API_KEY": settings.api_key,
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *pi_argv(program, settings.model, agent_dir / "system.md"),
                cwd=work,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # pi diagnostics can carry endpoints and provider text.
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            _error("PI_NOT_FOUND")
            return 1
        assert process.stdin is not None and process.stdout is not None
        # pi reads stdin to EOF as the first prompt; close it so pi never waits.
        process.stdin.write(spec.task.encode("utf-8"))
        with contextlib.suppress(ConnectionError):
            await process.stdin.drain()
        process.stdin.close()
        outcome = PiOutcome()
        await _read_events(process.stdout, outcome)
        if outcome.submitted:
            # Like the reference loop, the Agent ends at its accepted submission;
            # later calls could only add rejected world attempts.
            await _terminate(process)
        else:
            await process.wait()
        code = outcome.code(process.returncode)
        if code is not None:
            _error(code)
            return 1
        return 0
    finally:
        if process is not None:
            await _terminate(process)
        shutil.rmtree(root, ignore_errors=True)


def _error(code: str) -> None:
    print(str(code)[:64], file=sys.stderr, flush=True)


async def _main(spec: AgentSpec) -> int:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    # The Runner signals the whole process group; cancel so pi is reaped and
    # the private configuration directory is removed.
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await asyncio.wait_for(run(spec), timeout=spec.timeout_ms / 1000)
    except asyncio.TimeoutError:
        _error("AGENT_TIMEOUT")
        return 1
    except asyncio.CancelledError:
        _error("AGENT_CANCELLED")
        return 1


def main() -> int:
    try:
        spec = read_spec()
    except AgentInputError:
        print("INVALID_SPEC", file=sys.stderr, flush=True)
        return 2
    return asyncio.run(_main(spec))


if __name__ == "__main__":
    raise SystemExit(main())
