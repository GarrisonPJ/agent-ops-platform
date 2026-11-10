# Write-up: what was built, what changed, what was cut

This note records what AgentOps Workbench is, why the project changed direction in October 2026, what was built, and where each number comes from. It does not claim more than the linked records show.

## What it is

AgentOps Workbench is a self-hosted agent reliability harness. It runs a registered agent-under-test against a deterministic, resettable simulated tool environment. It then reports pass^k, classifies failures with fixed rules, and checks a baseline policy against a candidate policy ([ROADMAP](../ROADMAP.md), Direction). Traces can be exported with OTel GenAI when export is switched on ([ADR-0011](adr/0011-otel-genai-export.md)).

The target user is a person or small team that cannot send evaluation data to a cloud service. They need comparable numbers across models, prompts or agent scaffolds on the same environment ([ROADMAP](../ROADMAP.md), Direction).

The stack is a Python control plane (FastAPI and PostgreSQL) and a Rust Runner that uses leases and fencing ([ROADMAP](../ROADMAP.md), Phase 2 diagram). The vocabulary and the invariants are in [CONTEXT.md](../CONTEXT.md). In prose a Scenario is called an Environment. Protocol v1 still uses the field name `scenario_id` ([ADR-0007](adr/0007-environment-agent-split.md)).

## Why it changed direction (2026-10)

Phase 1 built a closed loop. A failed Run became a classified, comparison-ready regression asset, and that asset gated policy activation ([ROADMAP](../ROADMAP.md), Milestones). The weakness was that the environment and the agent lived in one class. Only the built-in Python agent could be evaluated ([ROADMAP](../ROADMAP.md), Direction).

By 2026-10-17 the roadmap judged the general features to be commodity. It lists trace UI, dataset versioning, trajectory assertions, replay diffs and failed-run-to-regression workflows. It names Langfuse, Phoenix and Braintrust as products that already offer them. It also says the industry is consolidating rather than expanding generic eval platforms ([ROADMAP](../ROADMAP.md), Direction). These are the roadmap's own judgments. This note does not add independent market data.

What the roadmap calls scarce for self-hosted users is narrower. It is a reproducible harness that runs external agents against resettable environments, reports pass^k, exports OTel GenAI spans, and keeps a deterministic failure taxonomy with a calibration story ([ROADMAP](../ROADMAP.md), Direction). The project therefore stopped presenting itself as a general evaluation platform. Trace UI features, annotation queues, dataset management, multi-tenancy and LLM-as-judge are listed as non-goals ([ROADMAP](../ROADMAP.md), Non-goals).

The product name stays AgentOps Workbench. A public rename is an open human decision ([phase2-tasks](agents/phase2-tasks.md), H0-2).

## What was built (by stage)

**H1: Environment/Agent split.** An Environment is now an MCP server, `app.env_server`, that captures world facts itself ([ADR-0008](adr/0008-environment-mcp-server-and-env-side-capture.md)). The Runner starts it and then the agent. The operator registers agents in a TOML file. The API sends only an `agent_id`, and the Runner runs only registered commands ([ADR-0009](adr/0009-agent-registry-trust-boundary.md)). The built-in environments were cut over once to canonical v2 versions, and a stateful `order-support.v1` was added ([ADR-0012](adr/0012-h1-canonical-cutover-and-control-plane-success.md), [ROADMAP](../ROADMAP.md), H1). Success is decided only by the control plane from trusted evidence ([ADR-0012](adr/0012-h1-canonical-cutover-and-control-plane-success.md)). H1 local engineering and the real-model integration run are accepted. Hosted CI passed on `b32da25`. Release, cold-cache and cross-platform claims are outside that boundary ([system-acceptance](operations/system-acceptance.md)).

**H2: external agents.** The required adapter is pi, run with the same local model as the reference agent, so the comparison isolates the scaffold. A plug-in guide covers the contract an external agent must meet ([learn/03](learn/03-plug-in-your-agent.md), [ROADMAP](../ROADMAP.md), H2). The reference agent and pi were each run once on four Environments ([H2 record](history/h2-pi-live-acceptance-2026-11-03.md)). One finding matters for users. pi validates tool arguments itself, so invalid calls never reach the Environment. Environment-side capture cannot see them ([H2 record](history/h2-pi-live-acceptance-2026-11-03.md), "What the comparison shows").

**H3: trial groups and pass^k.** A trial group is `n` Runs of the same task with a fixed Environment seed. pass^k is computed on read with `C(c,k) / C(n,k)` and is published only when every member is terminal and none is cancelled ([learn/06](learn/06-pass-k.md), sections 2 to 4). Candidate groups pass validation only with a positive mean score delta and a strictly higher pass^k ([learn/06](learn/06-pass-k.md), section 5). The Experiment page shows the summary ([phase2-tasks](agents/phase2-tasks.md), H3-3).

**H4: OTel GenAI export.** A terminal Run becomes one trace. The root span is `invoke_agent`, and each `step_completed` event becomes a child `execute_tool` span. Export is off by default, and the stored events remain the source of truth ([learn/07](learn/07-otel-mapping.md), sections 2 and 3). The mapping is pinned to semantic-conventions-genai commit `633cbb0`. Every attribute used there is at stability Development ([learn/07](learn/07-otel-mapping.md), section 1). Tests use a local fake collector ([learn/07](learn/07-otel-mapping.md), section 4). H4 is complete: a local Phoenix received one Run's trace, with the root `invoke_agent OpenAI-compatible reference agent` span and 8 `execute_tool` children. Local Phoenix needed `PHOENIX_ENABLE_AUTH=false` ([learn/07](learn/07-otel-mapping.md), section 5; screenshot [otel-phoenix.png](screenshots/otel-phoenix.png)).

## Results so far

All live runs used one model: the already installed Windows-native llama.cpp build `b12018-8956c769e`, alias `swift-1.5-27b-iq3xxs`, context 8192, on a GPU with about 12 GB of VRAM. Unless noted, settings were `temperature=0`, `max_tokens=256`, `reasoning_effort=none` ([H1 record](history/h1-live-model-acceptance-2026-11-01.md), [H2 record](history/h2-pi-live-acceptance-2026-11-03.md)).

**Single Runs, reference agent and pi (one Run per cell, temperature 0).** Source: [H2 record](history/h2-pi-live-acceptance-2026-11-03.md), "Outcomes".

| Environment | reference | pi |
|---|---|---|
| research | succeeded, 0.96202 | succeeded, 1.0 |
| checkout | failed, 0.39042 | timed_out, 0.86667 |
| fault | failed, 0.42272 | succeeded, 1.0 |
| order | succeeded, 0.56859 | succeeded, 0.66667 |

The reference column matches the earlier H1-5 record ([H1 record](history/h1-live-model-acceptance-2026-11-01.md), "Actual real-model outcomes"). The H2 record says quality scores are not comparable across scaffolds. pi's timed-out checkout scores higher than the reference's failed one because pi's failed attempts and token use are invisible. Compare status and the success gate instead ([H2 record](history/h2-pi-live-acceptance-2026-11-03.md)).

**Trial groups, n = 5, k = 5, seed 42, temperature 0.** Source: [H3 record](history/h3-pass-k-live-2026-11-04.md), "Outcomes".

| Agent | Environment | successes | pass^1 | pass^5 | distinct trajectories |
|---|---|---|---|---|---|
| reference | research v2 | 5/5 | 1.0 | 1.0 | 1 |
| reference | fault v2 | 0/5 | 0.0 | 0.0 | 1 |
| pi | research v2 | 5/5 | 1.0 | 1.0 | 1 |
| pi | fault v2 | 5/5 | 1.0 | 1.0 | 1 |

At temperature 0 every group repeated one trajectory, so pass^5 equals pass^1. The five trials are copies, not independent samples. These numbers overstate reliability ([H3 record](history/h3-pass-k-live-2026-11-04.md), "What this shows"; [learn/06](learn/06-pass-k.md), section 6).

**Temperature 1.0 trial groups.** The temperature-1.0 comparison ran on 2026-11-05, after the temperature-0 groups ([H3 record](history/h3-pass-k-live-2026-11-04.md), "Exercised boundary").

Temperature 1 (same setup, four Environments, n = 5, k = 5, seed 42; [record](history/h3-pass-k-temp1-2026-11-05.md)):

| Agent | Environment | successes | pass^1 | pass^5 | distinct trajectories |
|---|---|---|---|---|---|
| reference | research | 5/5 | 1.0 | 1.0 | 1 |
| reference | fault | 0/5 | 0.0 | 0.0 | 1 |
| reference | checkout | 0/5 | 0.0 | 0.0 | 1 |
| reference | order | 5/5 | 1.0 | 1.0 | 1 |
| pi | research | 5/5 | 1.0 | 1.0 | 1 |
| pi | fault | 2/5 | 0.4 | 0.0 | 2 |
| pi | checkout | 0/5 (all `timed_out`) | 0.0 | 0.0 | 1 |
| pi | order | 3/5 | 0.6 | 0.0 | 2 |

The installed llama.cpp service pins a default sampler seed (42). A direct check sent the same chat request twice at temperature 1 without a `seed` field and got identical output; with `"seed":1` versus `"seed":2` the outputs differed. So the reference agent's trials were still copies. pi's groups diverged on fault and order, likely because of per-Run differences in pi's prompt (for example its private temporary paths). That is not verified and is not independent sampling. Independent trials need a varying sampler seed, which is what the next group of runs adds.

**Per-Run sampler seed trial groups.** With `AGENTOPS_PROVIDER_SEED=per-run` (ADR-0003 H3 amendment, commit `f00ef7d`) each Run gets its own sampler seed. Same setup as above (build `b12018`, context 8192, `temperature=1`, `max_tokens=256`, `reasoning_effort=none`, `parallel_tool_calls=false`, n = 5, k = 5, Environment seed 42, fresh PostgreSQL, host control plane and Rust Runner), run on 2026-11-07 with `reference-openai-compatible.v1` ([record](history/h3-pass-k-seed-2026-11-07.md)):

| Agent | Environment | successes | pass^1 | pass^5 | distinct trajectories |
|---|---|---|---|---|---|
| reference | research | 5/5 | 1.0 | 1.0 | 1 |
| reference | fault | 2/5 | 0.4 | 0.0 | 3 |
| reference | checkout | 0/5 | 0.0 | 0.0 | 1 |
| reference | order | 5/5 | 1.0 | 1.0 | 1 |

pi, run with the same settings on 2026-11-05 (the batch was interrupted after two groups): research 5/5, pass^1 1.0, pass^5 1.0, 1 trajectory; fault 1/5, pass^1 0.2, pass^5 0.0, 2 trajectories. On 2026-11-08 pi was run with `AGENTOPS_PROVIDER_SEED=per-run` ([record](history/h3-pi-per-run-seed-2026-11-08.md)): checkout 0/5 and order 5/5, with only small trajectory divergence. One checkout trial ended `internal_failure`; whether the Runner bug described under Limitations caused it was not re-run, so it is unknown.

With a per-Run seed the reference fault trials diverged into 3 trajectories; before, at temperature 0 and at temperature 1 with the pinned seed, the group was 0/5 with 1 trajectory. Research, checkout and order still produced one trajectory per group under independent sampling, so on those tasks the model's choices are stable rather than copies caused by the seed. Fault shows pass^1 0.4 against pass^5 0.0: occasional success is not reliable success. This is still one model, one Environment seed and n = 5, not a pass rate.

## What was cut or deferred

Status is taken from [phase2-tasks](agents/phase2-tasks.md).

- **H2-2 (OpenAI Agents SDK), H2-3 (Codex CLI), H2-5 (Claude Code) adapters.** Optional, status `ready-for-agent`, not built. The cut line allows deferring them ([ROADMAP](../ROADMAP.md), Cut line). So pi is the only external adapter that was exercised.
- **H5 calibration study.** H5-2, the self-labeled agreement study on about 40 real runs (minimum 20), is `ready-for-human` because the labeling must be done by a person; its review status is `author_self_labeled` and the result stays `provisional_mechanics`. The H5 learn chapter waits on it ([phase2-tasks](agents/phase2-tasks.md), H5-2). The six-contract CI regression gate is unchanged. Calibration stays `provisional_mechanics` with `publishable_accuracy=false` ([ROADMAP](../ROADMAP.md), Current state). Any later study must state that the author labeled the runs.
- **H0-1 and H0-2.** The MIT LICENSE is in the repository. Setting the GitHub About text and topics, and choosing a public name, need a human ([phase2-tasks](agents/phase2-tasks.md), H0-1, H0-2). Neither blocks H1 ([ROADMAP](../ROADMAP.md), H0).
- **Non-goals and deferred.** Kubernetes, containerised agents, LLM-as-judge, multi-tenancy, training export and automatic policy activation stay out of scope ([ROADMAP](../ROADMAP.md), Non-goals and Deferred). A safety gate is required before side-effecting tools, untrusted users or shared operation ([ROADMAP](../ROADMAP.md), Conditional safety gate).

## Where the numbers come from

Each number in this note comes from one dated record or one tracked doc. The record states its own boundary.

- **Single-Run outcomes and scores:** [H1 record](history/h1-live-model-acceptance-2026-11-01.md) and [H2 record](history/h2-pi-live-acceptance-2026-11-03.md). The H1 record covers four Runs with the reference agent plus one standard-image research Run. The H2 record covers eight Runs, one per agent and Environment.
- **pass^1, pass^5, successes, trajectories:** [H3 record](history/h3-pass-k-live-2026-11-04.md). The estimator is defined in [learn/06](learn/06-pass-k.md), section 3, and follows tau-bench (arXiv:2406.12045).
- **Temperature 1.0 groups:** [H3 temperature-1 record](history/h3-pass-k-temp1-2026-11-05.md); the endpoint pins a default sampler seed, so identical requests still give identical trials.
- **Per-Run sampler seed groups:** [H3 per-Run seed record](history/h3-pass-k-seed-2026-11-07.md).
- **Test counts and CI status:** [system-acceptance](operations/system-acceptance.md) and the "Deterministic checks" sections of each record. They are parent-local runs, not independent review, unless the record says hosted CI.
- **OTel mapping and Phoenix check:** pinned to a spec commit in [learn/07](learn/07-otel-mapping.md), section 1; the Phoenix result is in section 5.
- **Task status:** [phase2-tasks](agents/phase2-tasks.md) is the only executable tracker. [ROADMAP](../ROADMAP.md) holds milestones.

Run IDs, trial group IDs, the model file hash and the build fingerprint are in the records. The raw capture directories are kept outside the repository ([H3 record](history/h3-pass-k-live-2026-11-04.md), "Limits").

## Limitations

- **Scope of the evidence.** All live evidence comes from one 27B IQ3_XXS quantized local model with an 8192-token context, at n = 5-scale samples. The conclusions are about mechanism, not general policy efficacy.
- **Seed divergence is thin.** Clear per-Run seed divergence was shown only on the reference agent's fault-injection scenario ([H3 per-Run seed record](history/h3-pass-k-seed-2026-11-07.md)); pi's per-Run seed run diverged little ([record](history/h3-pi-per-run-seed-2026-11-08.md)).
- **Calibration is single-author.** Labels come from the project author alone, with no inter-rater agreement; the status is `provisional_mechanics`, and `publishable` needs an independent second labeler ([evaluation-calibration](evaluation-calibration.md)).

- **No pass rate.** The records say the sampled Runs are not a pass-rate estimate ([H1 record](history/h1-live-model-acceptance-2026-11-01.md), "Acceptance conclusion and limits"; [H3 record](history/h3-pass-k-live-2026-11-04.md), "Limits").
- **One model, one machine.** Only a Windows-native llama.cpp service with one 27B GGUF was exercised. No Ollama or vLLM run is recorded. Ollama and vLLM remain the intended targets, not tested ones ([system-acceptance](operations/system-acceptance.md)).
- **Temperature 0 inflates pass^k.** See the results section. The per-Run seed table is still one model, one Environment seed and n = 5.
- **One pi version and no containerised pi.** pi 1.3.0 ran on a host Runner. The H2 record makes no claim about other models, pi versions or a containerised Runner ([H2 record](history/h2-pi-live-acceptance-2026-11-03.md), "Limits").
- **Analyzer accuracy is not established.** On order, the analyzer reports an execution failure rate of 0.667 for a succeeded Run because of injected error wording. The H1 record calls this an observed limitation, not calibrated accuracy ([H1 record](history/h1-live-model-acceptance-2026-11-01.md)).
- **Failures stay failures.** The checkout and fault failures were not repaired. No tool arguments were fixed and no world rules were changed to obtain success ([H1 record](history/h1-live-model-acceptance-2026-11-01.md)).
- **Delivery boundary.** Hosted CI covers the fixture and fake-provider gates only. Release, cold-cache, cross-platform and live-model claims are outside it ([system-acceptance](operations/system-acceptance.md)).
- **Single trusted host.** There is no public or multi-user security, no sandbox for a malicious agent, and no Windows-native Runner support ([system-acceptance](operations/system-acceptance.md)). The roadmap names a safety gate for those cases ([ROADMAP](../ROADMAP.md)).
- **A wire-contract bug survived until live runs.** The Rust Runner's `RunResponse` lacked `trial_group_id` and `trial_index`. Under `deny_unknown_fields` every completion ack failed to parse from H3 onward, and fixture-only tests did not catch it. It was fixed on 2026-11-08 and the Runner wire structs, enums and error-code allowlist now have cross-language contract tests, plus a zod-validated snapshot of real backend responses. The lesson: test the shape both sides actually exchange, not each side alone.
- **Known gaps.** `tool_priority` is set by candidate Policies but read by no Agent; the golden E2E and calibration capture do not cover order-support; the pi adapter relies on pi 1.3.0 JSONL event names with no runtime version check; order-support successful runs score 0.667 in analysis (see above). Listed in [phase2-tasks](agents/phase2-tasks.md).
- **Unstable spec.** The OTel GenAI attribute names are at stability Development and may change ([learn/07](learn/07-otel-mapping.md), section 1).
