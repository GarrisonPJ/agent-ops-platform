.PHONY: demo down logs contracts check-contracts fmt fmt-check test test-backend test-frontend test-rust db-backup db-restore-rehearsal retention-plan retention-execute health-probe

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

fmt:
	cd runner && cargo fmt --all
	cd frontend && npm run format

fmt-check:
	cd runner && cargo fmt --all -- --check
	cd frontend && npm run format:check

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
RETENTION_PLAN_FILE ?= retention-plan.json

retention-plan:
	@test -n "$$DATABASE_URL"
	@test -n "$$RETENTION_TERMINAL_BEFORE"
	PYTHONPATH=backend uv run --project backend python -m app.data_retention plan --terminal-before "$$RETENTION_TERMINAL_BEFORE" --limit "$(RETENTION_LIMIT)" > "$(RETENTION_PLAN_FILE)"

retention-execute:
	@test -n "$$DATABASE_URL"
	@test -n "$(RETENTION_PLAN_FILE)"
	@test -f "$(RETENTION_PLAN_FILE)"
	@test -n "$$RETENTION_CONFIRM"
	@test "$$RETENTION_CONFIRM" = "DELETE_ELIGIBLE_EXPERIMENTS"
	PYTHONPATH=backend uv run --project backend python -m app.data_retention execute --plan-file "$(RETENTION_PLAN_FILE)" --confirm "$$RETENTION_CONFIRM"

API_URL ?= http://127.0.0.1:8000

health-probe:
	python scripts/health_probe.py --api-url "$(API_URL)" --include-state
