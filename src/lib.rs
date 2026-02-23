//! conduit-agent-sdk — Rust core for the Conduit ACP Python SDK.
//!
//! This crate provides the `_conduit_sdk` native extension module,
//! exposing performance-critical ACP protocol operations to Python
//! via PyO3. The public Python API (`conduit_sdk`) wraps these
//! internals with an ergonomic async interface.

mod client;
mod control;
mod error;
mod hooks;
mod proxy;
mod session;
mod tools;
mod transport;
mod types;

use pyo3::prelude::*;

/// The native extension module, importable as `conduit_sdk._conduit_sdk`.
#[pymodule]
fn _conduit_sdk(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    // Register all submodule types on the flat module.
    types::register(m)?;
    control::register(m)?;
    client::register(m)?;
    session::register(m)?;
    tools::register(m)?;
    hooks::register(m)?;
    proxy::register(m)?;

    Ok(())
}
