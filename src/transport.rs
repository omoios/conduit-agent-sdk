//! Transport layer: manages byte-stream connections to agent subprocesses.
//!
//! Wraps the ACP `ByteStreams` transport and provides subprocess management for spawning
//! ACP-compatible agents. The Python layer never touches transport directly;
//! it goes through [`crate::client::RustClient`].

use crate::error::{ConduitError, Result};
use std::collections::HashMap;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::{Child, Command};

/// Handle to a running agent subprocess and its I/O streams.
pub struct AgentProcess {
    pub child: Child,
}

impl AgentProcess {
    /// Spawn an agent subprocess from the given command and environment.
    ///
    /// The subprocess is started with stdin/stdout piped for ACP byte-stream
    /// communication. Stderr is inherited for debug logging.
    pub async fn spawn(
        command: &[String],
        cwd: Option<&str>,
        env: &HashMap<String, String>,
    ) -> Result<Self> {
        if command.is_empty() {
            return Err(ConduitError::Connection(
                "agent command must not be empty".into(),
            ));
        }

        let mut cmd = Command::new(&command[0]);
        if command.len() > 1 {
            cmd.args(&command[1..]);
        }
        if let Some(dir) = cwd {
            cmd.current_dir(dir);
        }
        for (k, v) in env {
            cmd.env(k, v);
        }
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());

        // Put the agent in its own process group so that on teardown we can
        // kill the whole group (agent + any tool-time descendants that
        // inherited our pipes) instead of just the direct child.
        #[cfg(unix)]
        cmd.process_group(0);

        let child = cmd
            .spawn()
            .map_err(|e| ConduitError::Connection(format!("failed to spawn agent: {e}")))?;

        Ok(Self { child })
    }

    /// Take ownership of the child's stdin (for writing ACP messages).
    pub fn take_stdin(&mut self) -> Result<tokio::process::ChildStdin> {
        self.child
            .stdin
            .take()
            .ok_or_else(|| ConduitError::Transport("agent stdin already taken".into()))
    }

    /// Take ownership of the child's stdout (for reading ACP messages).
    pub fn take_stdout(&mut self) -> Result<tokio::process::ChildStdout> {
        self.child
            .stdout
            .take()
            .ok_or_else(|| ConduitError::Transport("agent stdout already taken".into()))
    }

    /// Terminate the agent subprocess and any descendants it spawned.
    ///
    /// The agent runs in its own process group (see [`Self::spawn`]), so we
    /// signal the *whole group* — this reaches tool-time children that
    /// inherited our pipes and would otherwise keep the transport open (the
    /// cause of teardown hangs on multi-step agent runs). The reap is bounded
    /// so a wedged child can never block disconnect indefinitely.
    pub async fn kill(&mut self) -> Result<()> {
        #[cfg(unix)]
        {
            if let Some(pid) = self.child.id() {
                // Negative pid => deliver the signal to the entire group.
                let _ = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
            }
            let _ = tokio::time::timeout(Duration::from_secs(5), self.child.wait()).await;
        }
        #[cfg(not(unix))]
        {
            let _ = tokio::time::timeout(Duration::from_secs(5), self.child.kill()).await;
        }
        Ok(())
    }
}
