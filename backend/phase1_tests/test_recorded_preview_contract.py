from __future__ import annotations

import json
from pathlib import Path

from app.phase1_schemas import (
    AnalysisResponse,
    EventEnvelope,
    ExperimentResponse,
    RunStatus,
)


ROOT = Path(__file__).resolve().parents[2]
RECORDED_JOURNEY = (
    ROOT / "frontend" / "src" / "services" / "recorded" / "goldenJourney.json"
)


def test_recorded_preview_matches_current_pydantic_contract() -> None:
    journey = json.loads(RECORDED_JOURNEY.read_text(encoding="utf-8"))
    experiment = ExperimentResponse.model_validate(journey["experiment"])
    ExperimentResponse.model_validate(journey["secondary_experiment"])
    baseline_analysis = AnalysisResponse.model_validate(journey["baseline_analysis"])
    replay_analysis = AnalysisResponse.model_validate(journey["replay_analysis"])

    baseline = next(run for run in experiment.runs if run.kind == "baseline")
    replay = next(run for run in experiment.runs if run.kind == "replay")
    assert baseline.status == RunStatus.FAILED
    assert replay.status == RunStatus.SUCCEEDED
    assert baseline_analysis.run_id == baseline.id
    assert replay_analysis.run_id == replay.id
    assert experiment.candidate_policy is not None
    assert experiment.candidate_policy.replay_run_id == replay.id

    for key in ("experiment_id", "scenario_id", "task", "seed", "limits"):
        assert getattr(baseline.evaluation_spec, key) == getattr(
            replay.evaluation_spec, key
        )
    assert baseline.evaluation_spec.policy is None
    assert replay.evaluation_spec.policy == experiment.candidate_policy.patch

    parsed_events = {
        run_id: [EventEnvelope.model_validate(event) for event in events]
        for run_id, events in journey["events"].items()
    }
    assert set(parsed_events) == {baseline.id, replay.id}
    for run_id, events in parsed_events.items():
        assert all(event.run_id == run_id for event in events)
        assert [event.sequence for event in events] == list(
            range(1, len(events) + 1)
        )
