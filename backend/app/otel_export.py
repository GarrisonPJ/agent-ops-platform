"""Optional OTLP export of terminal Runs as OpenTelemetry GenAI spans (ADR-0011).

After a Run's terminal transaction commits, the Control Plane maps its persisted
RunEvents to one ``invoke_agent`` root span and one ``execute_tool`` child per
``step_completed``. A background thread sends them to the standard
``OTEL_EXPORTER_OTLP_*`` endpoint.

Invariant: export is off unless ``AGENTOPS_OTEL_EXPORT=1``, lossy, never read
back, and cannot change a Run. Failures are logged with a fixed message. Spans
carry no credentials, Provider URLs, tool arguments, observations or reasoning.

Read ``docs/learn/07-otel-mapping.md`` next for the attribute table and the
pinned semantic-conventions revision.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

ENABLE_ENV = "AGENTOPS_OTEL_EXPORT"
SERVICE_NAME = "agentops-control-plane"
MAX_NAME_CHARS = 128
_pending: set[asyncio.Future] = set()


def export_enabled() -> bool:
    return os.environ.get(ENABLE_ENV) == "1"


@dataclass(frozen=True)
class SpanSpec:
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any]
    error: bool
    children: tuple[SpanSpec, ...] = field(default=())


def _ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def _bounded(value: object, fallback: str) -> str:
    text = value if isinstance(value, str) and value else fallback
    return text[:MAX_NAME_CHARS]


def build_run_spans(run: Any, events: Iterable[Any], agent_name: str | None) -> SpanSpec:
    """Pure mapping from a terminal Run and its ordered RunEvents to span specs."""
    events = sorted(events, key=lambda event: event.sequence)
    spec = run.evaluation_spec or {}
    agent_id = spec.get("agent_id")
    times = [_ns(event.occurred_at) for event in events]
    run_start = _ns(run.started_at) or (times[0] if times else _ns(run.queued_at)) or 0
    run_end = _ns(run.completed_at) or (times[-1] if times else run_start)
    children = []
    previous = run_start
    for event, at in zip(events, times):
        at = at if at is not None else previous
        if event.event_type == "step_completed":
            payload = event.payload or {}
            environment = payload.get("environment") or {}
            tool = _bounded(
                (payload.get("tool_call") or {}).get("name") or environment.get("control"), "unknown_tool"
            )
            result = _bounded(environment.get("result"), "unknown")
            children.append(SpanSpec(
                name=f"execute_tool {tool}",
                start_ns=min(previous, at),
                end_ns=at,
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": tool,
                    "gen_ai.tool.call.id": str(event.sequence),
                    "agentops.environment.kind": _bounded(environment.get("kind"), "unknown"),
                    "agentops.environment.result": result,
                },
                error=result != "ok",
            ))
        previous = max(previous, at)
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": _bounded(agent_name or agent_id, "unknown_agent"),
        "gen_ai.conversation.id": run.id,
        "agentops.run.status": run.status,
        "agentops.run.kind": run.kind,
        "agentops.experiment.id": run.experiment_id,
        "agentops.scenario.id": _bounded(spec.get("scenario_id"), "unknown"),
    }
    if agent_id:
        attributes["gen_ai.agent.id"] = _bounded(agent_id, "unknown_agent")
    if run.score is not None:
        attributes["agentops.run.score"] = float(run.score)
    if getattr(run, "trial_group_id", None):
        attributes["agentops.trial_group.id"] = run.trial_group_id
        attributes["agentops.trial.index"] = run.trial_index
    # Runner and Control Plane clocks can disagree; the root must enclose its children.
    return SpanSpec(
        name=f"invoke_agent {attributes['gen_ai.agent.name']}",
        start_ns=min([run_start] + [child.start_ns for child in children]),
        end_ns=max([run_end, previous] + [child.end_ns for child in children]),
        attributes=attributes,
        error=run.status != "succeeded",
        children=tuple(children),
    )


def _default_exporter():
    # Reads OTEL_EXPORTER_OTLP_ENDPOINT / _TRACES_ENDPOINT and _HEADERS itself.
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
    if protocol not in (None, "", "http/protobuf"):
        raise ValueError("only http/protobuf OTLP is supported")
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter()


def emit(root: SpanSpec, exporter_factory: Callable[[], Any] | None = None) -> None:
    """Send one Run's spans in one export through a private provider.

    Spans are collected in memory first, so one Run is one OTLP request; global
    tracing state is never touched.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode

    exporter = (exporter_factory or _default_exporter)()
    collected = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(SimpleSpanProcessor(collected))
    try:
        tracer = provider.get_tracer("agentops.otel_export")
        span = tracer.start_span(root.name, start_time=root.start_ns, attributes=root.attributes)
        context = trace.set_span_in_context(span)
        for child in root.children:
            item = tracer.start_span(child.name, context=context, start_time=child.start_ns,
                                     attributes=child.attributes)
            if child.error:
                item.set_status(Status(StatusCode.ERROR))
            item.end(end_time=child.end_ns)
        if root.error:
            span.set_status(Status(StatusCode.ERROR))
        span.end(end_time=root.end_ns)
        exporter.export(collected.get_finished_spans())
    finally:
        provider.shutdown()
        exporter.shutdown()


def _emit_safely(root: SpanSpec, exporter_factory: Callable[[], Any] | None) -> None:
    try:
        emit(root, exporter_factory)
    except Exception:
        # Never log the endpoint, headers or exception text: they can hold credentials.
        logger.warning("OTel export failed; Run state is unaffected")


def schedule(roots: list[SpanSpec], exporter_factory: Callable[[], Any] | None = None) -> None:
    """Fire-and-forget export on a worker thread after the terminal commit."""
    loop = asyncio.get_running_loop()
    for root in roots:
        future = loop.run_in_executor(None, _emit_safely, root, exporter_factory)
        _pending.add(future)
        future.add_done_callback(_pending.discard)


async def drain() -> None:
    """Wait for scheduled exports (tests and shutdown)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)
