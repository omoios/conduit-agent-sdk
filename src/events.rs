//! Canonical `SessionEvent` model — the typed decode of a `SessionUpdate`.
//!
//! The 13 variant structs + the total `normalize` decoder live here (Rust) so
//! the Python boundary receives already-typed objects and the future napi-rs
//! port wraps the same definitions. The wire-string enums (`ToolKind` /
//! `ToolStatus` / `StopReason`) stay in Python (they are str-enums; PyO3 `eq_int`
//! enums would be integer-valued and break the wire-string semantics). Variant
//! fields that hold a wire-enum value store the wire *string* (e.g. `"read"`);
//! Python str-enums compare equal to their own value, so
//! `event.kind == ToolKind.READ` holds.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString};
use serde_json::Value;

use crate::types::UpdateKind;

// ---------------------------------------------------------------------------
// Variant structs
// ---------------------------------------------------------------------------

/// Incremental assistant text chunk.
#[pyclass(get_all)]
pub struct TextDelta {
    pub text: String,
}

/// Incremental thought/reasoning chunk.
#[pyclass(get_all)]
pub struct ThoughtDelta {
    pub text: String,
}

/// A tool invocation started.
#[pyclass(get_all)]
pub struct ToolCallStart {
    pub tool_use_id: String,
    pub title: String,
    /// Wire string ("read", "execute", …) or None.
    pub kind: Option<String>,
    /// Parsed `tool_input` (dict/str/None).
    pub input: Py<PyAny>,
    /// Wire string ("pending", "in_progress", …) or None.
    pub status: Option<String>,
}

/// A tool invocation status/content update (terminal when status is
/// completed/failed).
#[pyclass(get_all)]
pub struct ToolCallUpdate {
    pub tool_use_id: String,
    pub status: Option<String>,
    pub output: Option<String>,
    pub raw_content: Py<PyAny>,
    pub locations: Py<PyAny>,
}

/// An agent execution plan.
#[pyclass(get_all)]
pub struct Plan {
    pub entries: Py<PyAny>,
}

/// Available slash commands.
#[pyclass(get_all)]
pub struct AvailableCommands {
    pub commands: Py<PyAny>,
}

/// The agent changed its mode.
#[pyclass(get_all)]
pub struct ModeChange {
    pub mode_id: String,
}

/// A config option changed.
#[pyclass(get_all)]
pub struct ConfigUpdate {
    pub config: Py<PyAny>,
}

/// Token usage update.
#[pyclass(get_all)]
pub struct Usage {
    pub used: Py<PyAny>,
    pub size: Py<PyAny>,
    pub cost_amount: Py<PyAny>,
    pub cost_currency: Py<PyAny>,
}

/// Session title/info update.
#[pyclass(get_all)]
pub struct SessionInfo {
    pub title: Option<String>,
    pub updated_at: Option<String>,
}

/// Rate-limit event from the agent (extension notification).
#[pyclass(get_all)]
pub struct RateLimit {
    pub status: String,
    pub resets_at: Py<PyAny>,
    pub rate_limit_type: String,
    pub utilization: Py<PyAny>,
    pub is_using_overage: Py<PyAny>,
    pub surpassed_threshold: Py<PyAny>,
}

/// The agent finished responding.
#[pyclass(get_all)]
pub struct Done {
    pub stop_reason: Option<String>,
}

/// Forward-compat bucket for anything unrecognized.
#[pyclass(get_all)]
pub struct Unknown {
    pub kind: String,
    pub raw: Py<PyAny>,
}

// ---------------------------------------------------------------------------
// Equality — Python-level: native fields via Rust ==, Py<PyAny> via Python ==
// ---------------------------------------------------------------------------

fn eq_py(a: &Py<PyAny>, b: &Py<PyAny>, py: Python<'_>) -> bool {
    a.bind(py).eq(b.bind(py)).unwrap_or(false)
}

macro_rules! variant_methods {
    ($ty:ty, { $($fname:ident : $fty:ty),* $(,)? }) => {
        #[pymethods]
        impl $ty {
            #[new]
            fn new($($fname: $fty),*) -> Self {
                Self { $($fname),* }
            }
            fn __eq__(&self, other: &Bound<'_, PyAny>, _py: Python<'_>) -> bool {
                other.extract::<PyRef<'_, Self>>().map(|o| self == &*o).unwrap_or(false)
            }
        }
    };
}

// PartialEq for the all-native structs (no Py<PyAny> fields).
impl PartialEq for TextDelta {
    fn eq(&self, o: &Self) -> bool { self.text == o.text }
}
impl PartialEq for ThoughtDelta {
    fn eq(&self, o: &Self) -> bool { self.text == o.text }
}
impl PartialEq for ModeChange {
    fn eq(&self, o: &Self) -> bool { self.mode_id == o.mode_id }
}
impl PartialEq for Done {
    fn eq(&self, o: &Self) -> bool { self.stop_reason == o.stop_reason }
}
impl PartialEq for SessionInfo {
    fn eq(&self, o: &Self) -> bool { self.title == o.title && self.updated_at == o.updated_at }
}

// Constructors + equality for every variant.
variant_methods!(TextDelta, { text: String });
variant_methods!(ThoughtDelta, { text: String });
variant_methods!(ModeChange, { mode_id: String });
variant_methods!(Done, { stop_reason: Option<String> });
variant_methods!(SessionInfo, { title: Option<String>, updated_at: Option<String> });


#[pymethods]
impl ToolCallStart {
    #[new]
    fn new(
        tool_use_id: String,
        title: String,
        kind: Option<String>,
        input: Py<PyAny>,
        status: Option<String>,
    ) -> Self {
        Self { tool_use_id, title, kind, input, status }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        if let Ok(o) = other.extract::<PyRef<'_, Self>>() {
            self.tool_use_id == o.tool_use_id
                && self.title == o.title
                && self.kind == o.kind
                && eq_py(&self.input, &o.input, py)
                && self.status == o.status
        } else {
            false
        }
    }
}

#[pymethods]
impl ToolCallUpdate {
    #[new]
    fn new(
        tool_use_id: String,
        status: Option<String>,
        output: Option<String>,
        raw_content: Py<PyAny>,
        locations: Py<PyAny>,
    ) -> Self {
        Self { tool_use_id, status, output, raw_content, locations }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        if let Ok(o) = other.extract::<PyRef<'_, Self>>() {
            self.tool_use_id == o.tool_use_id
                && self.status == o.status
                && self.output == o.output
                && eq_py(&self.raw_content, &o.raw_content, py)
                && eq_py(&self.locations, &o.locations, py)
        } else {
            false
        }
    }
}

#[pymethods]
impl Plan {
    #[new]
    fn new(entries: Py<PyAny>) -> Self {
        Self { entries }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        other.extract::<PyRef<'_, Self>>().map(|o| eq_py(&self.entries, &o.entries, py)).unwrap_or(false)
    }
}

#[pymethods]
impl AvailableCommands {
    #[new]
    fn new(commands: Py<PyAny>) -> Self {
        Self { commands }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        other.extract::<PyRef<'_, Self>>().map(|o| eq_py(&self.commands, &o.commands, py)).unwrap_or(false)
    }
}

#[pymethods]
impl ConfigUpdate {
    #[new]
    fn new(config: Py<PyAny>) -> Self {
        Self { config }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        other.extract::<PyRef<'_, Self>>().map(|o| eq_py(&self.config, &o.config, py)).unwrap_or(false)
    }
}

#[pymethods]
impl Usage {
    #[new]
    fn new(used: Py<PyAny>, size: Py<PyAny>, cost_amount: Py<PyAny>, cost_currency: Py<PyAny>) -> Self {
        Self { used, size, cost_amount, cost_currency }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        if let Ok(o) = other.extract::<PyRef<'_, Self>>() {
            eq_py(&self.used, &o.used, py)
                && eq_py(&self.size, &o.size, py)
                && eq_py(&self.cost_amount, &o.cost_amount, py)
                && eq_py(&self.cost_currency, &o.cost_currency, py)
        } else {
            false
        }
    }
}

#[pymethods]
impl RateLimit {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        status: String,
        resets_at: Py<PyAny>,
        rate_limit_type: String,
        utilization: Py<PyAny>,
        is_using_overage: Py<PyAny>,
        surpassed_threshold: Py<PyAny>,
    ) -> Self {
        Self { status, resets_at, rate_limit_type, utilization, is_using_overage, surpassed_threshold }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        if let Ok(o) = other.extract::<PyRef<'_, Self>>() {
            self.status == o.status
                && eq_py(&self.resets_at, &o.resets_at, py)
                && self.rate_limit_type == o.rate_limit_type
                && eq_py(&self.utilization, &o.utilization, py)
                && eq_py(&self.is_using_overage, &o.is_using_overage, py)
                && eq_py(&self.surpassed_threshold, &o.surpassed_threshold, py)
        } else {
            false
        }
    }
}

#[pymethods]
impl Unknown {
    #[new]
    fn new(kind: String, raw: Py<PyAny>) -> Self {
        Self { kind, raw }
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>, py: Python<'_>) -> bool {
        if let Ok(o) = other.extract::<PyRef<'_, Self>>() {
            self.kind == o.kind && eq_py(&self.raw, &o.raw, py)
        } else {
            false
        }
    }
}

// ---------------------------------------------------------------------------
// Decode helpers
// ---------------------------------------------------------------------------

/// Parse `s` as JSON, returning the raw string on failure, or `Null` for
/// `None`/empty. Mirrors the Python `_safe_json` (never raises).
fn safe_json(s: Option<&str>) -> Value {
    match s {
        None => Value::Null,
        Some(t) if t.is_empty() => Value::Null,
        Some(t) => serde_json::from_str(t).unwrap_or_else(|_| Value::String(t.to_string())),
    }
}

/// Recursively collect string leaves living under a `text`/`output` key
/// (ACP content blocks). Lists descend without a key; dicts descend with each
/// key. Mirrors `events._extract_text`.
fn extract_text(node: &Value, key: Option<&str>, out: &mut Vec<String>) {
    match node {
        Value::String(s) => {
            if matches!(key, Some("text") | Some("output")) {
                out.push(s.clone());
            }
        }
        Value::Array(a) => {
            for v in a {
                extract_text(v, None, out);
            }
        }
        Value::Object(o) => {
            for (k, v) in o {
                extract_text(v, Some(k.as_str()), out);
            }
        }
        _ => {}
    }
}

/// Parse `content_json` into `(output_text, raw_structure)`. Mirrors
/// `events._decode_tool_output`.
fn decode_tool_output(content: Option<&str>) -> (Option<String>, Value) {
    let raw = safe_json(content);
    match &raw {
        Value::Null => (None, Value::Null),
        Value::String(_) => (None, raw.clone()),
        _ => {
            let mut parts = Vec::new();
            extract_text(&raw, None, &mut parts);
            let output = if parts.is_empty() {
                None
            } else {
                Some(parts.join("\n"))
            };
            (output, raw)
        }
    }
}

/// Convert a `serde_json::Value` into the equivalent Python object.
fn value_to_py(py: Python<'_>, v: Value) -> Py<PyAny> {
    match v {
        Value::Null => py.None(),
        Value::Bool(b) => {
            let obj: Bound<'_, PyBool> = <Bound<'_, PyBool> as Clone>::clone(&PyBool::new(py, b));
            obj.unbind().into_any()
        }
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_pyobject(py)
                    .map(|x| x.into_any().unbind())
                    .unwrap_or_else(|_| py.None())
            } else if let Some(u) = n.as_u64() {
                u.into_pyobject(py)
                    .map(|x| x.into_any().unbind())
                    .unwrap_or_else(|_| py.None())
            } else if let Some(f) = n.as_f64() {
                f.into_pyobject(py)
                    .map(|x| x.into_any().unbind())
                    .unwrap_or_else(|_| py.None())
            } else {
                py.None()
            }
        }
        Value::String(s) => PyString::new(py, &s).into_any().unbind(),
        Value::Array(arr) => {
            let items: Vec<Py<PyAny>> = arr.into_iter().map(|x| value_to_py(py, x)).collect();
            PyList::new(py, items).unwrap().into_any().unbind()
        }
        Value::Object(obj) => {
            let d = PyDict::new(py);
            for (k, val) in obj {
                let _ = d.set_item(k, value_to_py(py, val));
            }
            d.into_any().unbind()
        }
    }
}

/// Convert an optional JSON value into a Python object (`None` when absent).
fn opt_value_to_py(py: Python<'_>, v: Option<&Value>) -> Py<PyAny> {
    match v {
        Some(val) => value_to_py(py, val.clone()),
        None => py.None(),
    }
}

/// Read an optional string attribute from a duck-typed object.
fn opt_str(obj: &Bound<'_, PyAny>, name: &str) -> Option<String> {
    obj.getattr(name)
        .ok()
        .and_then(|v| v.extract::<Option<String>>().ok().flatten())
}

// ---------------------------------------------------------------------------
// normalize — total, pure, duck-typed (accepts any object with a `kind`)
// ---------------------------------------------------------------------------

fn make_unknown(py: Python<'_>, kind: &str, raw: Py<PyAny>) -> PyObject {
    Py::new(py, Unknown {
        kind: kind.to_string(),
        raw,
    })
    .unwrap()
    .into_any()
}

#[pyfunction]
fn normalize(py: Python<'_>, update: &Bound<'_, PyAny>) -> PyObject {
    match normalize_inner(py, update) {
        Ok(obj) => obj,
        Err(_) => {
            // Mirror the Python totality fallback.
            let kind_repr = update
                .getattr("kind")
                .ok()
                .and_then(|k| k.str().ok())
                .map(|s| s.to_string())
                .unwrap_or_default();
            let raw = {
                let d = PyDict::new(py);
                let _ = d.set_item("kind", kind_repr.clone());
                d.into_any().unbind()
            };
            make_unknown(py, "normalize_error", raw)
        }
    }
}

fn normalize_inner(py: Python<'_>, update: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    // Determine the UpdateKind. Unknown values → Unknown(kind=str(kind)).
    let kind_obj = update.getattr("kind")?;
    let kind: UpdateKind = match kind_obj.extract::<UpdateKind>() {
        Ok(k) => k,
        Err(_) => {
            let repr = kind_obj.str()?.to_string();
            return Ok(make_unknown(py, &repr, py.None()));
        }
    };

    let obj: PyObject = match kind {
        UpdateKind::TextDelta => {
            let text = opt_str(update, "text").unwrap_or_default();
            Py::new(py, TextDelta { text })?.into_any()
        }
        UpdateKind::ThoughtDelta => {
            let text = opt_str(update, "text").unwrap_or_default();
            Py::new(py, ThoughtDelta { text })?.into_any()
        }
        UpdateKind::ToolUseStart => {
            let tool_use_id = opt_str(update, "tool_use_id").unwrap_or_default();
            let title = opt_str(update, "tool_name").unwrap_or_default();
            let tkind = opt_str(update, "tool_kind");
            let tstatus = opt_str(update, "tool_status");
            let input = value_to_py(py, safe_json(opt_str(update, "tool_input").as_deref()));
            Py::new(py, ToolCallStart {
                tool_use_id,
                title,
                kind: tkind,
                input,
                status: tstatus,
            })?
            .into_any()
        }
        UpdateKind::ToolUseUpdate => {
            let tool_use_id = opt_str(update, "tool_use_id").unwrap_or_default();
            let tstatus = opt_str(update, "tool_status");
            let content = opt_str(update, "tool_content");
            let (output, raw) = decode_tool_output(content.as_deref());
            let raw_content = value_to_py(py, raw);
            let locations = match safe_json(opt_str(update, "tool_locations").as_deref()) {
                Value::Array(_) => value_to_py(py, safe_json(opt_str(update, "tool_locations").as_deref())),
                _ => py.None(),
            };
            Py::new(py, ToolCallUpdate {
                tool_use_id,
                status: tstatus,
                output,
                raw_content,
                locations,
            })?
            .into_any()
        }
        UpdateKind::ToolUseEnd => Py::new(py, ToolCallUpdate {
            tool_use_id: opt_str(update, "tool_use_id").unwrap_or_default(),
            status: None,
            output: None,
            raw_content: py.None(),
            locations: py.None(),
        })?
        .into_any(),
        UpdateKind::Plan => {
            let parsed = safe_json(opt_str(update, "plan_json").as_deref());
            let entries = match parsed {
                Value::Array(_) => value_to_py(py, parsed),
                _ => value_to_py(py, Value::Array(vec![])),
            };
            Py::new(py, Plan { entries })?.into_any()
        }
        UpdateKind::CommandsUpdate => {
            let parsed = safe_json(opt_str(update, "commands_json").as_deref());
            let commands = match parsed {
                Value::Array(_) => value_to_py(py, parsed),
                _ => value_to_py(py, Value::Array(vec![])),
            };
            Py::new(py, AvailableCommands { commands })?.into_any()
        }
        UpdateKind::ModeChange => {
            let mode_id = opt_str(update, "mode_id").unwrap_or_default();
            Py::new(py, ModeChange { mode_id })?.into_any()
        }
        UpdateKind::ConfigUpdate => {
            let config = value_to_py(py, safe_json(opt_str(update, "config_json").as_deref()));
            Py::new(py, ConfigUpdate { config })?.into_any()
        }
        UpdateKind::Usage => {
            let raw = safe_json(opt_str(update, "usage_json").as_deref());
            let empty = serde_json::Map::new();
            let u = match &raw {
                Value::Object(m) => m,
                _ => &empty,
            };
            let cost = match u.get("cost") {
                Some(Value::Object(c)) => c,
                _ => &empty,
            };
            Py::new(py, Usage {
                used: opt_value_to_py(py, u.get("used")),
                size: opt_value_to_py(py, u.get("size")),
                cost_amount: opt_value_to_py(py, cost.get("amount")),
                cost_currency: opt_value_to_py(py, cost.get("currency")),
            })?
            .into_any()
        }
        UpdateKind::SessionInfo => {
            let raw = safe_json(opt_str(update, "session_info_json").as_deref());
            let empty = serde_json::Map::new();
            let i = match &raw {
                Value::Object(m) => m,
                _ => &empty,
            };
            Py::new(py, SessionInfo {
                title: i.get("title").and_then(|v| v.as_str()).map(String::from),
                updated_at: i.get("updated_at").and_then(|v| v.as_str()).map(String::from),
            })?
            .into_any()
        }
        UpdateKind::RateLimit => {
            let data = safe_json(opt_str(update, "rate_limit_json").as_deref());
            let empty = serde_json::Map::new();
            let params_val = match &data {
                Value::Object(m) => m.get("params").cloned().unwrap_or_else(|| Value::Object(empty.clone())),
                _ => Value::Object(empty.clone()),
            };
            let info = match &params_val {
                Value::Object(m) => m.get("rate_limit_info").cloned().unwrap_or(params_val.clone()),
                _ => Value::Object(empty.clone()),
            };
            let info_map = match &info {
                Value::Object(m) => m,
                _ => &empty,
            };
            let f = |k: &str| opt_value_to_py(py, info_map.get(k));
            let s = |k: &str, d: &str| -> String {
                info_map.get(k).and_then(|v| v.as_str()).unwrap_or(d).to_string()
            };
            Py::new(py, RateLimit {
                status: s("status", ""),
                resets_at: f("resetsAt"),
                rate_limit_type: s("rateLimitType", ""),
                utilization: f("utilization"),
                is_using_overage: f("isUsingOverage"),
                surpassed_threshold: f("surpassedThreshold"),
            })?
            .into_any()
        }
        UpdateKind::Done => {
            let stop_reason = opt_str(update, "stop_reason");
            Py::new(py, Done { stop_reason })?.into_any()
        }
        UpdateKind::Error => {
            let msg = opt_str(update, "error").unwrap_or_default();
            let raw = {
                let d = PyDict::new(py);
                let _ = d.set_item("message", msg);
                d.into_any().unbind()
            };
            make_unknown(py, "error", raw)
        }
    };
    Ok(obj)
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TextDelta>()?;
    m.add_class::<ThoughtDelta>()?;
    m.add_class::<ToolCallStart>()?;
    m.add_class::<ToolCallUpdate>()?;
    m.add_class::<Plan>()?;
    m.add_class::<AvailableCommands>()?;
    m.add_class::<ModeChange>()?;
    m.add_class::<ConfigUpdate>()?;
    m.add_class::<Usage>()?;
    m.add_class::<SessionInfo>()?;
    m.add_class::<RateLimit>()?;
    m.add_class::<Done>()?;
    m.add_class::<Unknown>()?;
    m.add_function(wrap_pyfunction!(normalize, m)?)?;
    Ok(())
}
