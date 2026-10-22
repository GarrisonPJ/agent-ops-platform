# Project Guide

## Agent skills

### Issue tracker

Issues and PRDs live as markdown files in `.scratch/`. See `docs/agents/issue-tracker.md`. `.scratch/` is git-ignored (local only); tracked docs must not cite it as evidence.

### Triage labels

Five canonical roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout with `CONTEXT.md` at root. See `docs/agents/domain.md`. ADR-0007..0011 define Phase 2 — Harness; in prose a Scenario is called an Environment while protocol v1 keeps `scenario_id`.

### Development standards

Phase 2 work follows [docs/agents/development-standards.md](docs/agents/development-standards.md): module docstring four-part header, learn-chapter definition of done, one issue per task, ADR before unplanned work, CI determinism.

### Learning path

[docs/learn/README.md](docs/learn/README.md) is the reading order for understanding the system; each Harness stage's definition of done includes its learn chapter.

### State management convention

- **Server cache** → RTK Query (`services/api.ts`)
- **Cross-route UI state** → Redux slice (`store/`)
- **Page-local ephemeral state** → `useState` / `useRef`

New features follow this rule. If a `useState` value is needed by a component outside the current page, promote it to a Redux slice.

### Formatting

After editing Rust or frontend source, run `make fmt` from the repo root before committing. Cursor rule: `.cursor/rules/auto-format.mdc`. CI checks `cargo fmt --check` in the rust job; frontend uses Prettier via `npm run format:check`.
