"""Strict CP ingress, registered evaluation and terminal agreement component gates.

API/SQLite tests relay local Environment facts and exercise the Job-to-Run service.
Only current durable evidence authorizes success; terminal effects commit exactly once.
Read app.phase1_service next for locking, projection and terminalization.
"""

from __future__ import annotations

import copy
import io
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.env_server import EnvironmentSession, jsonl_emitter
from app.phase1_events import event_notifier
from app.phase1_models import Policy, Run, RunAnalysis, RunEvent, RunnerJob, utcnow
from app.phase1_schemas import CompleteRequest, EvaluationSpec
from app.scenario_assertions import (
    AssertionCombination,
    AssertionSuite,
    ScenarioAssertion,
)
from app.phase1_service import _trajectory_from_events

AUTH = {"Authorization": "Bearer test-runner-token"}
SCENARIO = "checkout-api-latency.v2"


def facts(run_id, attempt=1, **changes):
    return {
        "run_id": run_id,
        "attempt": attempt,
        "eligible": True,
        "submitted": True,
        "check_succeeded": True,
        "planned_environment_teardown": True,
        "failure": None,
        **changes,
    }


def event(run_id, sequence, kind, payload, attempt=1):
    return {
        "run_id": run_id,
        "sequence": sequence,
        "type": kind,
        "occurred_at": utcnow().isoformat(),
        "payload": {"attempt": attempt, **payload},
    }


async def create_claim(client, scenario=SCENARIO):
    experiment = (
        await client.post(
            "/api/experiments",
            json={
                "name": "CP contract",
                "task": "Resolve the task",
                "scenario_id": scenario,
            },
        )
    ).json()
    run = (
        await client.post(f"/api/experiments/{experiment['id']}/runs", json={})
    ).json()
    claim = (
        await client.post(
            "/api/internal/runner/jobs/claim",
            headers=AUTH,
            json={"runner_id": "runner-cp"},
        )
    ).json()
    return run, claim


async def packet(run, *, refund=True, submit=True, refusal=False):
    output = io.StringIO()
    session = EnvironmentSession(
        EvaluationSpec.model_validate(run["evaluation_spec"]),
        "f123456789abcdef" * 4,
        jsonl_emitter(output),
    )
    if run["evaluation_spec"]["scenario_id"] == "order-support.v1":
        calls = [
            (
                name,
                {
                    "order_id": "order-1001",
                    **(
                        {"reason": "shipping_failed"}
                        if name == "request_refund"
                        else {}
                    ),
                },
            )
            for name in ["inspect_order", "authorize_payment", "ship_order"]
            + (["request_refund", "request_refund"] if refund else [])
        ]
    else:
        calls = [
            ("check_service_health", {"service": "checkout-api"}),
            (
                "query_service_metrics",
                {"service": "checkout-api", "metric": "dependency_latency"},
            ),
            (
                "fetch_service_logs",
                {"service": "checkout-api", "query": "payment dependency timeout"},
            ),
        ]
    for name, arguments in calls:
        await session.dispatch({"name": name, "arguments": arguments})
    if refusal:
        await session.dispatch({"name": "shell", "arguments": {}})
    if submit:
        await session.dispatch({"name": "submit_result"})
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    return [event(run["id"], 1, "run_started", {"source": "environment"})] + [
        event(run["id"], index + 2, record["type"], record["payload"])
        for index, record in enumerate(records)
    ]


async def upload(client, run, claim, events):
    return await client.post(
        f"/api/internal/runner/runs/{run['id']}/events",
        headers=AUTH,
        json={
            "runner_id": "runner-cp",
            "lease_id": claim["lease_id"],
            "events": events,
        },
    )


async def complete(client, run, claim, *, status="succeeded", execution=None):
    return await client.post(
        f"/api/internal/runner/jobs/{claim['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "runner-cp",
            "status": status,
            "execution": execution or facts(run["id"], claim["attempt"]),
        },
    )


async def assert_terminal(factory, response, *, expected, sequence=None):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == body["run"]["status"] == expected
    assert body["run_id"] == body["run"]["id"]
    assert body["terminal_sequence"] > 0
    if sequence is not None:
        assert body["terminal_sequence"] == sequence
    async with factory() as db:
        events = (
            await db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == body["run_id"])
                .order_by(RunEvent.sequence)
            )
        ).all()
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        terminal = [
            e
            for e in events
            if e.event_type in {"run_completed", "run_failed", "run_cancelled"}
        ]
        assert len(terminal) == 1
        assert terminal[0].sequence == body["terminal_sequence"]
        assert terminal[0].payload["status"] == expected
        assert terminal[0].payload["attempt"] == body["attempt"]
        assert (
            terminal[0].event_type
            == {
                "succeeded": "run_completed",
                "failed": "run_failed",
                "timed_out": "run_failed",
                "cancelled": "run_cancelled",
            }[expected]
        )
        assert (await db.get(Run, body["run_id"])).status == expected
        assert (
            len(
                (
                    await db.scalars(
                        select(RunAnalysis).where(RunAnalysis.run_id == body["run_id"])
                    )
                ).all()
            )
            == 1
        )
    return body


@pytest.mark.parametrize(
    "field",
    ["eligible", "submitted", "check_succeeded", "planned_environment_teardown"],
)
@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_complete_request_strict_booleans(field, value):
    with pytest.raises(ValidationError):
        CompleteRequest.model_validate(
            {
                "runner_id": "r",
                "status": "succeeded",
                "execution": facts("run", **{field: value}),
            }
        )


@pytest.mark.parametrize("value", [True, False, 0, 101, 1.0, "1", None])
def test_complete_request_strict_attempt(value):
    with pytest.raises(ValidationError):
        CompleteRequest.model_validate(
            {
                "runner_id": "r",
                "status": "succeeded",
                "execution": facts("run", attempt=value),
            }
        )


@pytest.mark.parametrize(
    "execution",
    [
        None,
        {},
        {**facts("run"), "failure": "AGENT_EXIT"},
        {**facts("run"), "extra": True},
        {key: value for key, value in facts("run").items() if key != "failure"},
    ],
)
def test_complete_request_required_exact_facts(execution):
    with pytest.raises(ValidationError):
        CompleteRequest.model_validate(
            {
                "runner_id": "r",
                "status": "succeeded",
                **({"execution": execution} if execution is not None else {}),
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [SCENARIO, "order-support.v1"])
async def test_current_durable_evidence_terminal_ack_retry_and_quality(api, scenario):
    client, factory = api
    run, claim = await create_claim(client, scenario)
    events = await packet(run)
    # Nonzero quality penalties never become task-success authority.
    events[1]["payload"]["latency_ms"] = 1000
    uploaded = await upload(client, run, claim, events)
    assert uploaded.status_code == 200, uploaded.text
    # Simulate upload commit with lost ACK: retry same sequence/content.
    assert (await upload(client, run, claim, events)).status_code == 200
    first = await complete(client, run, claim)
    body = await assert_terminal(
        factory, first, expected="succeeded", sequence=len(events) + 1
    )
    assert body["run"]["metrics"]["assertions"]["aggregate"] == 1
    assert body["run"]["score"] < 1
    assert body["run"]["metrics"]["steps"] == len(events) - 2
    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert (await complete(client, run, claim)).json() == body
    assert (
        await complete(client, run, claim, execution=facts(run["id"], 2))
    ).status_code == 409
    assert (
        await complete(client, run, claim, execution=facts("wrong-run"))
    ).status_code == 403
    await assert_terminal(
        factory, await complete(client, run, claim), expected="succeeded"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "invalid_environment_ready",
        "environment_exit",
        "agent_exit",
        "missing_submission",
        "cancelled",
        "timed_out",
        "output_limit_exceeded",
        "lease_rejected",
        "internal",
    ],
)
async def test_execution_failure_overrides_apparent_success(api, failure):
    client, factory = api
    run, claim = await create_claim(client)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    result = await complete(
        client, run, claim, execution=facts(run["id"], failure=failure)
    )
    body = await assert_terminal(
        factory,
        result,
        expected={"cancelled": "cancelled", "timed_out": "timed_out"}.get(
            failure, "failed"
        ),
    )
    assert body["run"]["metrics"]["assertions"]["aggregate"] == 1
    assert body["run"]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["eligible", "submitted", "check_succeeded", "planned_environment_teardown"],
)
async def test_negative_local_gate_never_succeeds(api, field):
    client, factory = api
    run, claim = await create_claim(client)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    await assert_terminal(
        factory,
        await complete(
            client, run, claim, execution=facts(run["id"], **{field: False})
        ),
        expected="failed",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["no_events", "no_submit", "markers_only", "split_flags", "check_exception"]
)
async def test_missing_required_same_event_evidence_fails(api, mode):
    client, factory = api
    run, claim = await create_claim(client)
    events = await packet(run, submit=mode not in {"no_submit", "no_events"})
    if mode == "no_events":
        events = []
    if mode in {"markers_only", "split_flags", "check_exception"}:
        provenance = events[-1]["payload"]["environment"]
        provenance.pop("final_check")
        if mode == "check_exception":
            provenance["result"] = "internal_error"
        if mode == "split_flags":
            provenance["check_called"] = False
            extra = copy.deepcopy(events[-1])
            extra["sequence"] += 1
            extra["payload"]["environment"].update(finalized=False, check_called=True)
            events.append(extra)
    if events:
        assert (await upload(client, run, claim, events)).status_code == 200
    await assert_terminal(
        factory, await complete(client, run, claim), expected="failed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "missing_attempt",
        "float_attempt",
        "bool_attempt",
        "clamped_attempt",
        "old_attempt",
        "wrong_source",
        "no_provenance",
        "coercible_flag",
        "runner_terminal",
        "second_start",
        "before_start",
        "wrong_scenario",
        "extra_provenance",
        "split_final_tuple",
    ],
)
async def test_raw_ingress_grammar_rejects_before_normalization(api, mode):
    client, factory = api
    run, claim = await create_claim(client)
    events = await packet(run)
    expected = 422
    if mode == "missing_attempt":
        events[1]["payload"].pop("attempt")
    elif mode in {"float_attempt", "bool_attempt", "clamped_attempt", "old_attempt"}:
        events[1]["payload"]["attempt"] = {
            "float_attempt": 1.0,
            "bool_attempt": True,
            "clamped_attempt": 1000,
            "old_attempt": 2,
        }[mode]
        expected = 409 if mode == "old_attempt" else 422
    elif mode == "wrong_source":
        events[0]["payload"]["source"] = "agent"
    elif mode == "no_provenance":
        events[1]["payload"].pop("environment")
    elif mode == "coercible_flag":
        events[1]["payload"]["environment"]["finalized"] = 1
    elif mode == "runner_terminal":
        events[-1]["type"] = "run_completed"
    elif mode == "second_start":
        events[1] = event(run["id"], 2, "run_started", {"source": "environment"})
        expected = 409
    elif mode == "before_start":
        events = events[1:]
        for i, item in enumerate(events):
            item["sequence"] = i + 1
    elif mode == "wrong_scenario":
        events[-1]["payload"]["environment"]["final_check"][
            "scenario_id"
        ] = "multi-step-research.v2"
    elif mode == "extra_provenance":
        events[-1]["payload"]["environment"]["score"] = 1
    elif mode == "split_final_tuple":
        events[-1]["payload"]["environment"]["check_called"] = False
    result = await upload(client, run, claim, events)
    assert result.status_code == expected, result.text
    async with factory() as db:
        assert not (
            await db.scalars(select(RunEvent).where(RunEvent.run_id == run["id"]))
        ).all()


@pytest.mark.asyncio
async def test_distinct_accepted_evidence_conflicts_and_controls_refusals_are_not_tools(
    api,
):
    client, factory = api
    run, claim = await create_claim(client)
    events = await packet(run, refusal=True)
    assert (await upload(client, run, claim, events)).status_code == 200
    duplicate = copy.deepcopy(events[-1])
    duplicate["sequence"] += 1
    result = await upload(client, run, claim, [duplicate])
    assert result.status_code == 409
    async with factory() as db:
        stored = await db.get(Run, run["id"])
        trajectory, metrics = await _trajectory_from_events(db, stored)
    assert metrics["steps"] == 4
    assert trajectory["steps"][-1]["action"] == {
        "name": "rejected_tool",
        "arguments": {},
    }
    assert trajectory["steps"][-1]["status"] == "failed"
    assert all(
        step["action"]["name"] != "submit_result" for step in trajectory["steps"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "combination,aggregate",
    [
        (AssertionCombination.ALL, 0),
        (AssertionCombination.ANY, 1),
        (AssertionCombination.WEIGHTED, 0.75),
    ],
)
async def test_registered_combination_is_success_gate_not_all_passed(
    api, monkeypatch, combination, aggregate
):
    import app.phase1_service as service

    client, factory = api
    run, claim = await create_claim(client)
    suite = AssertionSuite(
        combination,
        (
            ScenarioAssertion(type="tool-used", tool="check_service_health", weight=3),
            ScenarioAssertion(
                type="tool-used", tool="inspect_order", weight=1, threshold=0.5
            ),
        ),
    )
    monkeypatch.setattr(service, "assertion_suite_for_scenario", lambda _: suite)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    body = await assert_terminal(
        factory,
        await complete(client, run, claim),
        expected="succeeded" if aggregate == 1 else "failed",
    )
    assert body["run"]["metrics"]["assertions"]["aggregate"] == aggregate
    assert body["run"]["metrics"]["score_breakdown"]["success_reward"] == aggregate
    assert not body["run"]["metrics"]["assertions"]["assertions"][1]["passed"]
    async with factory() as db:
        analysis = await db.get(RunAnalysis, run["id"])
        assert any(
            e["details"] == {"assertion_type": "tool-used"} for e in analysis.evidence
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformation", ["missing", "bool", "float", "duplicates", "nan", "wrong_root"]
)
async def test_order_required_operands_invalid_even_under_any(
    api, monkeypatch, malformation
):
    import app.phase1_service as service

    client, factory = api
    run, claim = await create_claim(client, "order-support.v1")
    events = await packet(run)
    check = events[-1]["payload"]["environment"]["final_check"]
    observation = json.loads(check["final_observation"])
    if malformation == "missing":
        observation["order"].pop("refund_count")
    elif malformation in {"bool", "float"}:
        observation["order"]["refund_count"] = True if malformation == "bool" else 1.0
    check["final_observation"] = json.dumps(observation)
    if malformation == "duplicates":
        check["final_observation"] = check["final_observation"].replace(
            '"refund_count": 1', '"refund_count": 0, "refund_count": 1'
        )
    elif malformation == "nan":
        check["final_observation"] = check["final_observation"].replace(
            '"refund_count": 1', '"refund_count": NaN'
        )
    elif malformation == "wrong_root":
        check["final_observation"] = "[]"
    monkeypatch.setattr(
        service,
        "assertion_suite_for_scenario",
        lambda _: AssertionSuite(
            AssertionCombination.ANY,
            (ScenarioAssertion(type="step-count", max_steps=100),),
        ),
    )
    assert (await upload(client, run, claim, events)).status_code == 422
    await assert_terminal(
        factory, await complete(client, run, claim), expected="failed"
    )


@pytest.mark.asyncio
async def test_actual_final_ledger_not_last_tool_observation_or_requested_count(api):
    client, factory = api
    run, claim = await create_claim(client, "order-support.v1")
    events = await packet(run, refund=False)
    # A tool observation with a forged ledger is not final evidence.
    good = await packet(run)
    events[-2]["payload"]["observation"] = good[-1]["payload"]["environment"][
        "final_check"
    ]["final_observation"]
    assert (await upload(client, run, claim, events)).status_code == 200
    body = await assert_terminal(
        factory, await complete(client, run, claim), expected="failed"
    )
    assert body["run"]["metrics"]["assertions"]["aggregate"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["queued", "requeued", "exhausted", "cancel_exhausted"]
)
async def test_all_terminal_paths_atomically_publish_after_commit(api, path):
    client, factory = api
    run, claim = await create_claim(client)
    if path == "queued":
        async with factory() as db:
            stored = await db.get(Run, run["id"])
            stored.status = "queued"
            await db.commit()
    elif path in {"requeued", "exhausted", "cancel_exhausted"}:
        assert (await upload(client, run, claim, await packet(run))).status_code == 200
        async with factory() as db:
            job = await db.get(RunnerJob, run["id"])
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
            if "exhausted" in path:
                job.attempt = 3
            if path == "cancel_exhausted":
                job.cancel_requested_at = utcnow()
            await db.commit()
    queue = event_notifier.subscribe(run["id"])
    try:
        if path in {"exhausted", "cancel_exhausted"}:
            # Recovery happens through claim, including its no-work commit.
            assert (
                await client.post(
                    "/api/internal/runner/jobs/claim",
                    headers=AUTH,
                    json={"runner_id": "runner-other"},
                )
            ).status_code == 204
        elif path == "requeued":
            # Recover and commit without claiming, then queued cancellation.
            from app.phase1_service import (
                _recover_expired_jobs,
                _commit_terminal_notifications,
            )

            async with factory() as db:
                assert await _recover_expired_jobs(db) == 1
                await _commit_terminal_notifications(db)
            assert (
                await client.post(f"/api/runs/{run['id']}/cancel")
            ).status_code == 200
        else:
            assert (
                await client.post(f"/api/runs/{run['id']}/cancel")
            ).status_code == 200
        published = queue.get_nowait()
        expected = "failed" if path == "exhausted" else "cancelled"
        assert published["payload"]["status"] == expected
        assert queue.empty()
        # Notification is visible only after its database state and effects.
        async with factory() as db:
            stored = await db.get(Run, run["id"])
            assert stored.status == expected
            assert await db.get(RunAnalysis, run["id"]) is not None
            terminal = (
                await db.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run["id"])
                    .order_by(RunEvent.sequence.desc())
                )
            ).first()
            assert terminal.sequence == published["sequence"]
            assert terminal.payload == published["payload"]
        assert (await client.post(f"/api/runs/{run['id']}/cancel")).status_code == 200
        assert queue.empty()
    finally:
        event_notifier.unsubscribe(run["id"], queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_attempt", ["missing", True, 1.0, 2])
async def test_corrupt_current_refusal_identity_fails_closed(api, bad_attempt):
    client, factory = api
    run, claim = await create_claim(client)
    events = await packet(run, refusal=True)
    events.insert(-1, copy.deepcopy(events[-2]))
    for index, item in enumerate(events, 1):
        item["sequence"] = index
    assert (await upload(client, run, claim, events)).status_code == 200
    async with factory() as db:
        rows = (
            await db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run["id"])
                .order_by(RunEvent.sequence)
            )
        ).all()
        refusals = [
            row
            for row in rows
            if row.event_type == "step_completed"
            and row.payload.get("environment", {}).get("result") == "unknown_tool"
        ]
        assert len(refusals) == 2
        for row in refusals:
            payload = copy.deepcopy(row.payload)
            if bad_attempt == "missing":
                payload.pop("attempt")
            else:
                payload["attempt"] = bad_attempt
            row.payload = payload
            flag_modified(row, "payload")
        await db.commit()
    result = await complete(client, run, claim)
    await assert_terminal(factory, result, expected="failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested_status,failure,expected",
    [
        ("cancelled", "timed_out", "timed_out"),
        ("timed_out", "agent_exit", "failed"),
    ],
)
async def test_locked_execution_failure_dominates_requested_status(
    api, requested_status, failure, expected
):
    client, factory = api
    run, claim = await create_claim(client)
    result = await complete(
        client,
        run,
        claim,
        status=requested_status,
        execution=facts(run["id"], failure=failure),
    )
    await assert_terminal(factory, result, expected=expected, sequence=1)


@pytest.mark.asyncio
async def test_old_attempt_evidence_cannot_satisfy_replacement_attempt(api):
    client, factory = api
    run, claim = await create_claim(client)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    replacement = (
        await client.post(
            "/api/internal/runner/jobs/claim",
            headers=AUTH,
            json={"runner_id": "runner-cp"},
        )
    ).json()
    assert replacement["attempt"] == 2
    assert (await complete(client, run, claim)).status_code == 403
    body = await assert_terminal(
        factory, await complete(client, run, replacement), expected="failed"
    )
    assert body["attempt"] == 2
    assert body["run"]["metrics"]["steps"] == 0


@pytest.mark.asyncio
async def test_completion_wins_before_upload_and_cancel_intent_dominates(api):
    client, factory = api
    run, claim = await create_claim(client)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    assert (await client.post(f"/api/runs/{run['id']}/cancel")).json()[
        "status"
    ] == "cancelling"
    body = await assert_terminal(
        factory, await complete(client, run, claim), expected="cancelled"
    )
    later = event(
        run["id"], body["terminal_sequence"] + 1, "process_output", {"content": "late"}
    )
    assert (await upload(client, run, claim, [later])).status_code == 409
    assert (await complete(client, run, claim)).json() == body


@pytest.mark.asyncio
async def test_terminal_transaction_commit_failure_has_no_events_effects_or_notifications(
    api, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    client, factory = api
    run, claim = await create_claim(client)
    queue = event_notifier.subscribe(run["id"])
    original = AsyncSession.commit

    async def fail_commit(db):
        raise RuntimeError("local commit failure")

    try:
        monkeypatch.setattr(AsyncSession, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="local commit failure"):
            await complete(client, run, claim, status="failed")
        assert queue.empty()
        async with factory() as db:
            assert (await db.get(Run, run["id"])).status == "claimed"
            assert await db.get(RunAnalysis, run["id"]) is None
            assert not (
                await db.scalars(select(RunEvent).where(RunEvent.run_id == run["id"]))
            ).all()
            assert not (
                await db.scalars(
                    select(Policy).where(Policy.source_run_id == run["id"])
                )
            ).all()
        monkeypatch.setattr(AsyncSession, "commit", original)
        result = await complete(client, run, claim, status="failed")
        await assert_terminal(factory, result, expected="failed", sequence=1)
        assert queue.get_nowait()["sequence"] == 1
        assert queue.empty()
    finally:
        event_notifier.unsubscribe(run["id"], queue)


@pytest.mark.asyncio
async def test_projection_evaluated_once_and_not_on_committed_retry(api, monkeypatch):
    import app.phase1_service as service

    original = service.evaluate_assertion_suite
    calls = []

    def evaluated(suite, trajectory):
        calls.append(trajectory)
        return original(suite, trajectory)

    monkeypatch.setattr(service, "evaluate_assertion_suite", evaluated)
    client, factory = api
    run, claim = await create_claim(client)
    assert (await upload(client, run, claim, await packet(run))).status_code == 200
    result = await complete(client, run, claim)
    await assert_terminal(factory, result, expected="succeeded")
    assert len(calls) == 1
    assert (await complete(client, run, claim)).json() == result.json()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_final_exhaustion_returns_matching_identity_ack_without_duplicate_effects(
    api,
):
    client, factory = api
    run, claim = await create_claim(client)
    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        job.attempt = 3
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert (
        await client.post(
            "/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "other"}
        )
    ).status_code == 204
    result = await complete(client, run, claim, execution=facts(run["id"], 3))
    await assert_terminal(factory, result, expected="failed", sequence=1)
    assert (
        await complete(client, run, claim, execution=facts(run["id"], 3))
    ).json() == result.json()
    assert (await complete(client, run, claim)).status_code == 409


@pytest.mark.asyncio
async def test_refused_named_tool_cannot_satisfy_registered_arguments_or_sequence(
    api, monkeypatch
):
    import app.phase1_service as service

    client, factory = api
    run, claim = await create_claim(client)
    events = await packet(run)
    for item in events[1:-1]:
        item["payload"]["environment"]["result"] = "invalid_arguments"
    monkeypatch.setattr(
        service,
        "assertion_suite_for_scenario",
        lambda _: AssertionSuite(
            AssertionCombination.ANY,
            (
                ScenarioAssertion(type="tool-used", tool="check_service_health"),
                ScenarioAssertion(
                    type="tool-args-match",
                    tool="check_service_health",
                    arguments={"service": "checkout-api"},
                ),
                ScenarioAssertion(
                    type="tool-sequence", sequence=("check_service_health",)
                ),
            ),
        ),
    )
    assert (await upload(client, run, claim, events)).status_code == 200
    body = await assert_terminal(
        factory, await complete(client, run, claim), expected="failed"
    )
    assert [a["score"] for a in body["run"]["metrics"]["assertions"]["assertions"]] == [
        0,
        0,
        0,
    ]


@pytest.mark.asyncio
async def test_wrong_runner_cannot_acknowledge_or_mutate_committed_completion(api):
    client, factory = api
    run, claim = await create_claim(client)
    result = await complete(client, run, claim, status="failed")
    body = await assert_terminal(factory, result, expected="failed", sequence=1)
    wrong = await client.post(
        f"/api/internal/runner/jobs/{claim['lease_id']}/complete",
        headers=AUTH,
        json={
            "runner_id": "other",
            "status": "succeeded",
            "execution": facts(run["id"]),
        },
    )
    assert wrong.status_code == 403
    assert (await complete(client, run, claim)).json() == body


@pytest.mark.parametrize(
    "field,value",
    [
        ("terminal_sequence", 0),
        ("terminal_sequence", True),
        ("attempt", "1"),
        ("status", "running"),
        ("run_id", "wrong-run"),
    ],
)
def test_completion_response_rejects_invalid_ack_identity(field, value):
    from app.phase1_schemas import CompletionResponse, RunResponse

    run = RunResponse(
        id="run",
        experiment_id="experiment",
        kind="baseline",
        status="failed",
        score=0,
        metrics={},
        evaluation_spec={
            "run_id": "run",
            "experiment_id": "experiment",
            "task": "Task",
        },
        source_run_id=None,
        policy_id=None,
        error=None,
        queued_at=utcnow(),
        started_at=None,
        completed_at=utcnow(),
    )
    payload = {
        "run_id": "run",
        "attempt": 1,
        "terminal_sequence": 1,
        "status": "failed",
        "run": run,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        CompletionResponse.model_validate(payload)


@pytest.mark.asyncio
async def test_recovery_terminal_notification_commits_even_when_claim_finds_other_work(
    api,
):
    client, factory = api
    run, claim = await create_claim(client)
    async with factory() as db:
        job = await db.get(RunnerJob, run["id"])
        job.attempt = 3
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    other = (
        await client.post(f"/api/experiments/{run['experiment_id']}/runs", json={})
    ).json()
    queue = event_notifier.subscribe(run["id"])
    try:
        claimed = await client.post(
            "/api/internal/runner/jobs/claim", headers=AUTH, json={"runner_id": "other"}
        )
        assert claimed.status_code == 200
        assert claimed.json()["run"]["run_id"] == other["id"]
        assert queue.get_nowait()["payload"]["status"] == "failed"
        assert queue.empty()
        async with factory() as db:
            assert (await db.get(Run, run["id"])).status == "failed"
            assert await db.get(RunAnalysis, run["id"]) is not None
    finally:
        event_notifier.unsubscribe(run["id"], queue)
