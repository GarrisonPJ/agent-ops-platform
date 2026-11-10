# Development standards (Phase 2)

These rules bind Phase 2 — Harness work. Current invariants in `CONTEXT.md`
bind, including the implemented H1 boundaries. If a task needs to break one, stop and write an ADR first.

## Where truth lives

| Question | Read |
|---|---|
| What does this word mean? | `CONTEXT.md`. In prose say Environment; in protocol fields say `scenario_id`. |
| Why is the boundary this shape? | `docs/adr/`. Accepted is a decision status, not implementation proof: H1–H4 are implemented; check `phase2-tasks.md` for status. |
| What is in or out of this milestone? | `ROADMAP.md`, then the task in `docs/agents/phase2-tasks.md`. |
| How does a run actually move? | `docs/learn/00-map.md` (current code), then the ADR for the stage you are in. |
| How do I add an Environment today? | `docs/agents/scenario-development.md` (current Environment interface and assertions). |

`.scratch/` is git-ignored local evidence. Do not cite it from tracked docs.
Do not invent a second tracker.

## One task, one change

- Implement only the task id you were given (`H1-3`, not "H1").
- A change that is not in `phase2-tasks.md` needs an ADR (or an amendment) and
  a roadmap line before code.
- Do not add trace-UI features, annotation queues, dataset management,
  LLM-as-judge, or new calibration binding / hashing / strict-mode behavior.
  Those are non-goals in `ROADMAP.md`.

## Definition of done

1. Acceptance bullets on the task are true, including tests named there.
2. `make fmt` after Rust or frontend edits. `make contracts` if a Pydantic
   protocol model changed.
3. CI stays deterministic: fixture agent and local fake providers only. No
   GPU, Ollama, Claude, or network key in CI.
4. The stage's learning chapter exists (see the task). Chapters are Chinese,
   with English identifiers. Code, tests, and ADRs stay English.
5. Module docstring on every new Python module, four short parts:
   - what this module is;
   - where it sits in one Run (who calls it, who it calls);
   - the invariant it must not break;
   - the next file to read.

## Frontend state

Server cache → RTK Query. Cross-route UI state → a Redux slice. Page-local
state → `useState` / `useRef`. The Trace UI is frozen; H3 and H1 touched
the Experiment pages only, as their tasks said.

## Vocabulary

Use the glossary in `CONTEXT.md`. Do not introduce a synonym for Run, Trial
Group, Attempt, Lease, Policy, or Agent-under-test.

## Comments

Comment the invariant or the non-obvious ordering, not the restatement of the
next line. New Rust supervision code should say which ADR-0002 rule the test
is protecting.
