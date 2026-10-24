use agentops_protocol::{ChildEvent, EvaluationSpec, EventEnvelope};
use serde_json::json;

#[test]
fn python_golden_evaluation_spec_matches_rust_contract() {
    let fixture = include_str!("../../../../contracts/v1/fixtures/evaluation-spec.baseline.json");
    let spec: EvaluationSpec = serde_json::from_str(fixture).unwrap();
    spec.validate().unwrap();

    let multi_step =
        include_str!("../../../../contracts/v1/fixtures/evaluation-spec.multi-step-research.json");
    let spec: EvaluationSpec = serde_json::from_str(multi_step).unwrap();
    spec.validate().unwrap();
}

#[test]
fn order_support_fixture_and_tool_events_preserve_transport_contract() {
    let fixture =
        include_str!("../../../../contracts/v1/fixtures/evaluation-spec.order-support.json");
    let spec: EvaluationSpec = serde_json::from_str(fixture).unwrap();
    spec.validate().unwrap();
    assert_eq!(spec.scenario_id, "order-support.v1");
    // H1-6 uses an instruction-only policy; existing policy allowlists stay fixed.
    let mut replay = spec.clone();
    replay.policy = Some(agentops_protocol::PolicyPatch {
        instruction_patch: vec!["Verify exactly one refund in the final ledger.".into()],
        tool_priority: Default::default(),
        max_steps: 6,
    });
    replay.validate().unwrap();
    for name in [
        "inspect_order",
        "authorize_payment",
        "ship_order",
        "request_refund",
    ] {
        let mut arguments = json!({"order_id": "order-1001"});
        if name == "request_refund" {
            arguments["reason"] = json!("shipping_failed");
        }
        let payload = json!({
            "index": 0,
            "tool_call": {"name": name, "arguments": arguments},
            "observation": "{\"order\":{\"refund_count\":1}}",
            "latency_ms": 20
        });
        // Rust transports JSON; the Python Control Plane owns normalization.
        let child: ChildEvent = serde_json::from_value(json!({
            "type": "step_completed", "payload": payload
        }))
        .unwrap();
        child.validate().unwrap();
        assert_eq!(child.payload, payload);
        let envelope = EventEnvelope {
            schema_version: 1,
            run_id: spec.run_id.clone(),
            sequence: 1,
            occurred_at: "2026-10-22T00:00:00Z".into(),
            event_type: child.event_type,
            payload: child.payload,
        };
        envelope.validate().unwrap();
        let encoded = serde_json::to_string(&envelope).unwrap();
        let decoded: EventEnvelope = serde_json::from_str(&encoded).unwrap();
        assert_eq!(decoded.payload, payload);
    }
}

#[test]
fn python_golden_event_envelope_matches_rust_contract() {
    let fixture =
        include_str!("../../../../contracts/v1/fixtures/event-envelope.step-completed.json");
    let event: EventEnvelope = serde_json::from_str(fixture).unwrap();
    event.validate().unwrap();
}
