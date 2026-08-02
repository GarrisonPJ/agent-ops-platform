.PHONY: demo down logs contracts check-contracts test test-backend test-frontend test-rust db-backup db-restore-rehearsal retention-plan retention-execute

COMPOSE := docker compose -f infra/docker/docker-compose.phase1.yml

demo:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api runner web

contracts:
	PYTHONPATH=backend uv run --project backend python backend/scripts/export_protocol_schema.py

check-contracts: contracts
	git diff --exit-code -- contracts/v1/*.schema.json

test: check-contracts test-backend test-frontend test-rust

test-backend:
	cd backend && uv run pytest phase1_tests/ -q --tb=short

test-frontend:
	cd frontend && npm run typecheck && npm test

test-rust:
	cd runner && cargo test --workspace --locked

db-backup:
	@test -n "$$DATABASE_URL"
	@test -n "$(BACKUP_OUTPUT)"
	PYTHONPATH=backend uv run --project backend python -m app.database_recovery backup --output "$(BACKUP_OUTPUT)"

db-restore-rehearsal:
	@test -n "$$DATABASE_URL"
	@test -n "$$RESTORE_DATABASE_NAME"
	PYTHONPATH=backend uv run --project backend python scripts/backup_restore_e2e.py

RETENTION_LIMIT ?= 100

retention-plan:
	@test -n "$$DATABASE_URL"
	@test -n "$$RETENTION_TERMINAL_BEFORE"
	PYTHONPATH=backend uv run --project backend python -m app.data_retention plan --terminal-before "$$RETENTION_TERMINAL_BEFORE" --limit "$(RETENTION_LIMIT)"

retention-execute:
	@test -n "$$DATABASE_URL"
	@test -n "$$RETENTION_TERMINAL_BEFORE"
	@test -n "$$RETENTION_CONFIRM"
	@test "$$RETENTION_CONFIRM" = "DELETE_ELIGIBLE_EXPERIMENTS"
	PYTHONPATH=backend uv run --project backend python -m app.data_retention execute --terminal-before "$$RETENTION_TERMINAL_BEFORE" --limit "$(RETENTION_LIMIT)" --confirm "$$RETENTION_CONFIRM"
