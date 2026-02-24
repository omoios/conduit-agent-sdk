//! ACP client: connects to an agent subprocess, performs the initialize
//! handshake, and exposes session/prompt operations to Python.
//!
//! Architecture: a background tokio task owns the `JrHandlerChain` connection
//! (which is closure-based and cannot be held across separate Python calls).
//! The Python-facing methods communicate with that task via mpsc channels.
//!
//! We use `with_spawned` + `serve` rather than `with_client` because
//! `with_client`'s `AsyncFnOnce` bound doesn't propagate `Send` to the
//! produced future, but `tokio::spawn` requires `Send`. `with_spawned`
//! explicitly requires `F: Future + Send + 'static`.

use crate::error::ConduitError;
use crate::transport::AgentProcess;
use crate::types::{
    Capabilities, ClientConfig, ContentBlock, ContentType, Message, MessageRole, SessionUpdate,
    UpdateKind,
};
use pyo3::prelude::*;
use sacp::schema::{
    AgentNotification, CancelNotification, ContentBlock as AcpContentBlock,
    Implementation, InitializeRequest, LoadSessionRequest, NewSessionRequest,
    PermissionOptionKind, PromptRequest, RequestPermissionOutcome, RequestPermissionRequest,
    RequestPermissionResponse, SelectedPermissionOutcome,
    SessionNotification, SetSessionModeRequest,
    SessionUpdate as AcpSessionUpdate, ToolCallStatus,
};
use sacp::UntypedMessage;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};

// ---------------------------------------------------------------------------
// Internal types for communicating with the background ACP task
// ---------------------------------------------------------------------------

/// Commands sent from Python-facing methods to the background task.
enum AcpCommand {
    NewSession {
        cwd: String,
        meta_json: Option<String>,
        mcp_servers_json: Option<String>,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    LoadSession {
        session_id: String,
        cwd: String,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    SetSessionMode {
        session_id: String,
        mode_id: String,
        reply: oneshot::Sender<Result<(), ConduitError>>,
    },
    SetConfigOption {
        session_id: String,
        config_id: String,
        value: String,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    Cancel {
        session_id: String,
    },
    ForkSession {
        session_id: String,
        cwd: String,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    ListSessions {
        cwd: Option<String>,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    ResumeSession {
        session_id: String,
        cwd: String,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    Prompt {
        session_id: String,
        text: String,
        content_json: Option<String>,
        reply: oneshot::Sender<Result<(), ConduitError>>,
    },
    Shutdown,
}

/// Streaming events pushed from the notification handler to the prompt collector.
#[derive(Debug)]
enum StreamEvent {
    TextDelta(String),
    ThoughtDelta(String),
    ToolUseStart {
        tool_name: String,
        tool_input: String,
        tool_use_id: String,
        tool_kind: Option<String>,
        tool_status: Option<String>,
    },
    ToolUseUpdate {
        tool_use_id: String,
        tool_status: Option<String>,
        tool_content: Option<String>,
        tool_locations: Option<String>,
    },
    ToolUseEnd {
        tool_use_id: String,
    },
    ModeChange {
        mode_id: String,
    },
    Plan {
        entries_json: String,
    },
    ConfigUpdate {
        config_json: String,
    },
    CommandsUpdate {
        commands_json: String,
    },
    Usage {
        usage_json: String,
    },
    SessionInfo {
        info_json: String,
    },
    Done {
        stop_reason: Option<String>,
    },
    RateLimit {
        method: String,
        params_json: String,
    },
}

// ---------------------------------------------------------------------------
// ClientInner — state stored while connected
// ---------------------------------------------------------------------------

/// Internal state shared across the client's async operations.
struct ClientInner {
    process: AgentProcess,
    capabilities: Option<Capabilities>,
    initialized: bool,
    session_id: Option<String>,
    cmd_tx: mpsc::Sender<AcpCommand>,
    /// JSON-serialized agent info from initialize response.
    agent_info_json: Option<String>,
}

// ---------------------------------------------------------------------------
// RustClient — the PyO3-exposed client
// ---------------------------------------------------------------------------

/// Rust-side ACP client exposed to Python via PyO3.
///
/// The Python `conduit_sdk.Client` class wraps this to provide a friendlier
/// async API. `RustClient` manages the agent subprocess lifecycle and
/// delegates protocol messages through the sacp handler chain.
#[pyclass]
pub struct RustClient {
    inner: Arc<Mutex<Option<ClientInner>>>,
    config: ClientConfig,
    /// Streaming events from the background task's notification handler.
    /// Separated from `inner` so prompt() can drain it without holding the
    /// inner lock across await points.
    update_rx: Arc<Mutex<Option<mpsc::Receiver<StreamEvent>>>>,
    /// Reply receiver from the most recent `send_prompt()` call.
    prompt_reply_rx: Arc<Mutex<Option<oneshot::Receiver<Result<(), ConduitError>>>>>,
    /// Python permission callback, set before connect().
    permission_callback: Arc<std::sync::Mutex<Option<PyObject>>>,
}

#[pymethods]
impl RustClient {
    #[new]
    fn new(config: ClientConfig) -> Self {
        Self {
            inner: Arc::new(Mutex::new(None)),
            config,
            update_rx: Arc::new(Mutex::new(None)),
            prompt_reply_rx: Arc::new(Mutex::new(None)),
            permission_callback: Arc::new(std::sync::Mutex::new(None)),
        }
    }

    /// Store a Python permission callback to be invoked for tool use requests.
    ///
    /// Must be called before `connect()`. The callback signature should be:
    /// `async def callback(tool_name: str, tool_input: str, context) -> PermissionResult`
    fn set_permission_callback(&self, callback: PyObject) {
        *self.permission_callback.lock().unwrap() = Some(callback);
    }

    /// Spawn the agent subprocess and perform the ACP initialize handshake.
    ///
    /// Returns the agent's advertised [`Capabilities`].
    fn connect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let config = self.config.clone();
        let update_rx_slot = self.update_rx.clone();
        let perm_callback_for_connect = self.permission_callback.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut process = AgentProcess::spawn(
                &config.command,
                config.cwd.as_deref(),
                &config.env,
            )
            .await?;

            // Take ownership of subprocess stdio for the ACP byte-stream transport.
            let child_stdin = process.take_stdin()?;
            let child_stdout = process.take_stdout()?;
            let transport =
                sacp::ByteStreams::new(child_stdin.compat_write(), child_stdout.compat());

            // Channels: commands → background task, streaming events ← notification handler
            let (cmd_tx, cmd_rx) = mpsc::channel::<AcpCommand>(32);
            let (update_tx, update_rx) = mpsc::channel::<StreamEvent>(512);
            let (caps_tx, caps_rx) =
                oneshot::channel::<Result<(Capabilities, Option<String>), ConduitError>>();

            // Clone update_tx for the notification handler (the other copy
            // goes into the spawned task to send Done events).
            let notif_tx = update_tx.clone();
            let ext_notif_tx = update_tx.clone();

            // Clone the permission callback for the request handler.
            let perm_callback = perm_callback_for_connect;

            // Build the handler chain with a spawned client task.
            let chain = sacp::JrHandlerChain::new()
                .name("conduit-sdk")
                // --- Session update notifications (streaming chunks) ---
                .on_receive_notification(
                    async move |notification: SessionNotification, _cx| {
                        match &notification.update {
                            AcpSessionUpdate::AgentMessageChunk(chunk) => {
                                if let AcpContentBlock::Text(tc) = &chunk.content {
                                    let _ = notif_tx
                                        .send(StreamEvent::TextDelta(tc.text.clone()))
                                        .await;
                                }
                            }
                            AcpSessionUpdate::AgentThoughtChunk(chunk) => {
                                if let AcpContentBlock::Text(tc) = &chunk.content {
                                    let _ = notif_tx
                                        .send(StreamEvent::ThoughtDelta(tc.text.clone()))
                                        .await;
                                }
                            }
                            AcpSessionUpdate::ToolCall(tc) => {
                                let tool_name = tc.title.clone();
                                let tool_input = tc
                                    .raw_input
                                    .as_ref()
                                    .map(|v| v.to_string())
                                    .unwrap_or_default();
                                let tool_use_id = tc.tool_call_id.0.to_string();
                                let tool_kind = Some(format!("{:?}", tc.kind));
                                let tool_status = Some(format!("{:?}", tc.status));
                                let _ = notif_tx
                                    .send(StreamEvent::ToolUseStart {
                                        tool_name,
                                        tool_input,
                                        tool_use_id,
                                        tool_kind,
                                        tool_status,
                                    })
                                    .await;
                            }
                            AcpSessionUpdate::ToolCallUpdate(tcu) => {
                                let tool_use_id = tcu.tool_call_id.0.to_string();
                                let tool_status = tcu.fields.status.as_ref().map(|s| format!("{:?}", s));
                                let tool_content = tcu.fields.content.as_ref()
                                    .and_then(|c| serde_json::to_string(c).ok());
                                let tool_locations = tcu.fields.locations.as_ref()
                                    .and_then(|l| serde_json::to_string(l).ok());

                                // Send rich update event
                                let _ = notif_tx
                                    .send(StreamEvent::ToolUseUpdate {
                                        tool_use_id: tool_use_id.clone(),
                                        tool_status: tool_status.clone(),
                                        tool_content,
                                        tool_locations,
                                    })
                                    .await;

                                // Also send legacy ToolUseEnd if terminal status
                                let is_terminal = tcu.fields.status.as_ref().map_or(false, |s| {
                                    matches!(s, ToolCallStatus::Completed | ToolCallStatus::Failed)
                                });
                                if is_terminal {
                                    let _ = notif_tx
                                        .send(StreamEvent::ToolUseEnd { tool_use_id })
                                        .await;
                                }
                            }
                            AcpSessionUpdate::Plan(plan) => {
                                if let Ok(json) = serde_json::to_string(&plan.entries) {
                                    let _ = notif_tx
                                        .send(StreamEvent::Plan { entries_json: json })
                                        .await;
                                }
                            }
                            AcpSessionUpdate::AvailableCommandsUpdate(cmd_update) => {
                                if let Ok(json) = serde_json::to_string(&cmd_update.available_commands) {
                                    let _ = notif_tx
                                        .send(StreamEvent::CommandsUpdate { commands_json: json })
                                        .await;
                                }
                            }
                            AcpSessionUpdate::CurrentModeUpdate(mode_update) => {
                                let _ = notif_tx
                                    .send(StreamEvent::ModeChange {
                                        mode_id: mode_update.current_mode_id.0.to_string(),
                                    })
                                    .await;
                            }
                            AcpSessionUpdate::ConfigOptionUpdate(config_update) => {
                                if let Ok(json) = serde_json::to_string(&config_update.config_options) {
                                    let _ = notif_tx
                                        .send(StreamEvent::ConfigUpdate { config_json: json })
                                        .await;
                                }
                            }
                            AcpSessionUpdate::UsageUpdate(usage) => {
                                let usage_data = serde_json::json!({
                                    "used": usage.used,
                                    "size": usage.size,
                                    "cost": usage.cost.as_ref().map(|c| serde_json::json!({
                                        "amount": c.amount,
                                        "currency": &c.currency,
                                    })),
                                });
                                let _ = notif_tx
                                    .send(StreamEvent::Usage {
                                        usage_json: usage_data.to_string(),
                                    })
                                    .await;
                            }
                            AcpSessionUpdate::SessionInfoUpdate(info) => {
                                let info_data = serde_json::json!({
                                    "title": serde_json::to_value(&info.title).unwrap_or_default(),
                                    "updated_at": serde_json::to_value(&info.updated_at).unwrap_or_default(),
                                });
                                let _ = notif_tx
                                    .send(StreamEvent::SessionInfo {
                                        info_json: info_data.to_string(),
                                    })
                                    .await;
                            }
                            AcpSessionUpdate::UserMessageChunk(_) => {
                                // Echo of user message — ignore.
                            }
                            _ => {
                                // Future variants — ignore gracefully.
                            }
                        }
                        Ok(())
                    },
                )
                // --- Extension notifications (rate_limit_event, etc.) ---
                .on_receive_notification(
                    async move |notification: AgentNotification, _cx| {
                        if let AgentNotification::ExtNotification(ext) = notification {
                            let method = ext.method.to_string();
                            let params_json = ext.params.to_string();
                            let _ = ext_notif_tx
                                .send(StreamEvent::RateLimit {
                                    method,
                                    params_json,
                                })
                                .await;
                        }
                        Ok(())
                    },
                )
                // --- Permission requests ---
                .on_receive_request(
                    async move |request: RequestPermissionRequest, request_cx| {
                        // Try to call the Python permission callback.
                        let decision = call_permission_callback(
                            &perm_callback,
                            &request,
                        )
                        .await;

                        match decision {
                            PermissionDecision::Allow => {
                                // Select the first "allow" option, or just the first option.
                                let allow_option = request
                                    .options
                                    .iter()
                                    .find(|o| {
                                        o.kind == PermissionOptionKind::AllowOnce
                                            || o.kind == PermissionOptionKind::AllowAlways
                                    })
                                    .or_else(|| request.options.first());

                                if let Some(opt) = allow_option {
                                    request_cx.respond(RequestPermissionResponse::new(
                                        RequestPermissionOutcome::Selected(
                                            SelectedPermissionOutcome::new(
                                                opt.option_id.clone(),
                                            ),
                                        ),
                                    ))
                                } else {
                                    request_cx.respond(RequestPermissionResponse::new(
                                        RequestPermissionOutcome::Cancelled,
                                    ))
                                }
                            }
                            PermissionDecision::Deny => {
                                request_cx.respond(RequestPermissionResponse::new(
                                    RequestPermissionOutcome::Cancelled,
                                ))
                            }
                        }
                    },
                )
                // --- Client logic (init handshake + command loop) ---
                .with_spawned(move |cx| {
                    acp_task(cx, caps_tx, cmd_rx, update_tx)
                });

            // Spawn the long-lived background task that owns the ACP connection.
            tokio::spawn(async move {
                if let Err(e) = chain.serve(transport).await {
                    eprintln!("conduit-sdk: ACP background task error: {e}");
                }
            });

            // Wait for the background task to complete the initialize handshake.
            let (capabilities, agent_info_json) = caps_rx
                .await
                .map_err(|_| {
                    ConduitError::Connection(
                        "ACP background task dropped before sending capabilities".into(),
                    )
                })?
                ?;

            // Store the streaming receiver for prompt() to drain.
            *update_rx_slot.lock().await = Some(update_rx);

            let client_inner = ClientInner {
                process,
                capabilities: Some(capabilities.clone()),
                initialized: true,
                session_id: None,
                cmd_tx,
                agent_info_json,
            };

            *inner.lock().await = Some(client_inner);
            Ok(capabilities)
        })
    }

    /// Create a new ACP session and return its ID.
    #[pyo3(signature = (cwd=None, meta_json=None, mcp_servers_json=None))]
    fn new_session<'py>(
        &self,
        py: Python<'py>,
        cwd: Option<String>,
        meta_json: Option<String>,
        mcp_servers_json: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let cwd = cwd.unwrap_or_else(|| {
                std::env::current_dir()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string()
            });
            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::NewSession {
                    cwd,
                    meta_json,
                    mcp_servers_json,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            let session_id = reply_rx
                .await
                .map_err(|_| ConduitError::Connection("session reply dropped".into()))??;

            // Store as the default session for prompt() auto-use.
            {
                let mut guard = inner.lock().await;
                if let Some(client) = guard.as_mut() {
                    client.session_id = Some(session_id.clone());
                }
            }
            Ok(session_id)
        })
    }

    /// Resume an existing session by ID.
    fn load_session<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        cwd: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let cwd = cwd.unwrap_or_else(|| {
                std::env::current_dir()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string()
            });
            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::LoadSession {
                    session_id,
                    cwd,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            let session_id = reply_rx
                .await
                .map_err(|_| ConduitError::Connection("load session reply dropped".into()))??;

            // Store as the default session.
            {
                let mut guard = inner.lock().await;
                if let Some(client) = guard.as_mut() {
                    client.session_id = Some(session_id.clone());
                }
            }
            Ok(session_id)
        })
    }

    /// Set the agent mode for a session (e.g. "ask", "code", "architect").
    fn set_session_mode<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        mode_id: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::SetSessionMode {
                    session_id,
                    mode_id,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            reply_rx
                .await
                .map_err(|_| ConduitError::Connection("set mode reply dropped".into()))??;
            Ok(())
        })
    }

    /// Set a config option on a session (replaces set_mode/set_model).
    fn set_config_option<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        config_id: String,
        value: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::SetConfigOption {
                    session_id,
                    config_id,
                    value,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            reply_rx
                .await
                .map_err(|_| ConduitError::Connection("set config reply dropped".into()))?
                .map_err(Into::into)
        })
    }

    /// Cancel (interrupt) a running prompt in a session.
    fn cancel_session<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            cmd_tx
                .send(AcpCommand::Cancel { session_id })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            Ok(())
        })
    }

    /// Fork a session, creating a new session with shared history.
    fn fork_session<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        cwd: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let cwd = cwd.unwrap_or_else(|| {
                std::env::current_dir()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string()
            });
            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::ForkSession {
                    session_id,
                    cwd,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            reply_rx
                .await
                .map_err(|_| ConduitError::Connection("fork session reply dropped".into()))?
                .map_err(Into::into)
        })
    }

    /// List available sessions. Returns JSON array.
    fn list_sessions<'py>(
        &self,
        py: Python<'py>,
        cwd: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::ListSessions {
                    cwd,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            reply_rx
                .await
                .map_err(|_| ConduitError::Connection("list sessions reply dropped".into()))?
                .map_err(Into::into)
        })
    }

    /// Resume an existing agent-side session.
    fn resume_session<'py>(
        &self,
        py: Python<'py>,
        session_id: String,
        cwd: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cmd_tx = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                client.cmd_tx.clone()
            };

            let cwd = cwd.unwrap_or_else(|| {
                std::env::current_dir()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string()
            });
            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::ResumeSession {
                    session_id,
                    cwd,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            let session_id = reply_rx
                .await
                .map_err(|_| ConduitError::Connection("resume session reply dropped".into()))??;

            // Store as the default session.
            {
                let mut guard = inner.lock().await;
                if let Some(client) = guard.as_mut() {
                    client.session_id = Some(session_id.clone());
                }
            }
            Ok(session_id)
        })
    }

    /// Send a prompt to the agent within the given (or default) session.
    ///
    /// Returns a list of [`Message`] objects. Streaming is handled at the
    /// Python layer by wrapping this in an async iterator.
    #[pyo3(signature = (text, session_id=None, content_json=None))]
    fn prompt<'py>(
        &self,
        py: Python<'py>,
        text: String,
        session_id: Option<String>,
        content_json: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let update_rx_slot = self.update_rx.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            // Snapshot cmd_tx and session_id without holding the lock across awaits.
            let (cmd_tx, default_session_id) = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                if !client.initialized {
                    return Err(
                        ConduitError::Connection("client not initialized".into()).into()
                    );
                }
                (client.cmd_tx.clone(), client.session_id.clone())
            };

            // Use explicit session_id, or fall back to default, or auto-create.
            let session_id = match session_id.or(default_session_id) {
                Some(id) => id,
                None => {
                    let cwd = std::env::current_dir()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_string();
                    let (reply_tx, reply_rx) = oneshot::channel();
                    cmd_tx
                        .send(AcpCommand::NewSession {
                            cwd,
                            meta_json: None,
                            mcp_servers_json: None,
                            reply: reply_tx,
                        })
                        .await
                        .map_err(|_| {
                            ConduitError::Connection("background task closed".into())
                        })?;
                    let id = reply_rx.await.map_err(|_| {
                        ConduitError::Connection("session reply dropped".into())
                    })??;

                    // Persist session_id for subsequent prompts.
                    {
                        let mut guard = inner.lock().await;
                        if let Some(client) = guard.as_mut() {
                            client.session_id = Some(id.clone());
                        }
                    }
                    id
                }
            };

            // Send the prompt command to the background task.
            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::Prompt {
                    session_id: session_id.clone(),
                    text,
                    content_json: content_json.clone(),
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            // Collect streaming updates until the Done sentinel arrives.
            let mut collected_text = String::new();
            let mut got_message = false;
            let mut stop_reason: Option<String> = None;
            {
                let mut rx_guard = update_rx_slot.lock().await;
                let update_rx = rx_guard.as_mut().ok_or_else(|| {
                    ConduitError::Connection("update channel not initialized".into())
                })?;
                loop {
                    match update_rx.recv().await {
                        Some(StreamEvent::TextDelta(t)) => {
                            got_message = true;
                            collected_text.push_str(&t);
                        }
                        Some(StreamEvent::ThoughtDelta(t)) => {
                            if !got_message {
                                collected_text.push_str(&t);
                            }
                        }
                        Some(StreamEvent::ToolUseStart { .. })
                        | Some(StreamEvent::ToolUseEnd { .. })
                        | Some(StreamEvent::ToolUseUpdate { .. })
                        | Some(StreamEvent::ModeChange { .. })
                        | Some(StreamEvent::Plan { .. })
                        | Some(StreamEvent::ConfigUpdate { .. })
                        | Some(StreamEvent::CommandsUpdate { .. })
                        | Some(StreamEvent::Usage { .. })
                        | Some(StreamEvent::SessionInfo { .. })
                        | Some(StreamEvent::RateLimit { .. }) => {
                            // Non-text events consumed in batch mode.
                        }
                        Some(StreamEvent::Done { stop_reason: sr }) => {
                            stop_reason = sr;
                            break;
                        }
                        None => break,
                    }
                }
            }

            // Wait for the background task's confirmation that the prompt completed.
            reply_rx
                .await
                .map_err(|_| ConduitError::Connection("prompt reply dropped".into()))??;

            // Assemble a Message from the collected text.
            let messages: Vec<Message> = if collected_text.is_empty() {
                vec![]
            } else {
                vec![Message {
                    role: MessageRole::Assistant,
                    content: vec![ContentBlock {
                        content_type: ContentType::Text,
                        text: Some(collected_text),
                        tool_name: None,
                        tool_input: None,
                        tool_use_id: None,
                    }],
                    session_id: Some(session_id),
                    stop_reason,
                }]
            };

            Ok(messages)
        })
    }

    /// Send a prompt without waiting for completion.
    ///
    /// Use with [`recv_update`] for real-time streaming. The prompt is sent
    /// to the background ACP task and streaming events can be polled via
    /// `recv_update()` until `None` is returned.
    #[pyo3(signature = (text, session_id=None, content_json=None))]
    fn send_prompt<'py>(
        &self,
        py: Python<'py>,
        text: String,
        session_id: Option<String>,
        content_json: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let prompt_reply_rx = self.prompt_reply_rx.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let (cmd_tx, default_session_id) = {
                let guard = inner.lock().await;
                let client = guard
                    .as_ref()
                    .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
                if !client.initialized {
                    return Err(
                        ConduitError::Connection("client not initialized".into()).into(),
                    );
                }
                (client.cmd_tx.clone(), client.session_id.clone())
            };

            // Auto-create session if needed.
            let session_id = match session_id.or(default_session_id) {
                Some(id) => id,
                None => {
                    let cwd = std::env::current_dir()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_string();
                    let (reply_tx, reply_rx) = oneshot::channel();
                    cmd_tx
                        .send(AcpCommand::NewSession {
                            cwd,
                            meta_json: None,
                            mcp_servers_json: None,
                            reply: reply_tx,
                        })
                        .await
                        .map_err(|_| {
                            ConduitError::Connection("background task closed".into())
                        })?;
                    let id = reply_rx.await.map_err(|_| {
                        ConduitError::Connection("session reply dropped".into())
                    })??;
                    {
                        let mut guard = inner.lock().await;
                        if let Some(client) = guard.as_mut() {
                            client.session_id = Some(id.clone());
                        }
                    }
                    id
                }
            };

            // Send prompt and store the reply receiver for later.
            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::Prompt {
                    session_id,
                    text,
                    content_json,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            *prompt_reply_rx.lock().await = Some(reply_rx);
            Ok(())
        })
    }

    /// Receive the next streaming update from the agent.
    ///
    /// Returns a [`SessionUpdate`] for each chunk (text, thought, tool use,
    /// mode change, plan, config, commands, usage, session info),
    /// or `None` when the prompt is complete.
    fn recv_update<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let update_rx_slot = self.update_rx.clone();
        let prompt_reply_rx = self.prompt_reply_rx.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut rx_guard = update_rx_slot.lock().await;
            let update_rx = rx_guard.as_mut().ok_or_else(|| {
                ConduitError::Connection("update channel not initialized".into())
            })?;

            let su_defaults = || SessionUpdate {
                kind: UpdateKind::TextDelta,
                text: None,
                tool_name: None,
                tool_input: None,
                tool_use_id: None,
                error: None,
                stop_reason: None,
                tool_kind: None,
                tool_status: None,
                tool_content: None,
                tool_locations: None,
                mode_id: None,
                plan_json: None,
                config_json: None,
                commands_json: None,
                usage_json: None,
                session_info_json: None,
                rate_limit_json: None,
            };

            match update_rx.recv().await {
                Some(StreamEvent::TextDelta(t)) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::TextDelta,
                    text: Some(t),
                    ..su_defaults()
                })),
                Some(StreamEvent::ThoughtDelta(t)) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ThoughtDelta,
                    text: Some(t),
                    ..su_defaults()
                })),
                Some(StreamEvent::ToolUseStart {
                    tool_name,
                    tool_input,
                    tool_use_id,
                    tool_kind,
                    tool_status,
                }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ToolUseStart,
                    tool_name: Some(tool_name),
                    tool_input: Some(tool_input),
                    tool_use_id: Some(tool_use_id),
                    tool_kind,
                    tool_status,
                    ..su_defaults()
                })),
                Some(StreamEvent::ToolUseUpdate {
                    tool_use_id,
                    tool_status,
                    tool_content,
                    tool_locations,
                }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ToolUseUpdate,
                    tool_use_id: Some(tool_use_id),
                    tool_status,
                    tool_content,
                    tool_locations,
                    ..su_defaults()
                })),
                Some(StreamEvent::ToolUseEnd { tool_use_id }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ToolUseEnd,
                    tool_use_id: Some(tool_use_id),
                    ..su_defaults()
                })),
                Some(StreamEvent::ModeChange { mode_id }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ModeChange,
                    mode_id: Some(mode_id),
                    ..su_defaults()
                })),
                Some(StreamEvent::Plan { entries_json }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::Plan,
                    plan_json: Some(entries_json),
                    ..su_defaults()
                })),
                Some(StreamEvent::ConfigUpdate { config_json }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ConfigUpdate,
                    config_json: Some(config_json),
                    ..su_defaults()
                })),
                Some(StreamEvent::CommandsUpdate { commands_json }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::CommandsUpdate,
                    commands_json: Some(commands_json),
                    ..su_defaults()
                })),
                Some(StreamEvent::Usage { usage_json }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::Usage,
                    usage_json: Some(usage_json),
                    ..su_defaults()
                })),
                Some(StreamEvent::SessionInfo { info_json }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::SessionInfo,
                    session_info_json: Some(info_json),
                    ..su_defaults()
                })),
                Some(StreamEvent::Done { stop_reason }) => {
                    // Check prompt completion status.
                    if let Some(reply_rx) = prompt_reply_rx.lock().await.take() {
                        if let Ok(result) = reply_rx.await {
                            result?;
                        }
                    }
                    // Return a Done update with stop_reason if caller wants it.
                    if stop_reason.is_some() {
                        Ok(Some(SessionUpdate {
                            kind: UpdateKind::Done,
                            stop_reason,
                            ..su_defaults()
                        }))
                    } else {
                        Ok(None)
                    }
                }
                Some(StreamEvent::RateLimit { method, params_json }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::RateLimit,
                    rate_limit_json: Some(serde_json::json!({
                        "method": method,
                        "params": serde_json::from_str::<serde_json::Value>(&params_json).unwrap_or_default(),
                    }).to_string()),
                    ..su_defaults()
                })),
                None => Ok(None),
            }
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

    /// Return agent info as a JSON string (name, version, title).
    fn agent_info<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let guard = inner.lock().await;
            let client = guard
                .as_ref()
                .ok_or_else(|| ConduitError::Connection("client not connected".into()))?;
            Ok(client.agent_info_json.clone())
        })
    }

    /// Disconnect from the agent and terminate the subprocess.
    fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            if let Some(ref mut client) = *inner.lock().await {
                // Ask the background task to exit its command loop.
                let _ = client.cmd_tx.send(AcpCommand::Shutdown).await;
                client.process.kill().await?;
            }
            Ok(())
        })
    }
}

// ---------------------------------------------------------------------------
// Background task (runs inside JrHandlerChain::with_spawned)
// ---------------------------------------------------------------------------

/// The client task spawned on the ACP connection.
///
/// Performs the initialize handshake, sends the resulting capabilities back
/// to `connect()` via `caps_tx`, then enters a command loop that processes
/// [`AcpCommand`] messages from the Python-facing API.
async fn acp_task(
    cx: sacp::JrConnectionCx,
    caps_tx: oneshot::Sender<Result<(Capabilities, Option<String>), ConduitError>>,
    mut cmd_rx: mpsc::Receiver<AcpCommand>,
    update_tx: mpsc::Sender<StreamEvent>,
) -> Result<(), sacp::schema::Error> {
    // ---- Initialize handshake ----
    let init_req = InitializeRequest::new(sacp::schema::ProtocolVersion::LATEST)
        .client_info(Implementation::new("conduit-agent-sdk", env!("CARGO_PKG_VERSION")));

    let init_result = cx
        .send_request(init_req)
        .block_task()
        .await;

    let init_response = match init_result {
        Ok(resp) => resp,
        Err(e) => {
            let _ = caps_tx.send(Err(ConduitError::Protocol(e.to_string())));
            return Err(e);
        }
    };

    let capabilities = Capabilities::from_acp(&init_response.agent_capabilities);

    // Serialize agent info if available.
    let agent_info_json = init_response.agent_info.as_ref().map(|info| {
        serde_json::json!({
            "name": info.name,
            "version": info.version,
            "title": info.title,
        })
        .to_string()
    });

    let _ = caps_tx.send(Ok((capabilities, agent_info_json)));

    // ---- Command loop ----
    while let Some(cmd) = cmd_rx.recv().await {
        match cmd {
            AcpCommand::NewSession {
                cwd,
                meta_json,
                mcp_servers_json,
                reply,
            } => {
                let mut req = NewSessionRequest::new(PathBuf::from(&cwd));

                // Apply _meta if provided.
                if let Some(ref meta_str) = meta_json {
                    if let Ok(meta) =
                        serde_json::from_str::<serde_json::Map<String, serde_json::Value>>(
                            meta_str,
                        )
                    {
                        req = req.meta(meta);
                    }
                }

                // Apply MCP servers if provided.
                if let Some(ref servers_str) = mcp_servers_json {
                    // McpServer implements Deserialize via serde, try direct deser
                    if let Ok(servers) =
                        serde_json::from_str::<Vec<sacp::schema::McpServer>>(servers_str)
                    {
                        req = req.mcp_servers(servers);
                    }
                }

                let result = cx.send_request(req).block_task().await;
                match result {
                    Ok(resp) => {
                        let _ = reply.send(Ok(resp.session_id.0.to_string()));
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::LoadSession {
                session_id,
                cwd,
                reply,
            } => {
                let sid = session_id.clone();
                let result = cx
                    .send_request(LoadSessionRequest::new(session_id, PathBuf::from(&cwd)))
                    .block_task()
                    .await;
                match result {
                    Ok(_resp) => {
                        let _ = reply.send(Ok(sid));
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::SetSessionMode {
                session_id,
                mode_id,
                reply,
            } => {
                let result = cx
                    .send_request(SetSessionModeRequest::new(session_id, mode_id))
                    .block_task()
                    .await;
                match result {
                    Ok(_resp) => {
                        let _ = reply.send(Ok(()));
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::SetConfigOption {
                session_id,
                config_id,
                value,
                reply,
            } => {
                let params = serde_json::json!({
                    "session_id": session_id,
                    "config_id": config_id,
                    "value": value,
                });
                match UntypedMessage::new("session/set_config_option", &params) {
                    Ok(msg) => {
                        let result = cx.send_request(msg).block_task().await;
                        match result {
                            Ok(val) => {
                                let json = serde_json::to_string(&val)
                                    .unwrap_or_else(|_| "{}".into());
                                let _ = reply.send(Ok(json));
                            }
                            Err(e) => {
                                let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                            }
                        }
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::Cancel { session_id } => {
                // CancelNotification is a fire-and-forget notification.
                let _ = cx.send_notification(CancelNotification::new(session_id));
            }
            AcpCommand::ForkSession {
                session_id,
                cwd,
                reply,
            } => {
                let params = serde_json::json!({
                    "session_id": session_id,
                    "cwd": cwd,
                });
                match UntypedMessage::new("session/fork", &params) {
                    Ok(msg) => {
                        let result = cx.send_request(msg).block_task().await;
                        match result {
                            Ok(val) => {
                                let sid = val.get("session_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string();
                                let _ = reply.send(Ok(sid));
                            }
                            Err(e) => {
                                let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                            }
                        }
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::ListSessions { cwd, reply } => {
                let params = match cwd {
                    Some(c) => serde_json::json!({ "cwd": c }),
                    None => serde_json::json!({}),
                };
                match UntypedMessage::new("session/list", &params) {
                    Ok(msg) => {
                        let result = cx.send_request(msg).block_task().await;
                        match result {
                            Ok(val) => {
                                let json = serde_json::to_string(&val)
                                    .unwrap_or_else(|_| "[]".into());
                                let _ = reply.send(Ok(json));
                            }
                            Err(e) => {
                                let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                            }
                        }
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::ResumeSession {
                session_id,
                cwd,
                reply,
            } => {
                let sid = session_id.clone();
                let params = serde_json::json!({
                    "session_id": session_id,
                    "cwd": cwd,
                });
                match UntypedMessage::new("session/resume", &params) {
                    Ok(msg) => {
                        let result = cx.send_request(msg).block_task().await;
                        match result {
                            Ok(_) => {
                                let _ = reply.send(Ok(sid));
                            }
                            Err(e) => {
                                let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                            }
                        }
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::Prompt {
                session_id,
                text,
                content_json,
                reply,
            } => {
                // Build content blocks: use rich content JSON if provided,
                // otherwise wrap the text string as a single Text block.
                let content_blocks: Vec<sacp::schema::ContentBlock> = match content_json {
                    Some(json_str) => {
                        serde_json::from_str(&json_str).unwrap_or_else(|_| vec![text.into()])
                    }
                    None => vec![text.into()],
                };
                let result = cx
                    .send_request(PromptRequest::new(session_id, content_blocks))
                    .block_task()
                    .await;
                // Yield to the runtime to let any in-flight notification
                // handlers finish sending their StreamEvents through notif_tx
                // before we send the Done sentinel.
                for _ in 0..10 {
                    tokio::task::yield_now().await;
                }

                // Extract stop_reason from the response.
                let stop_reason = match &result {
                    Ok(resp) => Some(format!("{:?}", resp.stop_reason)),
                    Err(_) => None,
                };

                // Signal prompt completion so the collector loop exits.
                let _ = update_tx
                    .send(StreamEvent::Done { stop_reason })
                    .await;

                match result {
                    Ok(_resp) => {
                        let _ = reply.send(Ok(()));
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ConduitError::Protocol(e.to_string())));
                    }
                }
            }
            AcpCommand::Shutdown => break,
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Permission callback support
// ---------------------------------------------------------------------------

/// Decision from the Python permission callback.
enum PermissionDecision {
    Allow,
    Deny,
}

/// Call the Python permission callback, if set.
///
/// Acquires the GIL to invoke the async callback, awaits the resulting
/// future, and maps the Python `PermissionResult` to a `PermissionDecision`.
/// Falls back to `Allow` if no callback is set or if the callback errors.
async fn call_permission_callback(
    callback_arc: &Arc<std::sync::Mutex<Option<PyObject>>>,
    request: &RequestPermissionRequest,
) -> PermissionDecision {
    // Clone the Python callback under the GIL (if set).
    let callback = Python::with_gil(|py| {
        let guard = callback_arc.lock().unwrap();
        guard.as_ref().map(|cb| cb.clone_ref(py))
    });

    let callback = match callback {
        Some(cb) => cb,
        None => return PermissionDecision::Allow, // No callback = auto-approve.
    };

    // Extract tool details from the ACP request.
    let tool_name = request
        .tool_call
        .fields
        .title
        .clone()
        .unwrap_or_default();
    let tool_input = request
        .tool_call
        .fields
        .raw_input
        .as_ref()
        .map(|v| v.to_string())
        .unwrap_or_else(|| "{}".into());
    let tool_use_id = request.tool_call.tool_call_id.0.to_string();
    let session_id = request.session_id.0.to_string();

    // Call the Python callback: async def callback(tool_name, tool_input, context) -> PermissionResult
    let future_result = Python::with_gil(|py| -> PyResult<_> {
        // Build a ToolPermissionContext-like dict for the context argument.
        let ctx = pyo3::types::PyDict::new(py);
        ctx.set_item("tool_name", &tool_name)?;
        ctx.set_item("tool_input", &tool_input)?;
        ctx.set_item("tool_use_id", &tool_use_id)?;
        ctx.set_item("session_id", &session_id)?;

        let coro = callback.call1(py, (&tool_name, &tool_input, ctx))?;
        pyo3_async_runtimes::tokio::into_future(coro.into_bound(py))
    });

    let future = match future_result {
        Ok(f) => f,
        Err(_) => return PermissionDecision::Allow,
    };

    let py_result = match future.await {
        Ok(r) => r,
        Err(_) => return PermissionDecision::Allow,
    };

    // Check if the result is a PermissionResultDeny (has .reason attribute).
    // PermissionResultAllow has no .reason, PermissionResultDeny does.
    let is_deny = Python::with_gil(|py| {
        py_result
            .getattr(py, "reason")
            .map(|r| !r.is_none(py))
            .unwrap_or(false)
    });

    if is_deny {
        PermissionDecision::Deny
    } else {
        PermissionDecision::Allow
    }
}

/// Register client types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustClient>()?;
    Ok(())
}
