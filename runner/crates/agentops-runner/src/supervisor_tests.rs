use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant as StdInstant};

use agentops_protocol::{
    ClaimResponse, ClaimedRun, EvaluationSpec, ExecutionLimits, ExecutionMode, SCHEMA_VERSION,
};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::{ExecutionFailure, RunnerConfig, TrustedCommand, Worker, MAX_LINE_BYTES};

struct ServerState {
    completed: Mutex<Vec<Value>>,
    completion_body: Mutex<Option<String>>,
    chunked_completion: AtomicBool,
    events: Mutex<Vec<Value>>,
    accepted_events: Mutex<Vec<Value>>,
    event_attempts: AtomicUsize,
    heartbeat_attempts: AtomicUsize,
    initial_input_pending: Arc<std::sync::atomic::AtomicBool>,
    agent_exit_observed: Arc<AtomicBool>,
    final_read_window_closed: Arc<AtomicBool>,
    step_upload_in_flight: AtomicBool,
    step_upload_accepted: AtomicBool,
    cancel_during_delayed_step_upload: AtomicBool,
    event_response_delay_ms: AtomicU64,
    fail_event_responses: usize,
    heartbeat_command: &'static str,
}

struct TestServer {
    url: String,
    state: Arc<ServerState>,
    task: JoinHandle<()>,
}

impl TestServer {
    async fn start(heartbeat_command: &'static str, fail_event_responses: usize) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let state = Arc::new(ServerState {
            completed: Mutex::new(Vec::new()),
            completion_body: Mutex::new(None),
            chunked_completion: AtomicBool::new(false),
            events: Mutex::new(Vec::new()),
            accepted_events: Mutex::new(Vec::new()),
            event_attempts: AtomicUsize::new(0),
            heartbeat_attempts: AtomicUsize::new(0),
            initial_input_pending: Arc::default(),
            agent_exit_observed: Arc::default(),
            final_read_window_closed: Arc::default(),
            step_upload_in_flight: AtomicBool::new(false),
            step_upload_accepted: AtomicBool::new(false),
            cancel_during_delayed_step_upload: AtomicBool::new(false),
            event_response_delay_ms: AtomicU64::new(0),
            fail_event_responses,
            heartbeat_command,
        });
        let server_state = Arc::clone(&state);
        let task = tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let state = Arc::clone(&server_state);
                tokio::spawn(async move {
                    let _ = handle_request(stream, state).await;
                });
            }
        });
        Self {
            url: format!("http://{address}"),
            state,
            task,
        }
    }

    async fn completion(&self) -> Value {
        for _ in 0..100 {
            if let Some(value) = self.state.completed.lock().await.last().cloned() {
                return value;
            }
            sleep(Duration::from_millis(20)).await;
        }
        panic!("runner never submitted completion");
    }

    async fn accepted_events(&self) -> Vec<Value> {
        self.state.accepted_events.lock().await.clone()
    }

    async fn wait_for_accepted_events(&self, count: usize) -> Vec<Value> {
        for _ in 0..100 {
            let events = self.accepted_events().await;
            if events.len() >= count {
                return events;
            }
            sleep(Duration::from_millis(20)).await;
        }
        panic!("server did not acknowledge {count} accepted event requests");
    }

    async fn terminal_events(&self) -> Vec<Value> {
        let requests = self.state.events.lock().await.clone();
        requests
            .into_iter()
            .flat_map(|request| {
                request
                    .get("events")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default()
            })
            .filter(|event| {
                matches!(
                    event.get("type").and_then(Value::as_str),
                    Some("run_completed" | "run_failed" | "run_cancelled")
                )
            })
            .collect()
    }

    async fn terminal_event(&self) -> Value {
        let events = self.terminal_events().await;
        assert_eq!(events.len(), 1);
        events.into_iter().next().unwrap()
    }
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn handle_request(mut stream: TcpStream, state: Arc<ServerState>) -> std::io::Result<()> {
    let mut request = Vec::new();
    let mut chunk = [0_u8; 4 * 1024];
    let header_end = loop {
        let bytes = stream.read(&mut chunk).await?;
        if bytes == 0 {
            return Ok(());
        }
        request.extend_from_slice(&chunk[..bytes]);
        if let Some(index) = request.windows(4).position(|item| item == b"\r\n\r\n") {
            break index + 4;
        }
    };
    let headers = String::from_utf8_lossy(&request[..header_end]).into_owned();
    let content_length = headers
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .unwrap_or(0);
    while request.len() < header_end + content_length {
        let bytes = stream.read(&mut chunk).await?;
        if bytes == 0 {
            break;
        }
        request.extend_from_slice(&chunk[..bytes]);
    }

    let request_line = headers.lines().next().unwrap_or_default();
    let path = request_line.split_whitespace().nth(1).unwrap_or("/");
    let body = &request[header_end..request.len().min(header_end + content_length)];

    let mut accepted_event = None;
    let mut accepted_delayed_step = false;
    let (status, response) = if path.ends_with("/events") {
        let parsed = serde_json::from_slice(body).unwrap_or(Value::Null);
        let attempt = state.event_attempts.fetch_add(1, Ordering::SeqCst);
        if attempt < state.fail_event_responses {
            ("503 Service Unavailable", "{}".to_string())
        } else {
            let is_step = parsed
                .get("events")
                .and_then(Value::as_array)
                .is_some_and(|events| {
                    events.iter().any(|event| {
                        event.get("type").and_then(Value::as_str) == Some("step_completed")
                    })
                });
            let delay = state.event_response_delay_ms.load(Ordering::SeqCst);
            state.events.lock().await.push(parsed.clone());
            if is_step && delay > 0 {
                state.step_upload_in_flight.store(true, Ordering::SeqCst);
                sleep(Duration::from_millis(delay)).await;
                accepted_delayed_step = true;
            }
            accepted_event = Some(parsed);
            ("200 OK", r#"{"accepted_through":1000}"#.to_string())
        }
    } else if path.ends_with("/heartbeat") {
        let heartbeat_attempt = state.heartbeat_attempts.fetch_add(1, Ordering::SeqCst);
        let command = match state.heartbeat_command {
            "cancel_when_input_pending" if state.initial_input_pending.load(Ordering::SeqCst) => {
                "cancel"
            }
            "cancel_when_input_pending" | "reject_when_input_pending" => "continue",
            "cancel_after_one" if heartbeat_attempt >= 1 => "cancel",
            "cancel_after_two" if heartbeat_attempt >= 2 => "cancel",
            "cancel_after_two_during_final_upload"
                if heartbeat_attempt >= 2
                    && state.agent_exit_observed.load(Ordering::SeqCst)
                    && state.final_read_window_closed.load(Ordering::SeqCst)
                    && state.step_upload_in_flight.load(Ordering::SeqCst) =>
            {
                "cancel"
            }
            "cancel_after_three" if heartbeat_attempt >= 3 => "cancel",
            "cancel_after_one"
            | "cancel_after_two"
            | "cancel_after_two_during_final_upload"
            | "cancel_after_three" => "continue",
            command => command,
        };
        if command == "cancel"
            && state.agent_exit_observed.load(Ordering::SeqCst)
            && state.final_read_window_closed.load(Ordering::SeqCst)
            && state.step_upload_in_flight.load(Ordering::SeqCst)
        {
            state
                .cancel_during_delayed_step_upload
                .store(true, Ordering::SeqCst);
            for _ in 0..700 {
                if state.step_upload_accepted.load(Ordering::SeqCst) {
                    break;
                }
                sleep(Duration::from_millis(10)).await;
            }
            if state.heartbeat_command == "cancel_after_two_during_final_upload" {
                state.step_upload_in_flight.store(false, Ordering::SeqCst);
            }
        }
        (
            if state.heartbeat_command == "reject_when_input_pending"
                && state.initial_input_pending.load(Ordering::SeqCst)
            {
                "403 Forbidden"
            } else {
                "200 OK"
            },
            format!(
                r#"{{"command":"{}","lease_expires_at":"2026-07-16T00:00:15Z"}}"#,
                command
            ),
        )
    } else if path.ends_with("/complete") {
        let parsed = serde_json::from_slice(body).unwrap_or(Value::Null);
        let status = parsed
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("failed")
            .to_string();
        let attempt = parsed
            .get("execution")
            .and_then(|execution| execution.get("attempt"))
            .and_then(Value::as_u64)
            .unwrap_or(1);
        state.completed.lock().await.push(parsed);
        let response = completion_ack(&status, attempt);
        let body = state
            .completion_body
            .lock()
            .await
            .clone()
            .unwrap_or_else(|| response.to_string());
        ("200 OK", body)
    } else {
        ("404 Not Found", "{}".to_string())
    };
    let response = if path.ends_with("/complete") && state.chunked_completion.load(Ordering::SeqCst)
    {
        format!(
            "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n{:x}\r\n{response}\r\n0\r\n\r\n",
            response.len()
        )
    } else {
        format!(
            "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}",
            response.len()
        )
    };
    stream.write_all(response.as_bytes()).await?;
    stream.shutdown().await?;
    if let Some(parsed) = accepted_event {
        state.accepted_events.lock().await.push(parsed);
        if accepted_delayed_step {
            state.step_upload_accepted.store(true, Ordering::SeqCst);
            if state.heartbeat_command != "cancel_after_two_during_final_upload" {
                state.step_upload_in_flight.store(false, Ordering::SeqCst);
            }
        }
    }
    Ok(())
}

fn completion_ack(status: &str, attempt: u64) -> Value {
    json!({
        "run_id": "run-1",
        "attempt": attempt,
        "terminal_sequence": 2,
        "status": status,
        "run": {
            "id": "run-1",
            "experiment_id": "experiment-1",
            "kind": "baseline",
            "source_run_id": null,
            "policy_id": null,
            "regression_case_id": null,
            "status": status,
            "score": null,
            "metrics": {},
            "evaluation_spec": {
                "schema_version": 1,
                "run_id": "run-1",
                "experiment_id": "experiment-1",
                "scenario_id": "checkout-api-latency.v2",
                "task": "Investigate checkout latency",
                "seed": 42,
                "execution_mode": "fixture",
                "policy": null,
                "limits": {"timeout_ms": 10000, "max_output_bytes": 1048576},
                "scenario_params": {}
            },
            "error": null,
            "queued_at": "2026-07-16T00:00:00Z",
            "started_at": "2026-07-16T00:00:00Z",
            "completed_at": "2026-07-16T00:00:00Z"
        }
    })
}

#[path = "completion_tests.rs"]
mod completion_tests;
#[path = "exit_boundary_tests.rs"]
mod exit_boundary_tests;
pub(super) use exit_boundary_tests::ExitBoundaryProbe;

fn worker(server: &TestServer, python_code: String) -> Worker {
    Worker::new(RunnerConfig {
        server_url: server.url.clone(),
        runner_token: "test-token".into(),
        runner_id: "test-runner".into(),
        agent_program: "/usr/bin/python3".into(),
        agent_args: vec!["-c".into(), python_code.clone()],
        agent_workdir: None,
        agent_override: None,
        environment_workdir: None,
        environment_program: "/usr/bin/python3".into(),
        environment_args: vec!["-c".into(), "pass".into()],
        poll_interval: Duration::from_millis(10),
        environment_token_handoff_dir: Some(test_handoff_dir("worker")),
    })
    .map(|mut worker| {
        worker.legacy_agent_command = Some(TrustedCommand::new(
            "/usr/bin/python3",
            vec!["-c".to_string(), python_code],
        ));
        worker
    })
    .unwrap()
}

fn claim(timeout_ms: u64) -> ClaimResponse {
    let spec = EvaluationSpec {
        schema_version: SCHEMA_VERSION,
        run_id: "run-1".into(),
        experiment_id: "experiment-1".into(),
        scenario_id: "checkout-api-latency.v2".into(),
        agent_id: None,
        task: "Investigate checkout latency".into(),
        seed: 42,
        execution_mode: ExecutionMode::Fixture,
        policy: None,
        limits: ExecutionLimits {
            timeout_ms,
            max_output_bytes: 1_048_576,
        },
        scenario_params: HashMap::new(),
    };
    ClaimResponse {
        lease_id: "lease-1".into(),
        lease_expires_at: "2026-07-16T00:00:15Z".into(),
        attempt: 1,
        next_sequence: 1,
        recovery_reason: None,
        run: ClaimedRun {
            run_id: spec.run_id.clone(),
            evaluation_spec: spec,
        },
    }
}

fn pid_file(label: &str) -> PathBuf {
    std::env::temp_dir().join(format!(
        "agentops-runner-{}-{label}.pid",
        std::process::id()
    ))
}

fn test_handoff_dir(label: &str) -> PathBuf {
    super::unique_test_handoff_directory(&format!(
        "agentops-runner-{}-{label}-token-handoff",
        std::process::id()
    ))
}

// The parent shrinks the empty pipe before the first write, rather than racing
// a child-side F_SETPIPE_SZ against a write that may already have completed.
#[derive(Default)]
pub(super) struct InitialInputProbe {
    pending: Arc<std::sync::atomic::AtomicBool>,
    finished: std::sync::atomic::AtomicBool,
    dropped: std::sync::atomic::AtomicBool,
    pending_marker: PathBuf,
}

impl InitialInputProbe {
    pub(super) fn prepare(&self, stdin: &tokio::process::ChildStdin) {
        #[cfg(target_os = "linux")]
        {
            use std::os::fd::AsRawFd;
            // SAFETY: fcntl borrows the live pipe descriptor and changes only its capacity.
            let capacity =
                unsafe { nix::libc::fcntl(stdin.as_raw_fd(), nix::libc::F_SETPIPE_SZ, 4096) };
            assert_eq!(capacity, 4096, "could not set deterministic stdin capacity");
        }
        #[cfg(not(target_os = "linux"))]
        let _ = stdin;
    }
}

pub(super) async fn observe_initial_input<F: std::future::Future>(
    future: F,
    probe: Option<Arc<InitialInputProbe>>,
) -> F::Output {
    struct Dropped(Option<Arc<InitialInputProbe>>);
    impl Drop for Dropped {
        fn drop(&mut self) {
            if let Some(probe) = &self.0 {
                probe.dropped.store(true, Ordering::SeqCst);
            }
        }
    }
    let _dropped = Dropped(probe.clone());
    let mut future = std::pin::pin!(future);
    std::future::poll_fn(|cx| {
        let result = future.as_mut().poll(cx);
        if let Some(probe) = &probe {
            if result.is_pending() {
                if !probe.pending.swap(true, Ordering::SeqCst) {
                    std::fs::write(&probe.pending_marker, "pending").unwrap();
                }
            } else {
                probe.finished.store(true, Ordering::SeqCst);
            }
        }
        result
    })
    .await
}

fn blocked_input_children(
    environment_marker: &Path,
    agent_marker: &Path,
    pending_marker: &Path,
    output_bytes: usize,
    ready_delay: f64,
    close_input: bool,
) -> (TrustedCommand, TrustedCommand) {
    let environment_code = format!(
        r#"
import json, os, pathlib, signal, sys, time
sys.stdin.buffer.readline()
pathlib.Path({environment_marker:?}).write_text(str(os.getpid()))
time.sleep({ready_delay})
print(json.dumps({{"type":"env_ready", "payload":{{"mcp_url":"http://127.0.0.1:43127/mcp"}}}}), flush=True)
signal.pause()
"#
    );
    let agent_code = format!(
        r#"
import os, pathlib, signal, time
pathlib.Path({agent_marker:?}).write_text(str(os.getpid()))
while not pathlib.Path({pending_marker:?}).exists():
    time.sleep(0.01)
if {close_input}:
    os.close(0)
os.write(1, b"x" * {output_bytes})
os.write(2, b"y" * {output_bytes})
pathlib.Path({pending_marker:?} + ".output").write_text("written")
signal.pause()
"#,
        close_input = usize::from(close_input),
    );
    (
        TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), environment_code]),
        TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), agent_code]),
    )
}

#[cfg(target_os = "linux")]
async fn initial_input_child_pids(environment_marker: &Path, agent_marker: &Path) -> (u32, u32) {
    loop {
        let read_pid = |path| {
            std::fs::read_to_string(path)
                .ok()
                .and_then(|value| value.trim().parse::<u32>().ok())
        };
        if let (Some(environment), Some(agent)) =
            (read_pid(environment_marker), read_pid(agent_marker))
        {
            return (environment, agent);
        }
        sleep(Duration::from_millis(5)).await;
    }
}

async fn reap_blocked_input_group(environment_marker: &Path, agent_marker: &Path) {
    let environment_pid = std::fs::read_to_string(environment_marker)
        .ok()
        .and_then(|value| value.trim().parse::<i32>().ok());
    let agent_pid = std::fs::read_to_string(agent_marker)
        .ok()
        .and_then(|value| value.trim().parse::<i32>().ok());
    if let Some(pid) = environment_pid {
        let _ = nix::sys::signal::killpg(
            nix::unistd::Pid::from_raw(pid),
            nix::sys::signal::Signal::SIGKILL,
        );
    }
    for pid in [environment_pid, agent_pid].into_iter().flatten() {
        for _ in 0..100 {
            match nix::sys::wait::waitpid(
                nix::unistd::Pid::from_raw(pid),
                Some(nix::sys::wait::WaitPidFlag::WNOHANG),
            ) {
                Ok(nix::sys::wait::WaitStatus::StillAlive) => {
                    sleep(Duration::from_millis(10)).await
                }
                Ok(_) | Err(nix::errno::Errno::ECHILD) => break,
                Err(_) => break,
            }
        }
    }
    let _ = std::fs::remove_file(environment_marker);
    let _ = std::fs::remove_file(agent_marker);
}

async fn wait_for_pid(path: &Path) -> u32 {
    for _ in 0..100 {
        if let Ok(value) = std::fs::read_to_string(path) {
            return value.trim().parse().unwrap();
        }
        sleep(Duration::from_millis(20)).await;
    }
    panic!("agent did not record child pid");
}

async fn assert_process_gone(pid: u32) {
    let process = PathBuf::from(format!("/proc/{pid}"));
    for _ in 0..100 {
        if !process.exists() {
            return;
        }
        sleep(Duration::from_millis(20)).await;
    }
    panic!("descendant process {pid} survived runner termination");
}

fn process_tree_agent(path: &Path, before_sleep: &str) -> String {
    format!(
        "import pathlib, subprocess, time\nchild = subprocess.Popen(['sleep', '30'])\npathlib.Path({path:?}).write_text(str(child.pid))\n{before_sleep}\ntime.sleep(30)",
    )
}

#[tokio::test]
async fn invalid_jsonl_fails_run_and_terminates_process_group() {
    let server = TestServer::start("continue", 0).await;
    let path = pid_file("invalid-json");
    let _ = std::fs::remove_file(&path);
    let code = process_tree_agent(&path, "print('UNIQUE_INVALID_JSON_ATTACK', flush=True)");
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let pid = wait_for_pid(&path).await;
    assert_process_gone(pid).await;
    let completion = server.completion().await;
    assert_eq!(completion["status"], "failed");
    assert_eq!(completion["failure_kind"], "internal_failure");
    assert!(completion.get("error").is_none());
    let terminal = server.terminal_event().await;
    assert_eq!(terminal["payload"]["failure_kind"], "internal_failure");
    assert!(terminal["payload"].get("error").is_none());
    let terminal_json = serde_json::to_string(&server.terminal_events().await).unwrap();
    assert!(!terminal_json.contains("UNIQUE_INVALID_JSON_ATTACK"));
    let _ = std::fs::remove_file(path);
}

#[tokio::test]
async fn broken_pipe_while_writing_agent_input_terminates_process_group() {
    let path = pid_file("broken-pipe");
    let _ = std::fs::remove_file(&path);
    let code = format!(
        "import os, pathlib, time\nchild = os.fork()\nif child == 0:\n    os.close(0)\n    time.sleep(30)\n    os._exit(0)\npathlib.Path({path:?}).write_text(str(child))\nos.close(0)\ntime.sleep(30)",
    );
    let runner = Worker::new(RunnerConfig {
        server_url: "http://127.0.0.1:1".into(),
        runner_token: "test-token".into(),
        runner_id: "test-runner".into(),
        agent_program: "/usr/bin/python3".into(),
        agent_args: vec!["-c".into(), code],
        agent_workdir: None,
        agent_override: None,
        environment_workdir: None,
        environment_program: "/usr/bin/python3".into(),
        environment_args: vec!["-c".into(), "pass".into()],
        poll_interval: Duration::from_millis(10),
        environment_token_handoff_dir: Some(test_handoff_dir("broken-pipe")),
    })
    .unwrap();
    let mut spec = claim(10_000).run.evaluation_spec;
    spec.task = "x".repeat(1_000_000);

    let result = tokio::time::timeout(Duration::from_secs(5), runner.spawn_agent(&spec))
        .await
        .expect("spawn_agent did not return after the broken pipe");
    assert!(result.is_err(), "agent input unexpectedly succeeded");

    let pid = wait_for_pid(&path).await;
    assert_process_gone(pid).await;
    let _ = std::fs::remove_file(path);
}

#[tokio::test]
async fn nonzero_exit_maps_to_agent_exit_without_free_text() {
    let server = TestServer::start("continue", 0).await;
    let runner = worker(&server, "import sys; sys.exit(7)".to_string());

    runner.execute_claim(claim(10_000)).await.unwrap();

    let completion = server.completion().await;
    assert_eq!(completion["status"], "failed");
    assert_eq!(completion["failure_kind"], "agent_exit");
    assert!(completion.get("error").is_none());
    let terminal = server.terminal_event().await;
    assert_eq!(terminal["payload"]["failure_kind"], "agent_exit");
    assert!(terminal["payload"].get("error").is_none());
}

#[tokio::test]
async fn output_limit_maps_to_structured_failure() {
    let server = TestServer::start("continue", 0).await;
    let code = format!("print('x' * {}, flush=True)", MAX_LINE_BYTES + 1);
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let completion = server.completion().await;
    assert_eq!(completion["status"], "failed");
    assert_eq!(completion["failure_kind"], "output_limit_exceeded");
    assert!(completion.get("error").is_none());
    let terminal = server.terminal_event().await;
    assert_eq!(terminal["payload"]["failure_kind"], "output_limit_exceeded");
    assert!(terminal["payload"].get("error").is_none());
}

#[tokio::test]
async fn provider_event_maps_to_provider_failure_without_raw_provider_text() {
    let server = TestServer::start("continue", 0).await;
    let code = r#"
import json, sys
print(json.dumps({"type": "process_output", "payload": {
    "kind": "provider",
    "stream": "stderr",
    "provider_error": {
        "code": "PROVIDER_UNKNOWN",
        "message": "UNIQUE_RAW_PROVIDER_TEXT",
        "retryable": False,
        "attempts": 1
    }
}}), flush=True)
sys.exit(1)
"#
    .to_string();
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let completion = server.completion().await;
    assert_eq!(completion["status"], "failed");
    assert_eq!(completion["failure_kind"], "provider_failure");
    assert!(completion.get("error").is_none());
    let terminal = server.terminal_event().await;
    assert_eq!(terminal["payload"]["failure_kind"], "provider_failure");
    assert!(terminal["payload"].get("error").is_none());
    let terminal_json = serde_json::to_string(&terminal).unwrap();
    assert!(!terminal_json.contains("UNIQUE_RAW_PROVIDER_TEXT"));
}

#[tokio::test]
async fn timeout_terminates_process_group() {
    let server = TestServer::start("continue", 0).await;
    let path = pid_file("timeout");
    let _ = std::fs::remove_file(&path);
    let code = process_tree_agent(&path, "pass");
    let runner = worker(&server, code);

    runner.execute_claim(claim(1_000)).await.unwrap();

    let pid = wait_for_pid(&path).await;
    assert_process_gone(pid).await;
    let completion = server.completion().await;
    assert_eq!(completion["status"], "timed_out");
    assert_eq!(completion["failure_kind"], "timed_out");
    assert!(completion.get("error").is_none());
    let terminal = server.terminal_event().await;
    assert_eq!(terminal["payload"]["failure_kind"], "timed_out");
    assert!(terminal["payload"].get("error").is_none());
    let _ = std::fs::remove_file(path);
}

#[tokio::test]
async fn heartbeat_cancel_terminates_process_group() {
    let server = TestServer::start("cancel", 0).await;
    let path = pid_file("cancel");
    let _ = std::fs::remove_file(&path);
    let code = process_tree_agent(&path, "pass");
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let pid = wait_for_pid(&path).await;
    assert_process_gone(pid).await;
    let completion = server.completion().await;
    assert_eq!(completion["status"], "cancelled");
    assert_eq!(completion["failure_kind"], "cancelled");
    assert!(completion.get("error").is_none());
    let terminal = server.terminal_event().await;
    assert_eq!(terminal["payload"]["failure_kind"], "cancelled");
    assert!(terminal["payload"].get("error").is_none());
    assert_eq!(server.state.completed.lock().await.len(), 1);
    let _ = std::fs::remove_file(path);
}

#[cfg(target_os = "linux")]
async fn check_blocked_initial_agent_input(label: &str, expected: ExecutionFailure) {
    // ADR-0002: backpressure must not block cancellation, lease fencing, or the
    // attempt deadline. ADR-0008: Agent output is drained, never trusted JSONL.
    let server = TestServer::start(
        match expected {
            ExecutionFailure::Cancelled => "cancel_when_input_pending",
            ExecutionFailure::LeaseRejected => "reject_when_input_pending",
            _ => "continue",
        },
        0,
    )
    .await;
    let environment_marker = pid_file(&format!("stdin-{label}-environment"));
    let agent_marker = pid_file(&format!("stdin-{label}-agent"));
    let pending_marker = pid_file(&format!("stdin-{label}-pending"));
    let output_marker = PathBuf::from(format!("{}.output", pending_marker.display()));
    for path in [
        &environment_marker,
        &agent_marker,
        &pending_marker,
        &output_marker,
    ] {
        let _ = std::fs::remove_file(path);
    }
    let probe = Arc::new(InitialInputProbe {
        pending: server.state.initial_input_pending.clone(),
        pending_marker: pending_marker.clone(),
        ..Default::default()
    });
    let deadline_test = expected == ExecutionFailure::TimedOut;
    let (environment, agent) = blocked_input_children(
        &environment_marker,
        &agent_marker,
        &pending_marker,
        if expected == ExecutionFailure::OutputLimitExceeded {
            // Fits even the minimum pipe capacity while exceeding the 1024-byte limit.
            2048
        } else {
            128
        },
        if deadline_test { 1.0 } else { 0.0 },
        expected == ExecutionFailure::Internal,
    );
    let mut run_claim = claim(if deadline_test { 2000 } else { 10_000 });
    // Only the stdin spec is large. The task environment variable remains small.
    run_claim.run.evaluation_spec.experiment_id = "e".repeat(16_000);
    if expected == ExecutionFailure::OutputLimitExceeded {
        run_claim.run.evaluation_spec.limits.max_output_bytes = 1024;
    }
    assert!(run_claim.run.evaluation_spec.validate().is_ok());
    let payload_bytes = serde_json::to_vec(&run_claim.run.evaluation_spec)
        .unwrap()
        .len()
        + 1;
    assert!(payload_bytes > 4096 && payload_bytes <= MAX_LINE_BYTES);
    let mut runner = worker(&server, "pass".to_string());
    runner.initial_input_probe = Some(probe.clone());
    let started = StdInstant::now();
    let mut task = tokio::spawn(async move {
        runner
            .supervise_attempt(&run_claim, environment, agent)
            .await
    });
    let setup = tokio::time::timeout(Duration::from_secs(3), async {
        let (environment_pid, agent_pid) =
            initial_input_child_pids(&environment_marker, &agent_marker).await;
        while !probe.pending.load(Ordering::SeqCst)
            || (expected == ExecutionFailure::OutputLimitExceeded && !output_marker.exists())
        {
            sleep(Duration::from_millis(5)).await;
        }
        (environment_pid, agent_pid)
    })
    .await;
    let result = if setup.is_ok() {
        tokio::time::timeout(Duration::from_secs(5), &mut task)
            .await
            .ok()
    } else {
        None
    };
    if result.is_none() {
        task.abort();
        let _ = task.await;
    }
    // Snapshot Runner-owned reaping before fallback cleanup can hide a leak.
    let runner_reaped = setup.as_ref().is_ok_and(|(environment_pid, agent_pid)| {
        !Path::new(&format!("/proc/{environment_pid}")).exists()
            && !Path::new(&format!("/proc/{agent_pid}")).exists()
    });
    // Cleanup runs before the responsiveness assertion, including on pre-fix red.
    reap_blocked_input_group(&environment_marker, &agent_marker).await;
    let output_written = output_marker.exists();
    let _ = std::fs::remove_file(&pending_marker);
    let _ = std::fs::remove_file(&output_marker);
    if let Ok((environment_pid, agent_pid)) = &setup {
        assert_process_gone(*environment_pid).await;
        assert_process_gone(*agent_pid).await;
    }
    assert!(setup.is_ok(), "blocked-input setup failed: {setup:?}");
    assert!(
        probe.pending.load(Ordering::SeqCst),
        "initial write never returned Pending"
    );
    let outcome = result
        .expect("supervision did not respond while initial Agent write was demonstrably pending")
        .unwrap()
        .unwrap();
    assert_eq!(outcome.failure, Some(expected));
    if expected == ExecutionFailure::OutputLimitExceeded {
        assert!(output_written, "Agent did not write over-limit output");
    }
    assert!(runner_reaped, "Runner did not reap both direct children");
    assert!(!outcome.eligible);
    assert_eq!(
        probe.finished.load(Ordering::SeqCst),
        expected == ExecutionFailure::Internal
    );
    assert!(probe.dropped.load(Ordering::SeqCst), "input future leaked");
    if deadline_test {
        // Includes the original 2s budget and one accepted 2s TERM grace;
        // restarting the budget at Agent launch would take at least 5s.
        assert!(started.elapsed() < Duration::from_millis(4800));
    }
    assert_eq!(
        server.state.events.lock().await.len(),
        1,
        "Agent output became trusted events"
    );
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_cancellation_interrupts_blocked_initial_agent_input() {
    check_blocked_initial_agent_input("cancel", ExecutionFailure::Cancelled).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_output_limit_interrupts_blocked_initial_agent_input() {
    check_blocked_initial_agent_input("output", ExecutionFailure::OutputLimitExceeded).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_deadline_interrupts_blocked_initial_agent_input() {
    check_blocked_initial_agent_input("deadline", ExecutionFailure::TimedOut).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_lease_rejection_interrupts_blocked_initial_agent_input() {
    check_blocked_initial_agent_input("lease", ExecutionFailure::LeaseRejected).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_initial_agent_input_error_fails_closed() {
    check_blocked_initial_agent_input("broken-pipe", ExecutionFailure::Internal).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_initial_agent_input_delivered_once_across_monitor_turns() {
    let server = TestServer::start("continue", 0).await;
    let environment_marker = pid_file("stdin-delivery-environment");
    let agent_marker = pid_file("stdin-delivery-agent");
    let pending_marker = pid_file("stdin-delivery-pending");
    let release_marker = pid_file("stdin-delivery-release");
    let received_path = pid_file("stdin-delivery-received");
    let paths = [
        &environment_marker,
        &agent_marker,
        &pending_marker,
        &release_marker,
        &received_path,
    ];
    for path in paths {
        let _ = std::fs::remove_file(path);
    }
    let probe = Arc::new(InitialInputProbe {
        pending: server.state.initial_input_pending.clone(),
        pending_marker: pending_marker.clone(),
        ..Default::default()
    });
    let (environment, _) = blocked_input_children(
        &environment_marker,
        &agent_marker,
        &pending_marker,
        0,
        0.0,
        false,
    );
    let agent_code = format!(
        r#"
import fcntl, os, pathlib, time
pathlib.Path({agent_marker:?}).write_text(str(os.getpid()))
while not pathlib.Path({release_marker:?}).exists():
    time.sleep(0.01)
# Each output pipe is still empty, so shrinking here cannot race an earlier write.
for fd in (1, 2):
    assert fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, 4096) == 4096
# Both drains must run before stdin can make progress.
os.write(1, b"x" * 32768)
os.write(2, b"y" * 32768)
received = bytearray()
while True:
    chunk = os.read(0, 113)
    if not chunk:
        break
    received.extend(chunk)
pathlib.Path({received_path:?}).write_bytes(received)
print('{{"type":"step_completed","payload":{{"forged":"stdin-delivery"}}}}', flush=True)
"#
    );
    let mut run_claim = claim(10_000);
    run_claim.run.evaluation_spec.experiment_id = "e".repeat(16_000);
    assert!(run_claim.run.evaluation_spec.validate().is_ok());
    let mut expected = serde_json::to_vec(&run_claim.run.evaluation_spec).unwrap();
    expected.push(b'\n');
    assert!(expected.len() <= MAX_LINE_BYTES);
    let mut runner = worker(&server, "pass".to_string());
    runner.initial_input_probe = Some(probe.clone());
    let mut task = tokio::spawn(async move {
        runner
            .supervise_attempt(
                &run_claim,
                environment,
                TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), agent_code]),
            )
            .await
    });
    let result: anyhow::Result<_> = async {
        let (environment_pid, agent_pid) = tokio::time::timeout(
            Duration::from_secs(3),
            initial_input_child_pids(&environment_marker, &agent_marker),
        )
        .await?;
        tokio::time::timeout(Duration::from_secs(4), async {
            while !probe.pending.load(Ordering::SeqCst)
                || server.state.heartbeat_attempts.load(Ordering::SeqCst) < 3
            {
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await?;
        anyhow::ensure!(!probe.finished.load(Ordering::SeqCst));
        std::fs::write(&release_marker, "read")?;
        let outcome = tokio::time::timeout(Duration::from_secs(5), &mut task).await???;
        Ok((environment_pid, agent_pid, outcome))
    }
    .await;
    if result.is_err() {
        task.abort();
        let _ = task.await;
    }
    let runner_reaped = result
        .as_ref()
        .is_ok_and(|(environment_pid, agent_pid, _)| {
            !Path::new(&format!("/proc/{environment_pid}")).exists()
                && !Path::new(&format!("/proc/{agent_pid}")).exists()
        });
    reap_blocked_input_group(&environment_marker, &agent_marker).await;
    let received = std::fs::read(&received_path);
    for path in paths {
        let _ = std::fs::remove_file(path);
    }
    let (environment_pid, agent_pid, outcome) = result.unwrap();
    assert!(runner_reaped, "Runner did not reap both direct children");
    assert_process_gone(environment_pid).await;
    assert_process_gone(agent_pid).await;
    assert_eq!(outcome.failure, Some(ExecutionFailure::MissingSubmission));
    assert!(!outcome.eligible);
    assert_eq!(
        received.unwrap(),
        expected,
        "input was restarted, truncated, or lacked EOF"
    );
    assert!(probe.finished.load(Ordering::SeqCst));
    assert!(probe.dropped.load(Ordering::SeqCst));
    assert_eq!(
        server.state.events.lock().await.len(),
        1,
        "Agent output became trusted events"
    );
}

#[tokio::test]
async fn dual_supervision_does_not_launch_agent_without_valid_environment_ready() {
    let server = TestServer::start("continue", 0).await;
    let marker = pid_file("dual-agent-not-launched");
    let _ = std::fs::remove_file(&marker);
    let env = TrustedCommand::new(
        "/usr/bin/python3",
        vec!["-c", "print('{\\\"type\\\":\\\"env_ready\\\",\\\"payload\\\":{\\\"mcp_url\\\":\\\"http://127.0.0.1:0/mcp\\\"}}', flush=True)"],
    );
    let agent = TrustedCommand::new(
        "/usr/bin/python3",
        vec![
            "-c".to_string(),
            format!("open({marker:?}, 'w').write('launched')"),
        ],
    );
    let runner = worker(&server, "pass".to_string());
    let outcome = runner
        .supervise_attempt(&claim(10_000), env, agent)
        .await
        .unwrap();

    assert_eq!(
        outcome.failure,
        Some(ExecutionFailure::InvalidEnvironmentReady)
    );
    assert!(!outcome.agent_launched);
    assert!(!marker.exists());
    let events = server.state.events.lock().await;
    assert_eq!(events[0]["events"][0]["type"], "run_started");
    assert_eq!(events[0]["events"][0]["payload"]["source"], "environment");
    let _ = std::fs::remove_file(marker);
}

#[tokio::test]
async fn dual_supervision_relays_only_environment_steps_and_never_agent_json() {
    let server = TestServer::start("continue", 1).await;
    let env_code = r#"
import json, time
print(json.dumps({"type":"env_ready", "payload":{"mcp_url":"http://127.0.0.1:43123/mcp"}}), flush=True)
print(json.dumps({"type":"step_completed", "payload":{"index":0,"tool_call":{"name":"check_service_health","arguments":{}},"environment":{"kind":"world","result":"ok"}}}), flush=True)
time.sleep(30)
"#;
    let agent_code = r#"
import os, json
print(json.dumps({"type":"step_completed","payload":{"forged":"agent"}}), flush=True)
assert len(os.environ["AGENTOPS_ENV_TOKEN"]) == 64
assert os.environ["AGENTOPS_ENV_MCP_URL"].endswith("/mcp")
"#;
    let runner = worker(&server, "pass".to_string());
    let outcome = runner
        .supervise_attempt(
            &claim(10_000),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", env_code]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", agent_code]),
        )
        .await
        .unwrap();

    assert!(outcome.agent_launched);
    assert_eq!(outcome.failure, Some(ExecutionFailure::MissingSubmission));
    let events = server.state.events.lock().await;
    let uploaded = serde_json::to_string(&*events).unwrap();
    assert!(uploaded.contains("check_service_health"));
    assert!(!uploaded.contains("forged"));
}

#[tokio::test]
async fn dual_supervision_monitors_heartbeat_during_final_upload_after_agent_exit() {
    let server = TestServer::start("cancel_after_two_during_final_upload", 0).await;
    server
        .state
        .event_response_delay_ms
        .store(5_000, Ordering::SeqCst);
    let agent_started_marker = pid_file("dual-final-upload-agent-started");
    let release_marker = pid_file("dual-final-upload-agent-exit-release");
    let _ = std::fs::remove_file(&agent_started_marker);
    let _ = std::fs::remove_file(&release_marker);
    let env_code = r#"
import json, pathlib
print(json.dumps({"type":"env_ready", "payload":{"mcp_url":"http://127.0.0.1:43126/mcp"}}), flush=True)
release = pathlib.Path(__RELEASE_MARKER__)
while not release.exists():
    pass
print('{"type":"step_completed","payload":{"tool_call":{"name":"submit_result","arguments":{}},"environment":{"kind":"control","control":"submit_result","result":"', end='', flush=True)
print('ok","check_called":true,"finalized":true}}}', flush=True)
while True:
    pass
"#
    .replace("__RELEASE_MARKER__", &format!("{release_marker:?}"));
    let mut runner = worker(&server, "pass".to_string());
    runner.agent_exit_probe = Some(server.state.agent_exit_observed.clone());
    runner.final_read_window_probe = Some(server.state.final_read_window_closed.clone());
    let watcher_probe = server.state.agent_exit_observed.clone();
    let watcher_release_marker = release_marker.clone();
    let watcher = tokio::spawn(async move {
        tokio::time::timeout(Duration::from_secs(8), async {
            loop {
                if watcher_probe.load(Ordering::SeqCst) {
                    std::fs::write(&watcher_release_marker, "observed").unwrap();
                    return;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("Agent exit was not observed before the named test timeout");
    });
    let supervision = tokio::time::timeout(
        Duration::from_secs(8),
        runner.supervise_attempt(
            &claim(10_000),
            TrustedCommand::new(
                "/usr/bin/python3",
                vec!["-c".to_string(), env_code.to_string()],
            ),
            TrustedCommand::new(
                "/usr/bin/python3",
                vec![
                    "-c".to_string(),
                    format!(
                        r#"import os, pathlib
pathlib.Path({:?}).write_text(str(os.getpid()))
"#,
                        agent_started_marker
                    ),
                ],
            ),
        ),
    )
    .await;
    watcher.abort();
    let _ = watcher.await;
    let _ = std::fs::remove_file(&agent_started_marker);
    let _ = std::fs::remove_file(&release_marker);
    let outcome = supervision
        .expect("supervision did not monitor the final upload")
        .unwrap();
    assert_eq!(outcome.failure, Some(ExecutionFailure::Cancelled));
    assert!(!outcome.eligible);
    assert!(outcome.agent_launched);
    assert_eq!(outcome.next_sequence, 2);
    assert!(server.state.agent_exit_observed.load(Ordering::SeqCst));
    assert!(server
        .state
        .cancel_during_delayed_step_upload
        .load(Ordering::SeqCst));
    assert!(server.state.heartbeat_attempts.load(Ordering::SeqCst) >= 3);
    assert!(server.state.final_read_window_closed.load(Ordering::SeqCst));
    let accepted_requests = server.wait_for_accepted_events(2).await;
    assert_eq!(accepted_requests.len(), 2);
    assert!(server.state.step_upload_accepted.load(Ordering::SeqCst));
    assert_eq!(server.state.event_attempts.load(Ordering::SeqCst), 2);
    let uploaded_events: Vec<&Value> = accepted_requests
        .iter()
        .flat_map(|request| request["events"].as_array().into_iter().flatten())
        .collect();
    assert_eq!(uploaded_events.len(), 2);
    assert_eq!(
        uploaded_events
            .iter()
            .map(|event| event["sequence"].as_u64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 2]
    );
    assert_eq!(uploaded_events[0]["type"], "run_started");
    assert_eq!(
        uploaded_events[0]["payload"],
        json!({"attempt": 1, "source": "environment"})
    );
    assert_eq!(uploaded_events[1]["type"], "step_completed");
    assert_eq!(
        uploaded_events[1]["payload"],
        json!({
            "tool_call": {"name": "submit_result", "arguments": {}},
            "environment": {
                "kind": "control",
                "control": "submit_result",
                "result": "ok",
                "check_called": true,
                "finalized": true
            },
            "attempt": 1
        })
    );
    let _ = std::fs::remove_file(&agent_started_marker);
    let _ = std::fs::remove_file(&release_marker);
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn dual_supervision_reconstructs_environment_line_across_agent_exit_boundary() {
    exit_boundary_tests::check_exit_boundary(
        "world-only",
        Some(ExecutionFailure::MissingSubmission),
        1,
    )
    .await;
}

#[tokio::test]
async fn dual_supervision_shared_environment_output_limit_fails_closed_after_drains() {
    let server = TestServer::start("continue", 0).await;
    let env_code = r#"
import json, os, signal, time
print(json.dumps({"type":"env_ready", "payload":{"mcp_url":"http://127.0.0.1:43128/mcp"}}), flush=True)
os.write(2, b"environment-stderr" * 1000)
os.write(1, b"environment-stdout" * 1000)
signal.pause()
"#;
    let mut run_claim = claim(10_000);
    run_claim.run.evaluation_spec.limits.max_output_bytes = 1024;
    let outcome = worker(&server, "pass".to_string())
        .supervise_attempt(
            &run_claim,
            TrustedCommand::new("/usr/bin/python3", vec!["-c", env_code]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", "pass"]),
        )
        .await
        .unwrap();

    assert_eq!(outcome.failure, Some(ExecutionFailure::OutputLimitExceeded));
    assert!(!outcome.eligible);
    assert!(!outcome.planned_environment_teardown);
}

#[tokio::test]
async fn dual_supervision_eligible_attempt_retains_token_until_terminal_ack() {
    let server = TestServer::start("continue", 0).await;
    let env_code = r#"
import json, time
print(json.dumps({"type":"env_ready", "payload":{"mcp_url":"http://127.0.0.1:43124/mcp"}}), flush=True)
print(json.dumps({"type":"step_completed", "payload":{"index":0,"tool_call":{"name":"submit_result","arguments":{}},"environment":{"kind":"control","control":"submit_result","result":"ok","check_called":True,"finalized":True}}}), flush=True)
time.sleep(30)
"#;
    let runner = worker(&server, "pass".to_string());
    let original_lease = runner.environment_token_handoff.acquire("run-1").unwrap();
    let original_token = original_lease.token.clone();
    drop(original_lease);
    let outcome = runner
        .supervise_attempt(
            &claim(10_000),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", env_code]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", "pass"]),
        )
        .await
        .unwrap();

    assert!(outcome.eligible);
    assert!(outcome.submitted);
    assert!(outcome.check_succeeded);
    assert!(outcome.planned_environment_teardown);
    assert_eq!(outcome.failure, None);

    // Eligibility is not Control Plane terminal-completion acknowledgement:
    // a replacement handoff before that acknowledgement must reuse the record.
    let handoff_dir = runner.environment_token_handoff.directory.clone();
    let record_path = handoff_dir.join(format!(
        "{}{}",
        super::run_id_file_key("run-1"),
        super::ENV_TOKEN_RECORD_SUFFIX
    ));
    assert!(
        record_path.exists(),
        "eligible attempt removed its token record"
    );
    let token = super::read_token_record(&record_path, "run-1")
        .unwrap()
        .expect("eligible attempt did not retain a token record");
    assert_eq!(token, original_token);
    let replacement = Worker::new(runner.config.clone()).unwrap();
    let replacement_lease = replacement
        .environment_token_handoff
        .acquire("run-1")
        .unwrap();
    assert_eq!(replacement_lease.token, original_token);
    drop(replacement_lease);
    let mut recovery_claim = claim(10_000);
    recovery_claim.attempt = 2;
    recovery_claim.next_sequence = outcome.next_sequence;
    let recovered = replacement
        .supervise_attempt(
            &recovery_claim,
            TrustedCommand::new("/usr/bin/python3", vec!["-c", env_code]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", "pass"]),
        )
        .await
        .unwrap();
    assert!(recovered.eligible);
    assert_eq!(recovered.failure, None);
    assert_eq!(
        super::read_token_record(&record_path, "run-1").unwrap(),
        Some(original_token)
    );
    // This seam emits no terminal completion. The explicit cleanup invocation
    // below models the future caller's acknowledged-terminal precondition only.
    assert!(server.state.completed.lock().await.is_empty());
    assert!(runner
        .cleanup_environment_token_after_terminal_ack("run-1")
        .unwrap());
    assert!(!handoff_dir
        .join(format!(
            "{}{}",
            super::run_id_file_key("run-1"),
            super::ENV_TOKEN_RECORD_SUFFIX
        ))
        .exists());
    assert!(!runner
        .cleanup_environment_token_after_terminal_ack("run-1")
        .unwrap());
}

#[tokio::test]
async fn dual_supervision_reuses_one_capability_per_run_without_exposing_it() {
    let server = TestServer::start("continue", 2).await;
    let token_marker = pid_file("token-observation");
    let _ = std::fs::remove_file(&token_marker);
    let env_code = format!(
        r#"
import json, os, pathlib, time
with pathlib.Path({token_marker:?}).open("a") as marker:
    marker.write(os.environ["AGENTOPS_ENV_TOKEN"] + chr(10))
print(json.dumps({{"type":"env_ready", "payload":{{"mcp_url":"http://127.0.0.1:43125/mcp"}}}}), flush=True)
print(json.dumps({{"type":"step_completed", "payload":{{"index":0,"tool_call":{{"name":"x","arguments":{{}}}},"environment":{{"kind":"world","result":"ok"}}}}}}), flush=True)
time.sleep(30)
"#
    );
    let runner = worker(&server, "pass".to_string());
    let first = runner
        .supervise_attempt(
            &claim(10_000),
            TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), env_code.clone()]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", "pass"]),
        )
        .await
        .unwrap();
    let second_claim = {
        let mut value = claim(10_000);
        value.attempt = 2;
        value.next_sequence = first.next_sequence;
        value
    };
    let second = runner
        .supervise_attempt(
            &second_claim,
            TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), env_code.clone()]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", "pass"]),
        )
        .await
        .unwrap();
    let third_claim = {
        let mut value = second_claim.clone();
        value.run.run_id = "run-2".into();
        value.run.evaluation_spec.run_id = "run-2".into();
        value.attempt = 1;
        value.next_sequence = second.next_sequence;
        value
    };
    let third = runner
        .supervise_attempt(
            &third_claim,
            TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), env_code]),
            TrustedCommand::new("/usr/bin/python3", vec!["-c", "pass"]),
        )
        .await
        .unwrap();

    let observed_tokens: Vec<String> = std::fs::read_to_string(&token_marker)
        .unwrap()
        .lines()
        .map(str::to_owned)
        .collect();
    assert_eq!(observed_tokens.len(), 3);
    assert_eq!(observed_tokens[0], observed_tokens[1]);
    assert_ne!(observed_tokens[1], observed_tokens[2]);
    assert_eq!(observed_tokens[0].len(), 64);
    assert!(observed_tokens[0]
        .bytes()
        .all(|byte| byte.is_ascii_hexdigit()));
    assert_eq!(second.first_sequence, first.next_sequence);
    assert_eq!(third.first_sequence, second.next_sequence);
    // The first Attempt consumes the server's two transient failures. Retry
    // counters describe transport retries; accepted event sequences remain
    // unique and contiguous across recovery. Capability values never enter
    // trusted events or completion metrics.
    assert!(first.event_retries >= 2);
    assert_eq!(second.event_retries, 0);
    assert_eq!(third.event_retries, 0);
    let events = server.state.events.lock().await;
    let encoded_events = serde_json::to_string(&*events).unwrap();
    assert!(!observed_tokens
        .iter()
        .any(|token| encoded_events.contains(token)));
    let sequences: Vec<u64> = events
        .iter()
        .flat_map(|request| request["events"].as_array().unwrap())
        .map(|event| event["sequence"].as_u64().unwrap())
        .collect();
    assert_eq!(sequences, vec![1, 2, 3, 4, 5, 6]);
    let _ = std::fs::remove_file(token_marker);
}

#[tokio::test]
async fn stderr_is_drained_and_transient_event_failure_is_retried() {
    let server = TestServer::start("continue", 2).await;
    let code = r#"
import json, sys
sys.stderr.write("UNIQUE_RAW_STDERR_ATTACK" + "x" * 200000)
sys.stderr.flush()
print(json.dumps({"type": "process_output", "payload": {"stream": "stdout", "content": "ready"}}), flush=True)
"#
    .to_string();
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let completion = server.completion().await;
    assert_eq!(completion["status"], "succeeded");
    assert!(completion.get("failure_kind").is_none());
    assert!(completion.get("error").is_none());
    assert!(completion["metrics"]["stderr_bytes"].as_u64().unwrap() >= 200_000);
    assert!(server.state.event_attempts.load(Ordering::SeqCst) >= 4);
    assert_eq!(completion["metrics"]["event_retries"], 2);
    let terminal = server.terminal_event().await;
    assert!(terminal["payload"].get("failure_kind").is_none());
    assert!(terminal["payload"].get("error").is_none());
    let terminal_json = serde_json::to_string(&terminal).unwrap();
    assert!(!terminal_json.contains("UNIQUE_RAW_STDERR_ATTACK"));
    assert_eq!(server.state.completed.lock().await.len(), 1);
}

#[cfg(target_os = "linux")]
fn term_grace_child(code: &str, group: i32) -> tokio::process::Command {
    let mut command = tokio::process::Command::new("/usr/bin/python3");
    command
        .args(["-u", "-c", code])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .kill_on_drop(true)
        .process_group(group);
    command
}

#[cfg(target_os = "linux")]
async fn term_grace_line<R: tokio::io::AsyncBufRead + Unpin>(
    reader: &mut R,
) -> anyhow::Result<String> {
    use tokio::io::AsyncBufReadExt;
    let mut line = String::new();
    let bytes = tokio::time::timeout(Duration::from_secs(5), reader.read_line(&mut line)).await??;
    anyhow::ensure!(bytes > 0, "child closed its handshake pipe");
    Ok(line.trim().to_string())
}

#[cfg(target_os = "linux")]
fn term_grace_process_state(pid: u32) -> Option<char> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    stat.rsplit_once(") ")?.1.chars().next()
}

#[cfg(target_os = "linux")]
async fn term_grace_wait_dead(pid: u32) -> (bool, Vec<char>) {
    let mut states = Vec::new();
    for _ in 0..200 {
        match term_grace_process_state(pid) {
            None => return (true, states),
            Some(state) => {
                if states.last() != Some(&state) {
                    states.push(state);
                }
                // Orphan descendants are reaped by init, not by the Runner.
                if matches!(state, 'Z' | 'X') {
                    return (true, states);
                }
            }
        }
        sleep(Duration::from_millis(10)).await;
    }
    (false, states)
}

#[cfg(target_os = "linux")]
async fn check_process_group_descendant_grace(reap_leader_first: bool) {
    // ADR-0002: direct-child exit must not shorten a descendant's TERM grace.
    // The leader waits for the descendant's TERM acknowledgement before exiting.
    let code = r#"
import os, signal, sys
read_fd, write_fd = os.pipe()
child = os.fork()
if child == 0:
    os.close(read_fd)
    def term(signum, frame):
        print('TERM', flush=True)
        try:
            os.write(write_fd, b't')
        except BrokenPipeError:
            pass
    signal.signal(signal.SIGTERM, term)
    os.write(write_fd, b'r')
    while True:
        signal.pause()
os.close(write_fd)
os.read(read_fd, 1)
def term(signum, frame):
    os.read(read_fd, 1)
    os._exit(0)
signal.signal(signal.SIGTERM, term)
print(child, flush=True)
sys.stdin.buffer.read(1)
os._exit(0)
"#;
    let mut leader = term_grace_child(code, 0).spawn().unwrap();
    let pgid = leader.id().unwrap();
    let mut output = tokio::io::BufReader::new(leader.stdout.take().unwrap());
    let mut descendant = None;
    let result: anyhow::Result<_> = async {
        let pid = term_grace_line(&mut output).await?.parse::<u32>()?;
        descendant = Some(pid);
        anyhow::ensure!(nix::unistd::getpgrp().as_raw() != pgid as i32);
        anyhow::ensure!(
            nix::unistd::getpgid(Some(nix::unistd::Pid::from_raw(pid as i32)))?.as_raw()
                == pgid as i32
        );
        if reap_leader_first {
            leader.stdin.take().unwrap().write_all(b"x").await?;
            let status = tokio::time::timeout(Duration::from_secs(5), leader.wait()).await??;
            anyhow::ensure!(status.success() && leader.id().is_none());
        }
        let started = tokio::time::Instant::now();
        let observe = async {
            let term = term_grace_line(&mut output).await?;
            anyhow::ensure!(term == "TERM", "missing explicit TERM acknowledgement");
            // Observe inside the grace, after an explicit TERM acknowledgement,
            // not after an arbitrary startup delay.
            sleep(Duration::from_secs(1)).await;
            Ok::<_, anyhow::Error>((
                term_grace_process_state(pid),
                term_grace_process_state(pgid),
            ))
        };
        let terminate = async {
            tokio::time::timeout(
                Duration::from_secs(6),
                super::terminate_process_group(Some(pgid), &mut leader, None),
            )
            .await?;
            Ok::<_, anyhow::Error>(started.elapsed())
        };
        let (states, elapsed) = tokio::join!(observe, terminate);
        let states = states?;
        let elapsed = elapsed?;
        let reaped = leader.id().is_none();
        let dead = term_grace_wait_dead(pid).await;
        let mut extra_output = String::new();
        tokio::time::timeout(
            Duration::from_secs(5),
            output.read_to_string(&mut extra_output),
        )
        .await??;
        Ok((states, elapsed, reaped, dead, extra_output))
    }
    .await;

    // Cleanup precedes every assertion, including the red regression assertion.
    let _ = nix::sys::signal::killpg(
        nix::unistd::Pid::from_raw(pgid as i32),
        nix::sys::signal::Signal::SIGKILL,
    );
    let _ = leader.kill().await;
    let _ = leader.wait().await;
    let cleanup = if let Some(pid) = descendant {
        term_grace_wait_dead(pid).await
    } else {
        (true, Vec::new())
    };
    assert!(cleanup.0, "live descendant after cleanup: {cleanup:?}");
    let ((state, leader_state), elapsed, reaped, dead, extra_output) = result.unwrap();
    assert!(
        matches!(state, Some(state) if !matches!(state, 'Z' | 'X')),
        "descendant lost the shared TERM grace after leader exit: state={state:?}, elapsed={elapsed:?}"
    );
    assert!(matches!(leader_state, None | Some('Z' | 'X')));
    assert!(elapsed >= Duration::from_millis(1900), "{elapsed:?}");
    assert!(
        elapsed < Duration::from_millis(3500),
        "grace doubled: {elapsed:?}"
    );
    assert!(reaped, "direct child must be reaped");
    assert!(dead.0, "descendant survived SIGKILL: {dead:?}");
    assert!(
        extra_output.is_empty(),
        "more than one TERM: {extra_output:?}"
    );
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn terminate_process_group_preserves_descendant_grace_after_leader_exit() {
    check_process_group_descendant_grace(false).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn terminate_process_group_uses_retained_pgid_after_leader_reaped() {
    check_process_group_descendant_grace(true).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn terminate_process_group_reaps_two_resistant_children_on_one_deadline() {
    use std::os::unix::process::ExitStatusExt;
    // ADR-0002: two resistant direct children share one TERM-to-KILL deadline.
    let code = r#"
import signal
signal.signal(signal.SIGTERM, lambda signum, frame: print('TERM', flush=True))
print('READY', flush=True)
while True:
    signal.pause()
"#;
    let mut leader = term_grace_child(code, 0).spawn().unwrap();
    let pgid = leader.id().unwrap();
    let mut other = None;
    let result: anyhow::Result<_> = async {
        let mut output = tokio::io::BufReader::new(leader.stdout.take().unwrap());
        anyhow::ensure!(term_grace_line(&mut output).await? == "READY");
        anyhow::ensure!(nix::unistd::getpgrp().as_raw() != pgid as i32);
        let mut second = term_grace_child(code, pgid as i32).spawn()?;
        let mut second_output = tokio::io::BufReader::new(second.stdout.take().unwrap());
        other = Some(second);
        anyhow::ensure!(term_grace_line(&mut second_output).await? == "READY");
        let started = tokio::time::Instant::now();
        let observe = async {
            anyhow::ensure!(term_grace_line(&mut output).await? == "TERM");
            anyhow::ensure!(term_grace_line(&mut second_output).await? == "TERM");
            Ok::<_, anyhow::Error>(())
        };
        let terminate = tokio::time::timeout(
            Duration::from_secs(6),
            super::terminate_process_group(Some(pgid), &mut leader, other.as_mut()),
        );
        let (acknowledged, terminated) = tokio::join!(observe, terminate);
        let elapsed = started.elapsed();
        acknowledged?;
        terminated?;
        let reaped = leader.id().is_none() && other.as_ref().unwrap().id().is_none();
        let mut extra_output = String::new();
        tokio::time::timeout(Duration::from_secs(5), async {
            output.read_to_string(&mut extra_output).await?;
            second_output.read_to_string(&mut extra_output).await
        })
        .await??;
        Ok((
            elapsed,
            reaped,
            leader.wait().await?,
            other.as_mut().unwrap().wait().await?,
            extra_output,
        ))
    }
    .await;
    let _ = nix::sys::signal::killpg(
        nix::unistd::Pid::from_raw(pgid as i32),
        nix::sys::signal::Signal::SIGKILL,
    );
    let _ = leader.kill().await;
    let _ = leader.wait().await;
    if let Some(other) = other.as_mut() {
        let _ = other.kill().await;
        let _ = other.wait().await;
    }
    let (elapsed, reaped, leader_status, other_status, extra_output) = result.unwrap();
    assert!(elapsed >= Duration::from_millis(1900), "{elapsed:?}");
    assert!(
        elapsed < Duration::from_millis(3500),
        "grace doubled: {elapsed:?}"
    );
    assert!(reaped);
    assert_eq!(leader_status.signal(), Some(9));
    assert_eq!(other_status.signal(), Some(9));
    assert!(
        extra_output.is_empty(),
        "more than one TERM: {extra_output:?}"
    );
}
