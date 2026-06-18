# Rust ↔ Python ↔ TypeScript via PyO3 and napi-rs

> **Terminology note.** "Neon" / "napi-rs" here refer to **Rust↔Node.js
> binding libraries**, *not* serverless Postgres. **Decision: napi-rs is the
> chosen Node/TypeScript binding** — auto `.d.ts`, `#[napi] async fn` → Promise,
> prebuilt per-platform binaries. Neon is covered below as the alternative.
> This doc shows how to expose the **same Rust ACP core** to both **Python**
> (PyO3 — shipped) and **Node/TypeScript** (napi-rs). **PostgreSQL** (a regular
> instance) backs session storage through the existing `SqlSessionStore`
> (asyncpg + SQLAlchemy).

## 1. The idea in one paragraph

`conduit-agent-sdk` is a Rust core that speaks the Agent Client Protocol over
stdio. Today that core is compiled as a **PyO3** extension module
(`_conduit_sdk.abi3.so`) and wrapped by an ergonomic Python API. The goal of a
dual-binding architecture is to compile the **same ACP engine** a second way —
as a **napi-rs** native addon (`*.node`) — so TypeScript/JavaScript consumers get
the identical client, agent, and streaming behavior without a separate
reimplementation. Session persistence stays in **PostgreSQL** via
`SqlSessionStore` (asyncpg + SQLAlchemy), available from whichever language
drives the SDK.

```mermaid
flowchart LR
    CORE["Rust ACP core<br/>(engine: Client.builder, acp_task,<br/>transport, ACP types)"]
    CORE -->|PyO3 cdylib| PY["Python: conduit_sdk<br/>(Client, AgentOptions, @tool)"]
    CORE -->|Neon / napi-rs cdylib| JS["Node / TypeScript<br/>(require .node)"]
    PY --> PG[("PostgreSQL<br/>SqlSessionStore / asyncpg")]
    JS --> PG2[("PostgreSQL<br/>sqlx or HTTP→Python")]
```

## 2. Why Neon (and what it gives you)

Neon provides a safe, ergonomic bridge between Rust and the V8/Node-API
runtime. The concepts map almost 1:1 onto the PyO3 patterns already in this
repo, which is why the second binding is cheap to add once the engine is
extracted:

| PyO3 concept (existing) | Neon equivalent (new) | Role |
|---|---|---|
| `#[pyclass]` + `Py<>` | `JsBox<T>` + `Handle<T>` | Wrap a Rust struct so JS owns it; GC-managed via `Finalize` |
| `#[pymethods]` / `#[pyfunction]` | `cx.export_function("name", fn)` | Register a JS-callable entry point |
| `Python::with_gil` / `into_future` | `Context` / `cx.task().promise()` | Cross the FFI boundary safely |
| `Arc<Mutex<...>>` interior mutability | `RefCell<T>` inside `JsBox` | Mutable state held by the GC object |

**Core Neon primitives you will use:**

- **`Context`** (`FunctionContext`, `ModuleContext`) — every interaction with
  the JS engine is mediated by a context, exactly like `Python<'py>` in PyO3.
- **`JsBox<T>`** — lets JavaScript *own* a Rust value; when GC collects it,
  `T::Finalize` runs. This is how a JS `Client` object holds the live ACP
  connection.
- **`Task` / `Promise`** — `cx.task(move || { /* worker-thread work */ })`
  `.promise(|cx, result| { /* convert to JS */ })` runs blocking Rust work on
  libuv's worker pool and resolves a JS `Promise`.
- **`Channel`** — `cx.channel()` + `channel.send(move |cx| {...})` schedules a
  closure onto the JS event loop from a Rust thread. This is how streaming
  `session/update` events are pushed into JS.

**Project setup:**

```bash
npm init neon conduit-node        # scaffolds a Neon project
cd conduit-node && neon build --release
```

```toml
# conduit-node/native/Cargo.toml
[lib]
crate-type = ["cdylib"]

[dependencies]
neon = { version = "1", features = ["napi-6"] }
conduit-core = { path = "../conduit-core" }   # the shared engine (see §3)
```

```js
// the built addon is loaded like any CommonJS module
const { Client } = require("./index.node");
```

## 3. The refactor: separate the ACP engine from the FFI

Today the engine and the Python binding are **interleaved** in `src/client.rs`:
the ACP `Client.builder()` handler chain and `acp_task` sit next to
`#[pyclass] RustClient`, `call_permission_callback`, and the GIL bridging. To
serve two languages, extract the pure engine into a crate with **no FFI
dependencies**, then add a thin binding crate per language.

### Target workspace

```
conduit-agent-sdk/
├── Cargo.toml                      # [workspace] members below
├── crates/
│   ├── conduit-core/               # pure ACP engine (no pyo3, no neon)
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs              # re-exports
│   │       ├── client.rs           # the Client.builder + acp_task (moved here)
│   │       ├── transport.rs        # AgentProcess (moved)
│   │       ├── types.rs            # engine-level ACP types (no Py<>)
│   │       └── elicitation.rs      # engine elicitation types (moved)
│   ├── conduit-pyo3/               # Python binding (today's src/)
│   │   ├── Cargo.toml              # depends on conduit-core + pyo3
│   │   └── src/lib.rs              # #[pymodule], #[pyclass] wrappers
│   └── conduit-neon/               # Node/TS binding (new)
│       ├── Cargo.toml              # depends on conduit-core + neon
│       ├── build.rs                # neon_build::setup()
│       └── src/lib.rs              # #[neon::main], JsBox wrappers
└── python/conduit_sdk/             # Python package (unchanged public API)
```

### What moves into `conduit-core`

Everything that is pure protocol logic with no `pyo3`/`neon` imports:

- `acp_task`, the `Client.builder()` handler chain, the `AcpCommand` enum.
- `AgentProcess` (transport / subprocess stdio).
- The streaming `StreamEvent` / `SessionUpdate` **engine** types (the PyO3
  mirror of these stays in `conduit-pyo3`; the JS mirror stays in
  `conduit-neon`).
- The elicitation decision types (the *Rust* `ElicitationAction` etc. already
  come from the `agent-client-protocol` crate; the **callback dispatch** —
  `call_elicitation_callback` — is FFI-specific and stays in each binding).

### What stays in each binding crate

- **`conduit-pyo3`**: `#[pyclass] RustClient`, `set_permission_callback` /
  `set_elicitation_callback`, `call_permission_callback` /
  `call_elicitation_callback`, the `Python::with_gil` + `into_future` bridging,
  and the task-locals `scope()` fix (the event-loop re-scoping that lets
  `into_future()` work on the spawned ACP task — see `src/client.rs`).
- **`conduit-neon`**: the `JsBox<Client>` wrapper, `#[neon::main]` exports, the
  `Channel`-based streaming bridge, and the Neon equivalents of the permission
  / elicitation callback dispatch (JS callbacks held via `Root<JsFunction>`).

The extraction is mechanical: lift the engine fns into `conduit-core` taking
trait-based callbacks (e.g. `Fn(ElicitRequest) -> Future<ElicitResponse>`),
then have each binding supply a concrete callback that crosses its FFI.

## 4. Exposing the ACP client to TypeScript (Neon)

The engine client is long-lived and async — exactly what `JsBox` + `Channel`
are for.

```rust
// crates/conduit-neon/src/lib.rs
use neon::prelude::*;
use conduit_core::{Client as EngineClient, ClientConfig, StreamEvent};

/// A JS-owned handle to the live ACP connection.
pub struct JsClient {
    inner: EngineClient,
}

impl Finalize for JsClient {}

fn connect(mut cx: FunctionContext) -> JsResult<JsPromise> {
    // Read config from JS args (command[], cwd, env).
    let config = read_client_config(&mut cx)?;
    let channel = cx.channel();
    let (deferred, promise) = cx.promise();

    // Spawn the engine connect off the JS thread; settle the promise when done.
    std::thread::spawn(move || {
        let runtime = conduit_core::runtime(); // a tokio handle
        runtime.block_on(async move {
            match config.connect().await {
                Ok(client) => deferred.settle_with(&channel, move |mut cx| {
                    Ok(cx.boxed(JsClient { inner: client }))
                }),
                Err(e) => deferred.settle_with(&channel, move |mut cx| {
                    cx.throw_error(format!("connect failed: {e}"))
                }),
            }
        });
    });

    Ok(promise)
}

fn prompt(mut cx: FunctionContext) -> JsResult<JsUndefined> {
    // `this` is a JsBox<JsClient>; stream session/update via channel.send().
    let client = cx.this().downcast::<JsBox<JsClient>, _>(&mut cx)?;
    let text = cx.argument::<JsString>(0)?.value(&mut cx);
    let channel = cx.channel();
    let on_update = cx.argument::<JsFunction>(1)?.root(&mut cx); // callback

    client.inner.spawn_stream(text, move |event: StreamEvent| {
        let on_update = on_update.clone();
        channel.send(move |mut cx| {
            let s = cx.string(serde_json::to_string(&event).unwrap());
            on_update.to_inner(&mut cx).call1(&mut cx, s)?;
            Ok(())
        });
    });
    Ok(cx.undefined())
}

#[neon::main]
fn main(mut cx: ModuleContext) -> NeonResult<()> {
    cx.export_function("connect", connect)?;
    cx.export_function("prompt", prompt)?;
    Ok(())
}
```

```ts
// usage from TypeScript
import { connect, prompt } from "conduit-node";

const client = await connect({ command: ["claude-code", "--agent"] });
await prompt(client, "Summarize this repo", (updateJson) => {
  const ev = JSON.parse(updateJson);
  console.log(ev.kind, ev.text ?? "");
});
```

> **Streaming shape.** Pushing raw `session/update` JSON into a JS callback is
> the simplest bridge. A more idiomatic option exposes an **async iterator**
> (a `Channel` feeding a `JsAsyncIterable`); napi-rs makes that nearly free
> (see §5).

## 5. napi-rs (chosen) vs Neon

**Decision: napi-rs.** Both compile Rust to a `.node` addon over Node-API, but
napi-rs is the chosen path for this project. The comparison below records why;
Neon remains a viable fallback.

| | **Neon** (`neon-bindings`) | **napi-rs** (`napi-rs/napi-rs`) |
|---|---|---|
| Export style | Manual `cx.export_function("name", fn)` | `#[napi]` macro on `fn`/`struct`/`impl` |
| TypeScript types | Hand-written `.d.ts` | **Auto-generated** `.d.ts` from Rust types |
| Async → Promise | `cx.task().promise()` / `Channel` | `#[napi] pub async fn …` → Promise automatically |
| Wrap Rust struct | `JsBox<T>` + `Finalize` | `#[napi]` struct (macro-managed) |
| Prebuilt binaries | Roll your own | **`@napi-rs/cli`** ships per-platform `optionalDependencies` |
| Closest to PyO3 mental model | ✅ (manual, explicit) | Less so (macro-driven) |

**Why napi-rs won:** `#[napi] pub async fn prompt(...) -> Vec<StreamEvent>`
generates the `.d.ts` **and** the Promise wiring for you, and
`napi pre-publish` produces the platform packages (`@conduit/darwin-arm64`,
`@conduit/linux-x64-gnu`, …) that `npm install` resolves automatically — no
hand-written typings, no per-OS build matrix to operate. Reach for **Neon**
only if you need fine-grained, explicit control that mirrors the PyO3 layer,
or a behavior napi-rs does not expose.

napi-rs equivalent of the §4 client:

```rust
// crates/conduit-napi/src/lib.rs
use napi_derive::napi;

#[napi]
pub struct ConduitClient {
    inner: conduit_core::Client,
}

#[napi]
impl ConduitClient {
    #[napi(constructor)]
    pub fn new(command: Vec<String>) -> Result<Self> { /* … */ }

    /// Streams session/update events; napi-rs turns this into a JS Promise.
    #[napi]
    pub async fn prompt(&self, text: String) -> Result<Vec<StreamEventJson>> { /* … */ }
}
```

```ts
import { ConduitClient } from "@conduit/sdk";
const c = new ConduitClient(["claude-code", "--agent"]);
const events = await c.prompt("hello");   // fully typed from the #[napi] struct
```

## 6. PostgreSQL session storage

Session persistence already works from Python via `SqlSessionStore`, built on
**SQLAlchemy async** with **asyncpg** as the Postgres driver. Because the store
takes a SQLAlchemy engine, you can **swap engines and drivers in and out
freely** — asyncpg for production Postgres, aiosqlite for local tests, or
another driver for another database — without touching `SqlSessionStore` or any
ACP code. It supports **any PostgreSQL instance** (local, managed, or
self-hosted), not a specific vendor.

```python
from sqlalchemy.ext.asyncio import create_async_engine
from conduit_sdk import AgentOptions, SqlSessionStore, Client

# Regular PostgreSQL. Adjust host/port/db to your instance.
engine = create_async_engine(
    "postgresql+asyncpg://conduit:secret@localhost:5432/conduit"
    "?sslmode=disable",          # use sslmode=require for TLS
    pool_size=5,
    max_overflow=10,
)

options = AgentOptions(session_store=SqlSessionStore(engine))
client = Client(["claude-code", "--agent"], options=options)

# Every session/update is persisted; replay later:
#   await store.load_updates(session_id)
```

### Which layer owns the database?

| Consumer | Recommended path |
|---|---|
| **Python** SDK drives the agent | `SqlSessionStore` (asyncpg) — already shipped, zero new code |
| **TypeScript** SDK drives the agent | Two options: (a) call a small Python HTTP service that owns `SqlSessionStore`; (b) add a `conduit-core` session store backed by **`sqlx`** (Rust Postgres) so both bindings share one implementation |
| TS-only, no Python in the loop | Move session storage into `conduit-core` via `sqlx::PgPool`; both the PyO3 and Neon/napi bindings then persist through the same Rust code |

Option (b)/(c) — a Rust `sqlx` session store in `conduit-core` — is the clean
long-term answer: one schema, one writer, both languages. The Python
`SqlSessionStore` can stay as the reference implementation that defines the
row/event JSON shape the Rust version must match.

## 7. End-to-end wiring checklist

1. **Create the workspace** — split `src/` into `crates/conduit-core` (engine)
   + `crates/conduit-pyo3` (today's binding). Keep `maturin develop` building
   the PyO3 crate; existing 211 tests must still pass unchanged (they validate
   the engine moved cleanly).
2. **Parameterize callbacks** — replace the PyO3-specific
   `call_permission_callback` / `call_elicitation_callback` with engine trait
   callbacks (`Fn(...) -> Future<...>`); the PyO3 crate supplies a GIL-backed
   impl, the Neon/napi crate supplies a `Channel`/`Root<JsFunction>` impl.
3. **Add the Node binding** — `npm init neon conduit-node` (or
   `npm init napi-module`), depend on `conduit-core`, implement
   `connect`/`prompt`/`request_elicitation` per §4–5.
4. **Session storage** — decide the Postgres owner (§6). Default: keep Python
   `SqlSessionStore` until the TS path needs a native store.
5. **Ship** — `maturin develop` for Python; `napi build && npm publish` (with
   `napi pre-publish` for prebuilt platform packages) for Node.

## 8. Summary

- **Neon** = the `neon` crate (Rust↔Node bindings), **not** serverless
  Postgres. napi-rs is the modern alternative and is recommended for new TS
  SDKs (auto `.d.ts`, prebuilt binaries, `#[napi] async fn` → Promise).
- The path to a dual-language SDK is a **workspace refactor**: lift the pure
  ACP engine into `conduit-core`, keep `conduit-pyo3`, add `conduit-neon`
  (or `conduit-napi`).
- The elicitation/permission callback dispatch is the only FFI-specific seam —
  parameterize it behind an engine trait and each binding supplies its own
  crossing.
- **PostgreSQL** session storage is already solved on the Python side
  (`SqlSessionStore` / asyncpg); promote it into `conduit-core` via `sqlx` when
  the TS binding needs native persistence.
