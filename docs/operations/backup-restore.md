# PostgreSQL Backup and Restore Rehearsal

This runbook covers the Phase 1.3D PostgreSQL custom-format backup and
disposable restore rehearsal. It is an operator procedure, not an API request
path. The source database must remain available to the application while the
commands run.

## Prerequisites

- PostgreSQL 16 server access and PostgreSQL 16 pg_dump/pg_restore clients.
- The Python environment from backend/ installed with the locked dependencies.
- DATABASE_URL exported in the environment. Do not put it in a command
  argument, a Make variable, a script argument, or a log message.
- The source role can read the application database. A rehearsal also needs
  permission to create and drop databases on the PostgreSQL server.
- The source schema is already at the application Alembic Head and contains at
  least one persisted Run with ordered RunEvents.

Confirm the secret is present without printing it:

    test -n "$DATABASE_URL"

The CI job uses a separate PostgreSQL 16 service with source database
agentops_recovery_source and target name agentops_restore_ci.

## Create a backup

Choose a new output path. The backup command refuses to overwrite an existing
file.

    umask 077
    make db-backup BACKUP_OUTPUT="$PWD/private/agentops-$(date +%Y%m%d-%H%M%S).dump"

The target directory must already be suitable for sensitive database files.
The command atomically reserves a new output at mode 0600, writes a PostgreSQL
custom-format dump with owner and privilege restoration disabled, and verifies
that it is non-empty.
Store the resulting file according to the organization's retention and
encryption policy. Phase 1.3D does not implement retention or redaction.

The same operation can be invoked directly when Make is unavailable:

    PYTHONPATH=backend uv run --project backend python -m app.database_recovery \
      backup --output "$PWD/private/agentops-manual.dump"

## Rehearse a restore

Use a fresh, lowercase disposable name for every rehearsal. The tool refuses
the source database, any existing database, and names outside its
agentops_restore_... safety pattern.

    export RESTORE_DATABASE_NAME="agentops_restore_$(date +%Y%m%d%H%M%S)"
    make db-restore-rehearsal
    unset RESTORE_DATABASE_NAME

The rehearsal script inserts one temporary Experiment, Run, RunnerJob, and two
RunEvents into the source. It then:

1. Captures a read-only repeatable-read snapshot and the source manifest.
2. Runs pg_dump against that exported snapshot.
3. Creates the target only after confirming that it does not exist.
4. Runs pg_restore --exit-on-error into the new target.
5. Verifies the schema revision, every public table row count, the foreign-key
   count and validation state, and the selected Run trace.
6. Removes the target database because this invocation created it.
7. Removes only the temporary source fixture.

The source manifest and dump use the same exported snapshot. The selected Run
trace must have contiguous sequences beginning at one; event type and payload
are compared as well as the row count. A successful command prints a JSON
summary including the schema revision, table counts, foreign-key count, Run
ID, and event count.

## Safety rules

- Never restore over a production or application database. Only use a new
  target matching agentops_restore_[a-z0-9_]+ and different from the source.
- An existing target is never dropped or modified by the recovery tool. Choose
  another name instead.
- Credentials are parsed from DATABASE_URL and passed to libpq through the
  environment. They are not included in pg_dump, pg_restore, or Python
  subprocess arguments.
- Keep the backup file and command output access-controlled. Do not paste
  DATABASE_URL, dump contents, or secret-bearing error output into tickets.
- Run the rehearsal with a role that can create/drop only the disposable
  databases required by the procedure where possible. PostgreSQL database
  creation is server-wide, so review this permission before production use.
- The cleanup step drops only a target that this process successfully created.
  It uses PostgreSQL WITH (FORCE) so idle connections do not leave the
  rehearsal database behind.

## Failure handling

Stop and correct the condition before retrying:

- DATABASE_URL missing or non-PostgreSQL: export a valid PostgreSQL URL
  without placing it in argv.
- Source schema is not at Head: run the reviewed Alembic migration procedure,
  then restart the rehearsal. Do not bypass the revision check.
- No Run trace, non-contiguous events, missing foreign keys, or unvalidated
  foreign keys: treat this as source-data or schema drift and escalate it
  before restoring.
- Backup output already exists: select a new path; do not enable overwrite.
- Target already exists or violates the disposable pattern: select a fresh
  agentops_restore_... name. The tool will not modify an existing target.
- pg_dump or pg_restore exits non-zero: retain the command status and
  server-side diagnostics, inspect connectivity, permissions, disk space, and
  client/server compatibility, then retry with a new target name.
- Manifest comparison fails: treat the restored database as untrusted. The
  tool removes a target it created in the normal failure path; confirm cleanup
  before retrying.

If the process is interrupted after creating a target, do not reuse it for a
new rehearsal. Stop and ask the database owner to connect through the reviewed
administrative workflow, verify the exact server and database name, confirm the
disposable prefix, and then remove that target. Do not run a generic dropdb
example here: dropdb does not consume DATABASE_URL, so an incomplete command
can connect to the wrong PostgreSQL instance. Never compose a drop command from
untrusted input or remove a database outside the disposable prefix.

## Evidence and escalation

Record the UTC time, source database identifier (not its password), application
revision, backup path or object reference, target name, command exit status,
and the JSON verification summary. Do not record raw connection URLs or dump
contents. A failed rehearsal blocks declaring the backup path recoverable;
escalate repeated failures to the database owner with the PostgreSQL server
logs and the sanitized command status.

The isolated CI job is the repeatable baseline: it starts PostgreSQL 16,
migrates agentops_recovery_source to Head, runs the seeded backup/restore
rehearsal, then runs PostgreSQL retention integration tests, and expects the
disposable target agentops_restore_ci to be removed on completion.
