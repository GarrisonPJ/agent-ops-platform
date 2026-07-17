use std::path::PathBuf;

use agentops_protocol::{EvaluationSpec, SCHEMA_VERSION};
use agentops_runner::{RunnerConfig, Worker};
use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use serde_json::Value;
use tokio::process::Command;

#[derive(Parser)]
#[command(
    name = "agentops",
    about = "AgentOps evaluation runner and diagnostics"
)]
struct Cli {
    #[command(subcommand)]
    command: CommandGroup,
}

#[derive(Subcommand)]
enum CommandGroup {
    Runner {
        #[command(subcommand)]
        command: RunnerCommand,
    },
    Eval {
        #[command(subcommand)]
        command: EvalCommand,
    },
    Doctor,
}

#[derive(Subcommand)]
enum RunnerCommand {
    Start,
}

#[derive(Subcommand)]
enum EvalCommand {
    Validate { file: PathBuf },
}

#[tokio::main]
async fn main() -> Result<()> {
    match Cli::parse().command {
        CommandGroup::Runner {
            command: RunnerCommand::Start,
        } => {
            let config = RunnerConfig::from_env()?;
            println!(
                "Starting runner {} against {}",
                config.runner_id, config.server_url
            );
            Worker::new(config)?.run_forever().await
        }
        CommandGroup::Eval {
            command: EvalCommand::Validate { file },
        } => {
            let bytes = tokio::fs::read(&file)
                .await
                .with_context(|| format!("failed to read {}", file.display()))?;
            let spec: EvaluationSpec =
                serde_json::from_slice(&bytes).context("invalid EvaluationSpec JSON")?;
            spec.validate().map_err(anyhow::Error::msg)?;
            println!(
                "valid EvaluationSpec v{} for run {}",
                spec.schema_version, spec.run_id
            );
            Ok(())
        }
        CommandGroup::Doctor => doctor().await,
    }
}

async fn doctor() -> Result<()> {
    let config = RunnerConfig::from_env()?;
    let response = reqwest::Client::new()
        .get(format!("{}/api/health", config.server_url))
        .send()
        .await
        .context("AgentOps API is unreachable")?
        .error_for_status()
        .context("AgentOps API health check failed")?;
    let health: Value = response
        .json()
        .await
        .context("AgentOps API health response is invalid")?;
    if health["status"] != "ok" {
        bail!("AgentOps API reported an unhealthy status");
    }
    let protocol_version = health["protocol_version"]
        .as_u64()
        .context("AgentOps API did not report a protocol version")?;
    if protocol_version != u64::from(SCHEMA_VERSION) {
        bail!(
            "protocol mismatch: runner v{}, API v{}",
            SCHEMA_VERSION,
            protocol_version
        );
    }

    let agent_status = Command::new(&config.agent_program)
        .args(&config.agent_args)
        .arg("--help")
        .status()
        .await
        .with_context(|| {
            format!(
                "demo agent program '{}' is unavailable",
                config.agent_program
            )
        })?;
    if !agent_status.success() {
        bail!("demo agent self-check failed with {agent_status}");
    }

    println!("API: healthy");
    println!("Runner token: configured");
    println!("Runner ID: {}", config.runner_id);
    println!(
        "Demo agent: {} {:?}",
        config.agent_program, config.agent_args
    );
    println!("Protocol: v{}", SCHEMA_VERSION);
    Ok(())
}
