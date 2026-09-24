from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.failure_analyzer import analyze_trajectory
from app.phase1_models import Run, RunAnalysis, RunEvent
from app.phase1_schemas import EvaluationSpec
from app.phase1_service import _trajectory_from_events
from app.scenario_registry import assertion_suite_for_scenario, require_scenario
from app.scoring import compute_score
from evaluation_calibration_fixtures import (
    FAILURE_ANALYZER_FIXTURES,
    SCENARIO_CALIBRATION_FIXTURES,
    LabeledTrajectory,
    ScenarioCalibrationFixture,
)

# Importing built-ins populates the allowlisted registry used by the calibration cases.
import app.scenarios  # noqa: F401


def _observed_dimensions(fixture: LabeledTrajectory) -> set[str]:
    return set(analyze_trajectory(fixture.trajectory).dimensions)


def test_labeled_failure_fixture_set_has_no_dimension_false_positives_or_negatives() -> None:
    false_positives: list[tuple[str, str]] = []
    false_negatives: list[tuple[str, str]] = []

    for fixture in FAILURE_ANALYZER_FIXTURES:
        observed = _observed_dimensions(fixture)
        for dimension in observed - fixture.expected_dimensions:
            false_positives.append((fixture.name, dimension))
        for dimension in fixture.expected_dimensions - observed:
            false_negatives.append((fixture.name, dimension))

    assert not false_positives and not false_negatives, (
        f"false_positives={false_positives}; false_negatives={false_negatives}"
    )


@pytest.mark.parametrize(
    "fixture",
    FAILURE_ANALYZER_FIXTURES,
    ids=lambda fixture: fixture.name,
)
def test_labeled_failure_fixture_classifications_are_explainable(
    fixture: LabeledTrajectory,
) -> None:
    report = analyze_trajectory(fixture.trajectory)
    observed = {
        evidence.failure_type
        for evidence in report.evidence
        if evidence.failure_type is not None
    }

    assert fixture.expected_classifications <= observed, {
        "fixture": fixture.name,
        "expected": sorted(fixture.expected_classifications),
        "observed": sorted(observed),
        "evidence": [asdict(item) for item in report.evidence],
    }

    if not fixture.expected_classifications:
        assert not observed, {
            "fixture": fixture.name,
            "unexpected_classifications": sorted(observed),
        }


def test_score_penalizes_http_failure_but_not_success_status_control() -> None:
    http_failure = next(
        fixture
        for fixture in FAILURE_ANALYZER_FIXTURES
        if fixture.name == "http_503_execution_failure"
    )
    success_control = next(
        fixture
        for fixture in FAILURE_ANALYZER_FIXTURES
        if fixture.name == "http_200_service_available_control"
    )

    failure_score = compute_score(
        http_failure.trajectory,
        weights={"cost": 0.0, "latency": 0.0, "tool_failure": 0.5},
    )
    control_score = compute_score(
        success_control.trajectory,
        weights={"cost": 0.0, "latency": 0.0, "tool_failure": 0.5},
    )

    assert failure_score["breakdown"]["tool_failure_penalty"] == pytest.approx(-0.5)
    assert control_score["breakdown"]["tool_failure_penalty"] == pytest.approx(0.0)


def _run_fixture_trajectory(scenario_id: str, *, replay: bool) -> dict:
    entry = require_scenario(scenario_id)
    policy = entry.handler.candidate_policy_patch() if replay else None
    events: list[tuple[str, dict]] = []
    spec = EvaluationSpec(
        run_id=f"calibration-{scenario_id}-{'replay' if replay else 'baseline'}",
        experiment_id="calibration-fixture",
        scenario_id=scenario_id,
        task="calibration",
        policy=policy,
    )
    exit_code = entry.handler.run_fixture(spec, lambda event_type, payload: events.append((event_type, payload)))
    steps = [
        {
            "index": payload["index"],
            "action": payload["tool_call"],
            "observation": payload["observation"],
            "latency_ms": payload["latency_ms"],
            "context_window": payload["context_window"],
            "token_prompt": payload["token_prompt"],
            "token_completion": payload["token_completion"],
        }
        for event_type, payload in events
        if event_type == "step_completed"
    ]
    return {
        "status": "success" if exit_code == 0 else "failed",
        "run_status": "succeeded" if exit_code == 0 else "failed",
        "max_steps": 6,
        "steps": steps,
        "total_tokens": sum(
            item["token_prompt"] + item["token_completion"] for item in steps
        ),
        "total_latency_ms": sum(item["latency_ms"] for item in steps),
        "provider_error": False,
    }


@pytest.mark.parametrize(
    "fixture",
    SCENARIO_CALIBRATION_FIXTURES,
    ids=lambda fixture: fixture.scenario_id,
)
def test_builtin_scenario_baseline_and_replay_scores_are_calibrated(
    fixture: ScenarioCalibrationFixture,
) -> None:
    suite = assertion_suite_for_scenario(fixture.scenario_id)
    baseline = _run_fixture_trajectory(fixture.scenario_id, replay=False)
    replay = _run_fixture_trajectory(fixture.scenario_id, replay=True)

    baseline_report = analyze_trajectory(baseline, assertion_suite=suite)
    replay_report = analyze_trajectory(replay, assertion_suite=suite)
    baseline_score = compute_score(baseline, assertion_suite=suite)
    replay_score = compute_score(replay, assertion_suite=suite)

    assert set(baseline_report.dimensions) == set(fixture.expected_baseline_dimensions)
    assert replay_report.dimensions == {}
    assert replay_score["assertions"]["aggregate"] == pytest.approx(1.0)
    assert baseline_score["assertions"]["aggregate"] < replay_score["assertions"]["aggregate"]
    assert replay_score["score"] > baseline_score["score"] + 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture",
    SCENARIO_CALIBRATION_FIXTURES,
    ids=lambda fixture: fixture.scenario_id,
)
async def test_builtin_scenario_calibration_survives_run_event_round_trip(
    api, fixture: ScenarioCalibrationFixture
) -> None:
    client, factory = api
    entry = require_scenario(fixture.scenario_id)
    auth = {"Authorization": "Bearer test-runner-token"}
    created = await client.post(
        "/api/experiments",
        json={
            "name": "Persisted calibration",
            "task": entry.metadata.default_task,
            "scenario_id": fixture.scenario_id,
            "scenario_params": {
                param.name: "calibration" for param in entry.metadata.params if param.required
            },
        },
    )
    assert created.status_code == 201, created.text
    experiment_id = created.json()["id"]
    baseline = await client.post(f"/api/experiments/{experiment_id}/runs", json={})
    assert baseline.status_code == 201, baseline.text

    scores: list[float] = []
    for replay in (False, True):
        if replay:
            candidate = (await client.get(f"/api/experiments/{experiment_id}")).json()[
                "candidate_policy"
            ]
            queued = await client.post(f"/api/policies/{candidate['id']}/replay")
            assert queued.status_code == 201, queued.text

        claimed = await client.post(
            "/api/internal/runner/jobs/claim",
            headers=auth,
            json={"runner_id": "calibration-runner"},
        )
        assert claimed.status_code == 200, claimed.text
        claim = claimed.json()
        run_id = claim["run"]["run_id"]
        spec = EvaluationSpec.model_validate(claim["run"]["evaluation_spec"])
        assert (spec.policy is not None) is replay

        emitted: list[tuple[str, dict]] = []
        exit_code = entry.handler.run_fixture(
            spec, lambda event_type, payload: emitted.append((event_type, payload))
        )
        assert (exit_code == 0) is replay
        event_pairs = [("run_started", {"attempt": claim["attempt"]}), *emitted]
        event_pairs.append(("run_completed" if replay else "run_failed", {"exit_code": exit_code}))
        events = [
            {
                "schema_version": 1,
                "run_id": run_id,
                "sequence": index,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "payload": payload,
            }
            for index, (event_type, payload) in enumerate(event_pairs, start=1)
        ]
        uploaded = await client.post(
            f"/api/internal/runner/runs/{run_id}/events",
            headers=auth,
            json={
                "runner_id": "calibration-runner",
                "lease_id": claim["lease_id"],
                "events": events,
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["accepted_through"] == len(events)
        completed = await client.post(
            f"/api/internal/runner/jobs/{claim['lease_id']}/complete",
            headers=auth,
            json={
                "runner_id": "calibration-runner",
                "status": "succeeded" if replay else "failed",
            },
        )
        assert completed.status_code == 200, completed.text
        scores.append(completed.json()["score"])

        async with factory() as db:
            persisted = (
                await db.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.sequence)
                )
            ).scalars().all()
            assert [event.sequence for event in persisted] == list(range(1, len(events) + 1))
            run = await db.get(Run, run_id)
            assert run is not None
            trajectory, metrics = await _trajectory_from_events(db, run)
            analysis = await db.get(RunAnalysis, run_id)
            assert analysis is not None
            expected = set() if replay else set(fixture.expected_baseline_dimensions)
            assert set(analysis.dimensions) == expected
            assert [step["observation"] for step in trajectory["steps"]] == [
                payload["observation"]
                for event_type, payload in emitted
                if event_type == "step_completed"
            ]
            assert metrics["steps"] == len(trajectory["steps"])
            assert run.metrics["total_tokens"] == metrics["total_tokens"]
            assert run.score == pytest.approx(
                compute_score(
                    trajectory, assertion_suite=assertion_suite_for_scenario(fixture.scenario_id)
                )["score"]
            )

    assert scores[1] > scores[0] + 0.5
