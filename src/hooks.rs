//! Lifecycle hook system.
//!
//! Hooks allow Python code to intercept and modify ACP protocol events.
//! Hooks are registered on the client and dispatched at specific points
//! in the request/response lifecycle.

use pyo3::prelude::*;
use std::sync::Arc;
use tokio::sync::Mutex;

/// Hook types corresponding to ACP lifecycle events.
#[pyclass(eq, eq_int)]
#[derive(Clone, Debug, Hash, PartialEq, Eq)]
pub enum HookType {
    /// Before a tool invocation is sent to the agent.
    PreToolUse,
    /// After a tool invocation result is received.
    PostToolUse,
    /// Before a prompt is submitted to the agent.
    PromptSubmit,
    /// After a response is received from the agent.
    ResponseReceived,
    /// When a session is created.
    SessionCreated,
    /// When a session is destroyed.
    SessionDestroyed,
    /// When the client connects to the agent.
    Connected,
    /// When the client disconnects from the agent.
    Disconnected,
}

/// A registered hook with its Python callback.
struct RegisteredHook {
    hook_type: HookType,
    /// Python callable: `async def hook(context: dict) -> dict | None`
    #[allow(dead_code)]
    callback: PyObject,
    /// Priority for ordering (lower = earlier).
    priority: i32,
}

/// Rust-side hook dispatcher exposed to Python.
#[pyclass]
pub struct RustHookDispatcher {
    hooks: Arc<Mutex<Vec<RegisteredHook>>>,
}

#[pymethods]
impl RustHookDispatcher {
    #[new]
    fn new() -> Self {
        Self {
            hooks: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Register a hook callback for the given hook type.
    #[pyo3(signature = (hook_type, callback, priority=0))]
    fn register<'py>(
        &self,
        py: Python<'py>,
        hook_type: HookType,
        callback: PyObject,
        priority: i32,
    ) -> PyResult<Bound<'py, PyAny>> {
        let hooks = self.hooks.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut list = hooks.lock().await;
            list.push(RegisteredHook {
                hook_type,
                callback,
                priority,
            });
            // Keep sorted by priority.
            list.sort_by_key(|h| h.priority);
            Ok(())
        })
    }

    /// Dispatch all hooks of the given type with the provided context.
    ///
    /// Returns the (possibly modified) context dict after all hooks run.
    /// Hooks are invoked in priority order. A hook may return `None` to
    /// pass the context through unchanged, or return a modified dict.
    fn dispatch<'py>(
        &self,
        py: Python<'py>,
        hook_type: HookType,
        context_json: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let hooks = self.hooks.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let list = hooks.lock().await;
            let matching: Vec<&RegisteredHook> = list
                .iter()
                .filter(|h| h.hook_type == hook_type)
                .collect();

            // For each matching hook, acquire the GIL, parse the JSON
            // context, call the Python callback, and serialize back.
            let mut context = context_json;
            for hook in &matching {
                let py_result = Python::with_gil(|py| -> PyResult<_> {
                    let cb = hook.callback.clone_ref(py);
                    // Parse JSON string into Python dict via json.loads
                    let json_mod = py.import("json")?;
                    let py_ctx = json_mod.call_method1("loads", (&context,))?;
                    // Call the hook callback with the context dict
                    let result = cb.call1(py, (py_ctx,))?;
                    // If the callback is a coroutine, await it
                    if result.bind(py).hasattr("__await__")? {
                        let future = pyo3_async_runtimes::tokio::into_future(result.into_bound(py))?;
                        return Ok(Some(future));
                    }
                    // Synchronous callback — serialize result back to JSON
                    if result.is_none(py) {
                        return Ok(None);
                    }
                    let json_str = json_mod.call_method1("dumps", (result.bind(py),))?;
                    context = json_str.extract::<String>()?;
                    Ok(None)
                });
                match py_result {
                    Ok(Some(future)) => {
                        // Await the async callback result
                        match future.await {
                            Ok(py_obj) => {
                                Python::with_gil(|py| -> PyResult<()> {
                                    if !py_obj.is_none(py) {
                                        let json_mod = py.import("json")?;
                                        let json_str = json_mod.call_method1("dumps", (py_obj.bind(py),))?;
                                        context = json_str.extract::<String>()?;
                                    }
                                    Ok(())
                                }).ok();
                            }
                            Err(_) => {}
                        }
                    }
                    Ok(None) => {} // Sync callback already updated context
                    Err(_) => {}   // Callback error — pass context through unchanged
                }
            }
            Ok(context)
        })
    }

    /// Remove all hooks of a given type.
    fn clear<'py>(&self, py: Python<'py>, hook_type: HookType) -> PyResult<Bound<'py, PyAny>> {
        let hooks = self.hooks.clone();

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let mut list = hooks.lock().await;
            list.retain(|h| h.hook_type != hook_type);
            Ok(())
        })
    }
}

/// Register hook types on the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<HookType>()?;
    m.add_class::<RustHookDispatcher>()?;
    Ok(())
}
