# Legacy Kubernetes Manifests

Status: unsupported historical configuration

The manifests in this directory target the pre-Phase 1 API, executor model, environment variables, and deployment topology. They are not compatible with the current FastAPI control plane and Rust Runner architecture and must not be treated as a production deployment guide.

The only supported Phase 1 runtime is the focused Compose stack. Run these commands from the repository root:

```bash
cp .env.example .env
make demo
```

That stack runs PostgreSQL, FastAPI, the Rust Runner, and React using [`infra/docker/docker-compose.phase1.yml`](../docker/docker-compose.phase1.yml).

Kubernetes execution remains deliberately deferred in [ROADMAP.md](../../ROADMAP.md). Before these manifests can return to supported status, the project must define and verify:

- Runner recovery and multi-Runner lease behavior;
- image publication and immutable versioning;
- secret management and transport security;
- liveness/readiness semantics for API, database, and Runner;
- persistent database backup and restore;
- network policy, authentication, authorization, audit, and resource limits;
- a Kubernetes real-stack E2E matching the Compose Golden loop.

Until those requirements are implemented, do not run `kubectl apply` against this directory. The files are retained only as historical input for a future deployment design.
