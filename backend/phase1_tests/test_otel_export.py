"""H4-1 OTel GenAI export: mapping, default-off, failure isolation, OTLP wire (ADR-0011)."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import select

from app import otel_export
from app.phase1_models import Run, RunEvent
from test_trial_groups import claim_any, create_experiment, finish

ROOT_KEYS = {"gen_ai.operation.name", "gen_ai.agent.name", "gen_ai.agent.id", "gen_ai.conversation.id",
             "agentops.run.status", "agentops.run.kind", "agentops.experiment.id", "agentops.scenario.id",
             "agentops.run.score"}
TOOL_KEYS = {"gen_ai.operation.name", "gen_ai.tool.name", "gen_ai.tool.call.id",
             "agentops.environment.kind", "agentops.environment.result"}


async def golden_run(client, outcome: str) -> dict:
    experiment = await create_experiment(client)
    await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    return await finish(client, await claim_any(client), outcome)


async def load(factory, run_id: str):
    async with factory() as db:
        run = await db.get(Run, run_id)
        events = (await db.execute(select(RunEvent).where(RunEvent.run_id == run_id))).scalars().all()
        return run, events


async def test_golden_success_maps_to_invoke_agent_and_execute_tool_spans(api) -> None:
    client, factory = api
    run = await golden_run(client, "s")
    root = otel_export.build_run_spans(*await load(factory, run["id"]), "Deterministic fixture agent")
    assert root.name == "invoke_agent Deterministic fixture agent"
    assert set(root.attributes) == ROOT_KEYS
    assert root.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert root.attributes["gen_ai.conversation.id"] == run["id"]
    assert root.attributes["gen_ai.agent.id"] == "fixture-agent.v1"
    assert root.attributes["agentops.run.status"] == "succeeded" and not root.error
    assert [child.name for child in root.children] == [
        "execute_tool check_service_health", "execute_tool query_service_metrics",
        "execute_tool fetch_service_logs", "execute_tool submit_result"]
    assert all(set(child.attributes) == TOOL_KEYS for child in root.children)
    assert [child.attributes["gen_ai.tool.call.id"] for child in root.children] == ["2", "3", "4", "5"]
    assert [child.attributes["agentops.environment.kind"] for child in root.children] == [
        "world", "world", "world", "control"]
    assert all(c.start_ns <= c.end_ns and root.start_ns <= c.start_ns and c.end_ns <= root.end_ns
               for c in root.children)


async def test_failed_run_and_rejected_tool_are_error_spans(api) -> None:
    client, factory = api
    run = await golden_run(client, "f")
    db_run, events = await load(factory, run["id"])
    events[1].payload = {**events[1].payload, "environment": {"kind": "world", "result": "invalid_arguments"}}
    root = otel_export.build_run_spans(db_run, events, None)
    assert root.error and root.attributes["gen_ai.agent.name"] == "fixture-agent.v1"
    assert root.children[0].error and root.children[0].attributes["agentops.environment.result"] == "invalid_arguments"
    assert not any(child.error for child in root.children[1:])


async def test_export_is_off_by_default_and_never_builds_an_exporter(api, monkeypatch) -> None:
    client, _ = api
    monkeypatch.delenv(otel_export.ENABLE_ENV, raising=False)

    def forbidden():
        raise AssertionError("exporter must not be constructed when export is off")

    monkeypatch.setattr(otel_export, "_default_exporter", forbidden)
    run = await golden_run(client, "s")
    await otel_export.drain()
    assert run["status"] == "succeeded" and not otel_export._pending


async def test_enabled_export_sends_one_trace_per_terminal_run(api, monkeypatch) -> None:
    client, _ = api
    exporter = InMemorySpanExporter()
    monkeypatch.setenv(otel_export.ENABLE_ENV, "1")
    monkeypatch.setattr(otel_export, "_default_exporter", lambda: exporter)
    run = await golden_run(client, "s")
    await otel_export.drain()
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.parent is None)
    children = [span for span in spans if span.parent is not None]
    assert root.name == "invoke_agent Deterministic fixture agent"
    assert root.attributes["gen_ai.conversation.id"] == run["id"]
    assert len(children) == 4
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}
    assert {span.parent.span_id for span in children} == {root.context.span_id}
    assert root.status.status_code is StatusCode.UNSET
    assert root.resource.attributes["service.name"] == "agentops-control-plane"


async def test_export_failure_never_changes_the_run(api, monkeypatch, caplog) -> None:
    client, _ = api
    monkeypatch.setenv(otel_export.ENABLE_ENV, "1")

    def broken():
        raise RuntimeError("SECRET-endpoint http://user:pass@collector")

    monkeypatch.setattr(otel_export, "_default_exporter", broken)
    with caplog.at_level(logging.WARNING):
        run = await golden_run(client, "s")
        await otel_export.drain()
    assert run["status"] == "succeeded"
    assert (await client.get(f"/api/runs/{run['id']}")).json()["status"] == "succeeded"
    assert "OTel export failed; Run state is unaffected" in caplog.text
    assert "SECRET" not in caplog.text


def test_only_http_protobuf_is_accepted(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    with pytest.raises(ValueError):
        otel_export._default_exporter()


async def test_default_exporter_posts_otlp_protobuf_to_the_configured_endpoint(api, monkeypatch) -> None:
    client, factory = api
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            received.append((self.path, self.headers["content-type"], body))
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
        run = await golden_run(client, "s")
        otel_export.emit(otel_export.build_run_spans(*await load(factory, run["id"]), "Fixture"))
    finally:
        server.shutdown()
        server.server_close()
    assert len(received) == 1
    path, content_type, body = received[0]
    assert (path, content_type) == ("/v1/traces", "application/x-protobuf")
    request = ExportTraceServiceRequest.FromString(body)
    names = [span.name for rs in request.resource_spans for ss in rs.scope_spans for span in ss.spans]
    assert names[-1] == "invoke_agent Fixture" and len(names) == 5
    assert run["id"].encode() in body and b"test-runner-token" not in body
