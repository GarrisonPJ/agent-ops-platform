# ADR-0004: Experiment aggregate retention in the Python Control Plane

- Status: Accepted
- Date: 2026-08-01

## Context

RunEvents, analyses, Runner records, Runs, and Policies are one evidence graph.
Deleting individual rows would make a Trace, Replay, or Policy decision appear
valid while removing the facts that justify it. The roadmap also requires an
operator retention command, while the existing domain wording named FastAPI as
the only PostgreSQL writer.

## Decision

The Control Plane includes the Python API and explicit operator maintenance
commands; Runner and Agent processes still never access PostgreSQL. Retention
must use one complete Experiment as its atomic Retention Unit. An operator must
supply an absolute UTC terminal cutoff. Planning must be read-only and bounded;
execution must require the exact confirmation token, lock candidate Experiments,
re-evaluate the same guards after locking, and delete only aggregates whose Runs
are all terminal and old enough.

Candidate, replaying, validated, and active Policies protect an aggregate.
References from another Experiment also protect it, even when the database
foreign key would otherwise cascade or set the reference to null. Rejected and
superseded Policies may be deleted with their aggregate. Phase 1.3E has no
automatic schedule, legal-hold workflow, per-tenant period, or partial pruning.

## Consequences

- Retention cannot preserve a Policy while silently removing its evidence.
- A dry-run is safe to record because it exposes only status, mode, cutoff, unit
  count, IDs, row counts, and aggregate reason counts, not tasks, names, event
  payloads, analyses, or Provider content.
- Large cleanup proceeds in bounded batches and can be stopped between units.
- Adding legal holds or partial archival requires a new lifecycle decision.
