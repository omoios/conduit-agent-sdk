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
    Capabilities, ClientConfig, SessionUpdate,
    UpdateKind,
};
use pyo3::prelude::*;
use agent_client_protocol::schema::{
    AgentNotification, CancelNotification, ContentBlock as AcpContentBlock,
    Implementation, InitializeRequest, LoadSessionRequest, NewSessionRequest,
    PermissionOptionKind, PromptRequest, RequestPermissionOutcome, RequestPermissionRequest,
    RequestPermissionResponse, SelectedPermissionOutcome,
    SessionNotification, SetSessionModeRequest,
    SessionUpdate as AcpSessionUpdate,
};
use agent_client_protocol::{Client, ConnectionTo, Agent, Responder, UntypedMessage};
use agent_client_protocol::schema::{
    ClientCapabilities, CreateElicitationRequest, CreateElicitationResponse,
    ElicitationAcceptAction, ElicitationAction, ElicitationCapabilities,
    ElicitationContentValue, ElicitationFormCapabilities, ElicitationUrlCapabilities,
};
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
    DeleteSession {
        session_id: String,
        reply: oneshot::Sender<Result<String, ConduitError>>,
    },
    Shutdown,
}

/// Serialize a serde value to its wire string (e.g. `ToolKind::Read` → `"read"`,
/// `StopReason::EndTurn` → `"end_turn"`). Falls back to the `Debug` form if the
/// serialized value is not a string.
fn serde_wire_str<T: serde::Serialize + std::fmt::Debug>(v: &T) -> String {
    serde_json::to_value(v)
        .ok()
        .and_then(|val| val.as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| format!("{:?}", v))
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
    /// Python elicitation callback (unstable), set before connect().
    elicitation_callback: Arc<std::sync::Mutex<Option<PyObject>>>,
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
            elicitation_callback: Arc::new(std::sync::Mutex::new(None)),
        }
    }

    /// Store a Python permission callback to be invoked for tool use requests.
    ///
    /// Must be called before `connect()`. The callback signature should be:
    /// `async def callback(tool_name: str, tool_input: str, context) -> PermissionResult`
    fn set_permission_callback(&self, callback: PyObject) {
        *self.permission_callback.lock().unwrap() = Some(callback);
    }

    /// Store a Python elicitation callback invoked for `elicitation/create`
    /// requests. Must be called before `connect()`. When set, the client
    /// advertises the unstable `elicitation` capability during initialize.
    /// Signature: `async def bridge(payload_json: str) -> str`.
    fn set_elicitation_callback(&self, callback: PyObject) {
        *self.elicitation_callback.lock().unwrap() = Some(callback);
    }

    /// Spawn the agent subprocess and perform the ACP initialize handshake.
    ///
    /// Returns the agent's advertised [`Capabilities`].
    fn connect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let config = self.config.clone();
        let update_rx_slot = self.update_rx.clone();
        let perm_callback_for_connect = self.permission_callback.clone();
        let elicit_callback_for_connect = self.elicitation_callback.clone();
        // Advertise the unstable elicitation capability only when a handler
        // is configured, so we never claim support we cannot fulfill.
        let advertise_elicitation = self.elicitation_callback.lock().unwrap().is_some();
        // Capture the Python event-loop task locals so handler closures
        // running on the spawned background task can call `into_future()`.
        let task_locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;

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
                agent_client_protocol::ByteStreams::new(child_stdin.compat_write(), child_stdout.compat());

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
            let elicit_callback = elicit_callback_for_connect;

            // Build the handler chain with a spawned client task.
            let builder = Client.builder()
                .name("conduit-sdk")
                // --- Session update notifications (streaming chunks) ---
                .on_receive_notification(
                    async move |notification: SessionNotification, _cx: ConnectionTo<Agent>| {
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
                                let tool_kind = Some(serde_wire_str(&tc.kind));
                                let tool_status = Some(serde_wire_str(&tc.status));
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
                                let tool_status = tcu.fields.status.as_ref().map(serde_wire_str);
                                let tool_content = tcu.fields.content.as_ref()
                                    .and_then(|c| serde_json::to_string(c).ok());
                                let tool_locations = tcu.fields.locations.as_ref()
                                    .and_then(|l| serde_json::to_string(l).ok());

                                // A single terminal ToolUseUpdate carries status + content;
                                // no synthetic ToolUseEnd follows (avoids double-emit).
                                let _ = notif_tx
                                    .send(StreamEvent::ToolUseUpdate {
                                        tool_use_id,
                                        tool_status,
                                        tool_content,
                                        tool_locations,
                                    })
                                    .await;
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
                    agent_client_protocol::on_receive_notification!(),
                )
                // --- Extension notifications (rate_limit_event, etc.) ---
                .on_receive_notification(
                    async move |notification: AgentNotification, _cx: ConnectionTo<Agent>| {
                        if let AgentNotification::ExtNotification(ext) = notification {
                            let method = ext.method.to_string();
                            let params_json = ext.params.get().to_string();
                            let _ = ext_notif_tx
                                .send(StreamEvent::RateLimit {
                                    method,
                                    params_json,
                                })
                                .await;
                        }
                        Ok(())
                    },
                    agent_client_protocol::on_receive_notification!(),
                )
                // --- Permission requests ---
                .on_receive_request(
                    async move |request: RequestPermissionRequest, responder: Responder<RequestPermissionResponse>, _cx: ConnectionTo<Agent>| {
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
                                    responder.respond(RequestPermissionResponse::new(
                                        RequestPermissionOutcome::Selected(
                                            SelectedPermissionOutcome::new(
                                                opt.option_id.clone(),
                                            ),
                                        ),
                                    ))
                                } else {
                                    responder.respond(RequestPermissionResponse::new(
                                        RequestPermissionOutcome::Cancelled,
                                    ))
                                }
                            }
                            PermissionDecision::Deny => {
                                responder.respond(RequestPermissionResponse::new(
                                    RequestPermissionOutcome::Cancelled,
                                ))
                            }
                        }
                    },
                    agent_client_protocol::on_receive_request!(),
                )
                // --- Elicitation requests (unstable) ---
                .on_receive_request(
                    async move |request: CreateElicitationRequest, responder: Responder<CreateElicitationResponse>, _cx: ConnectionTo<Agent>| {
                        let response = call_elicitation_callback(&elicit_callback, &request).await;
                        responder.respond(response)
                    },
                    agent_client_protocol::on_receive_request!(),
                );

            // Spawn the long-lived background task that owns the ACP connection.
            // connect_with runs the dispatch loop and the main_fn (acp_task: the
            // init handshake + command loop) concurrently; the connection lives
            // as long as acp_task runs and ends when it returns (on Shutdown).
            tokio::spawn(async move {
                // Re-enter the Python event-loop context on this detached
                // task so `into_future()` (permission & elicitation handlers)
                // can resolve the running loop.
                let result = pyo3_async_runtimes::tokio::scope(task_locals, async move {
                    builder
                        .connect_with(transport, move |cx| acp_task(cx, caps_tx, cmd_rx, update_tx, advertise_elicitation))
                        .await
                })
                .await;
                if let Err(e) = result {
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

    /// Delete a session from the agent's session list.
    fn delete_session<'py>(
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

            let (reply_tx, reply_rx) = oneshot::channel();
            cmd_tx
                .send(AcpCommand::DeleteSession {
                    session_id,
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            reply_rx
                .await
                .map_err(|_| ConduitError::Connection("delete session reply dropped".into()))?
                .map_err(Into::into)
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
                    // Always surface a terminal Done update so downstream
                    // collectors and Stop hooks fire uniformly (D9).
                    Ok(Some(SessionUpdate {
                        kind: UpdateKind::Done,
                        stop_reason,
                        ..su_defaults()
                    }))
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
    cx: ConnectionTo<Agent>,
    caps_tx: oneshot::Sender<Result<(Capabilities, Option<String>), ConduitError>>,
    mut cmd_rx: mpsc::Receiver<AcpCommand>,
    update_tx: mpsc::Sender<StreamEvent>,
    advertise_elicitation: bool,
) -> Result<(), agent_client_protocol::Error> {
    // ---- Initialize handshake ----
    let init_req = InitializeRequest::new(agent_client_protocol::schema::ProtocolVersion::LATEST)
        .client_info(Implementation::new("conduit-agent-sdk", env!("CARGO_PKG_VERSION")));

    // Advertise the unstable elicitation capability only when a handler is
    // configured, so we never claim support we cannot fulfill.
    let init_req = if advertise_elicitation {
        init_req.client_capabilities(
            ClientCapabilities::new().elicitation(
                ElicitationCapabilities::new()
                    .form(ElicitationFormCapabilities::new())
                    .url(ElicitationUrlCapabilities::new()),
            ),
        )
    } else {
        init_req
    };

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
                    // McpServer is an internally-tagged enum (tag = "type");
                    // surface parse failures instead of silently dropping the
                    // server list, which would hide SDK tools from the agent.
                    match serde_json::from_str::<Vec<agent_client_protocol::schema::McpServer>>(
                        servers_str,
                    ) {
                        Ok(servers) => {
                            req = req.mcp_servers(servers);
                        }
                        Err(e) => {
                            eprintln!(
                                "conduit-sdk: ignoring mcp_servers (parse failed; SDK tools will NOT be exposed): {e}"
                            );
                        }
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
            AcpCommand::DeleteSession { session_id, reply } => {
                let params = serde_json::json!({
                    "session_id": session_id,
                });
                match UntypedMessage::new("session/delete", &params) {
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
                let content_blocks: Vec<agent_client_protocol::schema::ContentBlock> = match content_json {
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
                    Ok(resp) => Some(serde_wire_str(&resp.stop_reason)),
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

/// Invoke the Python elicitation bridge for an `elicitation/create` request.
///
/// Serializes the full ACP request to JSON, calls the Python callback
/// (`async def bridge(payload_json: str) -> str`), and parses the returned
/// JSON (`{"action": "accept"|"decline"|"cancel", "content": {...}}`) into a
/// [`CreateElicitationResponse`]. Any failure resolves to `Cancel`.
async fn call_elicitation_callback(
    callback_arc: &Arc<std::sync::Mutex<Option<PyObject>>>,
    request: &CreateElicitationRequest,
) -> CreateElicitationResponse {
    let callback = Python::with_gil(|py| {
        let guard = callback_arc.lock().unwrap();
        guard.as_ref().map(|cb| cb.clone_ref(py))
    });

    let callback = match callback {
        Some(cb) => cb,
        // No handler registered: cancel rather than blocking the agent.
        None => return CreateElicitationResponse::new(ElicitationAction::Cancel),
    };

    // Serialize the full request; the Python bridge reads message / mode /
    // requestedSchema / url / sessionId / toolCallId from it.
    let payload = serde_json::to_string(request).unwrap_or_else(|_| "{}".into());

    let future = Python::with_gil(|py| -> PyResult<_> {
        let coro = callback.call1(py, (payload,))?;
        pyo3_async_runtimes::tokio::into_future(coro.into_bound(py))
    });

    let py_string = match future {
        Ok(f) => match f.await {
            Ok(obj) => Python::with_gil(|py| {
                obj.extract::<String>(py)
                    .unwrap_or_else(|_| "{\"action\":\"cancel\"}".into())
            }),
            Err(_) => return CreateElicitationResponse::new(ElicitationAction::Cancel),
        },
        Err(_) => return CreateElicitationResponse::new(ElicitationAction::Cancel),
    };

    let parsed: serde_json::Value = serde_json::from_str(&py_string)
        .unwrap_or_else(|_| serde_json::json!({"action":"cancel"}));

    let action = parsed
        .get("action")
        .and_then(|v| v.as_str())
        .unwrap_or("cancel");

    match action {
        "accept" => {
            let mut content_map = std::collections::BTreeMap::new();
            if let Some(obj) = parsed.get("content").and_then(|v| v.as_object()) {
                for (k, v) in obj {
                    if let Some(ecv) = json_to_elicitation_value(v.clone()) {
                        content_map.insert(k.clone(), ecv);
                    }
                }
            }
            CreateElicitationResponse::new(ElicitationAction::Accept(
                ElicitationAcceptAction::new().content(Some(content_map)),
            ))
        }
        "decline" => CreateElicitationResponse::new(ElicitationAction::Decline),
        _ => CreateElicitationResponse::new(ElicitationAction::Cancel),
    }
}

/// Convert a JSON value into an elicitation content value.
fn json_to_elicitation_value(v: serde_json::Value) -> Option<ElicitationContentValue> {
    match v {
        serde_json::Value::String(s) => Some(ElicitationContentValue::String(s)),
        serde_json::Value::Bool(b) => Some(ElicitationContentValue::Boolean(b)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(ElicitationContentValue::Integer(i))
            } else {
                n.as_f64().map(ElicitationContentValue::Number)
            }
        }
        serde_json::Value::Array(arr) => Some(ElicitationContentValue::StringArray(
            arr.into_iter()
                .filter_map(|x| x.as_str().map(String::from))
                .collect(),
        )),
        _ => None,
    }
}

/// Register client types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustClient>()?;
    Ok(())
}
