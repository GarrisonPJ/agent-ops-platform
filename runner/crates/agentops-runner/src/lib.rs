use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
#[cfg(test)]
use std::process::ExitStatus;
use std::process::Stdio;
use std::sync::{
    atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    Arc, Mutex,
};
use std::time::Duration;

#[cfg(unix)]
use std::os::fd::AsRawFd;
#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};

use agent_error::AgentStderrTail;
use agentops_protocol::{
    ChildEvent, ClaimRequest, ClaimResponse, CompleteRequest, CompletionResponse,
    EnvironmentReadyMessage, EvaluationSpec, EventBatchRequest, EventBatchResponse, EventEnvelope,
    ExecutionFacts as ProtocolExecutionFacts, HeartbeatRequest, HeartbeatResponse, RunnerCommand,
    TerminalFailureKind, SCHEMA_VERSION,
};
use anyhow::{bail, Context, Result};
use chrono::Utc;
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use reqwest::{Client, StatusCode};
use serde_json::{json, Value};
use tokio::io::{
    AsyncBufRead, AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader, ReadBuf,
};
use tokio::process::{Child, Command};
#[cfg(test)]
use tokio::time::Interval;
use tokio::time::{interval, sleep, timeout, timeout_at, Instant};

mod agent_error;
mod registry;
pub use registry::{AgentMetadata, AgentRegistry, RegistryError};

pub const MAX_LINE_BYTES: usize = 65_536;
/// Repository-relative default; deployments should package config/agents.toml and set the
/// environment override when the binary is moved outside the repository.
pub const DEFAULT_AGENT_REGISTRY_PATH: &str =
    concat!(env!("CARGO_MANIFEST_DIR"), "/../../../config/agents.toml");
const NETWORK_RETRY_WINDOW: Duration = Duration::from_secs(10);
const MAX_COMPLETION_RESPONSE_BYTES: usize = 65_536;
const MAX_CACHED_ENVIRONMENT_TOKENS: usize = 1024;
const ENV_TOKEN_HANDOFF_ENV: &str = "AGENTOPS_ENV_TOKEN_HANDOFF_DIR";
const ENV_TOKEN_RECORD_SUFFIX: &str = ".token";
const ENV_TOKEN_LOCK_SUFFIX: &str = ".lock";
static TOKEN_TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);
#[cfg(test)]
static TEST_HANDOFF_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
pub struct RunnerConfig {
    pub server_url: String,
    pub runner_token: String,
    pub runner_id: String,
    /// Operator-only break-glass command retained for local compatibility tests.
    pub agent_program: String,
    pub agent_args: Vec<String>,
    pub agent_workdir: Option<PathBuf>,
    pub agent_override: Option<TrustedCommand>,
    pub environment_program: String,
    pub environment_args: Vec<String>,
    pub environment_workdir: Option<PathBuf>,
    pub poll_interval: Duration,
    pub environment_token_handoff_dir: Option<PathBuf>,
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
            Err(_) => vec!["-m".into(), "app.fixture_agent".into()],
        };
        let agent_workdir = std::env::var_os("AGENTOPS_AGENT_WORKDIR").map(PathBuf::from);
        let agent_override = std::env::var_os("AGENTOPS_AGENT_PROGRAM").map(|_| TrustedCommand {
            program: agent_program.clone(),
            args: agent_args.clone(),
            workdir: agent_workdir.clone(),
        });
        let environment_workdir = std::env::var_os("AGENTOPS_ENVIRONMENT_WORKDIR")
            .map(PathBuf::from)
            .or_else(|| agent_workdir.clone());
        let environment_program =
            std::env::var("AGENTOPS_ENVIRONMENT_PROGRAM").unwrap_or_else(|_| "python".into());
        let environment_args = match std::env::var("AGENTOPS_ENVIRONMENT_ARGS") {
            Ok(raw) => serde_json::from_str::<Vec<String>>(&raw)
                .context("AGENTOPS_ENVIRONMENT_ARGS must be a JSON string array")?,
            Err(_) => vec!["-m".into(), "app.env_server".into()],
        };
        let environment_token_handoff_dir = std::env::var_os(ENV_TOKEN_HANDOFF_ENV)
            .map(PathBuf::from)
            .or_else(default_environment_token_handoff_dir);
        Ok(Self {
            server_url,
            runner_token,
            runner_id,
            agent_program,
            agent_args,
            agent_workdir,
            agent_override,
            environment_program,
            environment_args,
            environment_workdir,
            poll_interval: Duration::from_secs(1),
            environment_token_handoff_dir,
        })
    }
}

#[derive(Clone)]
struct TokenHandoff {
    directory: PathBuf,
}

struct LockedFile(File);

impl Drop for LockedFile {
    fn drop(&mut self) {
        #[cfg(unix)]
        {
            // An inherited descriptor can keep an open-file-description lock
            // alive until a concurrently spawning child reaches exec. Unlock
            // explicitly so closing this process's descriptor releases the
            // lock for every descriptor sharing that description.
            unsafe {
                nix::libc::flock(self.0.as_raw_fd(), nix::libc::LOCK_UN);
            }
        }
    }
}

struct TokenLease {
    _lock_file: LockedFile,
    token: String,
}

impl TokenHandoff {
    fn new(directory: Option<PathBuf>) -> Result<Self> {
        let directory = directory
            .or_else(default_environment_token_handoff_dir)
            .context("Environment token handoff directory is not configured")?;
        ensure_secure_handoff_directory(&directory)?;
        Ok(Self { directory })
    }

    fn acquire(&self, run_id: &str) -> Result<TokenLease> {
        if run_id.is_empty() || run_id.len() > 256 {
            bail!("logical Run identifier is invalid for token handoff");
        }
        ensure_secure_handoff_directory(&self.directory)?;
        let key = run_id_file_key(run_id);
        let record_path = self
            .directory
            .join(format!("{key}{ENV_TOKEN_RECORD_SUFFIX}"));
        let lock_path = self.directory.join(format!("{key}{ENV_TOKEN_LOCK_SUFFIX}"));
        let capacity_path = self.directory.join(".capacity.lock");
        let capacity_lock = open_locked_file(&capacity_path)?;
        let lock_file = open_locked_file(&lock_path)?;
        let token = match read_token_record(&record_path, run_id)? {
            Some(token) => token,
            None => {
                let count = count_token_records(&self.directory)?;
                if count >= MAX_CACHED_ENVIRONMENT_TOKENS {
                    bail!("Environment token handoff capacity is full");
                }
                let token = fresh_environment_token()?;
                write_token_record(&record_path, run_id, &token)?;
                token
            }
        };
        drop(capacity_lock);
        Ok(TokenLease {
            _lock_file: lock_file,
            token,
        })
    }

    /// Remove a logical Run's token record after authoritative Control Plane
    /// terminal-completion acknowledgement. The caller must establish that
    /// acknowledgement; this local API does not contact or infer it. The
    /// capacity and per-run locks are held while the record is inspected and
    /// removed. A missing record is a successful no-op and never creates a
    /// replacement token.
    fn cleanup_after_terminal_ack(&self, run_id: &str) -> Result<bool> {
        if run_id.is_empty() || run_id.len() > 256 {
            bail!("logical Run identifier is invalid for token handoff");
        }
        ensure_secure_handoff_directory(&self.directory)?;
        let key = run_id_file_key(run_id);
        let record_path = self
            .directory
            .join(format!("{key}{ENV_TOKEN_RECORD_SUFFIX}"));
        let lock_path = self.directory.join(format!("{key}{ENV_TOKEN_LOCK_SUFFIX}"));
        let capacity_path = self.directory.join(".capacity.lock");
        let _capacity_lock = open_locked_file(&capacity_path)?;
        let _run_lock = open_locked_file(&lock_path)?;

        if read_token_record(&record_path, run_id)?.is_none() {
            return Ok(false);
        }
        fs::remove_file(&record_path)
            .with_context(|| "failed to remove Environment token handoff record")?;
        Ok(true)
    }
}

#[cfg(test)]
fn unique_test_handoff_directory(label: &str) -> PathBuf {
    loop {
        let counter = TEST_HANDOFF_COUNTER.fetch_add(1, Ordering::Relaxed);
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |duration| duration.as_nanos());
        let path = std::env::temp_dir().join(format!(
            "{label}-{}-{timestamp}-{counter}",
            std::process::id()
        ));
        match fs::create_dir(&path) {
            Ok(()) => {
                #[cfg(unix)]
                fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
                    .expect("failed to secure test handoff directory");
                return path;
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => panic!("failed to create test handoff directory: {error}"),
        }
    }
}

fn default_environment_token_handoff_dir() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    Some(base.join("agentops-runner-token-handoff"))
}

fn ensure_secure_handoff_directory(directory: &Path) -> Result<()> {
    if let Ok(metadata) = fs::symlink_metadata(directory) {
        if !metadata.is_dir() {
            bail!("Environment token handoff path is not a directory");
        }
    } else {
        fs::create_dir_all(directory)
            .with_context(|| "failed to create Environment token handoff directory")?;
        #[cfg(unix)]
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?;
    }
    let metadata = fs::symlink_metadata(directory)
        .with_context(|| "failed to inspect Environment token handoff directory")?;
    #[cfg(unix)]
    {
        if metadata.uid() != unsafe { nix::libc::geteuid() as u32 }
            || metadata.permissions().mode() & 0o777 != 0o700
        {
            bail!("Environment token handoff directory permissions are unsafe");
        }
    }
    Ok(())
}

fn open_locked_file(path: &Path) -> Result<LockedFile> {
    // Reject path redirection atomically. NONBLOCK prevents a nonregular
    // target from blocking before descriptor metadata can be validated.
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .mode(0o600)
        .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_NONBLOCK)
        .open(path)
        .with_context(|| "failed to open Environment token handoff lock")?;
    #[cfg(unix)]
    {
        let metadata = file.metadata()?;
        if !metadata.is_file()
            || metadata.uid() != unsafe { nix::libc::geteuid() as u32 }
            || metadata.permissions().mode() & 0o777 != 0o600
        {
            bail!("Environment token handoff lock permissions are unsafe");
        }
        let result =
            unsafe { nix::libc::flock(file.as_raw_fd(), nix::libc::LOCK_EX | nix::libc::LOCK_NB) };
        if result != 0 {
            bail!("Environment token handoff lock is busy or unavailable");
        }
    }
    Ok(LockedFile(file))
}

fn run_id_file_key(run_id: &str) -> String {
    run_id
        .as_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn validate_token_record_metadata(metadata: &fs::Metadata) -> Result<()> {
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        bail!("Environment token handoff record is not a regular file");
    }
    #[cfg(unix)]
    if metadata.uid() != unsafe { nix::libc::geteuid() as u32 }
        || metadata.permissions().mode() & 0o777 != 0o600
    {
        bail!("Environment token handoff record permissions are unsafe");
    }
    Ok(())
}

fn read_token_record(path: &Path, expected_run_id: &str) -> Result<Option<String>> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("failed to inspect Environment token handoff"),
    };
    validate_token_record_metadata(&metadata)?;

    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(nix::libc::O_NOFOLLOW);
    let mut file = options
        .open(path)
        .context("failed to read Environment token handoff")?;
    validate_token_record_metadata(&file.metadata()?)?;
    let mut encoded = String::new();
    file.read_to_string(&mut encoded)
        .context("failed to read Environment token handoff")?;
    let value: Value =
        serde_json::from_str(&encoded).context("malformed Environment token handoff record")?;
    let object = value
        .as_object()
        .filter(|object| object.len() == 3)
        .context("malformed Environment token handoff record")?;
    if object.get("version").and_then(Value::as_u64) != Some(1)
        || object.get("run_id").and_then(Value::as_str) != Some(expected_run_id)
    {
        bail!("Environment token handoff record collision or mismatch");
    }
    let token = object
        .get("token")
        .and_then(Value::as_str)
        .context("malformed Environment token handoff record")?;
    if token.len() != 64
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        bail!("malformed Environment token handoff record");
    }
    Ok(Some(token.to_owned()))
}

fn count_token_records(directory: &Path) -> Result<usize> {
    let mut count = 0;
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        if entry
            .file_name()
            .to_string_lossy()
            .ends_with(ENV_TOKEN_RECORD_SUFFIX)
        {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)?;
            validate_token_record_metadata(&metadata)?;
            count += 1;
        }
    }
    Ok(count)
}

fn write_token_record(path: &Path, run_id: &str, token: &str) -> Result<()> {
    let nonce = TOKEN_TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temp_path = path.with_extension(format!("token.tmp.{}.{}", std::process::id(), nonce));
    let encoded = serde_json::to_vec(&json!({"version": 1, "run_id": run_id, "token": token}))?;
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&temp_path)?;
        file.write_all(&encoded)?;
        file.sync_all()?;
        #[cfg(unix)]
        if file.metadata()?.permissions().mode() & 0o777 != 0o600 {
            bail!("Environment token handoff record permissions are unsafe");
        }
        drop(file);
        fs::rename(&temp_path, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp_path);
    }
    result
}

#[derive(Clone)]
pub struct Worker {
    client: Client,
    config: RunnerConfig,
    environment_token_handoff: TokenHandoff,
    #[cfg(test)]
    legacy_agent_command: Option<TrustedCommand>,
    #[cfg(test)]
    initial_input_probe: Option<Arc<supervisor_tests::InitialInputProbe>>,
    #[cfg(test)]
    agent_exit_probe: Option<Arc<AtomicBool>>,
    #[cfg(test)]
    final_read_window_probe: Option<Arc<AtomicBool>>,
    #[cfg(test)]
    exit_boundary_probe: Option<Arc<supervisor_tests::ExitBoundaryProbe>>,
}

#[derive(Debug, Clone)]
pub struct TrustedCommand {
    pub program: String,
    pub args: Vec<String>,
    pub workdir: Option<PathBuf>,
}

impl TrustedCommand {
    pub fn new<P, A>(program: P, args: Vec<A>) -> Self
    where
        P: Into<String>,
        A: Into<String>,
    {
        Self {
            program: program.into(),
            args: args.into_iter().map(Into::into).collect(),
            workdir: None,
        }
    }

    pub fn with_workdir(mut self, workdir: PathBuf) -> Self {
        self.workdir = Some(workdir);
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
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

#[derive(Debug, Clone)]
pub struct ExecutionFacts {
    pub first_sequence: u64,
    pub next_sequence: u64,
    pub event_retries: u32,
    pub failure: Option<ExecutionFailure>,
    pub eligible: bool,
    pub submitted: bool,
    pub check_succeeded: bool,
    pub planned_environment_teardown: bool,
    pub agent_launched: bool,
    /// Allowlisted code from the Agent's final stderr line. Diagnostics only:
    /// it never feeds `failure`, `eligible` or any terminal decision.
    pub agent_error_code: Option<&'static str>,
}

impl ExecutionFacts {
    fn new(claim: &ClaimResponse) -> Self {
        Self {
            first_sequence: claim.next_sequence,
            next_sequence: claim.next_sequence,
            event_retries: 0,
            failure: None,
            eligible: false,
            submitted: false,
            check_succeeded: false,
            planned_environment_teardown: false,
            agent_launched: false,
            agent_error_code: None,
        }
    }

    fn internal(claim: &ClaimResponse) -> Self {
        let mut facts = Self::new(claim);
        facts.failure = Some(ExecutionFailure::Internal);
        facts
    }

    fn protocol<'a>(&'a self, claim: &'a ClaimResponse) -> ProtocolExecutionFacts<'a> {
        ProtocolExecutionFacts {
            run_id: &claim.run.run_id,
            attempt: claim.attempt,
            eligible: self.eligible,
            submitted: self.submitted,
            check_succeeded: self.check_succeeded,
            planned_environment_teardown: self.planned_environment_teardown,
            failure: self.failure.map(ExecutionFailure::protocol),
            agent_error_code: self.agent_error_code,
        }
    }

    fn terminal_wire(&self) -> (&'static str, Option<TerminalFailureKind>) {
        match self.failure {
            Some(ExecutionFailure::Cancelled) => {
                ("cancelled", Some(TerminalFailureKind::Cancelled))
            }
            Some(ExecutionFailure::TimedOut) => ("timed_out", Some(TerminalFailureKind::TimedOut)),
            Some(ExecutionFailure::OutputLimitExceeded) => {
                ("failed", Some(TerminalFailureKind::OutputLimitExceeded))
            }
            Some(ExecutionFailure::AgentExit) => ("failed", Some(TerminalFailureKind::AgentExit)),
            Some(ExecutionFailure::Internal)
            | Some(ExecutionFailure::InvalidEnvironmentReady)
            | Some(ExecutionFailure::EnvironmentExit)
            | Some(ExecutionFailure::MissingSubmission)
            | Some(ExecutionFailure::LeaseRejected) => {
                ("failed", Some(TerminalFailureKind::InternalFailure))
            }
            None if self.eligible
                && self.submitted
                && self.check_succeeded
                && self.planned_environment_teardown =>
            {
                ("succeeded", None)
            }
            None => ("failed", Some(TerminalFailureKind::InternalFailure)),
        }
    }

    #[cfg(test)]
    fn from_legacy_outcome(claim: &ClaimResponse, outcome: &TerminalOutcome) -> Self {
        let mut facts = Self::new(claim);
        facts.failure = outcome.failure_kind().map(|kind| match kind {
            TerminalFailureKind::Cancelled => ExecutionFailure::Cancelled,
            TerminalFailureKind::TimedOut => ExecutionFailure::TimedOut,
            TerminalFailureKind::OutputLimitExceeded => ExecutionFailure::OutputLimitExceeded,
            TerminalFailureKind::AgentExit => ExecutionFailure::AgentExit,
            TerminalFailureKind::ProviderFailure | TerminalFailureKind::InternalFailure => {
                ExecutionFailure::Internal
            }
        });
        facts.eligible = matches!(outcome.status, TerminalStatus::Succeeded);
        facts.submitted = facts.eligible;
        facts.check_succeeded = facts.eligible;
        facts.planned_environment_teardown = facts.eligible;
        facts.next_sequence = outcome.next_sequence;
        facts.event_retries = 0;
        facts
    }
}

impl ExecutionFailure {
    fn protocol(self) -> agentops_protocol::ExecutionFailure {
        match self {
            Self::InvalidEnvironmentReady => {
                agentops_protocol::ExecutionFailure::InvalidEnvironmentReady
            }
            Self::EnvironmentExit => agentops_protocol::ExecutionFailure::EnvironmentExit,
            Self::AgentExit => agentops_protocol::ExecutionFailure::AgentExit,
            Self::MissingSubmission => agentops_protocol::ExecutionFailure::MissingSubmission,
            Self::Cancelled => agentops_protocol::ExecutionFailure::Cancelled,
            Self::TimedOut => agentops_protocol::ExecutionFailure::TimedOut,
            Self::OutputLimitExceeded => agentops_protocol::ExecutionFailure::OutputLimitExceeded,
            Self::LeaseRejected => agentops_protocol::ExecutionFailure::LeaseRejected,
            Self::Internal => agentops_protocol::ExecutionFailure::Internal,
        }
    }
}

impl Worker {
    pub fn new(config: RunnerConfig) -> Result<Self> {
        let client = Client::builder().timeout(Duration::from_secs(5)).build()?;
        Ok(Self {
            client,
            environment_token_handoff: TokenHandoff::new(
                config.environment_token_handoff_dir.clone(),
            )?,
            config,
            #[cfg(test)]
            legacy_agent_command: None,
            #[cfg(test)]
            initial_input_probe: None,
            #[cfg(test)]
            agent_exit_probe: None,
            #[cfg(test)]
            final_read_window_probe: None,
            #[cfg(test)]
            exit_boundary_probe: None,
        })
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
        if !(1..=100).contains(&claim.attempt) || claim.next_sequence == 0 {
            bail!("claim recovery metadata must contain positive attempt and sequence");
        }

        // The coupled Agent path is retained only behind a test seam so the
        // detailed process-supervision regression suite can continue to use
        // its tiny fake Agent. Production always resolves both children from
        // trusted configuration/registry and uses the split Attempt path.
        #[cfg(test)]
        if self.legacy_agent_command.is_some() {
            return self.execute_legacy_claim(claim).await;
        }

        let registry = match AgentRegistry::from_env() {
            Ok(registry) => registry,
            Err(_) => {
                let facts = ExecutionFacts::internal(&claim);
                return self.complete_facts(&claim, facts).await;
            }
        };
        let agent = match self.resolve_agent_command(&registry, &claim.run.evaluation_spec) {
            Ok(mut command) => {
                if let Some(workdir) = &self.config.agent_workdir {
                    command.workdir = Some(workdir.clone());
                }
                command
            }
            Err(_) => {
                let facts = ExecutionFacts::internal(&claim);
                return self.complete_facts(&claim, facts).await;
            }
        };
        let mut environment = TrustedCommand::new(
            self.config.environment_program.clone(),
            self.config.environment_args.clone(),
        );
        if let Some(workdir) = &self.config.environment_workdir {
            environment.workdir = Some(workdir.clone());
        }

        let facts = match self.supervise_attempt(&claim, environment, agent).await {
            Ok(facts) => facts,
            Err(_) => ExecutionFacts::internal(&claim),
        };
        self.complete_facts(&claim, facts).await
    }

    #[cfg(test)]
    async fn execute_legacy_claim(&self, claim: ClaimResponse) -> Result<()> {
        if claim.run.evaluation_spec.validate().is_err()
            || claim.run.run_id != claim.run.evaluation_spec.run_id
        {
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

    #[cfg(test)]
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

        let execution = ExecutionFacts::from_legacy_outcome(claim, &outcome);
        self.complete_request(
            claim,
            outcome.status(),
            outcome.failure_kind(),
            json!({
                "stdout_bytes": outcome.stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "total_output_bytes": total_output_bytes,
                "event_retries": event_retries,
            }),
            execution.protocol(claim),
        )
        .await
    }

    /// Remove a logical Run's handoff record after authoritative Control Plane
    /// terminal-completion acknowledgement. The caller is responsible for
    /// establishing that acknowledgement; this method does not contact or infer
    /// it. It is a successful no-op when no record exists and never creates a
    /// replacement token.
    pub fn cleanup_environment_token_after_terminal_ack(&self, run_id: &str) -> Result<bool> {
        self.environment_token_handoff
            .cleanup_after_terminal_ack(run_id)
    }

    /// Resolve only operator-owned argv. An explicit registered Agent always
    /// wins over the operator's local break-glass override.
    pub fn resolve_agent_command(
        &self,
        registry: &AgentRegistry,
        spec: &EvaluationSpec,
    ) -> Result<TrustedCommand> {
        let registered = registry.resolve_spec(spec).map_err(anyhow::Error::msg)?;
        Ok(if spec.agent_id.is_none() {
            self.config.agent_override.clone().unwrap_or(registered)
        } else {
            registered
        })
    }

    /// Additive H1-3 seam: supervise one trusted Environment and Agent attempt.
    /// The startup deadline includes spec writes, readiness, and the first heartbeat;
    /// no terminal completion is emitted here (H1-5 owns that Control Plane handoff).
    pub async fn supervise_attempt(
        &self,
        claim: &ClaimResponse,
        environment: TrustedCommand,
        agent: TrustedCommand,
    ) -> Result<ExecutionFacts> {
        let mut facts = ExecutionFacts::new(claim);
        let deadline =
            Instant::now() + Duration::from_millis(claim.run.evaluation_spec.limits.timeout_ms);
        let mut event_retries = 0;

        // Recovery metadata is a fencing boundary: do not launch either child
        // unless the claim is internally consistent and the spec is bounded.
        if claim.lease_id.trim().is_empty()
            || claim.run.run_id.trim().is_empty()
            || !(1..=100).contains(&claim.attempt)
            || claim.next_sequence == 0
            || claim.run.run_id != claim.run.evaluation_spec.run_id
            || claim.run.evaluation_spec.validate().is_err()
        {
            facts.failure = Some(ExecutionFailure::Internal);
            facts.event_retries = event_retries;
            return Ok(facts);
        }
        let spec_payload = serde_json::to_vec(&claim.run.evaluation_spec)?;
        if spec_payload.len().saturating_add(1) > MAX_LINE_BYTES {
            facts.failure = Some(ExecutionFailure::OutputLimitExceeded);
            facts.event_retries = event_retries;
            return Ok(facts);
        }
        // The OS-backed lease survives Worker replacement and remains held
        // until this attempt has completed cleanup. A same-run Attempt cannot
        // rotate or remove the token while another Attempt is supervising.
        let token_lease = match self.environment_token_handoff.acquire(&claim.run.run_id) {
            Ok(lease) => lease,
            Err(_) => {
                facts.failure = Some(ExecutionFailure::Internal);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        };
        let token = token_lease.token.clone();

        let started = envelope(
            &claim.run.run_id,
            claim.next_sequence,
            "run_started",
            json!({"attempt": claim.attempt, "source": "environment"}),
        );
        match timeout_at(deadline, self.upload_event(claim, started)).await {
            Ok(Ok(retries)) => {
                event_retries += retries;
                facts.next_sequence += 1;
            }
            Ok(Err(_)) => {
                facts.failure = Some(ExecutionFailure::Internal);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
            Err(_) => {
                facts.failure = Some(ExecutionFailure::TimedOut);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        }

        // Fence inherited cancellation before either child is allowed to launch.
        match timeout_at(deadline, self.heartbeat(claim)).await {
            Ok(Ok(RunnerCommand::Continue)) => {}
            Ok(Ok(RunnerCommand::Cancel)) => {
                facts.failure = Some(ExecutionFailure::Cancelled);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
            Ok(Err(error)) => {
                facts.failure = Some(if error.to_string().contains("rejected") {
                    ExecutionFailure::LeaseRejected
                } else {
                    ExecutionFailure::Internal
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
            Err(_) => {
                facts.failure = Some(ExecutionFailure::TimedOut);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        }

        let output_total = Arc::new(AtomicUsize::new(0));
        let output_overflow = Arc::new(AtomicBool::new(false));
        let mut output_drains = Vec::new();
        let mut environment_child = match self
            .spawn_supervised_child(&environment, &token, None, None, None, deadline)
            .await
        {
            Ok(child) => child,
            Err(_) => {
                facts.failure = Some(ExecutionFailure::Internal);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        };
        let environment_pid = environment_child.id();
        let environment_stdout = match environment_child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                facts.failure = Some(ExecutionFailure::Internal);
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        };
        let environment_stderr = environment_child.stderr.take();
        if let Some(stderr) = environment_stderr {
            output_drains.push(spawn_output_drain(
                stderr,
                output_total.clone(),
                output_overflow.clone(),
                claim.run.evaluation_spec.limits.max_output_bytes,
                None,
            ));
        }
        let mut environment_reader = BufReader::new(CountingReader::new(
            environment_stdout,
            output_total.clone(),
            output_overflow.clone(),
            claim.run.evaluation_spec.limits.max_output_bytes,
        ));
        let mut environment_framer = BoundedJsonlReader::default();
        let environment_stdin = environment_child.stdin.take();
        let environment_spec_payload = spec_payload.clone();
        let mut pending_environment_input = Some(Box::pin(async move {
            let mut stdin = environment_stdin
                .ok_or_else(|| std::io::Error::other("Environment stdin is unavailable"))?;
            stdin.write_all(&environment_spec_payload).await?;
            stdin.write_all(b"\n").await?;
            stdin.shutdown().await
        }));
        let mut heartbeat_tick = interval(Duration::from_secs(2));
        heartbeat_tick.tick().await;
        let mut overflow_tick = interval(Duration::from_millis(20));
        overflow_tick.tick().await;
        let mut heartbeat_task: Option<tokio::task::JoinHandle<Result<RunnerCommand>>> = None;
        let mut ready_url = None;
        let startup_failure = loop {
            tokio::select! {
                read = environment_framer.read_line(&mut environment_reader) => {
                    match read {
                        Ok(EnvironmentLine::Eof) => break Some(ExecutionFailure::EnvironmentExit),
                        Ok(EnvironmentLine::FragmentEof(_bytes)) => {
                            break Some(if output_overflow.load(Ordering::Relaxed) {
                                ExecutionFailure::OutputLimitExceeded
                            } else {
                                ExecutionFailure::EnvironmentExit
                            });
                        }
                        Ok(EnvironmentLine::TooLong(_bytes)) => {
                            output_overflow.store(true, Ordering::Relaxed);
                            break Some(ExecutionFailure::OutputLimitExceeded);
                        }
                        Ok(EnvironmentLine::Line(_bytes)) => {
                            if output_overflow.load(Ordering::Relaxed) {
                                break Some(ExecutionFailure::OutputLimitExceeded);
                            }
                            match parse_environment_ready(environment_framer.as_slice(), &token) {
                                Ok(url) => {
                                    if ready_url.is_some() {
                                        break Some(ExecutionFailure::InvalidEnvironmentReady);
                                    }
                                    ready_url = Some(url);
                                    if pending_environment_input.is_none() {
                                        break None;
                                    }
                                }
                                Err(_) => break Some(ExecutionFailure::InvalidEnvironmentReady),
                            }
                        }
                        Err(_) => break Some(ExecutionFailure::Internal),
                    }
                }
                result = async {
                    pending_environment_input
                        .as_mut()
                        .expect("Environment input branch is guarded")
                        .await
                }, if pending_environment_input.is_some() => {
                    pending_environment_input = None;
                    if result.is_err() {
                        break Some(ExecutionFailure::Internal);
                    }
                    // Readiness and stdin completion may be selected in
                    // either order. Do not wait for a second Environment line
                    // when the valid readiness line already arrived.
                    if ready_url.is_some() {
                        break None;
                    }
                }
                status = environment_child.wait() => {
                    break Some(if status.is_ok() {
                        ExecutionFailure::EnvironmentExit
                    } else {
                        ExecutionFailure::Internal
                    });
                }
                result = async {
                    heartbeat_task.as_mut().expect("heartbeat branch is guarded").await
                }, if heartbeat_task.is_some() => {
                    heartbeat_task = None;
                    match result {
                        Ok(Ok(RunnerCommand::Continue)) => {}
                        Ok(Ok(RunnerCommand::Cancel)) => break Some(ExecutionFailure::Cancelled),
                        Ok(Err(error)) => break Some(if error.to_string().contains("rejected") { ExecutionFailure::LeaseRejected } else { ExecutionFailure::Internal }),
                        Err(_) => break Some(ExecutionFailure::Internal),
                    }
                }
                _ = heartbeat_tick.tick(), if heartbeat_task.is_none() => {
                    heartbeat_task = Some(self.spawn_heartbeat_task(claim));
                }
                _ = overflow_tick.tick() => {
                    if output_overflow.load(Ordering::Relaxed) { break Some(ExecutionFailure::OutputLimitExceeded); }
                }
                _ = tokio::time::sleep_until(deadline) => break Some(ExecutionFailure::TimedOut),
            }
        };
        if let Some(task) = heartbeat_task.take() {
            task.abort();
            let _ = task.await;
        }
        drop(pending_environment_input);
        if let Some(failure) = startup_failure {
            terminate_process_group(environment_pid, &mut environment_child, None).await;
            drain_environment_stdout(&mut environment_reader).await;
            finish_output_drains(&mut output_drains).await;
            facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                // Stderr can cross the limit concurrently with readiness
                // parsing; output failure dominates the earlier startup fact.
                ExecutionFailure::OutputLimitExceeded
            } else {
                failure
            });
            facts.event_retries = event_retries;
            return Ok(facts);
        }
        let ready_url = ready_url.expect("startup loop only succeeds with ready URL");

        // A readiness line is not a liveness guarantee. Close the race where
        // the Environment exits between the line and the Agent launch. Keep
        // try_wait errors fail-closed inside the same bounded cleanup path.
        match environment_child.try_wait() {
            Ok(Some(_)) => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                drain_environment_stdout(&mut environment_reader).await;
                finish_output_drains(&mut output_drains).await;
                facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                    ExecutionFailure::OutputLimitExceeded
                } else {
                    ExecutionFailure::EnvironmentExit
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
            Ok(None) => {}
            Err(_) => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                drain_environment_stdout(&mut environment_reader).await;
                finish_output_drains(&mut output_drains).await;
                facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                    ExecutionFailure::OutputLimitExceeded
                } else {
                    ExecutionFailure::Internal
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        }

        match timeout_at(deadline, self.heartbeat(claim)).await {
            Ok(Ok(RunnerCommand::Continue)) => {}
            Ok(Ok(RunnerCommand::Cancel)) => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                drain_environment_stdout(&mut environment_reader).await;
                finish_output_drains(&mut output_drains).await;
                facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                    ExecutionFailure::OutputLimitExceeded
                } else {
                    ExecutionFailure::Cancelled
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
            Ok(Err(error)) => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                drain_environment_stdout(&mut environment_reader).await;
                finish_output_drains(&mut output_drains).await;
                facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                    ExecutionFailure::OutputLimitExceeded
                } else if error.to_string().contains("rejected") {
                    ExecutionFailure::LeaseRejected
                } else {
                    ExecutionFailure::Internal
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
            Err(_) => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                drain_environment_stdout(&mut environment_reader).await;
                finish_output_drains(&mut output_drains).await;
                facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                    ExecutionFailure::OutputLimitExceeded
                } else {
                    ExecutionFailure::TimedOut
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        }
        if output_overflow.load(Ordering::Relaxed) {
            // An Environment stderr/stdout drain may cross the limit while
            // the heartbeat is in flight; do not launch the Agent afterward.
            terminate_process_group(environment_pid, &mut environment_child, None).await;
            drain_environment_stdout(&mut environment_reader).await;
            finish_output_drains(&mut output_drains).await;
            facts.failure = Some(ExecutionFailure::OutputLimitExceeded);
            facts.event_retries = event_retries;
            return Ok(facts);
        }

        let mut agent_child = match self
            .spawn_supervised_child(
                &agent,
                &token,
                Some(&ready_url),
                Some(&claim.run.evaluation_spec.task),
                environment_pid,
                deadline,
            )
            .await
        {
            Ok(child) => {
                facts.agent_launched = true;
                child
            }
            Err(_) => {
                terminate_process_group(environment_pid, &mut environment_child, None).await;
                drain_environment_stdout(&mut environment_reader).await;
                finish_output_drains(&mut output_drains).await;
                facts.failure = Some(if output_overflow.load(Ordering::Relaxed) {
                    ExecutionFailure::OutputLimitExceeded
                } else {
                    ExecutionFailure::Internal
                });
                facts.event_retries = event_retries;
                return Ok(facts);
            }
        };
        let agent_stdin = agent_child.stdin.take();
        #[cfg(test)]
        if let (Some(probe), Some(stdin)) = (&self.initial_input_probe, &agent_stdin) {
            probe.prepare(stdin);
        }
        let write_input = async move {
            let mut stdin =
                agent_stdin.ok_or_else(|| std::io::Error::other("Agent stdin is unavailable"))?;
            stdin.write_all(&spec_payload).await?;
            stdin.write_all(b"\n").await?;
            stdin.flush().await?;
            stdin.shutdown().await
        };
        #[cfg(test)]
        let write_input =
            supervisor_tests::observe_initial_input(write_input, self.initial_input_probe.clone());
        // ADR-0002: retain one write future across select turns so stdin
        // backpressure cannot block monitoring or restart partially sent bytes.
        let mut pending_input = Some(Box::pin(write_input));
        let _agent_stdout = agent_child.stdout.take();
        let _agent_stderr = agent_child.stderr.take();
        if let Some(stdout) = _agent_stdout {
            output_drains.push(spawn_output_drain(
                stdout,
                output_total.clone(),
                output_overflow.clone(),
                claim.run.evaluation_spec.limits.max_output_bytes,
                None,
            ));
        }
        // Only the Agent's stderr keeps a (64-byte) candidate line; stdout and
        // the Environment's streams are counted and discarded (ADR-0009).
        let agent_stderr_tail = Arc::new(Mutex::new(AgentStderrTail::default()));
        if let Some(stderr) = _agent_stderr {
            output_drains.push(spawn_output_drain(
                stderr,
                output_total.clone(),
                output_overflow.clone(),
                claim.run.evaluation_spec.limits.max_output_bytes,
                Some(agent_stderr_tail.clone()),
            ));
        }

        #[cfg(test)]
        if let Some(probe) = &self.exit_boundary_probe {
            environment_framer.prefix_consumed = Some(probe.prefix_consumed.clone());
        }
        let mut agent_status = None;
        let mut environment_status = None;
        let mut local_failure = None;
        let mut pending_upload: Option<tokio::task::JoinHandle<Result<u32, ExecutionFailure>>> =
            None;
        let mut heartbeat_task: Option<tokio::task::JoinHandle<Result<RunnerCommand>>> = None;
        let mut agent_output_tick = interval(Duration::from_millis(20));
        agent_output_tick.tick().await;
        loop {
            tokio::select! {
                result = async {
                    pending_input.as_mut().expect("input branch is guarded").await
                }, if pending_input.is_some() => {
                    pending_input = None;
                    if result.is_err() {
                        local_failure = Some(ExecutionFailure::Internal);
                    }
                }
                read = environment_framer.read_line(&mut environment_reader), if environment_status.is_none() && pending_upload.is_none() => {
                    match read {
                        Ok(EnvironmentLine::Eof) => {
                            // EOF is an unexpected Environment exit. A valid
                            // submit/check never turns an Environment crash into
                            // planned cleanup (ADR-0008/ADR-0012).
                            local_failure = Some(ExecutionFailure::EnvironmentExit);
                            environment_status = Some(None);
                        }
                        Ok(EnvironmentLine::FragmentEof(_bytes)) => {
                            local_failure = Some(if output_overflow.load(Ordering::Relaxed) {
                                ExecutionFailure::OutputLimitExceeded
                            } else {
                                ExecutionFailure::EnvironmentExit
                            });
                            environment_status = Some(None);
                        }
                        Ok(EnvironmentLine::TooLong(_bytes)) => {
                            output_overflow.store(true, Ordering::Relaxed);
                            local_failure = Some(ExecutionFailure::OutputLimitExceeded);
                        }
                        Ok(EnvironmentLine::Line(_bytes)) => {
                            if output_overflow.load(Ordering::Relaxed) {
                                local_failure = Some(ExecutionFailure::OutputLimitExceeded);
                            } else {
                                match Self::record_environment_fact(
                                    environment_framer.as_slice(),
                                    &token,
                                    &mut facts,
                                ) {
                                    Ok(payload) => {
                                        let sequence = facts.next_sequence;
                                        let worker = self.clone();
                                        let upload_claim = claim.clone();
                                        pending_upload = Some(tokio::spawn(async move {
                                            match timeout_at(
                                                deadline,
                                                worker.upload_event(
                                                    &upload_claim,
                                                    envelope(
                                                        &upload_claim.run.run_id,
                                                        sequence,
                                                        "step_completed",
                                                        payload_with_attempt(
                                                            payload,
                                                            upload_claim.attempt,
                                                        ),
                                                    ),
                                                ),
                                            )
                                            .await
                                            {
                                                Ok(Ok(retries)) => Ok(retries),
                                                Ok(Err(_)) => Err(ExecutionFailure::Internal),
                                                Err(_) => Err(ExecutionFailure::TimedOut),
                                            }
                                        }));
                                    }
                                    Err(failure) => local_failure = Some(failure),
                                }
                            }
                        }
                        Err(_) => local_failure = Some(ExecutionFailure::Internal),
                    }
                }
                status = agent_child.wait(), if agent_status.is_none() => {
                    agent_status = Some(status);
                    #[cfg(test)]
                    if let Some(probe) = &self.agent_exit_probe {
                        probe.store(true, Ordering::SeqCst);
                    }
                }
                status = environment_child.wait(), if environment_status.is_none() => {
                    environment_status = Some(status.ok());
                    // A child exit is independently observed from stdout EOF;
                    // it is never Runner-requested cleanup at this point.
                    local_failure = Some(ExecutionFailure::EnvironmentExit);
                }
                result = async {
                    pending_upload.as_mut().expect("upload branch is guarded").await
                }, if pending_upload.is_some() => {
                    pending_upload = None;
                    match result {
                        Ok(Ok(retries)) => {
                            event_retries = event_retries.saturating_add(retries);
                            facts.next_sequence += 1;
                        }
                        Ok(Err(failure)) => {
                            local_failure = Some(failure);
                        }
                        Err(_) => {
                            local_failure = Some(ExecutionFailure::Internal);
                        }
                    }
                }
                result = async {
                    heartbeat_task.as_mut().expect("heartbeat branch is guarded").await
                }, if heartbeat_task.is_some() => {
                    heartbeat_task = None;
                    match result {
                        Ok(Ok(RunnerCommand::Continue)) => {}
                        Ok(Ok(RunnerCommand::Cancel)) => { local_failure = Some(ExecutionFailure::Cancelled); }
                        Ok(Err(error)) => { local_failure = Some(if error.to_string().contains("rejected") { ExecutionFailure::LeaseRejected } else { ExecutionFailure::Internal }); }
                        Err(_) => { local_failure = Some(ExecutionFailure::Internal); }
                    }
                }
                _ = heartbeat_tick.tick(), if heartbeat_task.is_none() => {
                    heartbeat_task = Some(self.spawn_heartbeat_task(claim));
                }
                _ = agent_output_tick.tick() => {
                    if output_overflow.load(Ordering::Relaxed) { local_failure = Some(ExecutionFailure::OutputLimitExceeded); }
                }
                _ = tokio::time::sleep_until(deadline) => { local_failure = Some(ExecutionFailure::TimedOut); }
            }
            // After planned Environment teardown, still observe the Agent exit;
            // exit zero/EOF alone never makes an attempt eligible.
            if local_failure.is_some()
                || (agent_status.is_some() && pending_upload.is_none() && pending_input.is_none())
            {
                break;
            }
        }
        // Drop any pending write (and its pipe) before process-group cleanup.
        // No input task is detached or allowed to outlive this attempt.
        drop(pending_input);
        // The Agent may exit in the same scheduling turn that the Environment
        // writes its final fact. Keep the existing 100ms final-fact opportunity,
        // but bound the whole post-exit phase by the startup-inclusive attempt
        // deadline. ADR-0002 requires cancellation, lease fencing, and deadline
        // observation to remain live while a final upload is pending.
        if local_failure.is_none() && agent_status.is_some() && environment_status.is_none() {
            #[cfg(test)]
            if let Some(probe) = &self.exit_boundary_probe {
                // Test-only barrier: the reader retained a prefix and the
                // supervisor observed Agent exit before any suffix is released.
                probe.final_phase_entered.notify_one();
                probe.resume_final_phase.notified().await;
            }
            let final_read_deadline =
                std::cmp::min(deadline, Instant::now() + Duration::from_millis(100));
            let mut final_read_open = true;
            let final_read_timer = tokio::time::sleep_until(final_read_deadline);
            tokio::pin!(final_read_timer);
            loop {
                tokio::select! {
                    read = environment_framer.read_line(&mut environment_reader), if final_read_open && pending_upload.is_none() => {
                        let read = match read {
                            Ok(read) => read,
                            Err(_) => {
                                local_failure = Some(ExecutionFailure::Internal);
                                continue;
                            }
                        };
                        match read {
                            EnvironmentLine::Eof => {
                                environment_status = Some(None);
                                local_failure = Some(ExecutionFailure::EnvironmentExit);
                            }
                            EnvironmentLine::FragmentEof(_bytes) => {
                                environment_status = Some(None);
                                local_failure = Some(if output_overflow.load(Ordering::Relaxed) {
                                    ExecutionFailure::OutputLimitExceeded
                                } else {
                                    ExecutionFailure::EnvironmentExit
                                });
                            }
                            EnvironmentLine::TooLong(_bytes) => {
                                output_overflow.store(true, Ordering::Relaxed);
                                local_failure = Some(ExecutionFailure::OutputLimitExceeded);
                            }
                            EnvironmentLine::Line(_bytes) => {
                                if output_overflow.load(Ordering::Relaxed) {
                                    local_failure = Some(ExecutionFailure::OutputLimitExceeded);
                                } else {
                                    match Self::record_environment_fact(
                                        environment_framer.as_slice(),
                                        &token,
                                        &mut facts,
                                    ) {
                                        Ok(payload) => {
                                            let sequence = facts.next_sequence;
                                            let worker = self.clone();
                                            let upload_claim = claim.clone();
                                            pending_upload = Some(tokio::spawn(async move {
                                                match timeout_at(
                                                    deadline,
                                                    worker.upload_event(
                                                        &upload_claim,
                                                        envelope(
                                                            &upload_claim.run.run_id,
                                                            sequence,
                                                            "step_completed",
                                                            payload_with_attempt(payload, upload_claim.attempt),
                                                        ),
                                                    ),
                                                )
                                                .await
                                                {
                                                    Ok(Ok(retries)) => Ok(retries),
                                                    Ok(Err(_)) => Err(ExecutionFailure::Internal),
                                                    Err(_) => Err(ExecutionFailure::TimedOut),
                                                }
                                            }));
                                        }
                                        Err(failure) => local_failure = Some(failure),
                                    }
                                }
                            }
                        }
                    }
                    result = async {
                        pending_upload.as_mut().expect("upload branch is guarded").await
                    }, if pending_upload.is_some() => {
                        pending_upload = None;
                        match result {
                            Ok(Ok(retries)) => {
                                event_retries = event_retries.saturating_add(retries);
                                facts.next_sequence += 1;
                            }
                            Ok(Err(failure)) => local_failure = Some(failure),
                            Err(_) => local_failure = Some(ExecutionFailure::Internal),
                        }
                    }
                    status = environment_child.wait(), if environment_status.is_none() => {
                        environment_status = Some(status.ok());
                        local_failure = Some(ExecutionFailure::EnvironmentExit);
                    }
                    _ = &mut final_read_timer, if final_read_open => {
                        // Close only the short Environment-read opportunity. The
                        // heartbeat, upload, child, output, and absolute-deadline
                        // branches below remain live until one resolves or fails.
                        final_read_open = false;
                        #[cfg(test)]
                        if let Some(probe) = &self.final_read_window_probe {
                            probe.store(true, Ordering::SeqCst);
                        }
                    }
                    result = async {
                        heartbeat_task.as_mut().expect("heartbeat branch is guarded").await
                    }, if heartbeat_task.is_some() => {
                        heartbeat_task = None;
                        match result {
                            Ok(Ok(RunnerCommand::Continue)) => {}
                            Ok(Ok(RunnerCommand::Cancel)) => local_failure = Some(ExecutionFailure::Cancelled),
                            Ok(Err(error)) => {
                                local_failure = Some(if error.to_string().contains("rejected") {
                                    ExecutionFailure::LeaseRejected
                                } else {
                                    ExecutionFailure::Internal
                                });
                            }
                            Err(_) => local_failure = Some(ExecutionFailure::Internal),
                        }
                    }
                    _ = heartbeat_tick.tick(), if heartbeat_task.is_none() => {
                        heartbeat_task = Some(self.spawn_heartbeat_task(claim));
                    }
                    _ = agent_output_tick.tick() => {
                        if output_overflow.load(Ordering::Relaxed) {
                            local_failure = Some(ExecutionFailure::OutputLimitExceeded);
                        }
                    }
                    _ = tokio::time::sleep_until(deadline) => {
                        local_failure = Some(ExecutionFailure::TimedOut);
                    }
                }
                if local_failure.is_some()
                    || environment_status.is_some()
                    || (!final_read_open && pending_upload.is_none() && heartbeat_task.is_none())
                {
                    break;
                }
            }
        }

        if let Some(task) = heartbeat_task.take() {
            task.abort();
            let _ = task.await;
        }

        if let Some(upload) = pending_upload.take() {
            if local_failure.is_some() {
                upload.abort();
                let _ = upload.await;
            } else {
                match upload.await {
                    Ok(Ok(retries)) => {
                        event_retries = event_retries.saturating_add(retries);
                        facts.next_sequence += 1;
                    }
                    Ok(Err(failure)) => local_failure = Some(failure),
                    Err(_) => local_failure = Some(ExecutionFailure::Internal),
                }
            }
        }
        // ADR-0008: a cancelled read retains partial bytes. A submitted check
        // cannot hide a trailing incomplete fact when the final window closes.
        if local_failure.is_none() && !environment_framer.partial.is_empty() {
            local_failure = Some(ExecutionFailure::EnvironmentExit);
        }
        // Once the bounded final-read opportunity and any pending upload or
        // heartbeat have resolved, perform one liveness check before planned
        // cleanup. This check is deliberately after those operations so read
        // expiry cannot discard cancellation or lease fencing; an error still
        // takes the existing failure/terminate/drain path below.
        if local_failure.is_none() && environment_status.is_none() {
            match environment_child.try_wait() {
                Ok(Some(_)) => {
                    environment_status = Some(None);
                    local_failure = Some(ExecutionFailure::EnvironmentExit);
                }
                Ok(None) => {}
                Err(_) => {
                    local_failure = Some(ExecutionFailure::Internal);
                }
            }
        }
        if let Some(failure) = local_failure {
            terminate_process_group(
                environment_pid,
                &mut environment_child,
                Some(&mut agent_child),
            )
            .await;
            facts.failure = Some(failure);
        } else if environment_status.is_some() {
            terminate_process_group(
                environment_pid,
                &mut environment_child,
                Some(&mut agent_child),
            )
            .await;
            facts.failure = Some(ExecutionFailure::EnvironmentExit);
        } else if agent_status
            .as_ref()
            .is_some_and(|status| status.as_ref().is_err() || !status.as_ref().unwrap().success())
        {
            terminate_process_group(
                environment_pid,
                &mut environment_child,
                Some(&mut agent_child),
            )
            .await;
            facts.failure = Some(ExecutionFailure::AgentExit);
        } else if !facts.submitted || !facts.check_succeeded {
            terminate_process_group(
                environment_pid,
                &mut environment_child,
                Some(&mut agent_child),
            )
            .await;
            facts.failure = Some(ExecutionFailure::MissingSubmission);
        } else if request_planned_environment_teardown(environment_pid, &mut environment_child) {
            // The Environment remains alive at the Runner-requested teardown
            // boundary, and process-group TERM was successfully initiated.
            // This explicit transition is the only path to planned success.
            facts.planned_environment_teardown = true;
            terminate_process_group(
                environment_pid,
                &mut environment_child,
                Some(&mut agent_child),
            )
            .await;
            facts.eligible = true;
        } else {
            // A natural Environment exit won the boundary race. It is not a
            // healthy planned teardown, even when all facts were submitted.
            terminate_process_group(
                environment_pid,
                &mut environment_child,
                Some(&mut agent_child),
            )
            .await;
            facts.failure = Some(ExecutionFailure::EnvironmentExit);
        }
        drain_environment_stdout(&mut environment_reader).await;
        finish_output_drains(&mut output_drains).await;
        facts.agent_error_code = agent_stderr_tail
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .reported_code();
        if output_overflow.load(Ordering::Relaxed) {
            // Drains can observe bytes after the supervision decision. Their
            // shared total is authoritative and cannot be hidden by planned
            // Environment teardown.
            facts.eligible = false;
            facts.planned_environment_teardown = false;
            facts.failure = Some(ExecutionFailure::OutputLimitExceeded);
        }
        // Eligibility is local execution evidence only. Retain the handoff
        // record until the Control Plane has authoritatively acknowledged
        // terminal completion through cleanup_environment_token_after_terminal_ack.
        facts.event_retries = event_retries;
        Ok(facts)
    }

    fn record_environment_fact(
        line: &[u8],
        token: &str,
        facts: &mut ExecutionFacts,
    ) -> Result<Value, ExecutionFailure> {
        let event: ChildEvent =
            serde_json::from_slice(line).map_err(|_| ExecutionFailure::InvalidEnvironmentReady)?;
        event
            .validate()
            .map_err(|_| ExecutionFailure::InvalidEnvironmentReady)?;
        let encoded = serde_json::to_string(&event.payload)
            .map_err(|_| ExecutionFailure::InvalidEnvironmentReady)?;
        if encoded.contains(token) {
            return Err(ExecutionFailure::InvalidEnvironmentReady);
        }
        if event.event_type != "step_completed" {
            return Err(ExecutionFailure::InvalidEnvironmentReady);
        }
        let environment = event
            .payload
            .get("environment")
            .and_then(Value::as_object)
            .ok_or(ExecutionFailure::InvalidEnvironmentReady)?;
        if environment.get("kind").and_then(Value::as_str) == Some("control")
            && environment.get("control").and_then(Value::as_str) == Some("submit_result")
            && environment.get("result").and_then(Value::as_str) == Some("ok")
        {
            facts.submitted = true;
            facts.check_succeeded = environment.get("check_called").and_then(Value::as_bool)
                == Some(true)
                && environment.get("finalized").and_then(Value::as_bool) == Some(true);
        }
        Ok(event.payload)
    }

    async fn spawn_supervised_child(
        &self,
        command: &TrustedCommand,
        token: &str,
        mcp_url: Option<&str>,
        task_prompt: Option<&str>,
        join_group: Option<u32>,
        _deadline: Instant,
    ) -> Result<Child> {
        let mut process = Command::new(&command.program);
        process.args(&command.args);
        if let Some(workdir) = &command.workdir {
            process.current_dir(workdir);
        }
        process
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        process.env_clear();
        if let Ok(path) = std::env::var("PATH") {
            process.env("PATH", path);
        }
        for (key, value) in
            std::env::vars().filter(|(key, _)| key.starts_with("AGENTOPS_PROVIDER_"))
        {
            process.env(key, value);
        }
        process.env("AGENTOPS_ENV_TOKEN", token);
        if let Some(url) = mcp_url {
            process.env("AGENTOPS_ENV_MCP_URL", url);
        }
        if let Some(prompt) = task_prompt {
            process.env("AGENTOPS_TASK_PROMPT", prompt);
        }
        #[cfg(unix)]
        {
            let group = join_group.map(|pid| Pid::from_raw(pid as i32));
            unsafe {
                process.pre_exec(move || {
                    let target = group.unwrap_or_else(|| Pid::from_raw(0));
                    nix::unistd::setpgid(Pid::from_raw(0), target).map_err(std::io::Error::other)
                });
            }
        }
        let child = process
            .spawn()
            .with_context(|| format!("failed to spawn trusted child {}", command.program))?;
        Ok(child)
    }

    #[cfg(test)]
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

    #[cfg(test)]
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

    #[cfg(test)]
    async fn spawn_agent(&self, spec: &EvaluationSpec) -> Result<Child> {
        let legacy = self
            .legacy_agent_command
            .as_ref()
            .map(|command| (&command.program, &command.args, command.workdir.as_ref()));
        let program = legacy
            .as_ref()
            .map_or(self.config.agent_program.as_str(), |(program, _, _)| {
                program
            });
        let args = legacy
            .as_ref()
            .map_or(self.config.agent_args.as_slice(), |(_, args, _)| {
                args.as_slice()
            });
        let workdir = legacy
            .as_ref()
            .and_then(|(_, _, workdir)| *workdir)
            .or(self.config.agent_workdir.as_ref());
        let mut command = Command::new(program);
        command.args(args);
        if let Some(workdir) = workdir {
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
        if let Err(error) = async {
            stdin.write_all(&payload).await?;
            stdin.write_all(b"\n").await?;
            stdin.shutdown().await
        }
        .await
        {
            terminate_child(&mut child).await;
            return Err(error.into());
        }
        Ok(child)
    }

    fn spawn_heartbeat_task(
        &self,
        claim: &ClaimResponse,
    ) -> tokio::task::JoinHandle<Result<RunnerCommand>> {
        let worker = self.clone();
        let claim = claim.clone();
        tokio::spawn(async move { worker.heartbeat(&claim).await })
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

    async fn complete_facts(&self, claim: &ClaimResponse, facts: ExecutionFacts) -> Result<()> {
        let (status, failure_kind) = facts.terminal_wire();
        self.complete_request(
            claim,
            status,
            failure_kind,
            json!({
                "event_retries": facts.event_retries,
                "first_sequence": facts.first_sequence,
                "next_sequence": facts.next_sequence,
            }),
            facts.protocol(claim),
        )
        .await
    }

    async fn complete_request(
        &self,
        claim: &ClaimResponse,
        status: &str,
        failure_kind: Option<TerminalFailureKind>,
        metrics: Value,
        execution: ProtocolExecutionFacts<'_>,
    ) -> Result<()> {
        let request = CompleteRequest {
            runner_id: &self.config.runner_id,
            status,
            failure_kind,
            metrics,
            execution,
        };
        let deadline = Instant::now() + NETWORK_RETRY_WINDOW;
        loop {
            let result = self
                .client
                .post(format!(
                    "{}/api/internal/runner/jobs/{}/complete",
                    self.config.server_url, claim.lease_id
                ))
                .bearer_auth(&self.config.runner_token)
                .json(&request)
                .send()
                .await;
            match result {
                Ok(mut response) if response.status().is_success() => {
                    // HTTP success alone is not authority to discard the Run
                    // capability. Enforce the limit while receiving, including
                    // chunked bodies without a trustworthy Content-Length.
                    if response
                        .content_length()
                        .is_some_and(|length| length > MAX_COMPLETION_RESPONSE_BYTES as u64)
                    {
                        bail!("completion response exceeds the byte limit");
                    }
                    let mut body = Vec::new();
                    loop {
                        let chunk = timeout_at(deadline, response.chunk())
                            .await
                            .context("completion response deadline exceeded")?
                            .context("completion response could not be read")?;
                        let Some(chunk) = chunk else { break };
                        if body.len().saturating_add(chunk.len()) > MAX_COMPLETION_RESPONSE_BYTES {
                            bail!("completion response exceeds the byte limit");
                        }
                        body.extend_from_slice(&chunk);
                    }
                    let ack: CompletionResponse = serde_json::from_slice(&body)
                        .context("completion response is not an authoritative wrapper")?;
                    ack.validate_for(&claim.run.run_id, claim.attempt)
                        .map_err(anyhow::Error::msg)?;
                    self.cleanup_environment_token_after_terminal_ack(&claim.run.run_id)?;
                    return Ok(());
                }
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

#[cfg(test)]
#[derive(Debug)]
struct TerminalOutcome {
    status: TerminalStatus,
    next_sequence: u64,
    stdout_bytes: usize,
}

#[cfg(test)]
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

#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TerminalStatus {
    Succeeded,
    Failed(TerminalFailureKind),
    Cancelled,
    TimedOut,
}

#[cfg(test)]
enum ExitSignal {
    Exited(std::io::Result<ExitStatus>),
    Heartbeat,
    Deadline,
}

#[cfg(test)]
struct ExitContext {
    deadline: Instant,
    next_sequence: u64,
    stdout_bytes: usize,
    provider_failure_seen: bool,
}

#[cfg(test)]
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

#[cfg(test)]
fn is_provider_failure_signal(event: &ChildEvent) -> bool {
    event.event_type == "process_output"
        && event
            .payload
            .get("provider_error")
            .is_some_and(|value| value.is_object())
}

fn fresh_environment_token() -> Result<String> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes)
        .map_err(|error| anyhow::anyhow!("failed to generate Environment capability: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn parse_environment_ready(line: &[u8], token: &str) -> Result<String, ExecutionFailure> {
    let message: EnvironmentReadyMessage =
        serde_json::from_slice(line).map_err(|_| ExecutionFailure::InvalidEnvironmentReady)?;
    message
        .validate()
        .map_err(|_| ExecutionFailure::InvalidEnvironmentReady)?;
    let encoded =
        serde_json::to_string(&message).map_err(|_| ExecutionFailure::InvalidEnvironmentReady)?;
    if encoded.contains(token) {
        return Err(ExecutionFailure::InvalidEnvironmentReady);
    }
    Ok(message.payload.mcp_url)
}

struct CountingReader<R> {
    inner: R,
    total: Arc<AtomicUsize>,
    overflow: Arc<AtomicBool>,
    max_output_bytes: usize,
}

impl<R> CountingReader<R> {
    fn new(
        inner: R,
        total: Arc<AtomicUsize>,
        overflow: Arc<AtomicBool>,
        max_output_bytes: usize,
    ) -> Self {
        Self {
            inner,
            total,
            overflow,
            max_output_bytes,
        }
    }
}

impl<R: AsyncRead + Unpin> AsyncRead for CountingReader<R> {
    fn poll_read(
        mut self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
        read_buf: &mut ReadBuf<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        let before = read_buf.filled().len();
        let result = std::pin::Pin::new(&mut self.inner).poll_read(cx, read_buf);
        if let std::task::Poll::Ready(Ok(())) = &result {
            let bytes = read_buf.filled().len().saturating_sub(before);
            if bytes > 0 {
                record_output_bytes(&self.total, &self.overflow, bytes, self.max_output_bytes);
            }
        }
        result
    }
}

fn record_output_bytes(
    total: &AtomicUsize,
    overflow: &AtomicBool,
    bytes: usize,
    max_output_bytes: usize,
) -> bool {
    let cap = max_output_bytes.saturating_add(1);
    let mut current = total.load(Ordering::Relaxed);
    loop {
        let next = current.saturating_add(bytes).min(cap);
        match total.compare_exchange_weak(current, next, Ordering::Relaxed, Ordering::Relaxed) {
            Ok(_) => {
                let exceeded = next > max_output_bytes;
                if exceeded {
                    overflow.store(true, Ordering::Relaxed);
                }
                return exceeded;
            }
            Err(actual) => current = actual,
        }
    }
}

fn spawn_output_drain<R: AsyncRead + Unpin + Send + 'static>(
    mut stream: R,
    total: Arc<AtomicUsize>,
    overflow: Arc<AtomicBool>,
    max_output_bytes: usize,
    tail: Option<Arc<Mutex<AgentStderrTail>>>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut buffer = [0_u8; 8 * 1024];
        loop {
            match stream.read(&mut buffer).await {
                Ok(0) | Err(_) => break,
                Ok(bytes) => {
                    record_output_bytes(&total, &overflow, bytes, max_output_bytes);
                    if let Some(tail) = &tail {
                        tail.lock()
                            .unwrap_or_else(|poisoned| poisoned.into_inner())
                            .observe(&buffer[..bytes]);
                    }
                }
            }
        }
    })
}

async fn finish_output_drains(drains: &mut Vec<tokio::task::JoinHandle<()>>) {
    for mut drain in drains.drain(..) {
        if tokio::time::timeout(Duration::from_millis(100), &mut drain)
            .await
            .is_err()
        {
            drain.abort();
            let _ = drain.await;
        }
    }
}

async fn drain_environment_stdout<R: AsyncRead + Unpin>(reader: &mut R) {
    let mut buffer = [0_u8; 8 * 1024];
    let drain = async {
        loop {
            match reader.read(&mut buffer).await {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
        }
    };
    let _ = timeout(Duration::from_millis(100), drain).await;
}

fn request_planned_environment_teardown(pid: Option<u32>, leader: &mut Child) -> bool {
    let Some(pid) = pid else {
        return false;
    };
    match leader.try_wait() {
        Ok(Some(_)) | Err(_) => return false,
        Ok(None) => {}
    }
    #[cfg(unix)]
    {
        // The direct-child signal is the lifecycle boundary. Group TERM is
        // retained for shared cleanup, but cannot establish planned teardown
        // after the Environment leader has exited while an Agent remains.
        let leader_signal_succeeded =
            nix::sys::signal::kill(Pid::from_raw(pid as i32), Signal::SIGTERM).is_ok();
        let _ = killpg(Pid::from_raw(pid as i32), Signal::SIGTERM);
        leader_signal_succeeded
    }
    #[cfg(not(unix))]
    {
        let _ = leader;
        true
    }
}

async fn terminate_process_group(
    pid: Option<u32>,
    leader: &mut Child,
    mut other: Option<&mut Child>,
) {
    #[cfg(unix)]
    if let Some(pid) = pid {
        let group = Pid::from_raw(pid as i32);
        // ADR-0002: signal the retained process group, not only the exited leader.
        let _ = killpg(group, Signal::SIGTERM);
        let deadline = Instant::now() + Duration::from_secs(2);
        let _ = timeout_at(deadline, leader.wait()).await;
        if let Some(other) = other.as_deref_mut() {
            let _ = timeout_at(deadline, other.wait()).await;
        }
        // ADR-0008/ADR-0012: direct-child exit does not end the shared TERM
        // grace for descendants that remain in the retained process group.
        tokio::time::sleep_until(deadline).await;
        let _ = killpg(group, Signal::SIGKILL);
    }
    let _ = leader.kill().await;
    let _ = leader.wait().await;
    if let Some(other) = other {
        let _ = other.kill().await;
        let _ = other.wait().await;
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

enum EnvironmentLine {
    Eof,
    FragmentEof(usize),
    Line(usize),
    TooLong(usize),
}

#[derive(Default)]
struct BoundedJsonlReader {
    partial: Vec<u8>,
    line: Vec<u8>,
    #[cfg(test)]
    prefix_consumed: Option<Arc<tokio::sync::Notify>>,
}

impl BoundedJsonlReader {
    fn as_slice(&self) -> &[u8] {
        &self.line
    }

    async fn read_line<R: AsyncBufRead + Unpin>(
        &mut self,
        reader: &mut R,
    ) -> std::io::Result<EnvironmentLine> {
        self.line.clear();
        loop {
            let available = reader.fill_buf().await?;
            if available.is_empty() {
                if self.partial.is_empty() {
                    return Ok(EnvironmentLine::Eof);
                }
                let bytes = self.partial.len();
                self.line = std::mem::take(&mut self.partial);
                return Ok(EnvironmentLine::FragmentEof(bytes));
            }

            let newline = available.iter().position(|byte| *byte == b'\n');
            let requested = newline.map_or(available.len(), |index| index + 1);
            if self.partial.len().saturating_add(requested) > MAX_LINE_BYTES {
                let remaining = MAX_LINE_BYTES + 1 - self.partial.len();
                let consumed = remaining.min(available.len());
                self.partial.extend_from_slice(&available[..consumed]);
                reader.consume(consumed);
                let bytes = self.partial.len();
                self.line = std::mem::take(&mut self.partial);
                return Ok(EnvironmentLine::TooLong(bytes));
            }

            self.partial.extend_from_slice(&available[..requested]);
            reader.consume(requested);
            #[cfg(test)]
            if newline.is_none() {
                if let Some(probe) = &self.prefix_consumed {
                    probe.notify_one();
                }
            }
            if newline.is_some() {
                self.line = std::mem::take(&mut self.partial);
                return Ok(EnvironmentLine::Line(self.line.len()));
            }
        }
    }
}

#[cfg(test)]
enum LineRead {
    Eof,
    Line(usize),
    TooLong(usize),
}

#[cfg(test)]
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

#[cfg(test)]
#[derive(Default)]
struct StderrCapture {
    total_bytes: usize,
}

#[cfg(test)]
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

#[cfg(test)]
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
    use std::pin::Pin;
    use std::task::{Context as TaskContext, Poll};

    use super::*;
    use tokio::io::ReadBuf;

    #[test]
    fn default_config_uses_argument_array() {
        std::env::set_var("RUNNER_TOKEN", "test");
        std::env::remove_var("AGENTOPS_AGENT_ARGS");
        let config = RunnerConfig::from_env().unwrap();
        assert_eq!(config.agent_program, "python");
        assert_eq!(config.agent_args, ["-m", "app.fixture_agent"]);
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
    async fn counting_reader_adds_stdout_to_shared_bounded_total() {
        let total = Arc::new(AtomicUsize::new(0));
        let overflow = Arc::new(AtomicBool::new(false));
        let data = vec![b'x'; 2_000];
        let mut reader = CountingReader::new(&data[..], total.clone(), overflow.clone(), 1_024);
        let mut output = Vec::new();
        reader.read_to_end(&mut output).await.unwrap();
        assert_eq!(output.len(), data.len());
        assert_eq!(total.load(Ordering::Relaxed), 1_025);
        assert!(overflow.load(Ordering::Relaxed));
    }

    #[cfg(unix)]
    #[test]
    fn environment_token_handoff_lock_lifetime_and_active_lease_regression() {
        use std::os::fd::AsRawFd;
        use std::os::unix::fs::OpenOptionsExt;

        let directory = unique_test_handoff_directory("agentops-token-handoff-lock-lifetime");
        let capacity_path = directory.join(".capacity.lock");

        // A local duplicate models the same open-file-description retention
        // mechanism that fork inheritance can expose. This is deliberately not
        // an actual cross-process proof.
        let bare_file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .mode(0o600)
            .open(&capacity_path)
            .unwrap();
        let lock_result = unsafe {
            nix::libc::flock(
                bare_file.as_raw_fd(),
                nix::libc::LOCK_EX | nix::libc::LOCK_NB,
            )
        };
        assert_eq!(lock_result, 0);
        let bare_duplicate = bare_file.try_clone().unwrap();
        drop(bare_file);
        assert!(
            open_locked_file(&capacity_path).is_err(),
            "closing the original File released a lock retained by its duplicate"
        );
        let unlock_result =
            unsafe { nix::libc::flock(bare_duplicate.as_raw_fd(), nix::libc::LOCK_UN) };
        assert_eq!(unlock_result, 0);
        drop(bare_duplicate);
        let reacquired = open_locked_file(&capacity_path)
            .expect("reacquiring after the duplicate explicitly unlocked should succeed");
        drop(reacquired);

        let locked_file = open_locked_file(&capacity_path).unwrap();
        let locked_duplicate = locked_file.0.try_clone().unwrap();
        drop(locked_file);
        assert!(
            open_locked_file(&capacity_path).is_ok(),
            "LockedFile did not explicitly release its flock on Drop"
        );
        drop(locked_duplicate);

        let handoff = TokenHandoff::new(Some(directory.clone())).unwrap();
        let active_lease = handoff.acquire("active-run").unwrap();
        assert!(
            handoff.acquire("active-run").is_err(),
            "an active same-run TokenLease did not reject concurrent acquisition"
        );
        drop(active_lease);
        let replacement_lease = handoff.acquire("active-run").unwrap();
        drop(replacement_lease);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn environment_token_handoff_reuses_token_and_cleans_only_after_terminal_ack() {
        let directory = unique_test_handoff_directory("agentops-token-handoff-test");
        let first = TokenHandoff::new(Some(directory.clone())).unwrap();
        let lease = first.acquire("run-1").unwrap();
        let token = lease.token.clone();
        drop(lease);
        let replacement = TokenHandoff::new(Some(directory.clone())).unwrap();
        let replacement_lease = replacement.acquire("run-1").unwrap();
        assert_eq!(replacement_lease.token, token);
        drop(replacement_lease);
        let unrelated_lease = replacement.acquire("run-2").unwrap();
        assert_ne!(unrelated_lease.token, token);
        drop(unrelated_lease);
        assert!(!replacement
            .cleanup_after_terminal_ack("missing-run")
            .unwrap());
        assert!(!directory
            .join(format!(
                "{}{ENV_TOKEN_RECORD_SUFFIX}",
                run_id_file_key("missing-run")
            ))
            .exists());
        let active_lease = replacement.acquire("run-1").unwrap();
        assert!(replacement.cleanup_after_terminal_ack("run-1").is_err());
        assert!(directory
            .join(format!(
                "{}{ENV_TOKEN_RECORD_SUFFIX}",
                run_id_file_key("run-1")
            ))
            .exists());
        drop(active_lease);
        assert!(replacement.cleanup_after_terminal_ack("run-1").unwrap());
        assert!(!directory
            .join(format!(
                "{}{ENV_TOKEN_RECORD_SUFFIX}",
                run_id_file_key("run-1")
            ))
            .exists());

        // A post-ack reacquisition is a new handoff only because the explicit
        // cleanup removed the old record; cleanup itself never creates one.
        let reacquired = replacement.acquire("run-1").unwrap();
        assert_ne!(reacquired.token, token);
        drop(reacquired);
        assert!(replacement.cleanup_after_terminal_ack("run-1").unwrap());
        assert!(!replacement.cleanup_after_terminal_ack("run-1").unwrap());

        let malformed_path = directory.join(format!(
            "{}{ENV_TOKEN_RECORD_SUFFIX}",
            run_id_file_key("malformed-cleanup")
        ));
        write_token_record(&malformed_path, "different-run", &token).unwrap();
        assert!(replacement
            .cleanup_after_terminal_ack("malformed-cleanup")
            .is_err());
        assert!(
            malformed_path.exists(),
            "cleanup removed a mismatched record"
        );
        let _ = fs::remove_dir_all(directory);
    }

    #[cfg(unix)]
    #[test]
    fn environment_token_handoff_rejects_lock_symlinks_for_acquire_and_cleanup() {
        use std::os::unix::fs::symlink;

        for capacity in [false, true] {
            let directory = unique_test_handoff_directory("agentops-lock-symlink");
            let handoff = TokenHandoff::new(Some(directory.clone())).unwrap();
            drop(handoff.acquire("run-lock").unwrap());
            let key = run_id_file_key("run-lock");
            let record_path = directory.join(format!("{key}{ENV_TOKEN_RECORD_SUFFIX}"));
            let record_before = fs::read(&record_path).unwrap();
            let lock_path = directory.join(if capacity {
                ".capacity.lock".to_string()
            } else {
                format!("{key}{ENV_TOKEN_LOCK_SUFFIX}")
            });
            fs::remove_file(&lock_path).unwrap();
            let target = directory.join("untouched-target");
            fs::write(&target, b"must not mutate").unwrap();
            fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
            symlink(&target, &lock_path).unwrap();
            let acquire_refused = handoff.acquire("run-lock").is_err();
            let cleanup_refused = handoff.cleanup_after_terminal_ack("run-lock").is_err();
            let target_unchanged = fs::read(&target).unwrap() == b"must not mutate";
            let record_unchanged =
                fs::read(&record_path).ok().as_deref() == Some(record_before.as_slice());

            // A dangling symlink must not create a new file at its target.
            fs::remove_file(&lock_path).unwrap();
            let absent_target = directory.join("must-not-create");
            symlink(&absent_target, &lock_path).unwrap();
            let dangling_acquire_refused = handoff.acquire("run-lock").is_err();
            let dangling_cleanup_refused = handoff.cleanup_after_terminal_ack("run-lock").is_err();
            let target_absent = !absent_target.exists();
            fs::remove_dir_all(directory).unwrap();
            assert!(
                acquire_refused && cleanup_refused,
                "lock symlink accepted (capacity={capacity})"
            );
            assert!(
                record_unchanged && target_unchanged,
                "refused operation changed data"
            );
            assert!(dangling_acquire_refused && dangling_cleanup_refused && target_absent);
        }
    }

    #[cfg(unix)]
    #[test]
    fn environment_token_handoff_rejects_nonregular_lock_descriptors() {
        // /dev/null is readable/writable and can pass ownership/mode checks
        // on some hosts, but is never a regular handoff lock file.
        assert!(open_locked_file(Path::new("/dev/null")).is_err());
        let directory = unique_test_handoff_directory("agentops-lock-fifo");
        let path = directory.join("fifo.lock");
        use std::os::unix::ffi::OsStrExt;
        let c_path = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { nix::libc::mkfifo(c_path.as_ptr(), 0o600) }, 0);
        let refused = open_locked_file(&path).is_err();
        fs::remove_dir_all(directory).unwrap();
        assert!(refused, "FIFO accepted as a lock file");
    }

    #[cfg(unix)]
    #[test]
    fn environment_token_handoff_rejects_insecure_existing_records_and_symlinks() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let directory = unique_test_handoff_directory("agentops-token-handoff-record-security");
        let handoff = TokenHandoff::new(Some(directory.clone())).unwrap();
        let record_path = directory.join(format!(
            "{}{ENV_TOKEN_RECORD_SUFFIX}",
            run_id_file_key("record-security")
        ));
        let lease = handoff.acquire("record-security").unwrap();
        drop(lease);

        fs::set_permissions(&record_path, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(
            handoff.acquire("record-security").is_err(),
            "existing token record with mode 0644 was accepted"
        );
        fs::set_permissions(&record_path, fs::Permissions::from_mode(0o600)).unwrap();
        let valid_lease = handoff.acquire("record-security").unwrap();
        drop(valid_lease);

        let target_path = directory.join("record-target");
        fs::copy(&record_path, &target_path).unwrap();
        fs::remove_file(&record_path).unwrap();
        symlink(&target_path, &record_path).unwrap();
        assert!(
            handoff.acquire("record-security").is_err(),
            "existing token record symlink was accepted"
        );

        fs::remove_file(&record_path).unwrap();
        fs::remove_file(target_path).unwrap();
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn environment_token_handoff_fails_closed_for_malformed_and_capacity_records() {
        let directory = unique_test_handoff_directory("agentops-token-handoff-capacity");
        let handoff = TokenHandoff::new(Some(directory.clone())).unwrap();
        let path = directory.join(format!(
            "{}{ENV_TOKEN_RECORD_SUFFIX}",
            run_id_file_key("bad")
        ));
        fs::write(&path, "not-json").unwrap();
        assert!(handoff.acquire("bad").is_err());
        fs::remove_file(path).unwrap();
        for index in 0..MAX_CACHED_ENVIRONMENT_TOKENS {
            let run_id = format!("run-{index}");
            let lease = handoff.acquire(&run_id).unwrap();
            drop(lease);
        }
        assert!(handoff.acquire("over-capacity").is_err());
        let _ = fs::remove_dir_all(directory);
    }

    struct BarrierReader {
        chunks: Vec<Vec<u8>>,
        barriers: Vec<Arc<tokio::sync::Notify>>,
        index: usize,
        allowed: usize,
        notified: bool,
        waker: Option<std::task::Waker>,
    }

    impl BarrierReader {
        fn new(
            chunks: impl IntoIterator<Item = Vec<u8>>,
            barriers: Vec<Arc<tokio::sync::Notify>>,
        ) -> Self {
            Self {
                chunks: chunks.into_iter().collect(),
                barriers,
                index: 0,
                allowed: 1,
                notified: false,
                waker: None,
            }
        }

        fn release(&mut self) {
            self.allowed += 1;
            self.notified = false;
            if let Some(waker) = self.waker.take() {
                waker.wake();
            }
        }
    }

    impl AsyncRead for BarrierReader {
        fn poll_read(
            mut self: Pin<&mut Self>,
            cx: &mut TaskContext<'_>,
            read_buf: &mut ReadBuf<'_>,
        ) -> Poll<std::io::Result<()>> {
            if self.index < self.allowed {
                let chunk = self.chunks[self.index].clone();
                self.index += 1;
                read_buf.put_slice(&chunk);
                return Poll::Ready(Ok(()));
            }
            if self.index < self.chunks.len() {
                if !self.notified {
                    self.barriers[self.index - 1].notify_one();
                    self.notified = true;
                }
                self.waker = Some(cx.waker().clone());
                return Poll::Pending;
            }
            Poll::Ready(Ok(()))
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn planned_teardown_rejects_gone_environment_even_with_group_member() {
        let mut environment = Command::new("/usr/bin/python3");
        environment
            .arg("-c")
            .arg("import subprocess; subprocess.Popen(['sleep', '30'])")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        unsafe {
            environment.pre_exec(|| {
                nix::unistd::setpgid(Pid::from_raw(0), Pid::from_raw(0))
                    .map_err(std::io::Error::other)
            });
        }
        let mut environment = environment.spawn().unwrap();
        let pid = environment.id().unwrap();
        environment.wait().await.unwrap();

        assert!(
            !request_planned_environment_teardown(Some(pid), &mut environment),
            "a group member must not make a naturally exited Environment planned"
        );
        assert!(
            killpg(Pid::from_raw(pid as i32), Signal::SIGKILL).is_ok(),
            "the retained group member was not present for the boundary check"
        );
    }

    #[tokio::test]
    async fn environment_framer_preserves_partial_line_across_cancelled_select_turns() {
        let barriers = vec![
            Arc::new(tokio::sync::Notify::new()),
            Arc::new(tokio::sync::Notify::new()),
        ];
        let prefix =
            br#"{"type":"step_completed","payload":{"environment":{"kind":"world","result":""#;
        let middle = b"ok".to_vec();
        let suffix = br#""}}}
"#
        .to_vec();
        let mut reader = BufReader::new(BarrierReader::new(
            vec![prefix.to_vec(), middle, suffix],
            barriers.clone(),
        ));
        let mut framer = BoundedJsonlReader::default();

        let first_wait = barriers[0].notified();
        tokio::select! {
            result = framer.read_line(&mut reader) => panic!("read unexpectedly completed: {:?}", result.map(|_| ())),
            _ = first_wait => {}
        }
        reader.get_mut().release();

        let second_wait = barriers[1].notified();
        tokio::select! {
            result = framer.read_line(&mut reader) => panic!("read unexpectedly completed: {:?}", result.map(|_| ())),
            _ = second_wait => {}
        }
        reader.get_mut().release();

        let result = framer.read_line(&mut reader).await.unwrap();
        assert!(matches!(result, EnvironmentLine::Line(_)));
        assert_eq!(
            framer.as_slice(),
            br#"{"type":"step_completed","payload":{"environment":{"kind":"world","result":"ok"}}}
"#
        );
    }

    #[tokio::test]
    async fn environment_framer_keeps_back_to_back_lines_separate() {
        let data = br#"{"type":"env_ready","payload":{"mcp_url":"http://127.0.0.1:43123/mcp"}}
{"type":"step_completed","payload":{"environment":{"kind":"world","result":"ok"}}}
"#;
        let mut reader = BufReader::new(&data[..]);
        let mut framer = BoundedJsonlReader::default();

        assert!(matches!(
            framer.read_line(&mut reader).await.unwrap(),
            EnvironmentLine::Line(_)
        ));
        assert!(framer.as_slice().starts_with(br#"{"type":"env_ready"#));
        assert!(matches!(
            framer.read_line(&mut reader).await.unwrap(),
            EnvironmentLine::Line(_)
        ));
        assert!(framer.as_slice().starts_with(br#"{"type":"step_completed"#));
    }

    #[tokio::test]
    async fn environment_framer_reports_accumulated_overlong_line_and_fragment_eof() {
        let mut overlong = vec![b'x'; MAX_LINE_BYTES + 1];
        overlong.push(b'\n');
        let mut reader = BufReader::new(&overlong[..]);
        let mut framer = BoundedJsonlReader::default();
        assert!(matches!(
            framer.read_line(&mut reader).await.unwrap(),
            EnvironmentLine::TooLong(bytes) if bytes == MAX_LINE_BYTES + 1
        ));

        let mut fragment = BoundedJsonlReader::default();
        let mut fragment_reader = BufReader::new(&b"partial-json"[..]);
        assert!(matches!(
            fragment.read_line(&mut fragment_reader).await.unwrap(),
            EnvironmentLine::FragmentEof(bytes) if bytes == b"partial-json".len()
        ));
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
