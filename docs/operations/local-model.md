# Local model: reference-Agent setup and acceptance

H1-5 is complete at the local integration boundary: the registered reference Agent
has exercised an already installed Windows-native **llama.cpp** service through
MCP, persisted real traces/analysis, and completed successful research and stateful
order Runs. Real failures are retained, not rewritten as successes. Exact results:
[H1 live-model record](../history/h1-live-model-acceptance-2026-11-01.md).
This does not install a runtime/model, promise compatibility with every Provider,
or make live inference a CI dependency.

## 1. Configure only the Runner

The existing `reference-openai-compatible.v1` discovers the Environment's tools
and calls `<AGENTOPS_PROVIDER_BASE_URL>/chat/completions` directly. No proxy,
response adapter, automatic argument repair or forced submission is required.
API requests choose only the registered Agent and Environment, never endpoints,
models, keys, generation settings, commands or scripts.

| Runner variable | Validation / behavior |
|---|---|
| `AGENTOPS_PROVIDER_BASE_URL` | Required HTTP(S) OpenAI-compatible base, including `/v1` when needed |
| `AGENTOPS_PROVIDER_API_KEY` | Required nonempty value; use a protected secret source; never print it |
| `AGENTOPS_PROVIDER_MODEL` | Required endpoint model identifier |
| `AGENTOPS_PROVIDER_TIMEOUT_MS` | 100..60000; default 10000, per HTTP request |
| `AGENTOPS_PROVIDER_MAX_RETRIES` | 0..3; default 1, additional attempts for retryable failures |
| `AGENTOPS_PROVIDER_MAX_TOKENS` | Optional integer 1..32768, sent as `max_tokens` |
| `AGENTOPS_PROVIDER_TEMPERATURE` | Optional finite number 0..2 |
| `AGENTOPS_PROVIDER_REASONING_EFFORT` | Optional `none`, `minimal`, `low`, `medium`, `high`, `xhigh` |
| `AGENTOPS_PROVIDER_PARALLEL_TOOL_CALLS` | Optional exact `true`/`false`, sent as JSON boolean |

Blank/unset optional settings omit fields, preserving the original request shape.
An endpoint may support fewer options/values; HTTP rejection is surfaced normally,
not silently retried with different settings. Invalid local settings fail before
the first request. See [ADR-0003](../adr/0003-openai-compatible-provider-boundary.md).

The exercised Swift setup used these **Runner** settings (key intentionally absent):

```sh
export AGENTOPS_PROVIDER_BASE_URL=http://127.0.0.1:8080/v1
export AGENTOPS_PROVIDER_MODEL=swift-1.5-27b-iq3xxs
export AGENTOPS_PROVIDER_TIMEOUT_MS=60000
export AGENTOPS_PROVIDER_MAX_RETRIES=1
export AGENTOPS_PROVIDER_MAX_TOKENS=256
export AGENTOPS_PROVIDER_TEMPERATURE=0
export AGENTOPS_PROVIDER_REASONING_EFFORT=none
export AGENTOPS_PROVIDER_PARALLEL_TOOL_CALLS=false
# Set AGENTOPS_PROVIDER_API_KEY privately from your installed service's secret source.
```

The installed model guidance warns against `high`/`minimal` for that endpoint;
the generic client's accepted values are not an endpoint support guarantee.
Provider timeout is not the whole Run budget: canonical Runs currently have a
60000 ms overall budget including startup and tool/Provider requests.

## 2. Start an isolated stack

### Existing Compose workflow

Use a fresh project (`docker compose -p h1-local-model ...`), fresh volumes and an
explicit shared `RUNNER_TOKEN` for API/Runner. `.env.example` lists the settings;
Compose passes Provider variables **only** to Runner. Keep the key in an ignored,
restricted configuration/secret source, never a browser `VITE_` value. Follow the
[fixture quickstart](../../README.md#local-fixture-quickstart) for standard builds.

Container localhost is not the Windows host. Verify the chosen engine's route
before running acceptance. In the measured WSL engine, ordinary
`host.docker.internal:host-gateway` failed; a Linux **host-network Runner** reached
Windows loopback. That is not a portable Docker Desktop/NAT recipe. If using this
measured topology, an invocation-owned override can contain:

```yaml
services:
  runner:
    network_mode: host
    environment:
      AGENTOPS_SERVER_URL: http://127.0.0.1:8000
```

Use the actual published API port. Do not expose the model, disable firewalls,
change global networking, or assume other WSL distributions have the same route.

### Host-run alternative (the four-Environment acceptance path)

Requirements: Linux/WSL Python 3.12 + `uv`, Rust toolchain, and an **already created,
fresh isolated PostgreSQL 16 database**. Do not point validation at existing data.
From the repository root, set an isolated `DATABASE_URL` and generate an explicit
`RUNNER_TOKEN` privately; API and Runner must receive the same value.

```sh
uv sync --project backend --frozen
(cd runner && cargo build --workspace --locked)
export AGENTOPS_AGENT_REGISTRY="$PWD/config/agents.toml"
# In the API terminal (Provider credentials are unnecessary here):
(cd backend && uv run python -m alembic upgrade head)
(cd backend && uv run uvicorn app.phase1_main:app --host 127.0.0.1 --port 8000)
```

In a separate Runner terminal, set the same `RUNNER_TOKEN`, then the Provider
settings above and the key privately. Prepend the **venv bin directory**, not a
symlink to its Python executable, so registered `python -m ...` commands retain
virtualenv dependency resolution:

```sh
export PATH="$PWD/backend/.venv/bin:$PATH"
export AGENTOPS_AGENT_REGISTRY="$PWD/config/agents.toml"
export AGENTOPS_AGENT_WORKDIR="$PWD/backend"
export AGENTOPS_ENVIRONMENT_PROGRAM=python
export AGENTOPS_ENVIRONMENT_ARGS='["-m","app.env_server"]'
export AGENTOPS_ENVIRONMENT_WORKDIR="$PWD/backend"
export AGENTOPS_SERVER_URL=http://127.0.0.1:8000
export AGENTOPS_RUNNER_ID=local-model-runner
# Choose a NEW directory owned by this invocation; retain it through recovery.
umask 077
export AGENTOPS_ENV_TOKEN_HANDOFF_DIR="$(mktemp -d /tmp/agentops-handoff.XXXXXX)"
./runner/target/debug/agentops runner start
```

The key and database credentials must not be printed or committed. Stop only
services/resources started by this invocation; preserve evidence and existing data.

## 3. Capture real Runs

Check `/api/health/ready` and `/api/health/runner` first. From the repository root:

```sh
python3 scripts/local_model_acceptance.py \
  --api-url http://127.0.0.1:8000 \
  --scenario multi-step-research.v2 \
  --output-directory /absolute/absent/evidence-directory \
  --require-success
```

The default scenario is research. Repeat `--scenario` to include checkout,
fault orchestration and `order-support.v1`. The parent directory must exist;
existing output directories are refused. The command creates ordinary provider
Experiments/Runs, retains final Run, SSE, analysis and diagnostics, and checks
identity, contiguous events, terminal agreement and successful submission/check.
It sends no Provider settings or credentials through the API.

Without `--require-success`, exit 0 means **capture completed**, not that all tasks
succeeded; `summary.json` states `all_tasks_succeeded` separately. With that flag,
any failed task gives a nonzero exit while preserving all evidence. An observation
timeout does not cancel the Run: use the saved ID to inspect it before retrying.

## Interpretation and limits

- H1 accepts a functioning evaluation boundary, not a model that solves every task.
  Preserve invalid calls, failures, timeouts, accepted submission/check and CP analysis.
  Never change assertions, auto-submit, fill arguments, relabel failures or hide retries.
- Deterministic fixture/fake-Provider CI proves protocol/failure mechanics. Live-model
  acceptance adds actual API/tool compatibility; repeatability/pass^k is future H3.
- Agent-reported token totals accumulate across requests, not concurrent context
  occupancy. Missing context/Provider timing remains unknown, not a measured zero.
- The exercised laptop has about 12 GB VRAM; Swift used 8192 **combined** tokens,
  parallel 1 and Q8 K/V cache. Headroom was small. Do not increase context or promise
  coexistence with other GPU workloads based on this acceptance.
- Windows llama.cpp is the tested service; Ollama/vLLM examples are not newly tested
  runtime claims. Supported networking, model, quantization and runtime identity are
  bounded by the [dated record](../history/h1-live-model-acceptance-2026-11-01.md).
- Real-model success, analyzer accuracy, hosted CI, cold-cache builds, public safety
  and cross-platform Runner support remain different claims. H2 is not started.

Official topology/API references: [WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking),
[llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server),
[Ollama OpenAI compatibility](https://docs.ollama.com/openai).
