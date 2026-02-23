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
    ContentBlock as AcpContentBlock, InitializeRequest, LoadSessionRequest, NewSessionRequest,
    PermissionOptionKind, PromptRequest, RequestPermissionOutcome, RequestPermissionRequest,
    RequestPermissionResponse, SelectedPermissionOutcome, SessionNotification,
    SetSessionModeRequest, SessionUpdate as AcpSessionUpdate,
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
    Prompt {
        session_id: String,
        text: String,
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
    },
    ToolUseEnd {
        tool_use_id: String,
    },
    Done,
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
                oneshot::channel::<Result<Capabilities, ConduitError>>();

            // Clone update_tx for the notification handler (the other copy
            // goes into the spawned task to send Done events).
            let notif_tx = update_tx.clone();

            // Clone the permission callback for the request handler.
            let perm_callback = perm_callback_for_connect;

            // Build the handler chain with a spawned client task.
            //
            // We use `with_spawned` + `serve` instead of `with_client` because
            // `with_client`'s `AsyncFnOnce` doesn't propagate `Send`, which
            // `tokio::spawn` requires.
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
                                let _ = notif_tx
                                    .send(StreamEvent::ToolUseStart {
                                        tool_name,
                                        tool_input,
                                        tool_use_id,
                                    })
                                    .await;
                            }
                            AcpSessionUpdate::ToolCallUpdate(tcu) => {
                                let tool_use_id = tcu.tool_call_id.0.to_string();
                                let _ = notif_tx
                                    .send(StreamEvent::ToolUseEnd { tool_use_id })
                                    .await;
                            }
                            _ => {} // Plan, AvailableCommandsUpdate, etc.
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
            let capabilities = caps_rx
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
            };

            *inner.lock().await = Some(client_inner);
            Ok(capabilities)
        })
    }

    /// Create a new ACP session and return its ID.
    fn new_session<'py>(&self, py: Python<'py>, cwd: Option<String>) -> PyResult<Bound<'py, PyAny>> {
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

    /// Send a prompt to the agent within the given (or default) session.
    ///
    /// Returns a list of [`Message`] objects. Streaming is handled at the
    /// Python layer by wrapping this in an async iterator.
    #[pyo3(signature = (text, session_id=None))]
    fn prompt<'py>(
        &self,
        py: Python<'py>,
        text: String,
        session_id: Option<String>,
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
                    reply: reply_tx,
                })
                .await
                .map_err(|_| ConduitError::Connection("background task closed".into()))?;

            // Collect streaming updates until the Done sentinel arrives.
            let mut collected_text = String::new();
            let mut got_message = false;
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
                            // Use thought text only when no regular message text
                            // has arrived (mirrors the Python prototype behaviour).
                            if !got_message {
                                collected_text.push_str(&t);
                            }
                        }
                        Some(StreamEvent::ToolUseStart { .. })
                        | Some(StreamEvent::ToolUseEnd { .. }) => {
                            // Tool events are consumed in batch mode.
                            // Use recv_update() for full streaming access.
                        }
                        Some(StreamEvent::Done) | None => break,
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
    #[pyo3(signature = (text, session_id=None))]
    fn send_prompt<'py>(
        &self,
        py: Python<'py>,
        text: String,
        session_id: Option<String>,
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
    /// Returns a [`SessionUpdate`] for each chunk (text, thought, tool use),
    /// or `None` when the prompt is complete.
    fn recv_update<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let update_rx_slot = self.update_rx.clone();
        let prompt_reply_rx = self.prompt_reply_rx.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut rx_guard = update_rx_slot.lock().await;
            let update_rx = rx_guard.as_mut().ok_or_else(|| {
                ConduitError::Connection("update channel not initialized".into())
            })?;

            match update_rx.recv().await {
                Some(StreamEvent::TextDelta(t)) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::TextDelta,
                    text: Some(t),
                    tool_name: None,
                    tool_input: None,
                    tool_use_id: None,
                    error: None,
                })),
                Some(StreamEvent::ThoughtDelta(t)) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ThoughtDelta,
                    text: Some(t),
                    tool_name: None,
                    tool_input: None,
                    tool_use_id: None,
                    error: None,
                })),
                Some(StreamEvent::ToolUseStart {
                    tool_name,
                    tool_input,
                    tool_use_id,
                }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ToolUseStart,
                    text: None,
                    tool_name: Some(tool_name),
                    tool_input: Some(tool_input),
                    tool_use_id: Some(tool_use_id),
                    error: None,
                })),
                Some(StreamEvent::ToolUseEnd { tool_use_id }) => Ok(Some(SessionUpdate {
                    kind: UpdateKind::ToolUseEnd,
                    text: None,
                    tool_name: None,
                    tool_input: None,
                    tool_use_id: Some(tool_use_id),
                    error: None,
                })),
                Some(StreamEvent::Done) => {
                    // Check prompt completion status.
                    if let Some(reply_rx) = prompt_reply_rx.lock().await.take() {
                        if let Ok(result) = reply_rx.await {
                            result?;
                        }
                    }
                    Ok(None)
                }
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
    caps_tx: oneshot::Sender<Result<Capabilities, ConduitError>>,
    mut cmd_rx: mpsc::Receiver<AcpCommand>,
    update_tx: mpsc::Sender<StreamEvent>,
) -> Result<(), sacp::schema::Error> {
    // ---- Initialize handshake ----
    let init_result = cx
        .send_request(InitializeRequest::new(sacp::schema::ProtocolVersion::LATEST))
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
    let _ = caps_tx.send(Ok(capabilities));

    // ---- Command loop ----
    while let Some(cmd) = cmd_rx.recv().await {
        match cmd {
            AcpCommand::NewSession { cwd, reply } => {
                let result = cx
                    .send_request(NewSessionRequest::new(PathBuf::from(&cwd)))
                    .block_task()
                    .await;
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
            AcpCommand::Prompt {
                session_id,
                text,
                reply,
            } => {
                let result = cx
                    .send_request(PromptRequest::new(session_id, vec![text.into()]))
                    .block_task()
                    .await;

                // Signal prompt completion so the collector loop exits.
                let _ = update_tx.send(StreamEvent::Done).await;

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
