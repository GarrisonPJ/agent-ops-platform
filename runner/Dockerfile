FROM rust:1.96-slim AS rust-builder

WORKDIR /src
COPY runner/ ./runner/
WORKDIR /src/runner
RUN cargo build --release -p agentops-cli

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENTOPS_SERVER_URL=http://api:8000 \
    AGENTOPS_AGENT_PROGRAM=python \
    AGENTOPS_AGENT_ARGS='["-m","app.demo_agent"]' \
    AGENTOPS_AGENT_WORKDIR=/workspace/backend

WORKDIR /workspace/backend
COPY backend/app/ ./app/
COPY --from=rust-builder /src/runner/target/release/agentops /usr/local/bin/agentops

RUN pip install --no-cache-dir "pydantic>=2.10,<3" \
    && useradd --create-home --uid 10001 runner \
    && chown -R runner:runner /workspace
USER runner

CMD ["agentops", "runner", "start"]
