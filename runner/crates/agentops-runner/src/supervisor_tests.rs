use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use agentops_protocol::{
    ClaimResponse, ClaimedRun, EvaluationSpec, ExecutionLimits, ExecutionMode, SCHEMA_VERSION,
};
use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::{RunnerConfig, Worker, MAX_LINE_BYTES};

struct ServerState {
    completed: Mutex<Vec<Value>>,
    events: Mutex<Vec<Value>>,
    event_attempts: AtomicUsize,
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
            events: Mutex::new(Vec::new()),
            event_attempts: AtomicUsize::new(0),
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

    let (status, response) = if path.ends_with("/events") {
        let parsed = serde_json::from_slice(body).unwrap_or(Value::Null);
        let attempt = state.event_attempts.fetch_add(1, Ordering::SeqCst);
        if attempt < state.fail_event_responses {
            ("503 Service Unavailable", "{}".to_string())
        } else {
            state.events.lock().await.push(parsed);
            ("200 OK", r#"{"accepted_through":1000}"#.to_string())
        }
    } else if path.ends_with("/heartbeat") {
        (
            "200 OK",
            format!(
                r#"{{"command":"{}","lease_expires_at":"2026-07-16T00:00:15Z"}}"#,
                state.heartbeat_command
            ),
        )
    } else if path.ends_with("/complete") {
        let parsed = serde_json::from_slice(body).unwrap_or(Value::Null);
        state.completed.lock().await.push(parsed);
        ("200 OK", "{}".to_string())
    } else {
        ("404 Not Found", "{}".to_string())
    };
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}",
        response.len()
    );
    stream.write_all(response.as_bytes()).await?;
    stream.shutdown().await
}

fn worker(server: &TestServer, python_code: String) -> Worker {
    Worker::new(RunnerConfig {
        server_url: server.url.clone(),
        runner_token: "test-token".into(),
        runner_id: "test-runner".into(),
        agent_program: "/usr/bin/python3".into(),
        agent_args: vec!["-c".into(), python_code],
        agent_workdir: None,
        poll_interval: Duration::from_millis(10),
    })
    .unwrap()
}

fn claim(timeout_ms: u64) -> ClaimResponse {
    let spec = EvaluationSpec {
        schema_version: SCHEMA_VERSION,
        run_id: "run-1".into(),
        experiment_id: "experiment-1".into(),
        scenario_id: "checkout-api-latency".into(),
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
