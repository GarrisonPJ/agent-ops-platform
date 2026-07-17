"""Export the Python-owned runner protocol as versioned JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

from app.phase1_schemas import EvaluationSpec, EventEnvelope


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIRECTORY = ROOT / "contracts" / "v1"
SCHEMAS = {
    "evaluation-spec.schema.json": EvaluationSpec.model_json_schema(),
    "event-envelope.schema.json": EventEnvelope.model_json_schema(),
}


def main() -> None:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, schema in SCHEMAS.items():
        target = OUTPUT_DIRECTORY / filename
        target.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
