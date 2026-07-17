use agentops_protocol::{EvaluationSpec, EventEnvelope};

#[test]
fn python_golden_evaluation_spec_matches_rust_contract() {
    let fixture = include_str!("../../../../contracts/v1/fixtures/evaluation-spec.baseline.json");
    let spec: EvaluationSpec = serde_json::from_str(fixture).unwrap();
    spec.validate().unwrap();
}

#[test]
fn python_golden_event_envelope_matches_rust_contract() {
    let fixture =
        include_str!("../../../../contracts/v1/fixtures/event-envelope.step-completed.json");
    let event: EventEnvelope = serde_json::from_str(fixture).unwrap();
    event.validate().unwrap();
}
