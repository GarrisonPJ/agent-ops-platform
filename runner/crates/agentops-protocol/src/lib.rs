use std::collections::{BTreeMap, HashMap};

use chrono::DateTime;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const SCHEMA_VERSION: u16 = 1;

/// Local stdout-only readiness message; this is deliberately not a RunEvent.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentReadyMessage {
    #[serde(rename = "type")]
    pub message_type: String,
    pub payload: EnvironmentReadyPayload,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentReadyPayload {
    pub mcp_url: String,
}

impl EnvironmentReadyMessage {
    pub fn validate(&self) -> Result<(), String> {
        if self.message_type != "env_ready" {
            return Err("local message is not env_ready".into());
        }
        validate_loopback_mcp_url(&self.payload.mcp_url)
    }
}

/// Validate the exact loopback endpoint emitted by the Environment server.
pub fn validate_loopback_mcp_url(value: &str) -> Result<(), String> {
    let prefix = "http://127.0.0.1:";
    let rest = value
        .strip_prefix(prefix)
        .ok_or_else(|| "mcp_url must use http://127.0.0.1".to_string())?;
    let port = rest
        .strip_suffix("/mcp")
        .ok_or_else(|| "mcp_url path must be /mcp".to_string())?;
    if port.is_empty() || !port.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("mcp_url port must be a non-zero decimal port".into());
    }
    let port = port
        .parse::<u16>()
        .map_err(|_| "mcp_url port is invalid".to_string())?;
    if port == 0 {
        return Err("mcp_url port must be non-zero".into());
    }
    Ok(())
}

const MAX_SCENARIO_PARAM_KEYS: usize = 20;
const MAX_SCENARIO_PARAM_VALUE_LENGTH: usize = 200;

fn is_valid_scenario_id(value: &str) -> bool {
    let Some((base, version)) = value.rsplit_once(".v") else {
        return false;
    };
    if base.is_empty() || version.is_empty() || value.len() > 64 {
        return false;
    }
    if !version.chars().all(|ch| ch.is_ascii_digit()) {
        return false;
    }
    let bytes = base.as_bytes();
    if bytes.is_empty() {
        return false;
    }
    if !bytes[0].is_ascii_lowercase() && !bytes[0].is_ascii_digit() {
        return false;
    }
    bytes[1..]
        .iter()
        .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-')
}

fn is_valid_scenario_param_key(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() > 64 {
        return false;
    }
    if !bytes[0].is_ascii_lowercase() && !bytes[0].is_ascii_digit() {
        return false;
    }
    bytes[1..]
        .iter()
        .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-')
}

const ALLOWED_TOOLS: [&str; 9] = [
    "check_service_health",
    "query_service_metrics",
    "fetch_service_logs",
    "search_documents",
    "fetch_document",
    "submit_answer",
    "invoke_service",
    "retry_call",
    "degrade_route",
];
const ALLOWED_EVENT_TYPES: [&str; 6] = [
    "run_started",
    "step_completed",
    "process_output",
    "run_completed",
    "run_failed",
    "run_cancelled",
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PolicyPatch {
    #[serde(default)]
    pub instruction_patch: Vec<String>,
    #[serde(default)]
    pub tool_priority: BTreeMap<String, f64>,
    pub max_steps: u32,
}

impl PolicyPatch {
    pub fn validate(&self) -> Result<(), String> {
        if self.instruction_patch.len() > 10 {
            return Err("instruction_patch may contain at most 10 entries".into());
        }
        if self
            .instruction_patch
            .iter()
            .any(|item| item.is_empty() || item.chars().count() > 500)
        {
            return Err("instruction_patch entries must contain 1 to 500 characters".into());
        }
        if !(3..=20).contains(&self.max_steps) {
            return Err("max_steps must be between 3 and 20".into());
        }
        for (tool, priority) in &self.tool_priority {
            if !ALLOWED_TOOLS.contains(&tool.as_str()) {
                return Err(format!("tool '{tool}' is not allowlisted"));
            }
            if !(0.0..=1.0).contains(priority) {
                return Err(format!("priority for '{tool}' must be between 0 and 1"));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExecutionLimits {
    pub timeout_ms: u64,
    pub max_output_bytes: usize,
}

impl Default for ExecutionLimits {
    fn default() -> Self {
        Self {
            timeout_ms: 60_000,
            max_output_bytes: 1_048_576,
        }
    }
}

impl ExecutionLimits {
    pub fn validate(&self) -> Result<(), String> {
        if !(1_000..=300_000).contains(&self.timeout_ms) {
            return Err("timeout_ms must be between 1000 and 300000".into());
        }
        if !(1_024..=10_485_760).contains(&self.max_output_bytes) {
            return Err("max_output_bytes must be between 1024 and 10485760".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionMode {
    #[default]
    Fixture,
    Provider,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EvaluationSpec {
    pub schema_version: u16,
    pub run_id: String,
    pub experiment_id: String,
    pub scenario_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    pub task: String,
    pub seed: u64,
    #[serde(default)]
    pub execution_mode: ExecutionMode,
    pub policy: Option<PolicyPatch>,
    #[serde(default)]
    pub limits: ExecutionLimits,
    #[serde(default)]
    pub scenario_params: HashMap<String, String>,
}

impl EvaluationSpec {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(format!(
                "unsupported schema_version {}; expected {}",
                self.schema_version, SCHEMA_VERSION
            ));
        }
        if self.run_id.trim().is_empty() || self.experiment_id.trim().is_empty() {
            return Err("run_id and experiment_id are required".into());
        }
        if !is_valid_scenario_id(&self.scenario_id) {
            return Err(format!(
                "scenario '{}' does not match the allowlisted id pattern",
                self.scenario_id
            ));
        }
        if let Some(agent_id) = &self.agent_id {
            // Agent identifiers share the Python registry grammar, whose base
            // has at least two ASCII characters. Do not change legacy scenario IDs.
            if !is_valid_scenario_id(agent_id)
                || agent_id
                    .rsplit_once(".v")
                    .is_none_or(|(base, _)| base.len() < 2)
            {
                return Err(format!(
                    "agent '{}' does not match the allowlisted id pattern",
                    agent_id
                ));
            }
        }
        if self.scenario_params.len() > MAX_SCENARIO_PARAM_KEYS {
            return Err(format!(
                "scenario_params may contain at most {MAX_SCENARIO_PARAM_KEYS} keys"
            ));
        }
        for (key, value) in &self.scenario_params {
            if !is_valid_scenario_param_key(key) {
                return Err("scenario_params keys must match the param key pattern".into());
            }
            if value.is_empty() || value.chars().count() > MAX_SCENARIO_PARAM_VALUE_LENGTH {
                return Err(format!(
                    "scenario_params values must be 1 to {MAX_SCENARIO_PARAM_VALUE_LENGTH} characters"
                ));
            }
        }
        if self.task.is_empty() || self.task.chars().count() > 4_000 {
            return Err("task must contain 1 to 4000 characters".into());
        }
        if self.seed > i32::MAX as u64 {
            return Err("seed must be between 0 and 2147483647".into());
        }
        self.limits.validate()?;
        if let Some(policy) = &self.policy {
            policy.validate()?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ChildEvent {
    #[serde(rename = "type")]
    pub event_type: String,
    #[serde(default)]
    pub payload: Value,
}

impl ChildEvent {
    pub fn validate(&self) -> Result<(), String> {
        if !matches!(
            self.event_type.as_str(),
            "step_completed" | "process_output"
        ) {
            return Err(format!(
                "child event type '{}' is not allowed",
                self.event_type
            ));
        }
        if !self.payload.is_object() {
            return Err("child event payload must be an object".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EventEnvelope {
    pub schema_version: u16,
    pub run_id: String,
    pub sequence: u64,
    pub occurred_at: String,
    #[serde(rename = "type")]
    pub event_type: String,
    #[serde(default)]
    pub payload: Value,
}

impl EventEnvelope {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(format!(
                "unsupported schema_version {}; expected {}",
                self.schema_version, SCHEMA_VERSION
            ));
        }
        if self.run_id.trim().is_empty() {
            return Err("run_id is required".into());
        }
        if self.sequence == 0 {
            return Err("sequence must be greater than zero".into());
        }
        DateTime::parse_from_rfc3339(&self.occurred_at)
            .map_err(|_| "occurred_at must be RFC3339".to_string())?;
        if !ALLOWED_EVENT_TYPES.contains(&self.event_type.as_str()) {
            return Err(format!("event type '{}' is not allowed", self.event_type));
        }
        if !self.payload.is_object() {
            return Err("event payload must be an object".into());
        }
        Ok(())
    }
}

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ClaimRequest<'a> {
    pub runner_id: &'a str,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClaimedRun {
    pub run_id: String,
    pub evaluation_spec: EvaluationSpec,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClaimResponse {
    pub lease_id: String,
    pub lease_expires_at: String,
    pub attempt: u32,
    pub next_sequence: u64,
    pub recovery_reason: Option<String>,
    pub run: ClaimedRun,
}

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HeartbeatRequest<'a> {
    pub runner_id: &'a str,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HeartbeatResponse {
    pub command: RunnerCommand,
    pub lease_expires_at: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RunnerCommand {
    Continue,
    Cancel,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TerminalFailureKind {
    Cancelled,
    TimedOut,
    OutputLimitExceeded,
    AgentExit,
    ProviderFailure,
    InternalFailure,
}

/// Controlled execution facts supplied by the Runner to the Control Plane.
/// These are observations, not a terminal-status or scoring authority.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionFailure {
    InvalidEnvironmentReady,
    EnvironmentExit,
    AgentExit,
    MissingSubmission,
    Cancelled,
    TimedOut,
    OutputLimitExceeded,
    LeaseRejected,
    Internal,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ExecutionFacts<'a> {
    pub run_id: &'a str,
    pub attempt: u32,
    pub eligible: bool,
    pub submitted: bool,
    pub check_succeeded: bool,
    pub planned_environment_teardown: bool,
    pub failure: Option<ExecutionFailure>,
}

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EventBatchRequest<'a> {
    pub runner_id: &'a str,
    pub lease_id: &'a str,
    pub events: &'a [EventEnvelope],
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventBatchResponse {
    pub accepted_through: u64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompleteRequest<'a> {
    pub runner_id: &'a str,
    pub status: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failure_kind: Option<TerminalFailureKind>,
    pub metrics: Value,
    pub execution: ExecutionFacts<'a>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CompletionStatus {
    Succeeded,
    Failed,
    Cancelled,
    TimedOut,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RunResponse {
    pub id: String,
    pub experiment_id: String,
    pub kind: String,
    pub source_run_id: Option<String>,
    pub policy_id: Option<String>,
    pub regression_case_id: Option<String>,
    #[serde(default)]
    pub trial_group_id: Option<String>,
    #[serde(default)]
    pub trial_index: Option<u32>,
    pub status: CompletionStatus,
    pub score: Option<f64>,
    pub metrics: Value,
    pub evaluation_spec: EvaluationSpec,
    pub error: Option<String>,
    pub queued_at: String,
    pub started_at: Option<String>,
    pub completed_at: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CompletionResponse {
    pub run_id: String,
    pub attempt: u32,
    pub terminal_sequence: u64,
    pub status: CompletionStatus,
    pub run: RunResponse,
}

impl CompletionResponse {
    pub fn validate_for(&self, expected_run_id: &str, expected_attempt: u32) -> Result<(), String> {
        if expected_run_id.trim().is_empty() || !(1..=100).contains(&expected_attempt) {
            return Err("completion expectation is invalid".into());
        }
        if self.run_id != expected_run_id {
            return Err("completion run_id does not match the claimed Run".into());
        }
        if self.attempt != expected_attempt {
            return Err("completion attempt does not match the claimed Attempt".into());
        }
        if self.terminal_sequence == 0 {
            return Err("completion terminal_sequence must be positive".into());
        }
        if self.run.id != self.run_id {
            return Err("completion Run identity does not match run_id".into());
        }
        if self.run.status != self.status {
            return Err("completion status does not match Run status".into());
        }
        if !matches!(self.run.kind.as_str(), "baseline" | "replay") {
            return Err("completion Run kind is invalid".into());
        }
        if !self.run.metrics.is_object() {
            return Err("completion Run metrics must be an object".into());
        }
        self.run.evaluation_spec.validate()?;
        if self.run.evaluation_spec.run_id != self.run_id {
            return Err("completion EvaluationSpec identity does not match Run".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn spec() -> EvaluationSpec {
        EvaluationSpec {
            schema_version: SCHEMA_VERSION,
            run_id: "run-1".into(),
            experiment_id: "experiment-1".into(),
            scenario_id: "checkout-api-latency.v2".into(),
            agent_id: None,
            task: "Investigate checkout latency".into(),
            seed: 42,
            execution_mode: ExecutionMode::Fixture,
            policy: None,
            limits: ExecutionLimits::default(),
            scenario_params: HashMap::new(),
        }
    }

    fn completion_json(extra: Value) -> Value {
        let mut run = json!({
            "id": "run-1",
            "experiment_id": "experiment-1",
            "kind": "baseline",
            "source_run_id": null,
            "policy_id": null,
            "regression_case_id": null,
            "status": "succeeded",
            "score": null,
            "metrics": {},
            "evaluation_spec": serde_json::to_value(spec()).unwrap(),
            "error": null,
            "queued_at": "2026-07-16T00:00:00Z",
            "started_at": null,
            "completed_at": null
        });
        run.as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        json!({
            "run_id": "run-1",
            "attempt": 1,
            "terminal_sequence": 2,
            "status": "succeeded",
            "run": run
        })
    }

    #[test]
    fn completion_response_accepts_trial_fields() {
        let raw = completion_json(json!({"trial_group_id": "tg-1", "trial_index": 3}));
        let parsed: CompletionResponse = serde_json::from_value(raw).unwrap();
        assert_eq!(parsed.run.trial_group_id.as_deref(), Some("tg-1"));
        assert_eq!(parsed.run.trial_index, Some(3));
    }

    #[test]
    fn completion_response_accepts_null_or_missing_trial_fields() {
        let nulls = completion_json(json!({"trial_group_id": null, "trial_index": null}));
        let parsed: CompletionResponse = serde_json::from_value(nulls).unwrap();
        assert_eq!(parsed.run.trial_group_id, None);
        let missing: CompletionResponse =
            serde_json::from_value(completion_json(json!({}))).unwrap();
        assert_eq!(missing.run.trial_index, None);
    }

    #[test]
    fn evaluation_spec_round_trips() {
        let original = spec();
        let encoded = serde_json::to_string(&original).unwrap();
        let decoded: EvaluationSpec = serde_json::from_str(&encoded).unwrap();
        assert_eq!(decoded, original);
        assert!(decoded.validate().is_ok());
    }

    #[test]
    fn defaults_execution_mode_for_older_specs() {
        let mut value = serde_json::to_value(spec()).unwrap();
        value.as_object_mut().unwrap().remove("execution_mode");
        let decoded: EvaluationSpec = serde_json::from_value(value).unwrap();
        assert_eq!(decoded.execution_mode, ExecutionMode::Fixture);
    }

    #[test]
    fn evaluation_spec_round_trips_scenario_params() {
        let mut original = spec();
        original
            .scenario_params
            .insert("topic".into(), "checkout latency".into());
        let encoded = serde_json::to_string(&original).unwrap();
        let decoded: EvaluationSpec = serde_json::from_str(&encoded).unwrap();
        assert_eq!(
            decoded.scenario_params.get("topic").map(String::as_str),
            Some("checkout latency")
        );
        assert!(decoded.validate().is_ok());
    }

    #[test]
    fn validates_environment_ready_as_local_message_only() {
        let ready: EnvironmentReadyMessage = serde_json::from_value(json!({
            "type": "env_ready",
            "payload": {"mcp_url": "http://127.0.0.1:43123/mcp"}
        }))
        .unwrap();
        assert!(ready.validate().is_ok());
        for url in [
            "http://127.0.0.1:0/mcp",
            "https://127.0.0.1:43123/mcp",
            "http://localhost:43123/mcp",
            "http://127.0.0.1:43123/mcp?token=secret",
            "http://127.0.0.1:43123/other",
        ] {
            let ready: EnvironmentReadyMessage = serde_json::from_value(json!({
                "type": "env_ready",
                "payload": {"mcp_url": url}
            }))
            .unwrap();
            assert!(ready.validate().is_err(), "accepted invalid URL {url}");
        }
    }

    #[test]
    fn rejects_invalid_scenario_id_pattern() {
        let mut value = spec();
        value.scenario_id = "INVALID".into();
        assert!(value.validate().unwrap_err().contains("allowlisted"));
    }

    #[test]
    fn agent_id_is_optional_and_matches_registry_grammar() {
        let mut value = spec();
        assert!(value.validate().is_ok());
        for id in [
            "INVALID", "a.v1", "ab.v", "ab.v-1", "éx.v1", " ab.v1", "ab.v1 ",
        ] {
            value.agent_id = Some(id.into());
            assert!(value.validate().unwrap_err().contains("agent"), "{id}");
        }
        for id in [
            "ab.v1".into(),
            "0-.v0".into(),
            format!("{}.v1", "a".repeat(61)),
        ] {
            value.agent_id = Some(id.clone());
            assert!(value.validate().is_ok(), "{id}");
        }
        value.agent_id = Some(format!("{}.v1", "a".repeat(62)));
        assert!(value.validate().is_err());
    }

    #[test]
    fn rejects_unknown_fields_and_out_of_range_limits() {
        let mut json = serde_json::to_value(spec()).unwrap();
        json["executable"] = Value::String("/bin/sh".into());
        assert!(serde_json::from_value::<EvaluationSpec>(json).is_err());

        let mut value = spec();
        value.limits.timeout_ms = 999;
        assert!(value.validate().unwrap_err().contains("timeout_ms"));
    }

    #[test]
    fn validates_policy_bounds() {
        let mut value = spec();
        value.policy = Some(PolicyPatch {
            instruction_patch: vec!["Use evidence first.".into()],
            tool_priority: BTreeMap::from([("unknown_tool".into(), 1.0)]),
            max_steps: 2,
        });
        assert!(value.validate().is_err());
    }

    #[test]
    fn event_uses_type_wire_name_and_validates() {
        let event = EventEnvelope {
            schema_version: 1,
            run_id: "run-1".into(),
            sequence: 1,
            occurred_at: "2026-07-16T00:00:00Z".into(),
            event_type: "run_started".into(),
            payload: json_object(),
        };
        let json = serde_json::to_value(&event).unwrap();
        assert_eq!(json["type"], "run_started");
        assert!(json.get("event_type").is_none());
        assert!(event.validate().is_ok());
    }

    #[test]
    fn terminal_failure_kind_matches_python_values() {
        let values = [
            (TerminalFailureKind::Cancelled, "cancelled"),
            (TerminalFailureKind::TimedOut, "timed_out"),
            (
                TerminalFailureKind::OutputLimitExceeded,
                "output_limit_exceeded",
            ),
            (TerminalFailureKind::AgentExit, "agent_exit"),
            (TerminalFailureKind::ProviderFailure, "provider_failure"),
            (TerminalFailureKind::InternalFailure, "internal_failure"),
        ];

        for (kind, expected) in values {
            assert_eq!(serde_json::to_value(kind).unwrap(), expected);
            assert_eq!(
                serde_json::from_str::<TerminalFailureKind>(&format!("\"{expected}\"")).unwrap(),
                kind
            );
        }
    }

    #[test]
    fn rejects_unknown_terminal_failure_kind() {
        assert!(serde_json::from_str::<TerminalFailureKind>("\"unknown_failure\"").is_err());
    }

    #[test]
    fn complete_request_serializes_failure_kind_without_free_text_error() {
        let request = CompleteRequest {
            runner_id: "runner-1",
            status: "failed",
            failure_kind: Some(TerminalFailureKind::AgentExit),
            metrics: json_object(),
            execution: execution_facts("run-1", Some(ExecutionFailure::AgentExit)),
        };
        let json = serde_json::to_value(request).unwrap();
        assert_eq!(json["failure_kind"], "agent_exit");
        assert!(json.get("error").is_none());
    }

    #[test]
    fn complete_request_deserializes_failure_kind_and_rejects_legacy_error() {
        let encoded = serde_json::to_string(&json!({
            "runner_id": "runner-1",
            "status": "failed",
            "failure_kind": "internal_failure",
            "metrics": {},
            "execution": {
                "run_id": "run-1",
                "attempt": 1,
                "eligible": false,
                "submitted": false,
                "check_succeeded": false,
                "planned_environment_teardown": false,
                "failure": "internal"
            }
        }))
        .unwrap();
        let request: CompleteRequest<'_> = serde_json::from_str(&encoded).unwrap();
        assert_eq!(
            request.failure_kind,
            Some(TerminalFailureKind::InternalFailure)
        );

        let legacy = serde_json::to_string(&json!({
            "runner_id": "runner-1",
            "status": "failed",
            "error": "UNIQUE_LEGACY_ERROR_ATTACK",
            "metrics": {},
            "execution": {
                "run_id": "run-1",
                "attempt": 1,
                "eligible": false,
                "submitted": false,
                "check_succeeded": false,
                "planned_environment_teardown": false,
                "failure": "internal"
            }
        }))
        .unwrap();
        assert!(serde_json::from_str::<CompleteRequest<'_>>(&legacy).is_err());
    }

    #[test]
    fn successful_complete_request_has_no_failure_kind() {
        let request = CompleteRequest {
            runner_id: "runner-1",
            status: "succeeded",
            failure_kind: None,
            metrics: json_object(),
            execution: execution_facts("run-1", None),
        };
        let json = serde_json::to_value(request).unwrap();
        assert_eq!(json["status"], "succeeded");
        assert!(json.get("failure_kind").is_none());
        assert!(json.get("error").is_none());
    }

    #[test]
    fn rejects_invalid_event_and_child_lifecycle_event() {
        let event = EventEnvelope {
            schema_version: 1,
            run_id: "run-1".into(),
            sequence: 0,
            occurred_at: "not-a-date".into(),
            event_type: "thought".into(),
            payload: Value::Null,
        };
        assert!(event.validate().is_err());

        let child = ChildEvent {
            event_type: "run_completed".into(),
            payload: json_object(),
        };
        assert!(child.validate().unwrap_err().contains("not allowed"));
    }

    fn execution_facts<'a>(
        run_id: &'a str,
        failure: Option<ExecutionFailure>,
    ) -> ExecutionFacts<'a> {
        ExecutionFacts {
            run_id,
            attempt: 1,
            eligible: failure.is_none(),
            submitted: failure.is_none(),
            check_succeeded: failure.is_none(),
            planned_environment_teardown: failure.is_none(),
            failure,
        }
    }

    fn json_object() -> Value {
        Value::Object(Default::default())
    }
}
