use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use agentops_protocol::{
    ClaimResponse, ClaimedRun, EvaluationSpec, ExecutionLimits, SCHEMA_VERSION,
};
use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::{RunnerConfig, Worker};

struct ServerState {
    completed: Mutex<Vec<Value>>,
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
        let attempt = state.event_attempts.fetch_add(1, Ordering::SeqCst);
        if attempt < state.fail_event_responses {
            ("503 Service Unavailable", "{}".to_string())
        } else {
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
        policy: None,
        limits: ExecutionLimits {
            timeout_ms,
            max_output_bytes: 1_048_576,
        },
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
    let code = process_tree_agent(&path, "print('not-json', flush=True)");
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let pid = wait_for_pid(&path).await;
    assert_process_gone(pid).await;
    let completion = server.completion().await;
    assert_eq!(completion["status"], "failed");
    assert!(completion["error"]
        .as_str()
        .unwrap()
        .contains("invalid JSONL"));
    let _ = std::fs::remove_file(path);
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
    let _ = std::fs::remove_file(path);
}

#[tokio::test]
async fn stderr_is_drained_and_transient_event_failure_is_retried() {
    let server = TestServer::start("continue", 2).await;
    let code = r#"
import json, sys
sys.stderr.write("x" * 200000)
sys.stderr.flush()
print(json.dumps({"type": "process_output", "payload": {"stream": "stdout", "content": "ready"}}), flush=True)
"#
    .to_string();
    let runner = worker(&server, code);

    runner.execute_claim(claim(10_000)).await.unwrap();

    let completion = server.completion().await;
    assert_eq!(completion["status"], "succeeded");
    assert!(completion["metrics"]["stderr_bytes"].as_u64().unwrap() >= 200_000);
    assert!(server.state.event_attempts.load(Ordering::SeqCst) >= 4);
}
