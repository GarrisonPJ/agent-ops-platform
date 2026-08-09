use std::path::PathBuf;
use std::process::{ExitStatus, Stdio};
use std::time::Duration;

use agentops_protocol::{
    ChildEvent, ClaimRequest, ClaimResponse, CompleteRequest, EvaluationSpec, EventBatchRequest,
    EventBatchResponse, EventEnvelope, HeartbeatRequest, HeartbeatResponse, RunnerCommand,
    TerminalFailureKind, SCHEMA_VERSION,
};
use anyhow::{bail, Context, Result};
use chrono::Utc;
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use reqwest::{Client, StatusCode};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::time::{interval, sleep, timeout, Instant, Interval};

pub const MAX_LINE_BYTES: usize = 65_536;
const NETWORK_RETRY_WINDOW: Duration = Duration::from_secs(10);

#[derive(Debug, Clone)]
pub struct RunnerConfig {
    pub server_url: String,
    pub runner_token: String,
    pub runner_id: String,
    pub agent_program: String,
    pub agent_args: Vec<String>,
    pub agent_workdir: Option<PathBuf>,
    pub poll_interval: Duration,
}

impl RunnerConfig {
    pub fn from_env() -> Result<Self> {
        let server_url = std::env::var("AGENTOPS_SERVER_URL")
            .unwrap_or_else(|_| "http://localhost:8000".into())
            .trim_end_matches('/')
            .to_string();
        let runner_token = std::env::var("RUNNER_TOKEN").context("RUNNER_TOKEN must be set")?;
        let runner_id = std::env::var("AGENTOPS_RUNNER_ID")
            .unwrap_or_else(|_| format!("local-{}", std::process::id()));
        let agent_program =
            std::env::var("AGENTOPS_AGENT_PROGRAM").unwrap_or_else(|_| "python".into());
        let agent_args = match std::env::var("AGENTOPS_AGENT_ARGS") {
            Ok(raw) => serde_json::from_str::<Vec<String>>(&raw)
                .context("AGENTOPS_AGENT_ARGS must be a JSON string array")?,
            Err(_) => vec!["-m".into(), "app.demo_agent".into()],
        };
        let agent_workdir = std::env::var_os("AGENTOPS_AGENT_WORKDIR").map(PathBuf::from);
        Ok(Self {
            server_url,
            runner_token,
            runner_id,
            agent_program,
            agent_args,
            agent_workdir,
            poll_interval: Duration::from_secs(1),
        })
    }
}

pub struct Worker {
    client: Client,
    config: RunnerConfig,
}

impl Worker {
    pub fn new(config: RunnerConfig) -> Result<Self> {
        let client = Client::builder().timeout(Duration::from_secs(5)).build()?;
        Ok(Self { client, config })
    }

    pub async fn run_forever(&self) -> Result<()> {
        loop {
            match self.claim().await {
                Ok(Some(claim)) => {
                    if let Err(error) = self.execute_claim(claim.clone()).await {
                        eprintln!("run {} failed: {error:#}", claim.run.run_id);
                    }
                }
                Ok(None) => sleep(self.config.poll_interval).await,
                Err(error) => {
                    eprintln!("claim failed: {error:#}");
                    sleep(Duration::from_secs(2)).await;
                }
            }
        }
    }

    async fn claim(&self) -> Result<Option<ClaimResponse>> {
        let response = self
            .client
            .post(format!(
                "{}/api/internal/runner/jobs/claim",
                self.config.server_url
            ))
            .bearer_auth(&self.config.runner_token)
            .json(&ClaimRequest {
                runner_id: &self.config.runner_id,
            })
            .send()
            .await?;
        if response.status() == StatusCode::NO_CONTENT {
            return Ok(None);
        }
        let response = response.error_for_status()?;
        Ok(Some(response.json().await?))
    }

    async fn execute_claim(&self, claim: ClaimResponse) -> Result<()> {
        if claim.attempt == 0 || claim.next_sequence == 0 {
            bail!("claim recovery metadata must contain positive attempt and sequence");
        }
        if claim.run.evaluation_spec.validate().is_err() {
            let outcome = TerminalOutcome::internal_failure(claim.next_sequence, 0);
            return self.finalize_terminal(&claim, outcome, 0, 0).await;
        }
        if claim.run.run_id != claim.run.evaluation_spec.run_id {
            let outcome = TerminalOutcome::internal_failure(claim.next_sequence, 0);
            return self.finalize_terminal(&claim, outcome, 0, 0).await;
        }

        let mut child = match self.spawn_agent(&claim.run.evaluation_spec).await {
            Ok(child) => child,
            Err(_) => {
                let outcome = TerminalOutcome::internal_failure(claim.next_sequence, 0);
                return self.finalize_terminal(&claim, outcome, 0, 0).await;
            }
        };
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                terminate_child(&mut child).await;
                let outcome = TerminalOutcome::internal_failure(claim.next_sequence, 0);
                return self.finalize_terminal(&claim, outcome, 0, 0).await;
            }
        };
        let stderr = match child.stderr.take() {
            Some(stderr) => stderr,
            None => {
                terminate_child(&mut child).await;
                let outcome = TerminalOutcome::internal_failure(claim.next_sequence, 0);
                return self.finalize_terminal(&claim, outcome, 0, 0).await;
            }
        };
        let stderr_task = tokio::spawn(drain_stderr(stderr));

        let mut event_retries = 0_u32;
        let mut outcome = {
            let mut next_sequence = claim.next_sequence;
            match self
                .upload_event(
                    &claim,
                    envelope(
                        &claim.run.run_id,
                        next_sequence,
                        "run_started",
                        json!({"attempt": claim.attempt}),
                    ),
                )
                .await
            {
                Ok(start_retries) => {
                    event_retries += start_retries;
                    next_sequence += 1;
                    self.supervise(
                        &claim,
                        &mut child,
                        stdout,
                        next_sequence,
                        &mut event_retries,
                    )
                    .await
                }
                Err(_) => {
                    terminate_child(&mut child).await;
                    TerminalOutcome::internal_failure(next_sequence, 0)
                }
            }
        };

        let stderr_capture = match stderr_task.await {
            Ok(capture) => capture,
            Err(_) => {
                outcome =
                    TerminalOutcome::internal_failure(outcome.next_sequence, outcome.stdout_bytes);
                StderrCapture::default()
            }
        };
        self.finalize_terminal(&claim, outcome, stderr_capture.total_bytes, event_retries)
            .await
    }

    async fn finalize_terminal(
        &self,
        claim: &ClaimResponse,
        mut outcome: TerminalOutcome,
        stderr_bytes: usize,
        mut event_retries: u32,
    ) -> Result<()> {
        let total_output_bytes = outcome.stdout_bytes.saturating_add(stderr_bytes);
        if total_output_bytes > claim.run.evaluation_spec.limits.max_output_bytes
            && !matches!(
                outcome.status,
                TerminalStatus::Cancelled | TerminalStatus::TimedOut
            )
        {
            outcome = TerminalOutcome::failed(
                TerminalFailureKind::OutputLimitExceeded,
                outcome.next_sequence,
                outcome.stdout_bytes,
            );
        }

        let mut payload = json!({
            "attempt": claim.attempt,
            "status": outcome.status(),
        });
        if let Some(failure_kind) = outcome.failure_kind() {
            payload["failure_kind"] = json!(failure_kind);
        }
        event_retries = event_retries.saturating_add(
            self.upload_event(
                claim,
                envelope(
                    &claim.run.run_id,
                    outcome.next_sequence,
                    outcome.event_type(),
                    payload,
                ),
            )
            .await?,
        );

        self.complete(
            claim,
            outcome.status(),
            outcome.failure_kind(),
            json!({
                "stdout_bytes": outcome.stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "total_output_bytes": total_output_bytes,
                "event_retries": event_retries,
            }),
        )
        .await
    }

    async fn supervise<R: AsyncRead + Unpin>(
        &self,
        claim: &ClaimResponse,
        child: &mut Child,
        stdout: R,
        mut next_sequence: u64,
        event_retries: &mut u32,
    ) -> TerminalOutcome {
        let mut reader = BufReader::new(stdout);
        let mut heartbeat_tick = interval(Duration::from_secs(2));
        heartbeat_tick.tick().await;
        let deadline =
            Instant::now() + Duration::from_millis(claim.run.evaluation_spec.limits.timeout_ms);
        let mut stdout_bytes = 0_usize;
        let mut buffer = Vec::with_capacity(8 * 1024);
        let mut provider_failure_seen = false;

        loop {
            buffer.clear();
            tokio::select! {
                read = read_bounded_line(&mut reader, &mut buffer) => {
                    let read = match read {
                        Ok(read) => read,
                        Err(_) => {
                            terminate_child(child).await;
                            return TerminalOutcome::internal_failure(next_sequence, stdout_bytes);
                        }
                    };
                    match read {
                        LineRead::Eof => {
                            let context = ExitContext {
                                deadline,
                                next_sequence,
                                stdout_bytes,
                                provider_failure_seen,
                            };
                            return self
                                .wait_for_exit(
                                    claim,
                                    child,
                                    &mut heartbeat_tick,
                                    context,
                                )
                                .await;
                        }
                        LineRead::TooLong(bytes) => {
                            stdout_bytes = stdout_bytes.saturating_add(bytes);
                            terminate_child(child).await;
                            return TerminalOutcome::output_limit(
                                next_sequence,
                                stdout_bytes,
                            );
                        }
                        LineRead::Line(bytes) => {
                            stdout_bytes = stdout_bytes.saturating_add(bytes);
                            if stdout_bytes > claim.run.evaluation_spec.limits.max_output_bytes {
                                terminate_child(child).await;
                                return TerminalOutcome::output_limit(
                                    next_sequence,
                                    stdout_bytes,
                                );
                            }
                            if buffer.iter().all(|byte| byte.is_ascii_whitespace()) {
                                continue;
                            }
                            let child_event: ChildEvent = match serde_json::from_slice(&buffer) {
                                Ok(event) => event,
                                Err(_) => {
                                    terminate_child(child).await;
                                    return TerminalOutcome::internal_failure(
                                        next_sequence,
                                        stdout_bytes,
                                    );
                                }
                            };
                            if child_event.validate().is_err() {
                                terminate_child(child).await;
                                return TerminalOutcome::internal_failure(
                                    next_sequence,
                                    stdout_bytes,
                                );
                            }
                            provider_failure_seen |= is_provider_failure_signal(&child_event);
                            let retries = match self
                                .upload_event(
                                    claim,
                                    envelope(
                                        &claim.run.run_id,
                                        next_sequence,
                                        &child_event.event_type,
                                        payload_with_attempt(child_event.payload, claim.attempt),
                                    ),
                                )
                                .await
                            {
                                Ok(retries) => retries,
                                Err(_) => {
                                    terminate_child(child).await;
                                    return TerminalOutcome::internal_failure(
                                        next_sequence,
                                        stdout_bytes,
                                    );
                                }
                            };
                            *event_retries = event_retries.saturating_add(retries);
                            next_sequence += 1;
                        }
                    }
                }
                _ = heartbeat_tick.tick() => {
                    match self.heartbeat(claim).await {
                        Ok(RunnerCommand::Continue) => {}
                        Ok(RunnerCommand::Cancel) => {
                            terminate_child(child).await;
                            return TerminalOutcome::cancelled(next_sequence, stdout_bytes);
                        }
                        Err(_) => {
                            terminate_child(child).await;
                            return TerminalOutcome::internal_failure(next_sequence, stdout_bytes);
                        }
                    }
                }
                _ = tokio::time::sleep_until(deadline) => {
                    terminate_child(child).await;
                    return TerminalOutcome::timed_out(next_sequence, stdout_bytes);
                }
            }
        }
    }

    async fn wait_for_exit(
        &self,
        claim: &ClaimResponse,
        child: &mut Child,
        heartbeat_tick: &mut Interval,
        context: ExitContext,
    ) -> TerminalOutcome {
        loop {
            let signal = tokio::select! {
                status = child.wait() => ExitSignal::Exited(status),
                _ = heartbeat_tick.tick() => ExitSignal::Heartbeat,
                _ = tokio::time::sleep_until(context.deadline) => ExitSignal::Deadline,
            };
            match signal {
                ExitSignal::Exited(status) => match status {
                    Ok(status) => {
                        return outcome_from_exit(
                            status,
                            context.next_sequence,
                            context.stdout_bytes,
                            context.provider_failure_seen,
                        );
                    }
                    Err(_) => {
                        return TerminalOutcome::internal_failure(
                            context.next_sequence,
                            context.stdout_bytes,
                        );
                    }
                },
                ExitSignal::Heartbeat => match self.heartbeat(claim).await {
                    Ok(RunnerCommand::Continue) => {}
                    Ok(RunnerCommand::Cancel) => {
                        terminate_child(child).await;
                        return TerminalOutcome::cancelled(
                            context.next_sequence,
                            context.stdout_bytes,
                        );
                    }
                    Err(_) => {
                        terminate_child(child).await;
                        return TerminalOutcome::internal_failure(
                            context.next_sequence,
                            context.stdout_bytes,
                        );
                    }
                },
                ExitSignal::Deadline => {
                    terminate_child(child).await;
                    return TerminalOutcome::timed_out(context.next_sequence, context.stdout_bytes);
                }
            }
        }
    }

    async fn spawn_agent(&self, spec: &EvaluationSpec) -> Result<Child> {
        let mut command = Command::new(&self.config.agent_program);
        command.args(&self.config.agent_args);
        if let Some(workdir) = &self.config.agent_workdir {
            command.current_dir(workdir);
        }
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            // SAFETY: setpgid is async-signal-safe and runs in the child before exec.
            unsafe {
                command.pre_exec(|| {
                    nix::unistd::setpgid(Pid::from_raw(0), Pid::from_raw(0))
                        .map_err(std::io::Error::other)
                });
            }
        }
        let mut child = command.spawn().with_context(|| {
            format!(
                "failed to spawn allowlisted agent program {}",
                self.config.agent_program
            )
        })?;
        let mut stdin = child.stdin.take().context("agent stdin is unavailable")?;
        let payload = serde_json::to_vec(spec)?;
        stdin.write_all(&payload).await?;
        stdin.write_all(b"\n").await?;
        stdin.shutdown().await?;
        Ok(child)
    }

    async fn heartbeat(&self, claim: &ClaimResponse) -> Result<RunnerCommand> {
        let deadline = Instant::now() + NETWORK_RETRY_WINDOW;
        loop {
            let result = self
                .client
                .post(format!(
                    "{}/api/internal/runner/jobs/{}/heartbeat",
                    self.config.server_url, claim.lease_id
                ))
                .bearer_auth(&self.config.runner_token)
                .json(&HeartbeatRequest {
                    runner_id: &self.config.runner_id,
                })
                .send()
                .await;
            match result {
                Ok(response) if response.status().is_success() => {
                    let heartbeat: HeartbeatResponse = response.json().await?;
                    return Ok(heartbeat.command);
                }
                Ok(response) if response.status().is_client_error() => {
                    bail!("heartbeat rejected with {}", response.status());
                }
                _ => {}
            }
            if Instant::now() >= deadline {
                bail!("heartbeat failed for 10 seconds");
            }
            sleep(Duration::from_millis(250)).await;
        }
    }

    async fn upload_event(&self, claim: &ClaimResponse, event: EventEnvelope) -> Result<u32> {
        event.validate().map_err(anyhow::Error::msg)?;
        let expected_sequence = event.sequence;
        let events = [event];
        let request = EventBatchRequest {
            runner_id: &self.config.runner_id,
            lease_id: &claim.lease_id,
            events: &events,
        };
        let deadline = Instant::now() + NETWORK_RETRY_WINDOW;
        let mut retries = 0_u32;
        loop {
            let result = self
                .client
                .post(format!(
                    "{}/api/internal/runner/runs/{}/events",
                    self.config.server_url, claim.run.run_id
                ))
                .bearer_auth(&self.config.runner_token)
                .json(&request)
                .send()
                .await;
            match result {
                Ok(response) if response.status().is_success() => {
                    let accepted: EventBatchResponse = response.json().await?;
                    if accepted.accepted_through >= expected_sequence {
                        return Ok(retries);
                    }
                }
                Ok(response) if response.status().is_client_error() => {
                    bail!("event upload rejected with {}", response.status());
                }
                _ => {}
            }
            if Instant::now() >= deadline {
                bail!("event upload failed for 10 seconds");
            }
            retries = retries.saturating_add(1);
            sleep(Duration::from_millis(250)).await;
        }
    }

    async fn complete(
        &self,
        claim: &ClaimResponse,
        status: &str,
        failure_kind: Option<TerminalFailureKind>,
        metrics: Value,
    ) -> Result<()> {
        let deadline = Instant::now() + NETWORK_RETRY_WINDOW;
        loop {
            let result = self
                .client
                .post(format!(
                    "{}/api/internal/runner/jobs/{}/complete",
                    self.config.server_url, claim.lease_id
                ))
                .bearer_auth(&self.config.runner_token)
                .json(&CompleteRequest {
                    runner_id: &self.config.runner_id,
                    status,
                    failure_kind,
                    metrics: metrics.clone(),
                })
                .send()
                .await;
            match result {
                Ok(response) if response.status().is_success() => return Ok(()),
                Ok(response) if response.status().is_client_error() => {
                    bail!("completion rejected with {}", response.status());
                }
                _ => {}
            }
            if Instant::now() >= deadline {
                bail!("completion failed for 10 seconds");
            }
            sleep(Duration::from_millis(250)).await;
        }
    }
}

#[derive(Debug)]
struct TerminalOutcome {
    status: TerminalStatus,
    next_sequence: u64,
    stdout_bytes: usize,
}

impl TerminalOutcome {
    fn success(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self {
            status: TerminalStatus::Succeeded,
            next_sequence,
            stdout_bytes,
        }
    }

    fn failed(failure_kind: TerminalFailureKind, next_sequence: u64, stdout_bytes: usize) -> Self {
        Self {
            status: TerminalStatus::Failed(failure_kind),
            next_sequence,
            stdout_bytes,
        }
    }

    fn cancelled(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self {
            status: TerminalStatus::Cancelled,
            next_sequence,
            stdout_bytes,
        }
    }

    fn timed_out(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self {
            status: TerminalStatus::TimedOut,
            next_sequence,
            stdout_bytes,
        }
    }

    fn output_limit(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self::failed(
            TerminalFailureKind::OutputLimitExceeded,
            next_sequence,
            stdout_bytes,
        )
    }

    fn provider_failure(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self::failed(
            TerminalFailureKind::ProviderFailure,
            next_sequence,
            stdout_bytes,
        )
    }

    fn process_exit(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self::failed(TerminalFailureKind::AgentExit, next_sequence, stdout_bytes)
    }

    fn internal_failure(next_sequence: u64, stdout_bytes: usize) -> Self {
        Self::failed(
            TerminalFailureKind::InternalFailure,
            next_sequence,
            stdout_bytes,
        )
    }

    fn status(&self) -> &'static str {
        match self.status {
            TerminalStatus::Succeeded => "succeeded",
            TerminalStatus::Failed(_) => "failed",
            TerminalStatus::Cancelled => "cancelled",
            TerminalStatus::TimedOut => "timed_out",
        }
    }

    fn failure_kind(&self) -> Option<TerminalFailureKind> {
        match self.status {
            TerminalStatus::Succeeded => None,
            TerminalStatus::Failed(kind) => Some(kind),
            TerminalStatus::Cancelled => Some(TerminalFailureKind::Cancelled),
            TerminalStatus::TimedOut => Some(TerminalFailureKind::TimedOut),
        }
    }

    fn event_type(&self) -> &'static str {
        match self.status {
            TerminalStatus::Succeeded => "run_completed",
            TerminalStatus::Cancelled => "run_cancelled",
            TerminalStatus::Failed(_) | TerminalStatus::TimedOut => "run_failed",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TerminalStatus {
    Succeeded,
    Failed(TerminalFailureKind),
    Cancelled,
    TimedOut,
}

enum ExitSignal {
    Exited(std::io::Result<ExitStatus>),
    Heartbeat,
    Deadline,
}

struct ExitContext {
    deadline: Instant,
    next_sequence: u64,
    stdout_bytes: usize,
    provider_failure_seen: bool,
}

fn outcome_from_exit(
    status: ExitStatus,
    next_sequence: u64,
    stdout_bytes: usize,
    provider_failure_seen: bool,
) -> TerminalOutcome {
    if provider_failure_seen {
        TerminalOutcome::provider_failure(next_sequence, stdout_bytes)
    } else if status.success() {
        TerminalOutcome::success(next_sequence, stdout_bytes)
    } else {
        TerminalOutcome::process_exit(next_sequence, stdout_bytes)
    }
}

fn is_provider_failure_signal(event: &ChildEvent) -> bool {
    event.event_type == "process_output"
        && event
            .payload
            .get("provider_error")
            .is_some_and(|value| value.is_object())
}

fn envelope(run_id: &str, sequence: u64, event_type: &str, payload: Value) -> EventEnvelope {
    EventEnvelope {
        schema_version: SCHEMA_VERSION,
        run_id: run_id.into(),
        sequence,
        occurred_at: Utc::now().to_rfc3339(),
        event_type: event_type.into(),
        payload,
    }
}

fn payload_with_attempt(payload: Value, attempt: u32) -> Value {
    let mut object = payload.as_object().cloned().unwrap_or_default();
    object.insert("attempt".into(), json!(attempt));
    Value::Object(object)
}

enum LineRead {
    Eof,
    Line(usize),
    TooLong(usize),
}

async fn read_bounded_line<R: AsyncRead + Unpin>(
    reader: &mut BufReader<R>,
    buffer: &mut Vec<u8>,
) -> std::io::Result<LineRead> {
    let bytes = (&mut *reader)
        .take((MAX_LINE_BYTES + 1) as u64)
        .read_until(b'\n', buffer)
        .await?;
    if bytes == 0 {
        Ok(LineRead::Eof)
    } else if bytes > MAX_LINE_BYTES {
        Ok(LineRead::TooLong(bytes))
    } else {
        Ok(LineRead::Line(bytes))
    }
}

#[derive(Default)]
struct StderrCapture {
    total_bytes: usize,
}

async fn drain_stderr<R: AsyncRead + Unpin>(mut stderr: R) -> StderrCapture {
    let mut total_bytes = 0_usize;
    let mut chunk = [0_u8; 8 * 1024];
    loop {
        let bytes = match stderr.read(&mut chunk).await {
            Ok(0) | Err(_) => break,
            Ok(bytes) => bytes,
        };
        total_bytes = total_bytes.saturating_add(bytes);
    }
    StderrCapture { total_bytes }
}

async fn terminate_child(child: &mut Child) {
    #[cfg(unix)]
    if let Some(pid) = child.id() {
        let group = Pid::from_raw(pid as i32);
        let _ = killpg(group, Signal::SIGTERM);
        if timeout(Duration::from_secs(2), child.wait()).await.is_ok() {
            return;
        }
        let _ = killpg(group, Signal::SIGKILL);
    }
    let _ = child.kill().await;
    let _ = child.wait().await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_uses_argument_array() {
        std::env::set_var("RUNNER_TOKEN", "test");
        std::env::remove_var("AGENTOPS_AGENT_ARGS");
        let config = RunnerConfig::from_env().unwrap();
        assert_eq!(config.agent_program, "python");
        assert_eq!(config.agent_args, ["-m", "app.demo_agent"]);
    }

    #[test]
    fn terminal_outcomes_have_deterministic_wire_mapping() {
        let cases = [
            (
                TerminalOutcome::success(1, 0),
                "succeeded",
                None,
                "run_completed",
            ),
            (
                TerminalOutcome::cancelled(1, 0),
                "cancelled",
                Some(TerminalFailureKind::Cancelled),
                "run_cancelled",
            ),
            (
                TerminalOutcome::timed_out(1, 0),
                "timed_out",
                Some(TerminalFailureKind::TimedOut),
                "run_failed",
            ),
            (
                TerminalOutcome::output_limit(1, 0),
                "failed",
                Some(TerminalFailureKind::OutputLimitExceeded),
                "run_failed",
            ),
            (
                TerminalOutcome::process_exit(1, 0),
                "failed",
                Some(TerminalFailureKind::AgentExit),
                "run_failed",
            ),
            (
                TerminalOutcome::provider_failure(1, 0),
                "failed",
                Some(TerminalFailureKind::ProviderFailure),
                "run_failed",
            ),
            (
                TerminalOutcome::internal_failure(1, 0),
                "failed",
                Some(TerminalFailureKind::InternalFailure),
                "run_failed",
            ),
        ];

        for (outcome, status, failure_kind, event_type) in cases {
            assert_eq!(outcome.status(), status);
            assert_eq!(outcome.failure_kind(), failure_kind);
            assert_eq!(outcome.event_type(), event_type);
        }
    }

    #[cfg(unix)]
    #[test]
    fn exit_status_mapping_prioritizes_provider_failure_and_handles_nonzero_exit() {
        use std::os::unix::process::ExitStatusExt;

        assert_eq!(
            outcome_from_exit(ExitStatus::from_raw(0), 1, 0, false).failure_kind(),
            None
        );
        assert_eq!(
            outcome_from_exit(ExitStatus::from_raw(7), 1, 0, false).failure_kind(),
            Some(TerminalFailureKind::AgentExit)
        );
        assert_eq!(
            outcome_from_exit(ExitStatus::from_raw(7), 1, 0, true).failure_kind(),
            Some(TerminalFailureKind::ProviderFailure)
        );
    }

    #[test]
    fn provider_failure_signal_requires_structured_provider_error() {
        let structured = ChildEvent {
            event_type: "process_output".into(),
            payload: json!({"provider_error": {"message": "UNIQUE_RAW_PROVIDER_TEXT"}}),
        };
        let raw = ChildEvent {
            event_type: "process_output".into(),
            payload: json!({"provider_error": "UNIQUE_RAW_PROVIDER_TEXT"}),
        };
        assert!(is_provider_failure_signal(&structured));
        assert!(!is_provider_failure_signal(&raw));
    }

    #[tokio::test]
    async fn stderr_is_drained_without_capture() {
        let data = vec![b'x'; 100_000];
        let output = drain_stderr(&data[..]).await;
        assert_eq!(output.total_bytes, data.len());
    }

    #[tokio::test]
    async fn overlong_jsonl_line_is_rejected_without_unbounded_buffering() {
        let data = vec![b'x'; MAX_LINE_BYTES + 1];
        let mut reader = BufReader::new(&data[..]);
        let mut buffer = Vec::new();
        let result = read_bounded_line(&mut reader, &mut buffer).await.unwrap();
        assert!(matches!(result, LineRead::TooLong(MAX_LINE_BYTES_PLUS_ONE)));
        assert_eq!(buffer.len(), MAX_LINE_BYTES + 1);
    }

    const MAX_LINE_BYTES_PLUS_ONE: usize = MAX_LINE_BYTES + 1;

    #[tokio::test]
    async fn empty_and_blank_lines_are_distinct() {
        let mut empty = BufReader::new(&b""[..]);
        let mut buffer = Vec::new();
        assert!(matches!(
            read_bounded_line(&mut empty, &mut buffer).await.unwrap(),
            LineRead::Eof
        ));

        let mut blank = BufReader::new(&b"\n"[..]);
        buffer.clear();
        assert!(matches!(
            read_bounded_line(&mut blank, &mut buffer).await.unwrap(),
            LineRead::Line(1)
        ));
    }
}

#[cfg(test)]
mod supervisor_tests;
