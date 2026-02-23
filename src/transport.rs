//! Transport layer: manages byte-stream connections to agent subprocesses.
//!
//! Wraps sacp's `ByteStreams` and provides subprocess management for spawning
//! ACP-compatible agents. The Python layer never touches transport directly;
//! it goes through [`crate::client::RustClient`].

use crate::error::{ConduitError, Result};
use std::collections::HashMap;
use std::process::Stdio;
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

    /// Terminate the agent subprocess.
    pub async fn kill(&mut self) -> Result<()> {
        self.child
            .kill()
            .await
            .map_err(|e| ConduitError::Transport(format!("failed to kill agent: {e}")))
    }
}
