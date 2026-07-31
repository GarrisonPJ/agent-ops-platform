"""Persist expired Runner attempts and sanitize legacy Provider correlation."""

from collections.abc import Sequence
import hashlib
import re

from alembic import op
import sqlalchemy as sa


revision: str = "0006_runner_attempts"
down_revision: str | None = "0005_runner_presence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_PROVIDER_LATENCY_MS = 86_400_000
MAX_PROVIDER_REQUEST_COUNT = 1_000
MAX_PROVIDER_TOKENS = 1_000_000_000
MAX_PROVIDER_TOTAL_TOKENS = 2_000_000_000
PROVIDER_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_RUN_METRIC_FIELDS = frozenset(
    {
        "event_retries",
        "steps",
        "latency_ms",
        "token_prompt",
        "token_completion",
        "total_tokens",
        "provider",
        "provider_error",
        "score_breakdown",
    }
)


def _bounded_int(value: object, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return None


def _bounded_text(value: object, maximum: int, *, strip: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip() if strip else value
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _request_fingerprint(value: object) -> str | None:
    normalized = _bounded_text(value, 500, strip=True)
    if normalized is None:
        return None
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _existing_fingerprint(value: object) -> str | None:
    if isinstance(value, str) and PROVIDER_FINGERPRINT_RE.fullmatch(value):
        return value
    return None


def _provider_fingerprint(value: dict[str, object]) -> str | None:
    if "request_id" in value:
        return _request_fingerprint(value.get("request_id"))
    if "request_fingerprint" in value:
        return _existing_fingerprint(value.get("request_fingerprint"))
    return None


def _sanitize_provider_telemetry(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    model = _bounded_text(value.get("model"), 200, strip=True)
    latency_ms = _bounded_int(value.get("latency_ms"), MAX_PROVIDER_LATENCY_MS)
    request_count = _bounded_int(value.get("request_count"), MAX_PROVIDER_REQUEST_COUNT)
    token_prompt = _bounded_int(value.get("token_prompt"), MAX_PROVIDER_TOKENS)
    token_completion = _bounded_int(value.get("token_completion"), MAX_PROVIDER_TOKENS)
    if None in {model, latency_ms, request_count, token_prompt, token_completion}:
        return None
    fingerprint = _provider_fingerprint(value)
    if ("request_id" in value or "request_fingerprint" in value) and fingerprint is None:
        return None
    sanitized: dict[str, object] = {
        "model": model,
        "latency_ms": latency_ms,
        "request_count": request_count,
        "token_prompt": token_prompt,
        "token_completion": token_completion,
    }
    if fingerprint is not None:
        sanitized["request_fingerprint"] = fingerprint
    return sanitized


def _sanitize_provider_error(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    code = _bounded_text(value.get("code"), 100)
    message = _bounded_text(value.get("message"), 500)
    retryable = value.get("retryable")
    attempts = _bounded_int(value.get("attempts"), 100)
    if code is None or message is None or not isinstance(retryable, bool) or attempts is None:
        return None
    fingerprint = _provider_fingerprint(value)
    if ("request_id" in value or "request_fingerprint" in value) and fingerprint is None:
        return None
    sanitized: dict[str, object] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "attempts": attempts,
    }
    if fingerprint is not None:
        sanitized["request_fingerprint"] = fingerprint
    return sanitized


def _sanitize_provider_metrics(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    model = _bounded_text(value.get("model"), 200, strip=True)
    latency_ms = _bounded_int(value.get("latency_ms"), MAX_PROVIDER_LATENCY_MS)
    request_count = _bounded_int(value.get("request_count"), MAX_PROVIDER_REQUEST_COUNT)
    token_prompt = _bounded_int(value.get("token_prompt"), MAX_PROVIDER_TOKENS)
    token_completion = _bounded_int(value.get("token_completion"), MAX_PROVIDER_TOKENS)
    total_tokens = _bounded_int(value.get("total_tokens"), MAX_PROVIDER_TOTAL_TOKENS)
    if None in {
        model,
        latency_ms,
        request_count,
        token_prompt,
        token_completion,
        total_tokens,
    }:
        return None

    fingerprints: list[str] = []
    raw_request_ids = value.get("request_ids", [])
    if isinstance(raw_request_ids, list):
        for request_id in raw_request_ids:
            fingerprint = _request_fingerprint(request_id)
            if fingerprint is not None and fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
            if len(fingerprints) == 20:
                break
    existing_fingerprints = value.get("request_fingerprints", [])
    if isinstance(existing_fingerprints, list):
        for candidate in existing_fingerprints:
            fingerprint = _existing_fingerprint(candidate)
            if fingerprint is not None and fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
            if len(fingerprints) == 20:
                break

    sanitized: dict[str, object] = {
        "model": model,
        "latency_ms": latency_ms,
        "request_count": request_count,
        "token_prompt": token_prompt,
        "token_completion": token_completion,
        "total_tokens": total_tokens,
    }
    if fingerprints:
        sanitized["request_fingerprints"] = fingerprints
    return sanitized


def _sanitize_event_payload(value: object) -> object:
    if not isinstance(value, dict):
        return value
    sanitized = dict(value)
    if "provider" in sanitized:
        provider = _sanitize_provider_telemetry(sanitized.get("provider"))
        if provider is None:
            sanitized.pop("provider")
        else:
            sanitized["provider"] = provider
    if "provider_error" in sanitized:
        provider_error = _sanitize_provider_error(sanitized.get("provider_error"))
        if provider_error is None:
            sanitized.pop("provider_error")
        else:
            sanitized["provider_error"] = provider_error
    return sanitized


def _sanitize_run_metrics(value: object) -> object:
    if not isinstance(value, dict):
        return value
    sanitized = {key: value[key] for key in SAFE_RUN_METRIC_FIELDS if key in value}
    if "provider" in sanitized:
        provider = _sanitize_provider_metrics(sanitized.get("provider"))
        if provider is None:
            sanitized.pop("provider")
        else:
            sanitized["provider"] = provider
    if "provider_error" in sanitized:
        provider_error = _sanitize_provider_error(sanitized.get("provider_error"))
        if provider_error is None:
            sanitized.pop("provider_error")
        else:
            sanitized["provider_error"] = provider_error
    return sanitized


def _sanitize_legacy_provider_data() -> None:
    bind = op.get_bind()
    run_events = sa.table(
        "run_events",
        sa.column("id", sa.Integer()),
        sa.column("payload", sa.JSON()),
    )
    runs = sa.table(
        "runs",
        sa.column("id", sa.String(length=36)),
        sa.column("metrics", sa.JSON()),
    )

    for row in bind.execute(sa.select(run_events.c.id, run_events.c.payload)).mappings():
        sanitized = _sanitize_event_payload(row["payload"])
        if sanitized != row["payload"]:
            bind.execute(
                run_events.update()
                .where(run_events.c.id == row["id"])
                .values(payload=sanitized)
            )

    for row in bind.execute(sa.select(runs.c.id, runs.c.metrics)).mappings():
        sanitized = _sanitize_run_metrics(row["metrics"])
        if sanitized != row["metrics"]:
            bind.execute(
                runs.update().where(runs.c.id == row["id"]).values(metrics=sanitized)
            )


def upgrade() -> None:
    op.create_table(
        "runner_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.String(length=36), nullable=False),
        sa.Column("runner_id", sa.String(length=100), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovery_reason", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt", name="uq_runner_attempts_run_attempt"),
    )
    _sanitize_legacy_provider_data()


def downgrade() -> None:
    # Redaction is intentionally irreversible; downgrades retain sanitized JSON.
    op.drop_table("runner_attempts")
