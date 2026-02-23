use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

/// Core error type for the conduit SDK.
///
/// Each variant maps to a corresponding Python exception class
/// under `conduit_sdk.exceptions`.
#[derive(Debug, thiserror::Error)]
pub enum ConduitError {
    #[error("connection error: {0}")]
    Connection(String),

    #[error("session error: {0}")]
    Session(String),

    #[error("transport error: {0}")]
    Transport(String),

    #[error("protocol error: {0}")]
    Protocol(String),

    #[error("tool error: {0}")]
    Tool(String),

    #[error("hook error: {0}")]
    Hook(String),

    #[error("proxy error: {0}")]
    Proxy(String),

    #[error("timeout: {0}")]
    Timeout(String),

    #[error("permission denied: {0}")]
    PermissionDenied(String),

    #[error("cancelled")]
    Cancelled,

    #[error("{0}")]
    Other(String),
}

impl From<ConduitError> for PyErr {
    fn from(err: ConduitError) -> PyErr {
        // TODO: Map to specific Python exception subclasses once they're
        // registered on the module. For now, all errors surface as RuntimeError.
        PyRuntimeError::new_err(err.to_string())
    }
}

impl From<serde_json::Error> for ConduitError {
    fn from(err: serde_json::Error) -> Self {
        ConduitError::Protocol(format!("JSON serialization error: {err}"))
    }
}

impl From<std::io::Error> for ConduitError {
    fn from(err: std::io::Error) -> Self {
        ConduitError::Transport(format!("I/O error: {err}"))
    }
}

pub type Result<T> = std::result::Result<T, ConduitError>;
