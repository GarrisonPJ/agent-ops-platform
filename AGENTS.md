# Project Guide

## Agent skills

### Issue tracker

Issues and PRDs live as markdown files in `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout with `CONTEXT.md` at root. See `docs/agents/domain.md`.

### State management convention

- **Server cache** → RTK Query (`services/api.ts`)
- **Cross-route UI state** → Redux slice (`store/`)
- **Page-local ephemeral state** → `useState` / `useRef`

New features follow this rule. If a `useState` value is needed by a component outside the current page, promote it to a Redux slice.
