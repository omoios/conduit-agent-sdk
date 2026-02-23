//! Session lifecycle management.
//!
//! Sessions are managed through the `RustClient` ACP command channel.

use pyo3::prelude::*;

/// Register session types on the Python module.
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
