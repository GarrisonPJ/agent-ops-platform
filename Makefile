.PHONY: demo down logs contracts check-contracts test test-backend test-frontend test-rust

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
