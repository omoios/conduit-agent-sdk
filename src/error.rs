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
        // Map each ConduitError variant to its corresponding Python exception
        // subclass in `conduit_sdk.exceptions`.
        let msg = err.to_string();
        Python::with_gil(|py| {
            let class_name = match &err {
                ConduitError::Connection(_) => "ConnectionError",
                ConduitError::Session(_) => "SessionError",
                ConduitError::Transport(_) => "TransportError",
                ConduitError::Protocol(_) => "ProtocolError",
                ConduitError::Tool(_) => "ToolError",
                ConduitError::Hook(_) => "HookError",
                ConduitError::Proxy(_) => "ProxyError",
                ConduitError::Timeout(_) => "TimeoutError",
                ConduitError::PermissionDenied(_) => "PermissionError",
                ConduitError::Cancelled => "CancelledError",
                ConduitError::Other(_) => "ConduitError",
            };
            // Try to import the exception class from conduit_sdk.exceptions.
            // Fall back to RuntimeError if the import fails.
            match py.import("conduit_sdk.exceptions")
                .and_then(|m| m.getattr(class_name))
            {
                Ok(exc_class) => {
                    match exc_class.call1((msg.clone(),)) {
                        Ok(instance) => PyErr::from_value(instance),
                        Err(_) => PyRuntimeError::new_err(msg),
                    }
                }
                Err(_) => PyRuntimeError::new_err(msg),
            }
        })
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
