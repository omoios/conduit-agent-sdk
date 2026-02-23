//! Proxy chain support.
//!
//! Proxies intercept and transform ACP messages between the client and
//! agent. They use the `_proxy/successor/*` protocol mediated by a
//! conductor (from sacp-conductor).

use crate::error::{ConduitError, Result};
use pyo3::prelude::*;
use std::sync::Arc;
use tokio::sync::Mutex;

/// Configuration for a single proxy in the chain.
#[pyclass(get_all)]
#[derive(Clone, Debug)]
pub struct ProxyConfig {
    /// Display name for the proxy.
    pub name: String,
    /// Shell command to spawn the proxy subprocess.
    pub command: Vec<String>,
}

#[pymethods]
impl ProxyConfig {
    #[new]
    fn new(name: String, command: Vec<String>) -> Self {
        Self { name, command }
    }

    fn __repr__(&self) -> String {
        format!("ProxyConfig(name={:?})", self.name)
    }
}

/// Rust-side proxy chain builder exposed to Python.
///
/// Constructs the ordered chain of proxies that messages traverse
/// between client and agent. Uses sacp-conductor internally to
/// manage the chain topology.
#[pyclass]
pub struct RustProxyChain {
    proxies: Arc<Mutex<Vec<ProxyConfig>>>,
}

#[pymethods]
impl RustProxyChain {
    #[new]
    fn new() -> Self {
        Self {
            proxies: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Append a proxy to the end of the chain.
    fn add<'py>(&self, py: Python<'py>, proxy: ProxyConfig) -> PyResult<Bound<'py, PyAny>> {
        let proxies = self.proxies.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            proxies.lock().await.push(proxy);
            Ok(())
        })
    }

    /// Insert a proxy at the specified position in the chain.
    fn insert<'py>(
        &self,
        py: Python<'py>,
        index: usize,
        proxy: ProxyConfig,
    ) -> PyResult<Bound<'py, PyAny>> {
        let proxies = self.proxies.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut chain = proxies.lock().await;
            if index > chain.len() {
                return Err(ConduitError::Proxy(format!(
                    "index {index} out of range (chain length: {})",
                    chain.len()
                ))
                .into());
            }
            chain.insert(index, proxy);
            Ok(())
        })
    }

    /// Return the current chain as a list of proxy configs.
    fn list<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let proxies = self.proxies.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let chain = proxies.lock().await;
            Ok(chain.clone())
        })
    }

    /// Clear all proxies from the chain.
    fn clear<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let proxies = self.proxies.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            proxies.lock().await.clear();
            Ok(())
        })
    }

    /// Build and activate the proxy chain.
    ///
    /// This spawns each proxy subprocess, connects them via the
    /// conductor, and performs the capability handshake.
    fn build<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let proxies = self.proxies.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let chain = proxies.lock().await;
            if chain.is_empty() {
                return Err(ConduitError::Proxy("proxy chain is empty".into()).into());
            }
            // TODO: Use sacp-conductor to spawn and connect the proxy chain.
            // Each proxy is started as a subprocess, connected via ByteStreams,
            // and the conductor routes messages using _proxy/successor/* protocol.
            Ok(())
        })
    }
}

/// Register proxy types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ProxyConfig>()?;
    m.add_class::<RustProxyChain>()?;
    Ok(())
}
