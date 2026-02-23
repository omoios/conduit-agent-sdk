//! Tool registration and MCP server support.
//!
//! Allows Python-defined tools to be registered with the ACP agent.
//! Tools are exposed via the MCP-over-ACP bridge (`_mcp/*` protocol),
//! letting the agent invoke Python callbacks during its execution.

use crate::error::ConduitError;
use crate::types::ToolDefinition;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

/// A registered tool with its Python callback.
struct RegisteredTool {
    #[allow(dead_code)]
    definition: ToolDefinition,
    /// Python callable: `async def handler(input: dict) -> str`
    callback: PyObject,
}

/// Rust-side tool registry exposed to Python.
///
/// Manages tool definitions and their Python callback handlers.
/// When the agent invokes a tool via MCP, the registry dispatches
/// to the appropriate Python function.
#[pyclass]
pub struct RustToolRegistry {
    tools: Arc<Mutex<HashMap<String, RegisteredTool>>>,
}

#[pymethods]
impl RustToolRegistry {
    #[new]
    fn new() -> Self {
        Self {
            tools: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Register a tool with its definition and Python callback.
    fn register<'py>(
        &self,
        py: Python<'py>,
        definition: ToolDefinition,
        callback: PyObject,
    ) -> PyResult<Bound<'py, PyAny>> {
        let tools = self.tools.clone();
        let name = definition.name.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let tool = RegisteredTool {
                definition,
                callback,
            };
            tools.lock().await.insert(name, tool);
            Ok(())
        })
    }

    /// Remove a registered tool by name.
    fn unregister<'py>(&self, py: Python<'py>, name: String) -> PyResult<Bound<'py, PyAny>> {
        let tools = self.tools.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            tools.lock().await.remove(&name);
            Ok(())
        })
    }

    /// List all registered tool names.
    fn list_tools<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let tools = self.tools.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let names: Vec<String> = tools.lock().await.keys().cloned().collect();
            Ok(names)
        })
    }

    /// Invoke a tool by name with the given JSON input string.
    ///
    /// Parses `input_json` as a dict and calls the registered async Python
    /// callback with keyword arguments. Returns the stringified result.
    fn invoke<'py>(
        &self,
        py: Python<'py>,
        name: String,
        input_json: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let tools = self.tools.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            // Get the callback and start the coroutine under the GIL,
            // then await outside the GIL.
            let result_future = Python::with_gil(|py| -> PyResult<_> {
                let map = tools.blocking_lock();
                let tool = map.get(&name).ok_or_else(|| {
                    ConduitError::Tool(format!("tool not found: {name}"))
                })?;

                // Parse JSON input to a Python dict for **kwargs.
                let json_mod = py.import("json")?;
                let parsed = json_mod.call_method1("loads", (&input_json,))?;
                let kwargs = parsed.downcast::<pyo3::types::PyDict>()?;

                // Call the async callback → get a coroutine → convert to Rust future.
                let coro = tool.callback.bind(py).call((), Some(kwargs))?;
                pyo3_async_runtimes::tokio::into_future(coro)
            })?;

            let result_obj = result_future.await?;

            // Convert the Python result to a JSON string.
            Python::with_gil(|py| -> PyResult<String> {
                result_obj.extract::<String>(py).or_else(|_| {
                    let json_mod = py.import("json")?;
                    let dumped = json_mod.call_method1("dumps", (result_obj.bind(py),))?;
                    dumped.extract::<String>()
                })
            })
        })
    }
}

/// Register tool types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustToolRegistry>()?;
    Ok(())
}
