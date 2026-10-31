//! Deterministic reader/Agent-exit barriers for the H1 supervision boundary.
//! Only fake children and the local HTTP test server participate; no MCP/model.

use super::*;

#[derive(Default)]
pub(crate) struct ExitBoundaryProbe {
    pub prefix_consumed: Arc<tokio::sync::Notify>,
    pub final_phase_entered: tokio::sync::Notify,
    pub resume_final_phase: tokio::sync::Notify,
}

pub(super) async fn check_exit_boundary(
    mode: &str,
    expected: Option<ExecutionFailure>,
    expected_steps: usize,
) {
    let server = TestServer::start("continue", 0).await;
    let directory = test_handoff_dir(&format!("exit-boundary-{mode}"));
    let environment_pid = directory.join("environment.pid");
    let agent_pid = directory.join("agent.pid");
    let exit_agent = directory.join("exit-agent");
    let release = directory.join("release-environment");
    let released = directory.join("environment-released");
    let environment_code = format!(
        r#"
import json, os, pathlib, signal, time
pathlib.Path({environment_pid:?}).write_text(str(os.getpid()))
def wait(path):
    while not pathlib.Path(path).exists(): time.sleep(.001)
def emit(payload):
    print(json.dumps({{"type":"step_completed","payload":payload}}), flush=True)
submit={{"index":1,"tool_call":{{"name":"submit_result","arguments":{{}}}},"environment":{{"kind":"control","control":"submit_result","result":"ok","check_called":True,"finalized":True}}}}
print(json.dumps({{"type":"env_ready","payload":{{"mcp_url":"http://127.0.0.1:43126/mcp"}}}}), flush=True)
mode={mode:?}
if mode in ('reconstruct', 'world-only'):
    print('{{"type":"step_completed","payload":{{"index":0,"environment":{{"kind":"world","result":"', end='', flush=True)
else:
    emit(submit)
    if mode not in ('clean-eof', 'clean-exit'):
        print('{{"type":"step_completed","payload":{{"index":2,"observation":"', end='', flush=True)
wait({release:?})
if mode in ('reconstruct', 'world-only'):
    print('ok"}}}}}}', flush=True)
    if mode == 'reconstruct': emit(submit)
elif mode in ('fragment-eof', 'clean-eof'):
    os.close(1)
elif mode == 'overlong':
    # Allow the reader to resume before writing more than the pipe capacity.
    pathlib.Path({released:?}).write_text('released')
    os.write(1, b'x' * 65537)
pathlib.Path({released:?}).write_text('released')
if mode in ('unexpected-exit', 'clean-exit'):
    os._exit(0)
signal.pause()
"#
    );
    let agent_code = format!(
        "import os,pathlib,time; pathlib.Path({agent_pid:?}).write_text(str(os.getpid()))\nwhile not pathlib.Path({exit_agent:?}).exists(): time.sleep(.001)\n"
    );
    let probe = Arc::new(ExitBoundaryProbe::default());
    let mut runner = worker(&server, "pass".into());
    runner.exit_boundary_probe = Some(probe.clone());
    let agent_observed = Arc::new(AtomicBool::new(false));
    runner.agent_exit_probe = Some(agent_observed.clone());
    let mut task = tokio::spawn(async move {
        runner
            .supervise_attempt(
                &claim(10_000),
                TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), environment_code]),
                TrustedCommand::new("/usr/bin/python3", vec!["-c".to_string(), agent_code]),
            )
            .await
    });
    let setup = tokio::time::timeout(Duration::from_secs(3), async {
        if matches!(mode, "clean-eof" | "clean-exit") {
            // Clean-stream controls have no partial prefix. Prove the complete
            // submission reached the upload sink before allowing Agent exit.
            loop {
                let uploaded = server.accepted_events().await;
                if uploaded.iter().any(|request| {
                    request["events"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|event| event["payload"]["tool_call"]["name"] == "submit_result")
                }) {
                    break;
                }
                sleep(Duration::from_millis(1)).await;
            }
        } else {
            // Reader-side, not writer-side: prefix bytes are already retained in
            // BoundedJsonlReader; no suffix exists when Agent is allowed to exit.
            probe.prefix_consumed.notified().await;
        }
        std::fs::write(&exit_agent, "exit").unwrap();
        probe.final_phase_entered.notified().await;
        assert!(agent_observed.load(Ordering::SeqCst));
        std::fs::write(&release, "release").unwrap();
        while !released.exists() {
            sleep(Duration::from_millis(1)).await;
        }
        if matches!(mode, "unexpected-exit" | "clean-exit") {
            let pid: i32 = std::fs::read_to_string(&environment_pid)
                .unwrap()
                .parse()
                .unwrap();
            // A zombie is an exited child too; try_wait must observe that exit.
            loop {
                let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap_or_default();
                if stat.is_empty() || stat.split_whitespace().nth(2) == Some("Z") {
                    break;
                }
                sleep(Duration::from_millis(1)).await;
            }
        }
    })
    .await;
    // Always release barriers, including on red/failure, before bounded cleanup.
    let _ = std::fs::write(&exit_agent, "exit");
    let _ = std::fs::write(&release, "release");
    probe.resume_final_phase.notify_one();
    let result = tokio::time::timeout(Duration::from_secs(8), &mut task).await;
    if result.is_err() {
        task.abort();
        let _ = task.await;
    }
    // Snapshot Runner-owned cleanup before fallback can kill/reap a leaked
    // child. A zombie still has /proc state and must not count as reaped.
    let runner_reaped = [&environment_pid, &agent_pid].iter().all(|marker| {
        std::fs::read_to_string(marker)
            .ok()
            .and_then(|value| value.trim().parse::<u32>().ok())
            .is_some_and(|pid| !Path::new(&format!("/proc/{pid}")).exists())
    });
    reap_blocked_input_group(&environment_pid, &agent_pid).await;
    let events: Vec<Value> = server
        .accepted_events()
        .await
        .into_iter()
        .flat_map(|request| request["events"].as_array().unwrap().clone())
        .collect();
    std::fs::remove_dir_all(directory).unwrap();
    assert!(setup.is_ok(), "exit-boundary barriers failed: {mode}");
    let facts = result
        .expect("supervision did not finish")
        .unwrap()
        .unwrap();
    assert!(
        runner_reaped,
        "Runner did not reap both direct children: {mode}"
    );
    assert_eq!(facts.failure, expected, "{mode}");
    assert_eq!(facts.eligible, expected.is_none());
    assert_eq!(facts.planned_environment_teardown, expected.is_none());
    assert_eq!(facts.submitted, mode != "world-only");
    assert_eq!(facts.check_succeeded, mode != "world-only");
    assert_eq!(
        events.len(),
        expected_steps + 1,
        "unexpected upload count: {events:?}"
    );
    assert_eq!(events[0]["type"], "run_started");
    for (index, event) in events.iter().enumerate() {
        assert_eq!(event["sequence"], index + 1);
        assert_eq!(event["payload"]["attempt"], 1);
        if index > 0 {
            assert_eq!(event["type"], "step_completed");
        }
    }
    assert_eq!(facts.next_sequence as usize, events.len() + 1);
    assert!(
        server.terminal_events().await.is_empty(),
        "Runner emitted a terminal fact"
    );
    if mode == "reconstruct" {
        assert_eq!(
            events[1]["payload"]["environment"],
            json!({"kind":"world","result":"ok"})
        );
        assert_eq!(events[2]["payload"]["tool_call"]["name"], "submit_result");
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn reconstructs_once_only_after_reader_prefix_and_observed_agent_exit() {
    check_exit_boundary("reconstruct", None, 2).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn unterminated_final_line_cannot_hide_behind_accepted_submit() {
    check_exit_boundary("partial", Some(ExecutionFailure::EnvironmentExit), 1).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn partial_stdout_eof_after_submit_is_not_planned_teardown() {
    check_exit_boundary("fragment-eof", Some(ExecutionFailure::EnvironmentExit), 1).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn observed_natural_exit_after_submit_is_not_planned_teardown() {
    check_exit_boundary(
        "unexpected-exit",
        Some(ExecutionFailure::EnvironmentExit),
        1,
    )
    .await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn clean_stdout_eof_after_submit_is_not_planned_teardown() {
    check_exit_boundary("clean-eof", Some(ExecutionFailure::EnvironmentExit), 1).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn clean_natural_exit_after_submit_is_not_planned_teardown() {
    check_exit_boundary("clean-exit", Some(ExecutionFailure::EnvironmentExit), 1).await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn overlong_final_line_cannot_become_eligible() {
    check_exit_boundary("overlong", Some(ExecutionFailure::OutputLimitExceeded), 1).await;
}
