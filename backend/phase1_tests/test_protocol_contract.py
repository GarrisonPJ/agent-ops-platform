from __future__ import annotations

import json
from pathlib import Path

from app.phase1_schemas import EvaluationSpec, EventEnvelope


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIRECTORY = ROOT / "contracts" / "v1"


def test_golden_runner_fixtures_match_python_contract() -> None:
    EvaluationSpec.model_validate_json(
        (CONTRACT_DIRECTORY / "fixtures" / "evaluation-spec.baseline.json").read_text(
            encoding="utf-8"
        )
    )
    EventEnvelope.model_validate_json(
        (
            CONTRACT_DIRECTORY / "fixtures" / "event-envelope.step-completed.json"
        ).read_text(encoding="utf-8")
    )


def test_exported_json_schemas_are_current() -> None:
    expected = {
        "evaluation-spec.schema.json": EvaluationSpec.model_json_schema(),
        "event-envelope.schema.json": EventEnvelope.model_json_schema(),
    }
    for filename, schema in expected.items():
        exported = json.loads((CONTRACT_DIRECTORY / filename).read_text(encoding="utf-8"))
        assert exported == schema, f"{filename} is stale; run make contracts"
