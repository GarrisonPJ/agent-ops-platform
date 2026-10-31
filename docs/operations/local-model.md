# Local model: deferred acceptance handoff

The operator explicitly deferred H1's real-local-model trace/analysis acceptance and assigned Windows model installation to another Agent. This is **not** a successful model acceptance or an automatic authorization to install software, download weights, change Windows/network/firewall configuration, or expose a service. CI remains fixture/fake-Provider only.

## Existing endpoint configuration

Once an operator supplies an already configured endpoint, the Runner's registered `reference-openai-compatible.v1` uses `AGENTOPS_PROVIDER_BASE_URL`, `AGENTOPS_PROVIDER_API_KEY`, `AGENTOPS_PROVIDER_MODEL`, timeout and bounded retries. The client appends `/chat/completions`; an Ollama example is `http://127.0.0.1:11434/v1`, model `qwen3:8b`, nonempty local placeholder key if the local server does not authenticate. These values belong only on the Runner, not in an Experiment request or browser bundle.

Model/tool-call compatibility and response timing must be measured. A `qwen3:8b` starting configuration is not a promise of successful task completion. The inspected local laptop GPU has about **12 GB VRAM**, not the original 16 GB assumption; current WSL-visible RAM is not total Windows RAM. Account for weights plus KV cache and start with modest context (for example 4K–8K) and one concurrent request. Do not promote 14B as guaranteed to fit.

## Shared Windows service, separate clients

A Windows-native Ollama service may serve multiple WSL distributions over HTTP; clients must not concurrently write a shared model directory. In mirrored WSL networking, Windows localhost services can generally be reached through `127.0.0.1`; NAT routing differs. This is topology guidance, not proof that another distribution was tested.

Container localhost is the container, not Windows. Separately verify the selected Docker engine's host route. Never replace the host address blindly, broaden the service to LAN, disable a firewall, or overwrite existing configuration as an incidental connectivity fix. Any such change needs operator approval.

Official references:
- [Microsoft WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking)
- [Ollama Windows](https://docs.ollama.com/windows)
- [OpenAI-compatible API](https://docs.ollama.com/openai)
- [Ollama FAQ](https://docs.ollama.com/faq)

## Reopen acceptance only with an approved endpoint

1. Record Provider/model/version, selected quantization/context/concurrency, GPU utilization, and the exact Runner network namespace. Do not record credentials or hidden reasoning.
2. Verify current WSL, any specifically requested other WSL distribution, and container routes independently. One route does not prove the others.
3. Use fresh isolated CP/database/Runner resources. Select the canonical Environment and reference Agent through the normal registry; do not invent world facts or force a successful score.
4. Persist actual tool attempts, accepted submit/final-check if produced, terminal outcome, safe usage/Provider diagnostics, and CP analysis. A legitimate model failure is evidence, not permission to change assertions or labels.
5. Bind the exercised payload and parent result to review. State any failure, unsupported tool-call shape or timing limit explicitly. Fixture/fake-Provider tests remain separate.

The executable deferred item is H1-5 in [phase2-tasks.md](../agents/phase2-tasks.md); this runbook is not another task tracker. Future vLLM parser/launch configuration must match the chosen model family and installed version, not an untested generic command copied into H1 acceptance.
