//! Control protocol: bidirectional JSON message routing between SDK and agent.
//!
//! The control protocol runs alongside the ACP conversation stream. The agent
//! sends control requests (permission checks, hook callbacks, MCP tool calls)
//! over stdout as JSON messages. The SDK responds via stdin.
//!
//! Message format:
//! ```json
//! {"type": "control", "request_id": "...", "subtype": "...", "data": {...}}
//! ```

use crate::error::ConduitError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, Mutex, Notify};

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

/// A control message sent between SDK and agent.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ControlMessage {
    /// Unique request identifier for correlating requests and responses.
    pub request_id: String,
    /// Message subtype (e.g. "can_use_tool", "hook_callback", "mcp_message").
    pub subtype: String,
    /// JSON-serialized payload.
    pub data: String,
}

#[pymethods]
impl ControlMessage {
    #[new]
    fn new(request_id: String, subtype: String, data: String) -> Self {
        Self {
            request_id,
            subtype,
            data,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ControlMessage(id={:?}, subtype={:?})",
            self.request_id, self.subtype
        )
    }
}

/// A control response sent from SDK back to the agent.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ControlResponse {
    /// Must match the ``request_id`` of the original request.
    pub request_id: String,
    /// Response subtype (mirrors the request subtype).
    pub subtype: String,
    /// JSON-serialized response payload.
    pub data: String,
}

#[pymethods]
impl ControlResponse {
    #[new]
    fn new(request_id: String, subtype: String, data: String) -> Self {
        Self {
            request_id,
            subtype,
            data,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ControlResponse(id={:?}, subtype={:?})",
            self.request_id, self.subtype
        )
    }
}

// ---------------------------------------------------------------------------
// Internal protocol state
// ---------------------------------------------------------------------------

/// Classifies a raw JSON line from agent stdout.
#[derive(Debug)]
enum AgentOutput {
    /// A control request from the agent (needs a response).
    ControlRequest(ControlMessage),
    /// A conversation/stream message (forwarded to the client).
    ConversationMessage(String),
}

/// Pending response slot for client-initiated control requests.
struct PendingRequest {
    notify: Arc<Notify>,
    response: Arc<Mutex<Option<String>>>,
}

/// Internal state for the control protocol.
struct ProtocolInner {
    /// Writer to agent stdin.
    stdin_tx: Option<mpsc::Sender<String>>,
    /// Channel for conversation messages forwarded from the read loop.
    conversation_rx: Option<mpsc::Receiver<String>>,
    /// Pending client-initiated requests awaiting responses.
    pending: HashMap<String, PendingRequest>,
    /// Auto-incrementing counter for generating request IDs.
    next_id: u64,
    /// Whether the protocol is running.
    running: bool,
}

// ---------------------------------------------------------------------------
// RustControlProtocol — exposed to Python
// ---------------------------------------------------------------------------

/// Bidirectional control protocol handler.
///
/// Reads JSON messages from agent stdout in a background task, classifies
/// them as control requests or conversation messages, and routes accordingly.
/// Control requests are dispatched to registered Python callbacks.
#[pyclass]
pub struct RustControlProtocol {
    inner: Arc<Mutex<ProtocolInner>>,
    /// Python callback for permission checks.
    permission_callback: Arc<Mutex<Option<PyObject>>>,
    /// Python callback for hook dispatch.
    hook_callback: Arc<Mutex<Option<PyObject>>>,
    /// Python callback for MCP tool requests.
    mcp_callback: Arc<Mutex<Option<PyObject>>>,
    /// Channel sender for conversation messages (used by read loop).
    conversation_tx: Arc<Mutex<Option<mpsc::Sender<String>>>>,
    /// Handle to the background read task.
    read_task: Arc<Mutex<Option<tokio::task::JoinHandle<()>>>>,
    /// Handle to the background write task.
    write_task: Arc<Mutex<Option<tokio::task::JoinHandle<()>>>>,
}

#[pymethods]
impl RustControlProtocol {
    #[new]
    fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(ProtocolInner {
                stdin_tx: None,
                conversation_rx: None,
                pending: HashMap::new(),
                next_id: 1,
                running: false,
            })),
            permission_callback: Arc::new(Mutex::new(None)),
            hook_callback: Arc::new(Mutex::new(None)),
            mcp_callback: Arc::new(Mutex::new(None)),
            conversation_tx: Arc::new(Mutex::new(None)),
            read_task: Arc::new(Mutex::new(None)),
            write_task: Arc::new(Mutex::new(None)),
        }
    }

    /// Start the control protocol read/write loops.
    ///
    /// Takes ownership of the agent's stdin and stdout streams.
    fn start<'py>(
        &self,
        py: Python<'py>,
        stdin_fd: i64,
        stdout_fd: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let _permission_cb = self.permission_callback.clone();
        let _hook_cb = self.hook_callback.clone();
        let _mcp_cb = self.mcp_callback.clone();
        let conv_tx_holder = self.conversation_tx.clone();
        let read_task_holder = self.read_task.clone();
        let write_task_holder = self.write_task.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let (stdin_tx, mut stdin_rx) = mpsc::channel::<String>(256);
            let (conv_tx, conv_rx) = mpsc::channel::<String>(256);

            {
                let mut guard = inner.lock().await;
                guard.stdin_tx = Some(stdin_tx);
                guard.conversation_rx = Some(conv_rx);
                guard.running = true;
            }

            *conv_tx_holder.lock().await = Some(conv_tx.clone());

            // Background write loop: sends messages to agent stdin.
            let write_handle = tokio::spawn(async move {
                #[cfg(unix)]
                use std::os::fd::FromRawFd;
                #[cfg(windows)]
                use std::os::windows::io::FromRawHandle;
                // Safety: we trust the caller provides valid FDs/handles from the child process.
                #[cfg(unix)]
                let stdin_file = unsafe { std::fs::File::from_raw_fd(stdin_fd as i32) };
                #[cfg(windows)]
                let stdin_file = unsafe { std::fs::File::from_raw_handle(stdin_fd as *mut std::ffi::c_void) };
                let mut stdin = tokio::io::BufWriter::new(tokio::fs::File::from_std(stdin_file));

                while let Some(line) = stdin_rx.recv().await {
                    let data = format!("{}\n", line);
                    if stdin.write_all(data.as_bytes()).await.is_err() {
                        break;
                    }
                    if stdin.flush().await.is_err() {
                        break;
                    }
                }
            });

            // Background read loop: reads JSON lines from agent stdout.
            let inner_read = inner.clone();
            let read_handle = tokio::spawn(async move {
                #[cfg(unix)]
                use std::os::fd::FromRawFd;
                #[cfg(windows)]
                use std::os::windows::io::FromRawHandle;
                #[cfg(unix)]
                let stdout_file = unsafe { std::fs::File::from_raw_fd(stdout_fd as i32) };
                #[cfg(windows)]
                let stdout_file = unsafe { std::fs::File::from_raw_handle(stdout_fd as *mut std::ffi::c_void) };
                let stdout = tokio::fs::File::from_std(stdout_file);
                let mut reader = BufReader::new(stdout).lines();

                while let Ok(Some(line)) = reader.next_line().await {
                    let line = line.trim().to_string();
                    if line.is_empty() {
                        continue;
                    }

                    match classify_message(&line) {
                        AgentOutput::ControlRequest(msg) => {
                            // Check if this is a response to a pending request.
                            let mut guard = inner_read.lock().await;
                            if let Some(pending) = guard.pending.remove(&msg.request_id) {
                                *pending.response.lock().await = Some(msg.data.clone());
                                pending.notify.notify_one();
                            }
                            // Otherwise, dispatch to the appropriate callback.
                            // The Python layer handles this via Query.
                            drop(guard);

                            // Forward control requests as conversation messages
                            // so the Python layer can process them.
                            let _ = conv_tx.send(line).await;
                        }
                        AgentOutput::ConversationMessage(raw) => {
                            let _ = conv_tx.send(raw).await;
                        }
                    }
                }
            });

            *read_task_holder.lock().await = Some(read_handle);
            *write_task_holder.lock().await = Some(write_handle);

            Ok(())
        })
    }

    /// Send a control request from the SDK to the agent.
    ///
    /// Returns the JSON response data from the agent.
    fn send_control_request<'py>(
        &self,
        py: Python<'py>,
        subtype: String,
        data: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let (request_id, stdin_tx) = {
                let mut guard = inner.lock().await;
                let id = format!("sdk_{}", guard.next_id);
                guard.next_id += 1;

                let tx = guard
                    .stdin_tx
                    .clone()
                    .ok_or_else(|| ConduitError::Protocol("control protocol not started".into()))?;

                let notify = Arc::new(Notify::new());
                let response = Arc::new(Mutex::new(None));
                guard.pending.insert(
                    id.clone(),
                    PendingRequest {
                        notify: notify.clone(),
                        response: response.clone(),
                    },
                );

                (id, tx)
            };

            let msg = serde_json::json!({
                "type": "control",
                "request_id": request_id,
                "subtype": subtype,
                "data": serde_json::from_str::<serde_json::Value>(&data)
                    .unwrap_or(serde_json::Value::String(data.clone())),
            });

            stdin_tx
                .send(msg.to_string())
                .await
                .map_err(|_| ConduitError::Protocol("failed to send control request".into()))?;

            // Wait for the response (with a timeout).
            let guard = inner.lock().await;
            if let Some(pending) = guard.pending.get(&request_id) {
                let notify = pending.notify.clone();
                let response = pending.response.clone();
                drop(guard);

                tokio::time::timeout(std::time::Duration::from_secs(30), notify.notified())
                    .await
                    .map_err(|_| {
                        ConduitError::Timeout(format!(
                            "control request {:?} timed out",
                            request_id
                        ))
                    })?;

                let resp = response.lock().await.take().unwrap_or_default();
                Ok(resp)
            } else {
                drop(guard);
                Err(ConduitError::Protocol("pending request lost".into()).into())
            }
        })
    }

    /// Send a control response from the SDK back to the agent.
    fn send_control_response<'py>(
        &self,
        py: Python<'py>,
        request_id: String,
        subtype: String,
        data: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let stdin_tx = {
                let guard = inner.lock().await;
                guard
                    .stdin_tx
                    .clone()
                    .ok_or_else(|| ConduitError::Protocol("control protocol not started".into()))?
            };

            let msg = serde_json::json!({
                "type": "control_response",
                "request_id": request_id,
                "subtype": subtype,
                "data": serde_json::from_str::<serde_json::Value>(&data)
                    .unwrap_or(serde_json::Value::String(data.clone())),
            });

            stdin_tx
                .send(msg.to_string())
                .await
                .map_err(|_| ConduitError::Protocol("failed to send control response".into()))?;

            Ok(())
        })
    }

    /// Receive the next message from the conversation channel.
    ///
    /// Returns ``None`` if the channel is closed.
    fn recv_message<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut guard = inner.lock().await;
            if let Some(ref mut rx) = guard.conversation_rx {
                let msg = rx.recv().await;
                Ok(msg)
            } else {
                Ok(None)
            }
        })
    }

    /// Register the permission check callback.
    fn set_permission_callback(&self, callback: PyObject) {
        // Block briefly to set the callback. This is called during setup,
        // not in the hot path.
        let cb = self.permission_callback.clone();
        tokio::task::block_in_place(|| {
            let rt = tokio::runtime::Handle::current();
            rt.block_on(async {
                *cb.lock().await = Some(callback);
            });
        });
    }

    /// Register the hook dispatch callback.
    fn set_hook_callback(&self, callback: PyObject) {
        let cb = self.hook_callback.clone();
        tokio::task::block_in_place(|| {
            let rt = tokio::runtime::Handle::current();
            rt.block_on(async {
                *cb.lock().await = Some(callback);
            });
        });
    }

    /// Register the MCP tool request callback.
    fn set_mcp_callback(&self, callback: PyObject) {
        let cb = self.mcp_callback.clone();
        tokio::task::block_in_place(|| {
            let rt = tokio::runtime::Handle::current();
            rt.block_on(async {
                *cb.lock().await = Some(callback);
            });
        });
    }

    /// Whether the protocol is currently running.
    fn is_running<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            Ok(inner.lock().await.running)
        })
    }

    /// Shut down the control protocol read/write loops.
    fn stop<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let read_task = self.read_task.clone();
        let write_task = self.write_task.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            {
                let mut guard = inner.lock().await;
                guard.running = false;
                guard.stdin_tx = None; // Dropping sender closes the write loop.
            }

            // Abort the background tasks.
            if let Some(handle) = read_task.lock().await.take() {
                handle.abort();
            }
            if let Some(handle) = write_task.lock().await.take() {
                handle.abort();
            }

            Ok(())
        })
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Classify a raw JSON line from agent stdout.
fn classify_message(line: &str) -> AgentOutput {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(line) {
        if value.get("type").and_then(|t| t.as_str()) == Some("control") {
            if let (Some(request_id), Some(subtype)) = (
                value.get("request_id").and_then(|v| v.as_str()),
                value.get("subtype").and_then(|v| v.as_str()),
            ) {
                let data = value
                    .get("data")
                    .map(|d| d.to_string())
                    .unwrap_or_else(|| "{}".to_string());

                return AgentOutput::ControlRequest(ControlMessage {
                    request_id: request_id.to_string(),
                    subtype: subtype.to_string(),
                    data,
                });
            }
        }
    }

    AgentOutput::ConversationMessage(line.to_string())
}

/// Register control protocol types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ControlMessage>()?;
    m.add_class::<ControlResponse>()?;
    m.add_class::<RustControlProtocol>()?;
    Ok(())
}
