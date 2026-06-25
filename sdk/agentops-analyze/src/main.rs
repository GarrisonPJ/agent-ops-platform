mod live_logs;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use serde::Serialize;
use sqlx::postgres::PgPoolOptions;

#[derive(Parser)]
#[command(name = "agentops-analyze", about = "AgentOps trajectory analysis & live log aggregator")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Print top-N most frequently called tools
    TopTools {
        #[arg(long)]
        json: bool,
    },
    /// Print slowest 20 steps
    SlowestStep {
        #[arg(long)]
        json: bool,
    },
    /// Analyze failure patterns
    FailurePattern {
        #[arg(long)]
        json: bool,
    },
    /// Watch Docker container logs concurrently (tokio + bollard)
    LiveLogs {
        /// Container names to watch
        containers: Vec<String>,
    },
}

#[derive(Serialize)]
struct TopTool {
    tool_name: String,
    call_count: i64,
}

#[derive(Serialize)]
struct SlowStep {
    trajectory_id: String,
    index: i32,
    latency_ms: i32,
}

#[derive(Serialize)]
struct FailureAnalysis {
    total_failed_trajectories: i64,
    most_common_last_tool: Option<String>,
    most_common_last_tool_count: i64,
    avg_steps_failed: f64,
    avg_steps_overall: f64,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::LiveLogs { containers } => {
            let agg = live_logs::LogAggregator::new()?;
            agg.run(containers).await?;
        }
        _ => {
            let database_url = std::env::var("DATABASE_URL")
                .context("DATABASE_URL environment variable must be set")?;

            let pool = PgPoolOptions::new()
                .max_connections(5)
                .connect(&database_url)
                .await
                .context("Failed to connect to PostgreSQL. Check DATABASE_URL.")?;

            match cli.command {
                Commands::TopTools { json } => top_tools(&pool, json).await?,
                Commands::SlowestStep { json } => slowest_step(&pool, json).await?,
                Commands::FailurePattern { json } => failure_pattern(&pool, json).await?,
                Commands::LiveLogs { .. } => unreachable!(),
            }
        }
    }

    Ok(())
}

async fn top_tools(pool: &sqlx::PgPool, json: bool) -> Result<()> {
    let rows = sqlx::query_as::<_, (String, i64)>(
        r#"
        SELECT action->>'name' AS tool_name, COUNT(*)::bigint AS call_count
        FROM steps
        WHERE action ? 'name'
        GROUP BY tool_name
        ORDER BY call_count DESC
        "#,
    )
    .fetch_all(pool)
    .await
    .context("Failed to query top tools")?;

    if json {
        let tools: Vec<TopTool> = rows
            .into_iter()
            .map(|(name, count)| TopTool {
                tool_name: name,
                call_count: count,
            })
            .collect();
        println!("{}", serde_json::to_string_pretty(&tools)?);
    } else {
        if rows.is_empty() {
            println!("No tool calls found.");
            return Ok(());
        }
        // Find max tool name length for alignment
        let max_name_len = rows.iter().map(|(n, _)| n.len()).max().unwrap_or(0);
        let name_col_width = max_name_len.max(9); // at least "Tool Name"

        println!("{:<width$}  {:>10}", "Tool Name", "Call Count", width = name_col_width);
        println!("{}", "-".repeat(name_col_width + 13));
        for (name, count) in &rows {
            println!("{:<width$}  {:>10}", name, count, width = name_col_width);
        }
    }

    Ok(())
}

async fn slowest_step(pool: &sqlx::PgPool, json: bool) -> Result<()> {
    let rows = sqlx::query_as::<_, (String, i32, i32)>(
        r#"
        SELECT trajectory_id, index, latency_ms
        FROM steps
        WHERE latency_ms IS NOT NULL
        ORDER BY latency_ms DESC
        LIMIT 20
        "#,
    )
    .fetch_all(pool)
    .await
    .context("Failed to query slowest steps")?;

    if json {
        let steps: Vec<SlowStep> = rows
            .into_iter()
            .map(|(tid, idx, lat)| SlowStep {
                trajectory_id: tid,
                index: idx,
                latency_ms: lat,
            })
            .collect();
        println!("{}", serde_json::to_string_pretty(&steps)?);
    } else {
        if rows.is_empty() {
            println!("No steps found.");
            return Ok(());
        }
        println!("{:>5}  {:>10}  Trajectory ID", "Step", "Latency (ms)");
        println!("{}", "-".repeat(50));
        for (tid, idx, lat) in &rows {
            println!("{:>5}  {:>10}  {}", idx, lat, tid);
        }
    }

    Ok(())
}

async fn failure_pattern(pool: &sqlx::PgPool, json: bool) -> Result<()> {
    // Count total failed trajectories
    let (total_failed_trajectories,): (i64,) = sqlx::query_as(
        "SELECT COUNT(*)::bigint FROM trajectories WHERE status = 'failed'",
    )
    .fetch_one(pool)
    .await
    .context("Failed to count failed trajectories")?;

    // Most common last tool call before failure.
    // For each failed trajectory, find the step with the highest index,
    // and extract the tool name from its action JSONB.
    let last_tool_rows = sqlx::query_as::<_, (String, i64)>(
        r#"
        SELECT action->>'name' AS tool_name, COUNT(*)::bigint AS cnt
        FROM steps s
        JOIN trajectories t ON t.id = s.trajectory_id
        WHERE t.status = 'failed'
          AND s.index = (
              SELECT MAX(s2.index) FROM steps s2
              WHERE s2.trajectory_id = s.trajectory_id
          )
          AND s.action ? 'name'
        GROUP BY tool_name
        ORDER BY cnt DESC
        LIMIT 1
        "#,
    )
    .fetch_all(pool)
    .await
    .context("Failed to query last tool calls")?;

    let (most_common_last_tool, most_common_last_tool_count) = last_tool_rows
        .into_iter()
        .next()
        .map(|(n, c)| (Some(n), c))
        .unwrap_or((None, 0));

    // Average step count for failed trajectories
    let (avg_steps_failed,): (Option<f64>,) = sqlx::query_as(
        r#"
        SELECT AVG(step_count::float)
        FROM (
            SELECT s.trajectory_id, MAX(s.index) + 1 AS step_count
            FROM steps s
            JOIN trajectories t ON t.id = s.trajectory_id
            WHERE t.status = 'failed'
            GROUP BY s.trajectory_id
        ) sub
        "#,
    )
    .fetch_one(pool)
    .await
    .context("Failed to compute avg step count for failed trajectories")?;

    let avg_steps_failed = avg_steps_failed.unwrap_or(0.0);

    // Average step count overall
    let (avg_steps_overall,): (Option<f64>,) = sqlx::query_as(
        r#"
        SELECT AVG(step_count::float)
        FROM (
            SELECT trajectory_id, MAX(index) + 1 AS step_count
            FROM steps
            GROUP BY trajectory_id
        ) sub
        "#,
    )
    .fetch_one(pool)
    .await
    .context("Failed to compute overall avg step count")?;

    let avg_steps_overall = avg_steps_overall.unwrap_or(0.0);

    if json {
        let analysis = FailureAnalysis {
            total_failed_trajectories,
            most_common_last_tool,
            most_common_last_tool_count,
            avg_steps_failed,
            avg_steps_overall,
        };
        println!("{}", serde_json::to_string_pretty(&analysis)?);
    } else {
        println!("=== Failure Pattern Analysis ===\n");
        println!("Total failed trajectories: {}", total_failed_trajectories);
        println!(
            "Most common last tool call:  {} ({} times)",
            most_common_last_tool.as_deref().unwrap_or("(none)"),
            most_common_last_tool_count
        );
        println!(
            "Average steps (failed):       {:.2}",
            avg_steps_failed
        );
        println!(
            "Average steps (overall):      {:.2}",
            avg_steps_overall
        );
    }

    Ok(())
}
