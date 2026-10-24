# ADR-0007: Environment / Agent-under-test split

- Status: Accepted
- Date: 2026-10-17
- Implementation: Phase 2 H1. Not in code at `8bd2ef0`. Invariants 23–25 in
  `CONTEXT.md` become binding when H1 ships.
- Amended: 2026-10-24 by [ADR-0012](0012-h1-canonical-cutover-and-control-plane-success.md)
  (breaking canonical cutover and Control Plane success contract).
- Amends: [ADR-0003](0003-openai-compatible-provider-boundary.md),
  [ADR-0006](0006-scenario-registry-boundary.md)

## Context

Phase 1 registers three Scenarios. Each `ScenarioHandler` both simulates the
tool world and runs the agent loop (`run_fixture` and `run_provider` in
`backend/app/scenario_registry.py`). The Rust Runner's default child is
`python -m app.demo_agent`. An operator can override
`AGENTOPS_AGENT_PROGRAM`, but the API still accepts only a registered
`scenario_id`, so a Claude Code process, an Agents SDK app, or another local
loop cannot be the thing under test.

The 2026-10-17 review kept the harness (runner, environments, failure
taxonomy) and dropped the "general eval platform" framing. That only works if
the world and the decision loop are different objects.

## Decision

In prose, a Scenario is an **Environment**: task, tools, deterministic state,
seed, fault schedule, limits, and terminal checks. Protocol v1 keeps the
field name `scenario_id` and the `<name>.v<N>` rule from ADR-0006. Runs that
share an id stay score-comparable. Semantic changes still require a new
version.

An Environment implements:

- `reset(seed)` — rebuild state; same seed, same observations.
- `tool_definitions()` — the only tools the agent may see.
- `call(name, args) -> observation` — deterministic; no arbitrary code, no
  extra network.
- `check()` — trajectory assertions (ADR-0006 vocabulary) plus outcome
  assertions on final environment state.
- `fault_schedule` — injected faults declared with the Environment, not by
  the agent.

The **Agent-under-test** is a separate registered process. It receives the
task prompt and the Environment endpoint. It does not persist Runs, score
itself, or emit trusted `step_completed` events.

Three kinds:

| Kind | Id (initial) | Role |
|---|---|---|
| `fixture` | `fixture-agent.v1` | Scripted trajectory. CI and Golden path. Successor of `run_fixture`. |
| `reference` | `reference-openai-compatible.v1` | OpenAI-compatible tool loop. Successor of `run_provider`. Default local model is Ollama `qwen3:8b`. |
| `external` | registered per adapter | Claude Code, Agents SDK, Codex, or the operator's own loop. |

`execution_mode` stays `fixture` or `provider`; these modes retain their
roles, not old-v1 execution compatibility. ADR-0012 approves a one-time H1
cutover to the three canonical v2 identities and retires their v1 registrations
and coupled loops. `order-support.v1` is unchanged. Omitting `agent_id` selects
`fixture-agent.v1` in fixture mode and `reference-openai-compatible.v1` in
provider mode. An explicit registered
`agent_id` overrides that default. There is no third execution mode.

Agent-internal facts the Environment did not observe (context window, hidden
reasoning) are unknown dimensions. The analyzer must not infer them
(`failure_analyzer.py` already has this distinction).

`PolicyPatch` stays `instruction_patch`, `tool_priority`, and `max_steps`.
Replay still re-executes; it does not play back recorded tool outputs.

## Consequences

- H1 is the only structural change in Phase 2. Later stages add trials,
  export, and adapters on top of this split.
- Phase 1 `ScenarioHandler` remains the code path until H1 replaces it. The
  contributor guide states that explicitly.
- Canonical defaults (including omitted IDs) follow ADR-0012; no aliases,
  historical replay preservation, or data cleanup are included.
- Golden E2E keeps using the fixture agent. Live models stay out of CI
  (ADR-0003).
- A custom agent that cannot speak MCP still cannot be plugged in until it
  can call the Environment. That constraint is ADR-0008.
