use std::path::PathBuf;
use std::process::{ExitStatus, Stdio};
use std::time::Duration;

use agentops_protocol::{
    ChildEvent, ClaimRequest, ClaimResponse, CompleteRequest, EvaluationSpec, EventBatchRequest,
    EventBatchResponse, EventEnvelope, HeartbeatRequest, HeartbeatResponse, RunnerCommand,
    SCHEMA_VERSION,
};
use anyhow::{anyhow, bail, Context, Result};
use chrono::Utc;
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use reqwest::{Client, StatusCode};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::time::{interval, sleep, timeout, Instant, Interval};

pub const MAX_LINE_BYTES: usize = 65_536;
const MAX_STDERR_CAPTURE_BYTES: usize = 65_536;
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
        claim
            .run
            .evaluation_spec
            .validate()
            .map_err(|error| anyhow!(error))?;
        if claim.run.run_id != claim.run.evaluation_spec.run_id {
            bail!("claim run_id does not match EvaluationSpec");
        }
        if claim.attempt == 0 || claim.next_sequence == 0 {
            bail!("claim recovery metadata must contain positive attempt and sequence");
        }

        let mut child = self.spawn_agent(&claim.run.evaluation_spec).await?;
        let stdout = child.stdout.take().context("agent stdout is unavailable")?;
        let stderr = child.stderr.take().context("agent stderr is unavailable")?;
        let stderr_task = tokio::spawn(drain_stderr(stderr));

        let execution = async {
            let mut next_sequence = claim.next_sequence;
            self.upload_event(
                &claim,
                envelope(
                    &claim.run.run_id,
                    next_sequence,
                    "run_started",
                    json!({"attempt": claim.attempt}),
                ),
            )
            .await?;
            next_sequence += 1;
            self.supervise(&claim, &mut child, stdout, next_sequence)
                .await
        }
        .await;

        let mut outcome = match execution {
            Ok(outcome) => outcome,
            Err(error) => {
                terminate_child(&mut child).await;
                let _ = stderr_task.await;
                return Err(error);
            }
        };

        let stderr_capture = stderr_task.await.unwrap_or_default();
        let total_output_bytes = outcome
            .stdout_bytes
            .saturating_add(stderr_capture.total_bytes);
        if total_output_bytes > claim.run.evaluation_spec.limits.max_output_bytes
            && !matches!(outcome.status, "cancelled" | "timed_out")
        {
            outcome.status = "failed";
            outcome.error = Some("agent output exceeded the configured limit".into());
        } else if outcome.error.is_none() && !stderr_capture.text.trim().is_empty() {
            outcome.error = Some(stderr_capture.text.trim().chars().take(500).collect());
        }

        let event_type = match outcome.status {
            "succeeded" => "run_completed",
            "cancelled" => "run_cancelled",
            _ => "run_failed",
        };
        self.upload_event(
            &claim,
            envelope(
                &claim.run.run_id,
                outcome.next_sequence,
                event_type,
                json!({
                    "attempt": claim.attempt,
                    "status": outcome.status,
                    "error": outcome.error.as_deref(),
                }),
            ),
        )
        .await?;
        self.complete(
            &claim,
            outcome.status,
            outcome.error.as_deref(),
            json!({
                "stdout_bytes": outcome.stdout_bytes,
                "stderr_bytes": stderr_capture.total_bytes,
                "total_output_bytes": total_output_bytes,
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
    ) -> Result<TerminalOutcome> {
        let mut reader = BufReader::new(stdout);
        let mut heartbeat_tick = interval(Duration::from_secs(2));
        heartbeat_tick.tick().await;
        let deadline =
            Instant::now() + Duration::from_millis(claim.run.evaluation_spec.limits.timeout_ms);
        let mut stdout_bytes = 0_usize;
        let mut buffer = Vec::with_capacity(8 * 1024);

        loop {
            buffer.clear();
            tokio::select! {
                read = read_bounded_line(&mut reader, &mut buffer) => {
                    match read? {
                        LineRead::Eof => {
                            return self
                                .wait_for_exit(
                                    claim,
                                    child,
                                    &mut heartbeat_tick,
                                    deadline,
                                    next_sequence,
                                    stdout_bytes,
                                )
                                .await;
                        }
                        LineRead::TooLong(bytes) => {
                            stdout_bytes = stdout_bytes.saturating_add(bytes);
                            terminate_child(child).await;
                            return Ok(TerminalOutcome::failed(
                                "agent emitted a JSONL line larger than 64 KiB",
                                next_sequence,
                                stdout_bytes,
                            ));
                        }
                        LineRead::Line(bytes) => {
                            stdout_bytes = stdout_bytes.saturating_add(bytes);
                            if stdout_bytes > claim.run.evaluation_spec.limits.max_output_bytes {
                                terminate_child(child).await;
                                return Ok(TerminalOutcome::failed(
                                    "agent output exceeded the configured limit",
                                    next_sequence,
                                    stdout_bytes,
                                ));
                            }
                            if buffer.iter().all(|byte| byte.is_ascii_whitespace()) {
                                continue;
                            }
                            let child_event: ChildEvent = match serde_json::from_slice(&buffer) {
                                Ok(event) => event,
                                Err(error) => {
                                    terminate_child(child).await;
                                    return Ok(TerminalOutcome::failed(
                                        format!("agent emitted invalid JSONL: {error}"),
                                        next_sequence,
                                        stdout_bytes,
                                    ));
                                }
                            };
                            if let Err(error) = child_event.validate() {
                                terminate_child(child).await;
                                return Ok(TerminalOutcome::failed(
                                    error,
                                    next_sequence,
                                    stdout_bytes,
                                ));
                            }
                            self.upload_event(
                                claim,
                                envelope(
                                    &claim.run.run_id,
                                    next_sequence,
                                    &child_event.event_type,
                                    payload_with_attempt(child_event.payload, claim.attempt),
                                ),
                            )
                            .await?;
                            next_sequence += 1;
                        }
                    }
                }
                _ = heartbeat_tick.tick() => {
                    if self.heartbeat(claim).await? == RunnerCommand::Cancel {
                        terminate_child(child).await;
                        return Ok(TerminalOutcome {
                            status: "cancelled",
                            error: Some("cancelled by user".into()),
                            next_sequence,
                            stdout_bytes,
                        });
                    }
                }
                _ = tokio::time::sleep_until(deadline) => {
                    terminate_child(child).await;
                    return Ok(TerminalOutcome {
                        status: "timed_out",
                        error: Some("execution timed out".into()),
                        next_sequence,
                        stdout_bytes,
                    });
                }
            }
        }
    }

    async fn wait_for_exit(
        &self,
        claim: &ClaimResponse,
        child: &mut Child,
        heartbeat_tick: &mut Interval,
        deadline: Instant,
        next_sequence: u64,
        stdout_bytes: usize,
    ) -> Result<TerminalOutcome> {
        loop {
            let signal = tokio::select! {
                status = child.wait() => ExitSignal::Exited(status),
                _ = heartbeat_tick.tick() => ExitSignal::Heartbeat,
                _ = tokio::time::sleep_until(deadline) => ExitSignal::Deadline,
            };
            match signal {
                ExitSignal::Exited(status) => {
                    let status = status?;
                    return Ok(outcome_from_exit(status, next_sequence, stdout_bytes));
                }
                ExitSignal::Heartbeat => {
                    if self.heartbeat(claim).await? == RunnerCommand::Cancel {
                        terminate_child(child).await;
                        return Ok(TerminalOutcome {
                            status: "cancelled",
                            error: Some("cancelled by user".into()),
                            next_sequence,
                            stdout_bytes,
                        });
                    }
                }
                ExitSignal::Deadline => {
                    terminate_child(child).await;
                    return Ok(TerminalOutcome {
                        status: "timed_out",
                        error: Some("execution timed out".into()),
                        next_sequence,
                        stdout_bytes,
                    });
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

    async fn upload_event(&self, claim: &ClaimResponse, event: EventEnvelope) -> Result<()> {
        event.validate().map_err(anyhow::Error::msg)?;
        let expected_sequence = event.sequence;
        let events = [event];
        let request = EventBatchRequest {
            runner_id: &self.config.runner_id,
            lease_id: &claim.lease_id,
            events: &events,
        };
        let deadline = Instant::now() + NETWORK_RETRY_WINDOW;
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
                        return Ok(());
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
            sleep(Duration::from_millis(250)).await;
        }
    }

    async fn complete(
        &self,
        claim: &ClaimResponse,
        status: &str,
        error: Option<&str>,
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
                    error,
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
    status: &'static str,
    error: Option<String>,
    next_sequence: u64,
    stdout_bytes: usize,
}

impl TerminalOutcome {
    fn failed(error: impl Into<String>, next_sequence: u64, stdout_bytes: usize) -> Self {
        Self {
            status: "failed",
            error: Some(error.into()),
            next_sequence,
            stdout_bytes,
        }
    }
}

enum ExitSignal {
    Exited(std::io::Result<ExitStatus>),
    Heartbeat,
    Deadline,
}

fn outcome_from_exit(
    status: ExitStatus,
    next_sequence: u64,
    stdout_bytes: usize,
) -> TerminalOutcome {
    if status.success() {
        TerminalOutcome {
            status: "succeeded",
            error: None,
            next_sequence,
            stdout_bytes,
        }
    } else {
        TerminalOutcome::failed(
            format!("agent exited with {status}"),
            next_sequence,
            stdout_bytes,
        )
    }
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
    text: String,
    total_bytes: usize,
}

async fn drain_stderr<R: AsyncRead + Unpin>(mut stderr: R) -> StderrCapture {
    let mut captured = Vec::with_capacity(8 * 1024);
    let mut total_bytes = 0_usize;
    let mut chunk = [0_u8; 8 * 1024];
    loop {
        let bytes = match stderr.read(&mut chunk).await {
            Ok(0) | Err(_) => break,
            Ok(bytes) => bytes,
        };
        total_bytes = total_bytes.saturating_add(bytes);
        let remaining = MAX_STDERR_CAPTURE_BYTES.saturating_sub(captured.len());
        captured.extend_from_slice(&chunk[..bytes.min(remaining)]);
    }
    StderrCapture {
        text: String::from_utf8_lossy(&captured).into_owned(),
        total_bytes,
    }
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

    #[tokio::test]
    async fn stderr_is_drained_and_capture_is_bounded() {
        let data = vec![b'x'; 100_000];
        let output = drain_stderr(&data[..]).await;
        assert_eq!(output.text.len(), MAX_STDERR_CAPTURE_BYTES);
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
