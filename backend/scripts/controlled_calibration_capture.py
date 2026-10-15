"""Capture six contract-labeled samples in an empty, disposable PostgreSQL database.

Run from backend with PYTHONPATH=. DATABASE_URL and RUNNER_TOKEN set. This uses
the authenticated ASGI Runner API and a loopback Fake Provider, not Rust process
supervision. Labels are agent-authored provisional contracts, never accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calibration_report import LabelSet, ReviewPacket, ReviewRecord, report_labeled_runs
from app.calibration_review import build_review_packet, write_packet
from app.database_recovery import backup_database
from app.failure_analyzer import ANALYZER_REVISION
from app.migrations import application_alembic_head
from app.phase1_database import make_engine
from app.phase1_main import create_app
from app.phase1_models import Base
from app.phase1_provider import run_provider_agent
from app.phase1_schemas import EvaluationSpec
from app.scenario_ids import CHECKOUT_SCENARIO_ID


TOOLS = (
    ("check_service_health", {"service": "checkout-api"}, "Service is healthy but checkout latency is elevated."),
    ("query_service_metrics", {"service": "checkout-api", "metric": "dependency_latency"}, "Payment dependency p95 increased from 110ms to 1.8s."),
    ("fetch_service_logs", {"service": "checkout-api", "query": "payment dependency timeout"}, "Requests are delayed by payment-gateway connection pool saturation."),
)


@dataclass(frozen=True)
class CaseContract:
    name: str
    mode: str
    context_state: str
    dimensions: tuple[str, ...]
    evidence: str
    provider_error: str | None = None


CONTRACTS = (
    CaseContract("context_pressure", "fixture", "positive", ("context",), "Each injected step records 8191/8192 context-window use, above 95%; the full checkout tool sequence succeeds."),
    CaseContract("context_low_pressure", "fixture", "negative", (), "Each injected step records 1200/8192 context-window use; observations contain no truncation signal and the full tool sequence succeeds."),
    CaseContract("context_unknown", "fixture", "unknown", (), "Context-window measurement is absent; observations contain no truncation signal. No context signal is supported, but absence of pressure is not established."),
    CaseContract("provider_success", "provider", "unknown", (), "The loopback Fake Provider selects all three contracted checkout tools with valid arguments, then returns a nonempty final response; no context measurement is available."),
    CaseContract("provider_timeout", "provider", "unknown", ("execution",), "The loopback Fake Provider delays its first response beyond the configured timeout; PROVIDER_TIMEOUT must persist, demonstrating an execution failure with no completed tool step.", "PROVIDER_TIMEOUT"),
    CaseContract("provider_invalid_response", "provider", "unknown", ("execution",), "The loopback Fake Provider returns an empty choices array; PROVIDER_INVALID_RESPONSE must persist, demonstrating an execution failure with no completed tool step.", "PROVIDER_INVALID_RESPONSE"),
)


class FakeProvider:
    def __init__(self, case: CaseContract) -> None:
        self.request_count = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                index = owner.request_count
                owner.request_count += 1
                if case.provider_error == "PROVIDER_TIMEOUT":
                    time.sleep(0.3)
                message: dict[str, object] = {"role": "assistant", "content": "Investigation complete."}
                if index < len(TOOLS):
                    name, arguments, _ = TOOLS[index]
                    message.update(content=None, tool_calls=[{
                        "id": f"controlled-{index}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }])
                body = {"choices": [{"message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
                if case.provider_error == "PROVIDER_INVALID_RESPONSE":
                    body["choices"] = []
                encoded = json.dumps(body).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self) -> FakeProvider:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


async def require_empty_database(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        if revision != application_alembic_head():
            raise ValueError("capture requires the current Alembic head")
        for table in Base.metadata.tables.values():
            if await db.scalar(select(func.count()).select_from(table)):
                raise ValueError(f"capture requires empty domain tables: {table.name}")


async def capture_cases(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession], token: str
) -> list[dict[str, object]]:
    await require_empty_database(factory)
    headers = {"Authorization": f"Bearer {token}"}
    captured = []
    for case in CONTRACTS:
        created = await client.post("/api/experiments", json={
            "name": f"Controlled calibration: {case.name}",
            "task": "Investigate checkout API latency",
            "scenario_id": CHECKOUT_SCENARIO_ID, "execution_mode": case.mode,
        })
        created.raise_for_status()
        queued = await client.post(f"/api/experiments/{created.json()['id']}/runs", json={})
        queued.raise_for_status()
        claim_response = await client.post("/api/internal/runner/jobs/claim", headers=headers, json={"runner_id": "controlled-capture"})
        claim_response.raise_for_status()
        claim = claim_response.json()
        run_id = claim["run"]["run_id"]
        if run_id != queued.json()["id"]:
            raise ValueError("isolated capture claimed an unexpected Run")
        spec = EvaluationSpec.model_validate(claim["run"]["evaluation_spec"])
        emitted: list[tuple[str, dict[str, object]]] = []
        requests = 0
        if case.mode == "provider":
            with FakeProvider(case) as server, patch.dict(os.environ, {
                "AGENTOPS_PROVIDER_BASE_URL": server.url,
                "AGENTOPS_PROVIDER_API_KEY": "controlled-fake-provider-secret",
                "AGENTOPS_PROVIDER_MODEL": "controlled-fake-checkout",
                "AGENTOPS_PROVIDER_TIMEOUT_MS": "100" if case.provider_error == "PROVIDER_TIMEOUT" else "1000",
                "AGENTOPS_PROVIDER_MAX_RETRIES": "0",
                "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
            }):
                exit_code = await run_provider_agent(spec, lambda kind, payload: emitted.append((kind, payload)))
                requests = server.request_count
        else:
            for index, (name, arguments, observation) in enumerate(TOOLS):
                payload: dict[str, object] = {
                    "index": index, "decision_summary": "Controlled checkout tool observation.",
                    "tool_call": {"name": name, "arguments": arguments},
                    "observation": observation, "latency_ms": 1,
                    "token_prompt": 100, "token_completion": 10,
                }
                if case.context_state != "unknown":
                    payload["context_window"] = {"used": 8191 if case.context_state == "positive" else 1200, "limit": 8192}
                emitted.append(("step_completed", payload))
            exit_code = 0
        status = "failed" if case.provider_error else "succeeded"
        if (exit_code != 0) != (status == "failed"):
            raise ValueError(f"unexpected controlled Agent exit: {case.name}")
        errors = [payload["provider_error"]["code"] for _, payload in emitted if "provider_error" in payload]
        if errors != ([case.provider_error] if case.provider_error else []):
            raise ValueError(f"unexpected controlled Provider error: {case.name}")
        pairs = [("run_started", {"attempt": claim["attempt"]}), *emitted,
                 ("run_failed" if exit_code else "run_completed", {"attempt": claim["attempt"], "exit_code": exit_code})]
        events = [{"schema_version": 1, "run_id": run_id, "sequence": index,
                   "occurred_at": datetime.now(timezone.utc).isoformat(), "type": kind, "payload": payload}
                  for index, (kind, payload) in enumerate(pairs, start=1)]
        uploaded = await client.post(f"/api/internal/runner/runs/{run_id}/events", headers=headers,
                                     json={"runner_id": "controlled-capture", "lease_id": claim["lease_id"], "events": events})
        uploaded.raise_for_status()
        if uploaded.json()["accepted_through"] != len(events):
            raise ValueError("Runner API did not accept the complete event sequence")
        completed = await client.post(f"/api/internal/runner/jobs/{claim['lease_id']}/complete", headers=headers,
                                      json={"runner_id": "controlled-capture", "status": status})
        completed.raise_for_status()
        if completed.json()["status"] != status:
            raise ValueError(f"unexpected persisted terminal status: {case.name}")
        captured.append({"name": case.name, "run_id": run_id, "context_state": case.context_state,
                         "expected_dimensions": list(case.dimensions), "expected_provider_error": case.provider_error,
                         "evidence": case.evidence, "event_count": len(events), "provider_request_count": requests})
    return captured


def file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


async def export_artifacts(
    factory: async_sessionmaker[AsyncSession], output: Path, captured: list[dict[str, object]], snapshot: Path
) -> dict[str, object]:
    source_snapshot = file_digest(snapshot)
    async with factory() as db:
        packet = await build_review_packet(db, limit=len(CONTRACTS), source_snapshot=source_snapshot)
    if {case["run_id"] for case in packet["cases"]} != {case["run_id"] for case in captured}:
        raise ValueError("packet did not select exactly the controlled Runs")
    write_packet(output / "packet.json", packet)
    labels = {"name": "controlled-calibration.v1", "provenance": "Agent-authored input contracts fixed in controlled_calibration_capture.py before capture; not independent labels or accuracy evidence.",
              "cases": [{"run_id": case["run_id"], "dimensions": case["expected_dimensions"]} for case in captured]}
    write_packet(output / "provisional-labels.json", labels)
    captured_by_id = {case["run_id"]: case for case in captured}
    record = {
        "version": "v1",
        "packet": {"analyzer_revision": ANALYZER_REVISION, "source_snapshot": source_snapshot,
                   "case_count": len(captured), "packet_sha256": file_digest(output / "packet.json"),
                   "labels_sha256": file_digest(output / "provisional-labels.json")},
        "reviewer": {"status": "agent_generated_provisional", "reviewer_ids": ["codex-agent"],
                     "method": "Agent-authored fixed event contracts; source/analyzer inspection was available. No independent or blind human review.",
                     "reviewed_at": datetime.now(timezone.utc).isoformat()},
        "adjudication": {"status": "pending"}, "publishable_accuracy": False,
        "cases": [{key: case[key] for key in ("run_id", "scenario_id", "execution_mode", "kind", "task")} |
                  {"evidence": [captured_by_id[case["run_id"]]["evidence"]],
                   "uncertainty": "Controlled injection/Fake Provider only; unknown context is absence of a supported signal, not a verified negative. No causal or general accuracy claim."}
                  for case in packet["cases"]],
    }
    write_packet(output / "review-record.json", record)
    async with factory() as db:
        report = await report_labeled_runs(db, LabelSet.model_validate(labels), packet=ReviewPacket.model_validate(packet),
                                          review_record=ReviewRecord.model_validate(record), packet_sha256=record["packet"]["packet_sha256"],
                                          labels_sha256=record["packet"]["labels_sha256"], require_binding=True)
    write_packet(output / "strict-report.json", report)
    write_packet(output / "capture-manifest.json", {
        "captured_at": datetime.now(timezone.utc).isoformat(), "schema_revision": application_alembic_head(),
        "source_snapshot": source_snapshot, "script_sha256": file_digest(Path(__file__)),
        "boundary": "ASGI authenticated Runner API, injected context measurements and loopback Fake Provider; no Rust supervision or external model.",
        "context_contract": "positive: observed pressure; negative: observed low use with no truncation signal; unknown: measurement absent, no supported signal. Unknown cases in report counts do not validate true context negatives.",
        "publishable_accuracy": False, "cases": captured,
    })
    if report["disagreements"]:
        raise ValueError("controlled contract/report disagreement; retained artifacts require investigation")
    return report


async def run_capture(database_url: str, token: str, output: Path) -> dict[str, object]:
    url = make_url(database_url)
    disposable_name = (url.database or "").startswith("agentops_calibration_") or url.database == "agentops_controlled_calibration"
    if url.drivername != "postgresql+asyncpg" or not disposable_name:
        raise ValueError("capture requires a disposable PostgreSQL database named agentops_calibration_<suffix>")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_packet(output / "case-contracts.json", {"cases": [case.__dict__ for case in CONTRACTS]})
    engine = make_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with patch.dict(os.environ, {"RUNNER_TOKEN": token}):
            app = create_app(session_factory=factory, database_engine=engine, initialize_database=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://controlled-capture") as client:
            captured = await capture_cases(client, factory, token)
        snapshot = output / "snapshot.dump"
        await asyncio.to_thread(backup_database, database_url, snapshot)
        return await export_artifacts(factory, output, captured, snapshot)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL", "")
    token = os.getenv("RUNNER_TOKEN", "").strip()
    if not database_url or not token:
        parser.error("DATABASE_URL and RUNNER_TOKEN must be set")
    try:
        report = asyncio.run(run_capture(database_url, token, args.output))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "case_count": report["case_count"], "binding": report["binding"]["status"],
                      "accuracy_status": report["accuracy_status"], "publishable_accuracy": False}))


if __name__ == "__main__":
    main()
