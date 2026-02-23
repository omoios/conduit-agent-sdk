//! ACP client: connects to an agent subprocess, performs the initialize
//! handshake, and exposes session/prompt operations to Python.

use crate::error::ConduitError;
use crate::transport::AgentProcess;
use crate::types::{Capabilities, ClientConfig, Message};
use pyo3::prelude::*;
use std::sync::Arc;
use tokio::sync::Mutex;

/// Internal state shared across the client's async operations.
struct ClientInner {
    process: AgentProcess,
    capabilities: Option<Capabilities>,
    initialized: bool,
}

/// Rust-side ACP client exposed to Python via PyO3.
///
/// The Python `conduit_sdk.Client` class wraps this to provide a friendlier
/// async API. `RustClient` manages the agent subprocess lifecycle and
/// delegates protocol messages through the sacp handler chain.
///
/// Control protocol, permissions, and query lifecycle are managed at the
/// Python layer via `Query` and `RustControlProtocol`.
#[pyclass]
pub struct RustClient {
    inner: Arc<Mutex<Option<ClientInner>>>,
    config: ClientConfig,
}

#[pymethods]
impl RustClient {
    #[new]
    fn new(config: ClientConfig) -> Self {
        Self {
            inner: Arc::new(Mutex::new(None)),
            config,
        }
    }

    /// Spawn the agent subprocess and perform the ACP initialize handshake.
    ///
    /// Returns the agent's advertised [`Capabilities`].
    fn connect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let config = self.config.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let process = AgentProcess::spawn(
                &config.command,
                config.cwd.as_deref(),
                &config.env,
            )
            .await?;

            // TODO: Wire up sacp JrHandlerChain, perform initialize handshake,
            // and capture returned capabilities from the agent.
            let capabilities = Capabilities::default();

            let client_inner = ClientInner {
                process,
                capabilities: Some(capabilities.clone()),
                initialized: true,
            };

            *inner.lock().await = Some(client_inner);
            Ok(capabilities)
        })
    }

    /// Send a prompt to the agent within the current (or new) session.
    ///
    /// Returns a list of [`Message`] objects. Streaming is handled at the
    /// Python layer by wrapping this in an async iterator.
    fn prompt<'py>(&self, py: Python<'py>, text: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let guard = inner.lock().await;
            let client = guard
                .as_ref()
                .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;

            if !client.initialized {
                return Err(ConduitError::Connection("client not initialized".into()).into());
            }

            // TODO: Send prompt via JrHandlerChain, collect streamed
            // SessionNotification messages, assemble into Messages.
            let _ = text;
            let messages: Vec<Message> = vec![];
            Ok(messages)
        })
    }

    /// Return the capabilities received during the initialize handshake.
    fn capabilities<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let guard = inner.lock().await;
            let client = guard
                .as_ref()
                .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
            Ok(client.capabilities.clone())
        })
    }

    /// Disconnect from the agent and terminate the subprocess.
    fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            if let Some(ref mut client) = *inner.lock().await {
                client.process.kill().await?;
            }
            Ok(())
        })
    }
}

/// Register client types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustClient>()?;
    Ok(())
}
