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
use crate::types::{Capabilities, ClientConfig, ContentBlock, ContentType, Message, MessageRole};
use pyo3::prelude::*;
use sacp::schema::{
    ContentBlock as AcpContentBlock, InitializeRequest, NewSessionRequest, PromptRequest,
    RequestPermissionOutcome, RequestPermissionRequest, RequestPermissionResponse,
    SelectedPermissionOutcome, SessionNotification, SessionUpdate as AcpSessionUpdate,
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
}

#[pymethods]
impl RustClient {
    #[new]
    fn new(config: ClientConfig) -> Self {
        Self {
            inner: Arc::new(Mutex::new(None)),
            config,
            update_rx: Arc::new(Mutex::new(None)),
        }
    }

    /// Spawn the agent subprocess and perform the ACP initialize handshake.
    ///
    /// Returns the agent's advertised [`Capabilities`].
    fn connect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let config = self.config.clone();
        let update_rx_slot = self.update_rx.clone();

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
                            _ => {} // ToolCall, Plan, etc. — follow-up phases
                        }
                        Ok(())
                    },
                )
                // --- Permission requests (auto-approve in Phase 1) ---
                .on_receive_request(
                    async move |request: RequestPermissionRequest, request_cx| {
                        let option_id =
                            request.options.first().map(|o| o.option_id.clone());
                        if let Some(id) = option_id {
                            request_cx.respond(RequestPermissionResponse::new(
                                RequestPermissionOutcome::Selected(
                                    SelectedPermissionOutcome::new(id),
                                ),
                            ))
                        } else {
                            request_cx.respond(RequestPermissionResponse::new(
                                RequestPermissionOutcome::Cancelled,
                            ))
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

    /// Send a prompt to the agent within the current (or new) session.
    ///
    /// Returns a list of [`Message`] objects. Streaming is handled at the
    /// Python layer by wrapping this in an async iterator.
    fn prompt<'py>(&self, py: Python<'py>, text: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let update_rx_slot = self.update_rx.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            // Snapshot cmd_tx and session_id without holding the lock across awaits.
            let (cmd_tx, session_id) = {
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

            // Auto-create session on first prompt.
            let session_id = match session_id {
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

/// Register client types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustClient>()?;
    Ok(())
}
