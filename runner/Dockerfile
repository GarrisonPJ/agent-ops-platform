FROM rust:1.96-slim AS rust-builder

WORKDIR /src
COPY runner/Cargo.toml runner/Cargo.lock ./runner/
COPY runner/crates/ ./runner/crates/
WORKDIR /src/runner
RUN cargo build --release --locked -p agentops-cli

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=15 \
    PIP_RETRIES=2 \
    AGENTOPS_SERVER_URL=http://api:8000 \
    AGENTOPS_ENVIRONMENT_PROGRAM=python \
    AGENTOPS_ENVIRONMENT_ARGS='["-m","app.env_server"]' \
    AGENTOPS_ENVIRONMENT_WORKDIR=/workspace/backend \
    AGENTOPS_AGENT_WORKDIR=/workspace/backend \
    AGENTOPS_AGENT_REGISTRY=/etc/agentops/agents.toml \
    AGENTOPS_ENV_TOKEN_HANDOFF_DIR=/run/agentops/token-handoff

WORKDIR /workspace/backend
COPY --from=rust-builder /src/runner/target/release/agentops /usr/local/bin/agentops
COPY runner-requirements.lock /tmp/runner-requirements.lock

RUN pip install --no-cache-dir --only-binary=:all: --requirement /tmp/runner-requirements.lock \
    && useradd --create-home --uid 10001 runner \
    && install --directory --owner=runner --group=runner --mode=0700 /run/agentops/token-handoff \
    && install --directory --owner=runner --group=runner --mode=0755 /etc/agentops \
    && chown -R runner:runner /workspace
COPY backend/app/ ./app/
COPY config/agents.toml config/agents.recovery.toml /etc/agentops/
USER runner

CMD ["agentops", "runner", "start"]
