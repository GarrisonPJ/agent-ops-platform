use std::collections::HashSet;
use std::sync::Arc;

use anyhow::{Context, Result};
use bollard::container::LogsOptions;
use bollard::Docker;
use futures::StreamExt;
use tokio::sync::Mutex;
use tokio::time::{interval, Duration};

pub struct LogAggregator {
    docker: Docker,
    active: Arc<Mutex<HashSet<String>>>,
}

impl LogAggregator {
    pub fn new() -> Result<Self> {
        let docker = Docker::connect_with_local_defaults()
            .context("Failed to connect to Docker daemon — is it running?")?;
        Ok(Self { docker, active: Arc::new(Mutex::new(HashSet::new())) })
    }

    async fn watch_container(&self, name: String) -> Result<()> {
        let options = LogsOptions {
            follow: true,
            stdout: true,
            stderr: true,
            tail: "10".into(),
            ..Default::default()
        };
        let mut stream = self.docker.logs::<String>(&name, Some(options));

        let mut tick = interval(Duration::from_secs(1));

        loop {
            tokio::select! {
                Some(log_result) = stream.next() => {
                    match log_result {
                        Ok(output) => {
                            let bytes = output.into_bytes();
                            let text = String::from_utf8_lossy(&bytes);
                            for line in text.lines() {
                                if !line.is_empty() {
                                    println!("[{}] {}", name, line);
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[{}] stream error: {}", name, e);
                            break;
                        }
                    }
                }
                _ = tick.tick() => {}
            }
        }
        Ok(())
    }

    pub async fn run(&self, container_names: Vec<String>) -> Result<()> {
        if container_names.is_empty() {
            anyhow::bail!("Provide at least one container name");
        }

        for name in &container_names {
            self.active.lock().await.insert(name.clone());
        }

        let mut handles = vec![];

        for name in &container_names {
            let name = name.clone();
            let docker = self.docker.clone();
            let active = self.active.clone();

            handles.push(tokio::spawn(async move {
                let agg = LogAggregator { docker, active };
                if let Err(e) = agg.watch_container(name.clone()).await {
                    eprintln!("Container {} disconnected: {}", name, e);
                }
                agg.active.lock().await.remove(&name);
            }));
        }

        println!("Watching {} container(s). Press Ctrl+C to stop.", container_names.len());

        tokio::signal::ctrl_c().await?;
        println!("\nShutting down...");

        for h in handles {
            h.abort();
        }

        tokio::time::sleep(Duration::from_millis(200)).await;

        let remaining: Vec<String> = self.active.lock().await.iter().cloned().collect();
        if !remaining.is_empty() {
            println!("Stopped. Remaining: {}", remaining.join(", "));
        }

        Ok(())
    }
}
