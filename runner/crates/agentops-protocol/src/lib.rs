use std::collections::BTreeMap;

use chrono::DateTime;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const SCHEMA_VERSION: u16 = 1;

const ALLOWED_TOOLS: [&str; 3] = [
    "check_service_health",
    "query_service_metrics",
    "fetch_service_logs",
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EvaluationSpec {
    pub schema_version: u16,
    pub run_id: String,
    pub experiment_id: String,
    pub scenario_id: String,
    pub task: String,
    pub seed: u64,
    pub policy: Option<PolicyPatch>,
    #[serde(default)]
    pub limits: ExecutionLimits,
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
        if self.scenario_id != "checkout-api-latency" {
            return Err(format!(
                "scenario '{}' is not allowlisted",
                self.scenario_id
            ));
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

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CompleteRequest<'a> {
    pub runner_id: &'a str,
    pub status: &'a str,
    pub error: Option<&'a str>,
    pub metrics: Value,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> EvaluationSpec {
        EvaluationSpec {
            schema_version: SCHEMA_VERSION,
            run_id: "run-1".into(),
            experiment_id: "experiment-1".into(),
            scenario_id: "checkout-api-latency".into(),
            task: "Investigate checkout latency".into(),
            seed: 42,
            policy: None,
            limits: ExecutionLimits::default(),
        }
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
    fn rejects_unknown_scenario() {
        let mut value = spec();
        value.scenario_id = "arbitrary-command".into();
        assert!(value.validate().unwrap_err().contains("not allowlisted"));
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

    fn json_object() -> Value {
        Value::Object(Default::default())
    }
}
