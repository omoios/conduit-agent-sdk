//! Session lifecycle management.
//!
//! Sessions represent independent conversation threads with an agent.
//! Each session maintains its own message history and state. Sessions
//! can be created, loaded (resumed), and forked.

use crate::error::{ConduitError, Result};
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

/// Unique session identifier.
pub type SessionId = String;

/// Internal session state.
#[derive(Clone, Debug)]
struct SessionState {
    id: SessionId,
    mode: Option<String>,
    model: Option<String>,
    active: bool,
}

/// Rust-side session manager exposed to Python.
///
/// Tracks all active sessions and delegates lifecycle operations
/// to the underlying sacp connection.
#[pyclass]
pub struct RustSessionManager {
    sessions: Arc<Mutex<HashMap<SessionId, SessionState>>>,
}

#[pymethods]
impl RustSessionManager {
    #[new]
    fn new() -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Create a new session, returning its unique ID.
    fn create<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let sessions = self.sessions.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let id = uuid_v4();
            let state = SessionState {
                id: id.clone(),
                mode: None,
                model: None,
                active: true,
            };
            sessions.lock().await.insert(id.clone(), state);
            // TODO: Send new_session request to agent via JrHandlerChain
            Ok(id)
        })
    }

    /// Resume an existing session by ID.
    fn load<'py>(&self, py: Python<'py>, session_id: String) -> PyResult<Bound<'py, PyAny>> {
        let sessions = self.sessions.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut map = sessions.lock().await;
            if let Some(state) = map.get_mut(&session_id) {
                state.active = true;
                // TODO: Send load_session request to agent
                Ok(session_id)
            } else {
                Err(ConduitError::Session(format!(
                    "session not found: {session_id}"
                ))
                .into())
            }
        })
    }

    /// Fork an existing session, creating a new independent session
    /// with the same conversation history up to this point.
    fn fork<'py>(&self, py: Python<'py>, source_id: String) -> PyResult<Bound<'py, PyAny>> {
        let sessions = self.sessions.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let map = sessions.lock().await;
            let source = map.get(&source_id).ok_or_else(|| {
                ConduitError::Session(format!("source session not found: {source_id}"))
            })?;

            let new_id = uuid_v4();
            let forked = SessionState {
                id: new_id.clone(),
                mode: source.mode.clone(),
                model: source.model.clone(),
                active: true,
            };
            drop(map);

            sessions.lock().await.insert(new_id.clone(), forked);
            // TODO: Send fork_session request to agent
            Ok(new_id)
        })
    }

    /// Set the agent mode for a session (e.g. "ask", "code", "architect").
    fn set_mode<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        mode: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let sessions = self.sessions.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut map = sessions.lock().await;
            let state = map.get_mut(&session_id).ok_or_else(|| {
                ConduitError::Session(format!("session not found: {session_id}"))
            })?;
            state.mode = Some(mode);
            // TODO: Send set_session_mode to agent
            Ok(())
        })
    }

    /// Set the model for a session.
    fn set_model<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        model: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let sessions = self.sessions.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut map = sessions.lock().await;
            let state = map.get_mut(&session_id).ok_or_else(|| {
                ConduitError::Session(format!("session not found: {session_id}"))
            })?;
            state.model = Some(model);
            // TODO: Send set_model to agent
            Ok(())
        })
    }

    /// List all active session IDs.
    fn list_sessions<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let sessions = self.sessions.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let map = sessions.lock().await;
            let ids: Vec<String> = map
                .values()
                .filter(|s| s.active)
                .map(|s| s.id.clone())
                .collect();
            Ok(ids)
        })
    }
}

/// Generate a simple UUID v4 (no external dependency).
fn uuid_v4() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    format!(
        "{:08x}-{:04x}-4{:03x}-{:04x}-{:012x}",
        (now.as_nanos() & 0xFFFF_FFFF) as u32,
        (now.as_nanos() >> 32 & 0xFFFF) as u16,
        (now.as_nanos() >> 48 & 0x0FFF) as u16,
        (0x8000 | (now.as_nanos() >> 60 & 0x3FFF)) as u16,
        (now.as_secs() ^ now.subsec_nanos() as u64) & 0xFFFF_FFFF_FFFF,
    )
}

/// Register session types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustSessionManager>()?;
    Ok(())
}
