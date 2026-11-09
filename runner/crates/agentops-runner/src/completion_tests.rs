//! Production dispatch and authoritative completion-ACK regressions.
//! The local CP double controls ACK bytes; no database or model is used.
//! ADR-0002 fencing and ADR-0012 token retention must survive HTTP success.
//! See supervisor_tests.rs for the bounded process and HTTP fixtures.

use super::*;
use crate::{ExecutionFacts, MAX_COMPLETION_RESPONSE_BYTES};

#[tokio::test]
async fn malformed_or_contradictory_http_success_retains_capability() {
    let valid = completion_ack("failed", 1);
    let mut invalid = vec!["{}".to_string(), "not-json".to_string()];
    for (pointer, value) in [
        ("/run_id", json!("other-run")),
        ("/attempt", json!(2)),
        ("/attempt", json!(true)),
        ("/attempt", json!(1.0)),
        ("/terminal_sequence", json!(0)),
        ("/terminal_sequence", json!(true)),
        ("/status", json!("running")),
        ("/run/id", json!("other-run")),
        ("/run/status", json!("succeeded")),
        ("/run/evaluation_spec/run_id", json!("other-run")),
    ] {
        let mut ack = valid.clone();
        *ack.pointer_mut(pointer).unwrap() = value;
        invalid.push(ack.to_string());
    }
    // ADR-0012: a valid HTTP status, even with an unbounded chunked body,
    // cannot authorize removal of the only recovery capability.
    invalid.push("x".repeat(MAX_COMPLETION_RESPONSE_BYTES + 1));
    for (index, body) in invalid.into_iter().enumerate() {
        let server = TestServer::start("continue", 0).await;
        *server.state.completion_body.lock().await = Some(body);
        server
            .state
            .chunked_completion
            .store(true, Ordering::SeqCst);
        let runner = worker(&server, "pass".into());
        let claimed = claim(10_000);
        let token = runner.environment_token_handoff.acquire("run-1").unwrap();
        let original = token.token.clone();
        drop(token);
        let result = runner
            .complete_facts(&claimed, ExecutionFacts::internal(&claimed))
            .await;
        assert!(result.is_err(), "invalid ACK {index} was accepted");
        let retained = runner.environment_token_handoff.acquire("run-1").unwrap();
        assert_eq!(
            retained.token, original,
            "invalid ACK {index} rotated the capability"
        );
        drop(retained);
        runner
            .cleanup_environment_token_after_terminal_ack("run-1")
            .unwrap();
    }
}

#[tokio::test]
async fn authoritative_cp_failure_ack_cleans_token_despite_eligible_attempt() {
    let server = TestServer::start("continue", 0).await;
    *server.state.completion_body.lock().await = Some(completion_ack("failed", 1).to_string());
    let runner = worker(&server, "pass".into());
    let claimed = claim(10_000);
    let token = runner.environment_token_handoff.acquire("run-1").unwrap();
    drop(token);
    let mut facts = ExecutionFacts::new(&claimed);
    facts.eligible = true;
    facts.submitted = true;
    facts.check_succeeded = true;
    facts.planned_environment_teardown = true;
    // CP may reject the assertion aggregate even when execution is eligible.
    runner.complete_facts(&claimed, facts).await.unwrap();
    assert!(!runner
        .cleanup_environment_token_after_terminal_ack("run-1")
        .unwrap());
    assert_eq!(server.completion().await["status"], "succeeded");
}

#[tokio::test]
async fn production_dispatch_uses_split_supervision_and_never_sends_terminal_event() {
    let server = TestServer::start("continue", 0).await;
    let mut runner = worker(&server, "pass".into());
    runner.legacy_agent_command = None;
    runner.config.agent_override = Some(TrustedCommand::new(
        "/usr/bin/python3",
        vec![
            "-c",
            r#"
import json, sys, time
json.loads(sys.stdin.readline())
print('{"type":"step_completed","payload":{"forged":"agent-only"}}', flush=True)
time.sleep(0.1)
"#,
        ],
    ));
    runner.config.environment_args = vec!["-c".into(), r#"
import json, sys, time
spec = json.loads(sys.stdin.readline())
print(json.dumps({"type":"env_ready","payload":{"mcp_url":"http://127.0.0.1:43123/mcp"}}), flush=True)
print(json.dumps({"type":"step_completed","payload":{"tool_call":{"name":"submit_result","arguments":{}},"environment":{"kind":"control","control":"submit_result","result":"ok","finalized":True,"check_called":True,"final_check":{"scenario_id":spec["scenario_id"],"final_observation":None}}}}), flush=True)
time.sleep(30)
"#.into()];
    runner.execute_claim(claim(10_000)).await.unwrap();
    let completion = server.completion().await;
    assert_eq!(completion["execution"]["run_id"], "run-1");
    assert_eq!(completion["execution"]["attempt"], 1);
    assert_eq!(completion["execution"]["submitted"], true);
    assert_eq!(completion["execution"]["failure"], Value::Null);
    assert!(server.terminal_events().await.is_empty());
    let events = server.state.events.lock().await;
    let encoded = serde_json::to_string(&*events).unwrap();
    assert!(encoded.contains("final_check"));
    assert!(!encoded.contains("agent-only"));
    assert!(!runner
        .cleanup_environment_token_after_terminal_ack("run-1")
        .unwrap());
}

#[tokio::test]
async fn explicit_unknown_agent_never_falls_back_to_operator_override() {
    let server = TestServer::start("continue", 0).await;
    let marker = pid_file("unknown-agent-override");
    let mut runner = worker(&server, "pass".into());
    runner.legacy_agent_command = None;
    runner.config.agent_override = Some(TrustedCommand::new(
        "/usr/bin/python3",
        vec![
            "-c".to_string(),
            format!("open({marker:?}, 'w').write('unexpected')"),
        ],
    ));
    let mut claimed = claim(10_000);
    claimed.run.evaluation_spec.agent_id = Some("not-registered.v1".into());
    runner.execute_claim(claimed).await.unwrap();
    assert!(!marker.exists());
    assert!(server.state.events.lock().await.is_empty());
    assert_eq!(
        server.completion().await["execution"]["failure"],
        "internal"
    );
}

#[tokio::test]
async fn completion_carries_agent_error_code_only_when_present() {
    for code in [Some("PI_PROVIDER_ERROR"), None] {
        let server = TestServer::start("continue", 0).await;
        let runner = worker(&server, "pass".into());
        let claimed = claim(10_000);
        let mut facts = ExecutionFacts::new(&claimed);
        facts.failure = Some(ExecutionFailure::AgentExit);
        facts.agent_error_code = code;
        runner.complete_facts(&claimed, facts).await.unwrap();

        let execution = &server.completion().await["execution"];
        match code {
            Some(code) => assert_eq!(execution["agent_error_code"], code),
            None => assert!(execution.get("agent_error_code").is_none()),
        }
    }
}

#[tokio::test]
async fn unparseable_claim_is_completed_as_internal_failure_not_leaked() {
    // The server already leased the Run; a strict-parse failure must close the
    // lease out (ADR-0002 fencing) instead of leaving it to expire.
    let server = TestServer::start("continue", 0).await;
    // Unknown field makes the strict ClaimResponse parse fail.
    *server.state.claim_body.lock().await = Some(
        json!({
            "lease_id": "lease-1",
            "lease_expires_at": "2026-07-16T00:00:15Z",
            "attempt": 1,
            "next_sequence": 1,
            "recovery_reason": null,
            "surprise_field": true,
            "run": {"run_id": "run-1", "evaluation_spec": {"UNIQUE_UNTRUSTED": "x"}}
        })
        .to_string(),
    );
    *server.state.completion_body.lock().await = Some(completion_ack("failed", 1).to_string());
    let runner = worker(&server, "pass".into());
    assert!(runner.claim().await.unwrap().is_none());
    let completion = server.completion().await;
    assert_eq!(completion["status"], "failed");
    assert_eq!(completion["failure_kind"], "internal_failure");
    assert_eq!(completion["execution"]["run_id"], "run-1");
    assert_eq!(completion["execution"]["attempt"], 1);
    assert_eq!(completion["execution"]["failure"], "internal");
    assert_eq!(completion["execution"]["eligible"], false);
    assert!(!completion.to_string().contains("UNIQUE_UNTRUSTED"));
}

#[tokio::test]
async fn claim_without_recoverable_identity_errors_and_completes_nothing() {
    for body in [
        "not-json".to_string(),
        json!({"lease_id": "l", "attempt": 0, "run": {"run_id": "run-1"}}).to_string(),
        json!({"attempt": 1, "run": {"run_id": "run-1"}}).to_string(),
    ] {
        let server = TestServer::start("continue", 0).await;
        *server.state.claim_body.lock().await = Some(body);
        let runner = worker(&server, "pass".into());
        assert!(runner.claim().await.is_err());
        assert!(server.state.completed.lock().await.is_empty());
    }
}

#[tokio::test]
async fn valid_claim_still_parses_after_loose_fallback_was_added() {
    let server = TestServer::start("continue", 0).await;
    *server.state.claim_body.lock().await =
        Some(serde_json::to_string(&serde_json::to_value(claim_json()).unwrap()).unwrap());
    let runner = worker(&server, "pass".into());
    let claimed = runner.claim().await.unwrap().expect("claim");
    assert_eq!(claimed.run.run_id, "run-1");
    assert!(server.state.completed.lock().await.is_empty());
}

fn claim_json() -> Value {
    let claimed = claim(10_000);
    json!({
        "lease_id": claimed.lease_id,
        "lease_expires_at": claimed.lease_expires_at,
        "attempt": claimed.attempt,
        "next_sequence": claimed.next_sequence,
        "recovery_reason": null,
        "run": {"run_id": claimed.run.run_id, "evaluation_spec": claimed.run.evaluation_spec},
    })
}
