# Data Retention

Phase 1.3E retention removes complete Experiment aggregates through an explicit
Python Control Plane maintenance command. It never prunes individual events,
Runs, analyses, Runner records, or Policies.

## Safety contract

- Export DATABASE_URL in the environment; never put it in command arguments.
- Supply an absolute timezone-aware UTC cutoff. Future and timezone-naive
  values are rejected.
- Run retention-plan first. It is read-only and bounded to 100 units by
  default.
- Execute requires the exact token DELETE_ELIGIBLE_EXPERIMENTS and the exact
  plan file produced by the prior dry-run.
- Every Run must be terminal and have completed_at at or before the cutoff.
- Candidate, replaying, validated, and active Policies block deletion.
- References from another Experiment block deletion.

The JSON report contains only status, mode, cutoff, unit count, Experiment IDs,
aggregate table counts, and blocked reason counts. It never includes Experiment
names/tasks, event payloads, analysis evidence, or Provider content.

## Plan

    export RETENTION_TERMINAL_BEFORE="2026-01-01T00:00:00Z"
    make retention-plan

Review every returned ID and row count. Reason counts may overlap because one
Experiment can violate more than one guard.

The default output file is retention-plan.json in the current directory.

## Execute

    export RETENTION_CONFIRM="DELETE_ELIGIBLE_EXPERIMENTS"
    make retention-execute
    unset RETENTION_CONFIRM

Execution must not trust an earlier plan from memory. It reads the reviewed
plan file, starts a new transaction, locks the candidate Experiments,
re-evaluates every guard after locking, records the pre-delete row counts in
the response, and removes each complete aggregate using the defined
foreign-key cascade. If the locked candidate set or row counts no longer match
the plan digest, the transaction aborts with RETENTION_PLAN_STALE.

Use RETENTION_LIMIT to reduce a batch below the default 100. The hard maximum
is 500. There is no automatic schedule in Phase 1.3E.

## Failure handling

- A missing DATABASE_URL, cutoff, plan file, or confirmation token stops before deletion.
- A future/naive cutoff or out-of-range limit is rejected.
- A zero-unit result is successful and deletes nothing; inspect the reason
  counts before changing the cutoff.
- Database or constraint failure rolls back the transaction. Do not bypass a
  cross-Experiment dependency guard by manually deleting rows.
- PostgreSQL lock or statement timeouts fail closed with fixed, redacted error
  codes. Retry during a maintenance window with a smaller batch.
