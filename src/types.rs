use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Capabilities — exchanged during the ACP initialize handshake
// ---------------------------------------------------------------------------

/// Agent capabilities advertised during the `initialize` handshake.
#[pyclass(get_all)]
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Capabilities {
    /// Whether the agent supports session management.
    pub sessions: bool,
    /// Whether the agent supports tool registration via MCP.
    pub tools: bool,
    /// Whether the agent supports proxy chains.
    pub proxy: bool,
    /// Supported agent modes (e.g. "ask", "code", "architect").
    pub modes: Vec<String>,
    /// Supported model identifiers.
    pub models: Vec<String>,
}

#[pymethods]
impl Capabilities {
    #[new]
    #[pyo3(signature = (sessions=false, tools=false, proxy=false, modes=vec![], models=vec![]))]
    fn new(
        sessions: bool,
        tools: bool,
        proxy: bool,
        modes: Vec<String>,
        models: Vec<String>,
    ) -> Self {
        Self {
            sessions,
            tools,
            proxy,
            modes,
            models,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "Capabilities(sessions={}, tools={}, proxy={}, modes={:?}, models={:?})",
            self.sessions, self.tools, self.proxy, self.modes, self.models
        )
    }
}

// ---------------------------------------------------------------------------
// Message — a single message in the ACP conversation stream
// ---------------------------------------------------------------------------

/// The role of a message sender.
#[pyclass(eq, eq_int)]
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum MessageRole {
    User,
    Assistant,
    System,
    Tool,
}

/// Content type within a message.
#[pyclass(eq, eq_int)]
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContentType {
    Text,
    ToolUse,
    ToolResult,
    Image,
    Error,
}

/// A single content block inside a [`Message`].
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ContentBlock {
    pub content_type: ContentType,
    pub text: Option<String>,
    pub tool_name: Option<String>,
    pub tool_input: Option<String>,
    pub tool_use_id: Option<String>,
}

#[pymethods]
impl ContentBlock {
    #[new]
    #[pyo3(signature = (content_type, text=None, tool_name=None, tool_input=None, tool_use_id=None))]
    fn new(
        content_type: ContentType,
        text: Option<String>,
        tool_name: Option<String>,
        tool_input: Option<String>,
        tool_use_id: Option<String>,
    ) -> Self {
        Self {
            content_type,
            text,
            tool_name,
            tool_input,
            tool_use_id,
        }
    }

    fn __repr__(&self) -> String {
        format!("ContentBlock(type={:?})", self.content_type)
    }
}

/// A message exchanged between client and agent.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Message {
    pub role: MessageRole,
    pub content: Vec<ContentBlock>,
    pub session_id: Option<String>,
}

#[pymethods]
impl Message {
    #[new]
    #[pyo3(signature = (role, content, session_id=None))]
    fn new(role: MessageRole, content: Vec<ContentBlock>, session_id: Option<String>) -> Self {
        Self {
            role,
            content,
            session_id,
        }
    }

    /// Convenience: return concatenated text of all `Text` content blocks.
    fn text(&self) -> String {
        self.content
            .iter()
            .filter_map(|b| {
                if b.content_type == ContentType::Text {
                    b.text.as_deref()
                } else {
                    None
                }
            })
            .collect::<Vec<_>>()
            .join("")
    }

    fn __repr__(&self) -> String {
        let preview: String = self.text().chars().take(60).collect();
        format!("Message(role={:?}, text={:?}...)", self.role, preview)
    }
}

// ---------------------------------------------------------------------------
// SessionUpdate — real-time streaming updates from the agent
// ---------------------------------------------------------------------------

/// The kind of streaming update from the agent.
#[pyclass(eq, eq_int)]
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum UpdateKind {
    /// Incremental text chunk.
    TextDelta,
    /// Tool invocation started.
    ToolUseStart,
    /// Tool invocation completed.
    ToolUseEnd,
    /// Agent finished responding.
    Done,
    /// An error occurred during processing.
    Error,
}

/// A real-time streaming update from the agent during a session.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SessionUpdate {
    pub kind: UpdateKind,
    pub text: Option<String>,
    pub tool_name: Option<String>,
    pub tool_input: Option<String>,
    pub tool_use_id: Option<String>,
    pub error: Option<String>,
}

#[pymethods]
impl SessionUpdate {
    #[new]
    #[pyo3(signature = (kind, text=None, tool_name=None, tool_input=None, tool_use_id=None, error=None))]
    fn new(
        kind: UpdateKind,
        text: Option<String>,
        tool_name: Option<String>,
        tool_input: Option<String>,
        tool_use_id: Option<String>,
        error: Option<String>,
    ) -> Self {
        Self {
            kind,
            text,
            tool_name,
            tool_input,
            tool_use_id,
            error,
        }
    }

    fn __repr__(&self) -> String {
        format!("SessionUpdate(kind={:?})", self.kind)
    }
}

// ---------------------------------------------------------------------------
// ClientConfig
// ---------------------------------------------------------------------------

/// Configuration for a conduit [`Client`] connection.
#[pyclass(get_all, set_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClientConfig {
    /// Shell command to spawn the agent (e.g. `["claude", "--agent"]`).
    pub command: Vec<String>,
    /// Working directory for the spawned agent process.
    pub cwd: Option<String>,
    /// Additional environment variables passed to the agent.
    pub env: HashMap<String, String>,
    /// Connection timeout in seconds.
    pub timeout_secs: u64,
}

#[pymethods]
impl ClientConfig {
    #[new]
    #[pyo3(signature = (command, cwd=None, env=HashMap::new(), timeout_secs=30))]
    fn new(
        command: Vec<String>,
        cwd: Option<String>,
        env: HashMap<String, String>,
        timeout_secs: u64,
    ) -> Self {
        Self {
            command,
            cwd,
            env,
            timeout_secs,
        }
    }

    fn __repr__(&self) -> String {
        format!("ClientConfig(command={:?})", self.command)
    }
}

// ---------------------------------------------------------------------------
// ToolDefinition
// ---------------------------------------------------------------------------

/// Schema definition for a tool exposed to the agent.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    /// JSON Schema string for the tool's input parameters.
    pub input_schema: String,
}

#[pymethods]
impl ToolDefinition {
    #[new]
    fn new(name: String, description: String, input_schema: String) -> Self {
        Self {
            name,
            description,
            input_schema,
        }
    }

    fn __repr__(&self) -> String {
        format!("ToolDefinition(name={:?})", self.name)
    }
}

// ---------------------------------------------------------------------------
// PermissionRequest / PermissionResponse — control protocol permission flow
// ---------------------------------------------------------------------------

/// A permission check request sent from agent to SDK via the control protocol.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PermissionRequest {
    /// The name of the tool the agent wants to use.
    pub tool_name: String,
    /// JSON-serialized tool input parameters.
    pub tool_input: String,
    /// Unique identifier for this tool use invocation.
    pub tool_use_id: Option<String>,
    /// Session in which the tool use occurs.
    pub session_id: Option<String>,
}

#[pymethods]
impl PermissionRequest {
    #[new]
    #[pyo3(signature = (tool_name, tool_input, tool_use_id=None, session_id=None))]
    fn new(
        tool_name: String,
        tool_input: String,
        tool_use_id: Option<String>,
        session_id: Option<String>,
    ) -> Self {
        Self {
            tool_name,
            tool_input,
            tool_use_id,
            session_id,
        }
    }

    fn __repr__(&self) -> String {
        format!("PermissionRequest(tool={:?})", self.tool_name)
    }
}

/// The SDK's response to a permission check.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PermissionResponse {
    /// "allow" or "deny".
    pub decision: String,
    /// Reason for denial (if denied).
    pub reason: Option<String>,
}

#[pymethods]
impl PermissionResponse {
    #[new]
    #[pyo3(signature = (decision, reason=None))]
    fn new(decision: String, reason: Option<String>) -> Self {
        Self { decision, reason }
    }

    fn __repr__(&self) -> String {
        format!("PermissionResponse(decision={:?})", self.decision)
    }
}

// ---------------------------------------------------------------------------
// ResultMessage — final result from agent at the end of a query
// ---------------------------------------------------------------------------

/// Final result message sent by the agent when a query completes.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResultMessage {
    /// Result subtype (e.g. "result").
    pub subtype: String,
    /// Total duration of the query in milliseconds.
    pub duration_ms: u64,
    /// Whether the result represents an error.
    pub is_error: bool,
    /// Number of conversation turns consumed.
    pub num_turns: u32,
    /// Session identifier.
    pub session_id: String,
    /// Total cost in USD (if available).
    pub total_cost_usd: Option<f64>,
    /// The final result text (if available).
    pub result: Option<String>,
}

#[pymethods]
impl ResultMessage {
    #[new]
    #[pyo3(signature = (subtype, duration_ms, is_error, num_turns, session_id, total_cost_usd=None, result=None))]
    fn new(
        subtype: String,
        duration_ms: u64,
        is_error: bool,
        num_turns: u32,
        session_id: String,
        total_cost_usd: Option<f64>,
        result: Option<String>,
    ) -> Self {
        Self {
            subtype,
            duration_ms,
            is_error,
            num_turns,
            session_id,
            total_cost_usd,
            result,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ResultMessage(subtype={:?}, turns={}, error={})",
            self.subtype, self.num_turns, self.is_error
        )
    }
}

// ---------------------------------------------------------------------------
// StreamEvent — real-time streaming event from the agent control protocol
// ---------------------------------------------------------------------------

/// A streaming event received from the agent during a query.
#[pyclass(get_all)]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StreamEvent {
    /// Unique identifier for this event.
    pub uuid: String,
    /// Session in which the event occurred.
    pub session_id: String,
    /// JSON-serialized event payload.
    pub event: String,
}

#[pymethods]
impl StreamEvent {
    #[new]
    fn new(uuid: String, session_id: String, event: String) -> Self {
        Self {
            uuid,
            session_id,
            event,
        }
    }

    fn __repr__(&self) -> String {
        format!("StreamEvent(uuid={:?}, session={:?})", self.uuid, self.session_id)
    }
}

/// Register all types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Capabilities>()?;
    m.add_class::<MessageRole>()?;
    m.add_class::<ContentType>()?;
    m.add_class::<ContentBlock>()?;
    m.add_class::<Message>()?;
    m.add_class::<UpdateKind>()?;
    m.add_class::<SessionUpdate>()?;
    m.add_class::<ClientConfig>()?;
    m.add_class::<ToolDefinition>()?;
    m.add_class::<PermissionRequest>()?;
    m.add_class::<PermissionResponse>()?;
    m.add_class::<ResultMessage>()?;
    m.add_class::<StreamEvent>()?;
    Ok(())
}
