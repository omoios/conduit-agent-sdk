# Python + Neon + TypeScript: Building an Agent Platform on conduit-agent-sdk

## 1. Goal & Topology

A Python-first agent platform where `conduit-agent-sdk` drives ACP-compatible
agents, Neon (serverless Postgres) stores sessions, telemetry, and auth state,
PyO3 keeps protocol throughput in Rust, and an optional thin TypeScript layer
(edge function, Next.js route, or Cloudflare Worker) authenticates requests and
proxies them to the Python service. The architecture keeps ACP protocol logic
entirely on the Python+Rust side — the TS edge never touches ACP types.

```
┌─────────────────────────────────────────────────────────────────────┐
│  [TS Edge / Worker]                                                 │
│  Next.js API route / Cloudflare Worker / Vercel Function            │
│  * Authenticate request (JWT, session cookie, API key)              │
│  * Proxy JSON body -> Python service (HTTP/2, or WebSocket fallback)│
│  * Stream SSE events back to browser                                │
│  * NO ACP logic -- just passthrough + auth                          │
└─────────────┬─────────────────────────────────────────────────────┘
              | HTTP POST /prompt  |  GET /sse
              v                    v
+-------------------------------------------------------+
|  [Python Service]                                       |
|  FastAPI / aiohttp / Quart                             |
|  * Top-level HTTP handler -- receives prompts,          |
|    returns SSE stream                                   |
|  * Calls conduit_sdk.Client / conduit_sdk.AgentServer   |
|  * Business logic, orchestration, tool definitions,     |
|    hooks, auth                                          |
+---------------------------+---------------------------+
| uses                      | persists to               |
v                           v
+---------------------+   +-----------------------------+
|  [PyO3 Rust Core]   |   |  [Neon Postgres]            |
|  src/client.rs      |   |  SqlSessionStore + asyncpg  |
|  src/types.rs       |   |  * session metadata + events|
|  src/proxy.rs       |   |  * telemetry / usage        |
|  * ACP protocol     |   |  * auth tokens / profiles   |
|  * Subprocess mgmt  |   +-----------------------------+
|  * Stream framing   |
+---------+-----------+
          | stdio (JSON-RPC 2.0)
          v
+-----------------------------------+
|  [ACP Agent Subprocess]           |
|  claude --agent / goose /         |
|  gemini-cli / ...                 |
+-----------------------------------+
```

## 2. Why Python-First

conduit-agent-sdk is already a Python-first SDK with a Rust core via PyO3.
Every load-bearing construct lives in Python:

- **`Client`** — async client with `connect()`, `prompt()`, `prompt_stream()`,
  `activate_skill()`, session management (`new_session`, `fork`, `cancel`,
  `list`, `resume`). From `conduit_sdk/client.py`.

- **`AgentOptions`** — `system_prompt`, `model`, `can_use_tool` callback,
  `allowed_tools`/`disallowed_tools`, `mcp_servers`, `session_store`,
  `max_turns`, `cwd`, `env`. Serialized to ACP `_meta` JSON at the boundary.
  From `conduit_sdk/options.py`.

- **`@tool` decorator** — In-process MCP HTTP server; functions decorated with
  `@tool` are callable by the agent during a session. From `conduit_sdk/tools.py`.

- **`AgentServer`** — Author an ACP agent in pure Python: register handlers
  via `@server.on_prompt`, `@server.on_new_session`, `@server.on_initialize`,
  `@server.on_cancel`, `@server.on_session_load`. Streaming inside the handler
  via `ctx.send_text()`, `ctx.send_thought()`, `ctx.send_update()`,
  `ctx.call_tool()`. From `conduit_sdk/agent.py`.

- **Hooks** — 8 lifecycle event types (`HookType.PreInitialize`,
  `HookType.PostInitialize`, `HookType.PreSessionCreate`, `HookType.PostSessionCreate`,
  `HookType.PrePrompt`, `HookType.PostPrompt`, `HookType.PreToolUse`,
  `HookType.PostToolUse`) with priority dispatch. From `conduit_sdk/hooks.py`.

- **Permissions** — `can_use_tool` async callbacks: `allow_all`, `deny_all`,
  `console_approve`. The callback receives `(tool_name, tool_input, context)`
  and returns a `PermissionResult`. From `conduit_sdk/permissions.py`.

- **Proxy chain** — `ProxyChain.add(ContextInjector(...))` / `ResponseFilter(...)`,
  Rust-side `RustProxyChain.build()` (TODO -- marks the conductor integration
  point). From `conduit_sdk/proxy.py`.

- **SessionStore** — Abstract persistence protocol (not class, a `Protocol`):
  `InMemorySessionStore`, `FileSessionStore`, `SqlSessionStore`, `RedisSessionStore`.
  From `conduit_sdk/session_store.py`.

**Reserve Rust for the ACP hot path only.** The existing PyO3 boundary
(`src/client.rs`, `src/types.rs`, `src/proxy.rs`) already handles subprocess
spawning, JSON-RPC framing, stream parsing, and the async tokio command loop.
New business logic -- orchestrating multi-agent workflows, rate limiting
callbacks, custom permission policies, audit logging -- belongs in Python.
Only push new ACP wire types or performance-critical stream handling into Rust.

**TypeScript is a transport/auth boundary, nothing more.** It authenticates
inbound HTTP requests, validates payloads, and proxies them to the Python
service. It never touches ACP types, never constructs `AgentOptions`, never
calls `prompt_stream` -- that all stays in Python.

## 3. Neon as the Persistence Layer

conduit-agent-sdk ships a `SessionStore` protocol with four implementations.
`SqlSessionStore` uses SQLAlchemy 2.0 async and works with any dialect,
including PostgreSQL via `asyncpg`.

### Session storage on Neon

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    "postgresql+asyncpg://myuser:password@ep-winter-sun-123456.us-east-2.aws.neon.tech/conduit"
    "?sslmode=require",
    pool_size=5,
    max_overflow=10,
)

store = SqlSessionStore(engine)
```

The `sslmode=require` parameter is **required** -- Neon enforces TLS on all
connections. The engine is passed directly to `SqlSessionStore`, which manages
two tables:

| Table | Purpose |
|-------|---------|
| `conduit_sessions` | `id` (PK), `metadata_json` (text), `created_at`, `updated_at` |
| `conduit_session_events` | `id` (auto), `session_id` (FK -> sessions), `event_json` (text), `created_at` |

Each streaming update from `client.prompt_stream(...)` is appended via
`store.append_update(session_id, record)`, where `record` is a dict
serialized by `Client._record_update()` -- it captures the `kind`, text,
tool metadata, commands, config, usage, rate-limit, and session-info fields
from every `SessionUpdate`. Later, `store.load_updates(session_id)` returns
the full ordered event log for replay or audit.

### Wiring the store into a Client

```python
from conduit_sdk import Client, AgentOptions

options = AgentOptions(session_store=store)

async with Client(["claude", "--agent"], options=options) as client:
    async for update in client.prompt_stream("Deploy the service"):
        # _record_update serializes each update -> store.append_update
        ...
```

The client checks `self._options.session_store` inside `prompt_stream()` and
calls `store.append_update(session_id, self._record_update(update))` for every
yielded `SessionUpdate`. No additional wiring required.

### Neon for edge contexts (HTTP driver)

Neon's serverless HTTP driver (`@neondatabase/serverless`) lets you connect
from environments that cannot hold a pooled TCP connection -- Cloudflare Workers,
Vercel Edge Functions, Deno Deploy.

For these contexts, wrap the HTTP driver in a `SessionStore`-compatible shim:

```python
import httpx
from conduit_sdk.session_store import SessionStore


class NeonHttpSessionStore:
    """SessionStore backed by the Neon SQL-over-HTTP API (edge-friendly)."""

    def __init__(self, http_endpoint: str, api_key: str):
        self._endpoint = http_endpoint.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def append_update(self, session_id: str, update: dict) -> None:
        await self._client.post(
            f"{self._endpoint}/sql",
            json={
                "query": (
                    "INSERT INTO conduit_session_events "
                    "(session_id, event_json, created_at) "
                    "VALUES ($1, $2, NOW())"
                ),
                "params": [session_id, update],
            },
        )

    async def load_updates(self, session_id: str) -> list[dict]:
        resp = await self._client.post(
            f"{self._endpoint}/sql",
            json={
                "query": (
                    "SELECT event_json FROM conduit_session_events "
                    "WHERE session_id = $1 ORDER BY id"
                ),
                "params": [session_id],
            },
        )
        return [row["event_json"] for row in resp.json()]

    async def set_metadata(self, session_id: str, metadata: dict) -> None:
        ...  # UPSERT into conduit_sessions

    async def get_metadata(self, session_id: str) -> dict | None:
        ...

    async def list_sessions(self) -> list[str]:
        ...

    async def delete_session(self, session_id: str) -> None:
        ...

    async def close(self) -> None:
        await self._client.aclose()
```

### Beyond session storage

Neon also stores:

- **Auth records** -- user <-> agent-session mappings, tokens
- **Telemetry** -- usage events (token counts, turn counts, latency) for billing
  and dashboards
- **Rate-limit state** -- per-user or per-tenant counters
- **Elicitation audit log** -- decisions from `elicitation/create` flows (see SS6)

## 4. PyO3 Boundary

The SDK crosses Python<->Rust at exactly one point: the `_conduit_sdk` native
extension module (`src/lib.rs`). Every complex data structure crosses as a
**JSON string** or a **simple PyO3 `#[pyclass]`**. This keeps the boundary
lean and avoids mapping every ACP schema type through PyO3's type system.

### The JSON-string pattern (used everywhere)

```python
# Python side -- conduit_sdk/options.py
class AgentOptions:
    def to_meta_json(self) -> str | None:
        import json
        meta: dict = {}
        if self.system_prompt:
            meta["system_prompt"] = self.system_prompt
        if self.model:
            meta["model"] = self.model
        # ...
        return json.dumps(meta) if meta else None
```

```rust
// Rust side -- src/client.rs
// AcpCommand::NewSession stores meta_json: Option<String>
// and forwards it directly to the ACP NewSessionRequest as _meta
match &command {
    AcpCommand::NewSession { meta_json, .. } => {
        let mut req = NewSessionRequest::new(session_id.clone());
        if let Some(json_str) = meta_json {
            if let Ok(meta) = serde_json::from_str::<Value>(json_str) {
                req = req.meta(meta);
            }
        }
        // ...
    }
}
```

**Benefits:**
- No per-field PyO3 getters/setters to write and maintain
- Serde's `#[derive(Serialize, Deserialize)]` does the heavy lifting
- Adding a new option is a one-line change (one Python field + one JSON key)
- Zero-copy deserialization when the JSON string is forwarded unchanged

### Typed PyO3 structs (used sparingly)

A few critical types ARE proper `#[pyclass]` -- they are inspected and
iterated by Python code frequently and benefit from typed access:

| PyO3 type | Source | Purpose |
|-----------|--------|---------|
| `Capabilities` | `src/types.rs` | Agent identity + capabilities after initialize |
| `Message` | `src/types.rs` | A single conversation turn (role + content blocks) |
| `ContentBlock` | `src/types.rs` | Text, tool_use, tool_result, image, audio, resource |
| `SessionUpdate` | `src/types.rs` | Streamed update: kind discriminator + typed fields |
| `UpdateKind` | `src/types.rs` | Enum: TextDelta, ThoughtDelta, ToolUseStart, Done, etc. |
| `ClientConfig` | `src/types.rs` | Command, cwd, env, timeout -- passed into RustClient |
| `HookType` | `src/hooks.rs` | Exported via `pyo3` enum |
| `ProxyConfig` | `src/proxy.rs` | Proxy name + command list |
| `ToolDefinition` | `src/tools.rs` | Name, description, input_schema for @tool |

**Rule:** If a type is only serialized->deserialized (never inspected field by
field in Python), keep it as JSON strings. If Python code branches on the
type's fields, make it a `#[pyclass]` with `#[pymethods]` accessors.

### Streaming updates across the boundary

All streaming flows through a single Rust `mpsc::Receiver<StreamEvent>` ->
Python `recv_update()` channel:

```rust
// src/client.rs -- notification handler
AcpSessionUpdate::AgentMessageChunk(chunk) => {
    if let AcpContentBlock::Text(tc) = &chunk.content {
        let _ = notif_tx
            .send(StreamEvent::TextDelta(tc.text.clone()))
            .await;
    }
}
```

```python
# Python receive loop -- conduit_sdk/client.py
while True:
    update = await self._rust_client.recv_update()
    if update is None:
        break
    yield update  # SessionUpdate pyclass
    if store is not None:
        await store.append_update(session_id, self._record_update(update))
```

## 5. TypeScript Edge Layer

The TS layer is intentionally **thin**. It has exactly two responsibilities:

1. **Authenticate** the inbound request (JWT, session cookie, API key, etc.)
2. **Proxy** the JSON body to the Python service and **stream** SSE events back

No ACP types, no session management, no tool definitions, no permission
callbacks -- that is all Python territory.

### TS side: Next.js API route or Cloudflare Worker

```typescript
// app/api/agent/prompt/route.ts -- Next.js App Router
export const runtime = "edge";

export async function POST(req: Request) {
  // 1. Authenticate
  const auth = req.headers.get("authorization");
  if (!auth || !verifyToken(auth)) {
    return new Response("Unauthorized", { status: 401 });
  }

  // 2. Forward to Python service
  const body = await req.json();
  const pythonUrl = process.env.PYTHON_SERVICE_URL;

  const response = await fetch(`${pythonUrl}/v1/prompt`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

  // 3. Stream SSE back
  return new Response(response.body, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    },
  });
}
```

```typescript
// worker/src/index.ts -- Cloudflare Worker
export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    // 1. Authenticate
    const session = await validateSession(req, env.SESSIONS);
    if (!session) return new Response("Unauthorized", { status: 401 });

    // 2. Proxy to Python
    const body = await req.json();
    const upstream = await fetch(env.PYTHON_SERVICE_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-session-id": session.id,
      },
      body: JSON.stringify(body),
    });

    // 3. Stream SSE.
    return new Response(upstream.body, {
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
      },
    });
  },
};
```

### Python side: FastAPI SSE endpoint

```python
import json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from conduit_sdk import Client, AgentOptions
from conduit_sdk._conduit_sdk import UpdateKind

app = FastAPI()

# Shared agent client -- one per lifetime, or per-tenant pool.
client: Client | None = None


@app.on_event("startup")
async def startup():
    global client
    opts = AgentOptions(
        # session_store=neon_store,  # see SS3
        can_use_tool=allow_all,
        model="claude-sonnet-4-20250514",
    )
    client = Client(["claude", "--agent"], options=opts)
    await client.connect()


@app.on_event("shutdown")
async def shutdown():
    if client:
        await client.disconnect()


@app.post("/v1/prompt")
async def prompt(body: dict, request: Request):
    text = body.get("prompt", "")
    session_id = body.get("session_id")

    async def event_stream():
        async for update in client.prompt_stream(
            text, session_id=session_id
        ):
            if update.kind == UpdateKind.Done:
                yield f"event: done\ndata: {json.dumps({'stop_reason': update.stop_reason})}\n\n"
                return
            yield f"data: {json.dumps(record_update(update))}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache"},
    )


def record_update(update):
    """Mirrors Client._record_update for the SSE path."""
    d = {"kind": str(update.kind)}
    for attr in ("text", "tool_name", "tool_use_id", "tool_kind",
                 "tool_status", "tool_input", "stop_reason", "mode_id"):
        val = getattr(update, attr, None)
        if val is not None:
            d[attr] = val
    for attr in ("commands_json", "config_json", "plan_json",
                 "usage_json", "session_info_json"):
        val = getattr(update, attr, None)
        if val:
            d[attr] = val if isinstance(val, dict) else json.loads(val)
    return d
```

### Client-side (browser) consumption

```typescript
// In the browser -- EventSource or fetch + ReadableStream
async function* promptAgent(text: string, sessionId?: string) {
  const response = await fetch("/api/agent/prompt", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ prompt: text, session_id: sessionId }),
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const event of events) {
      const dataLine = event.split("\n").find((l) => l.startsWith("data: "));
      if (!dataLine) continue;
      const data = JSON.parse(dataLine.slice(6));

      if (event.startsWith("event: done")) {
        return { done: true, stopReason: data.stop_reason };
      }
      yield data;
    }
  }
}
```

## 6. Elicitation & Interactive Flows Across the Stack

The unstable ACP elicitation feature (`capabilities.elicitation` and
`method: elicitation/create`) requires end-to-end cooperation between all
layers. The agent asks the client (user) for a decision -- approval, new
form input, or cancellation -- and the response must travel back through
the stack unmodified.

### Data shapes

```python
# Elicitation request -- agent -> client
class ElicitationRequest(TypedDict, total=False):
    mode: str                      # "form" | "url"
    message: str                   # Prompt text describing what is needed
    requestedSchema: dict | None   # JSON Schema for form fields (form mode)
    elicitationId: str | None      # Identifier to resume (url mode)
    url: str | None                # URL to fetch (url mode)

# Elicitation response -- client -> agent
class ElicitationResponse(TypedDict, total=False):
    action: str                    # "accept" | "decline" | "cancel"
    values: dict | None            # Form values matching requestedSchema
```

### End-to-end flow

```
Browser/UI                           TS Edge              Python Service     ACP Agent
    |                                  |                      |                 |
    |  POST /api/agent/prompt          |                      |                 |
    +--------------------------------->|                      |                 |
    |                                  |  POST /v1/prompt     |                 |
    |                                  +--------------------->|                 |
    |                                  |                      | prompt_stream() |
    |                                  |                      +---------------->|
    |                                  |                      |                 |
    |                                  |                      |  elicitation/   |
    |                                  |                      |  create request |
    |                                  |                      |<----------------|
    |                                  |                      |                 |
    |  SSE: elicitation/create         |                      |                 |
    |<-----------------------------------------------------------------|       |
    |                                  |                      |                 |
    |  User fills form / approves      |                      |                 |
    +--------------------------+       |                      |                 |
    |                          |       |                      |                 |
    |  POST action=accept      |       |                      |                 |
    |  +values={...}           |       |                      |                 |
    +--------------------------+       |                      |                 |
    |                                  |  POST /v1/elicitation|                 |
    |                                  |  {elicitationId,     |                 |
    |                                  |   action, values}    |                 |
    |                                  +--------------------->|                 |
    |                                  |                      |  respond via    |
    |                                  |                      |  control proto  |
    |                                  |                      +---------------->|
```

### Python handler (FastAPI)

```python
from pydantic import BaseModel

# Neon stores the pending elicitation for audit
class PendingElicitation(BaseModel):
    elicitation_id: str
    session_id: str
    mode: str
    message: str
    requested_schema: dict | None = None
    created_at: str
    status: str = "pending"  # pending | accepted | declined | cancelled


# The Python service emits an SSE event when elicitation arrives:
# event: elicitation/create
# data: {"mode": "form", "message": "Approve deployment?",
#        "requestedSchema": {...}, "elicitationId": "..."}

# The TS edge exposes a separate endpoint to collect the response:
@app.post("/v1/elicitation")
async def respond_elicitation(body: dict):
    """Receive the user's elicitation decision (via TS edge)."""
    elicitation_id = body["elicitation_id"]
    action = body["action"]       # "accept" | "decline" | "cancel"
    values = body.get("values")

    # Persist to Neon for audit
    async with neon_engine.begin() as conn:
        await conn.execute(
            elicitations_table.update()
            .where(elicitations_table.c.id == elicitation_id)
            .values(status=action, values_json=json.dumps(values))
        )

    # Forward to the agent via the control protocol
    await client.respond_elicitation(
        elicitation_id=elicitation_id,
        action=action,
        values=values,
    )
    return {"ok": True}
```

### Permission flows (can_use_tool) follow the same path

The `can_use_tool` callback already crosses the PyO3 boundary as a Python
callable stored in `RustClient.permission_callback`. When surfacing it to a
remote user:

1. Rust receives a `RequestPermissionRequest` from the agent
2. Calls the Python `can_use_tool` callback with `(tool_name, tool_input, context)`
3. Python handler pauses, sends an SSE event to TS edge -> browser
4. User approves/denies -> TS edge -> Python endpoint -> callback returns
5. `PermissionResponse` travels back through PyO3 to the agent

Neon records every permission decision (who, what tool, when, allow/deny)
via `store.set_metadata(session_id, {"permissions": ...})` for audit trails.

## 7. Deployment Topology

```
                         +--------------+
                         |  CDN / DNS   |
                         +------+-------+
                                |
+-------------------------------v------------------------------------+
|  Edge / Regionless                                               |
|                                                                   |
|  +---------------------+    +----------------------------------+  |
|  |  Cloudflare Worker   |    |  Next.js (Vercel Edge / SSR)    |  |
|  |  * Auth (JWT/APIkey) |    |  * Auth (session cookie)        |  |
|  |  * Proxy to Python   |    |  * SSE passthrough              |  |
|  |  * Rate-limit guard  |    |  * Server-rendered UI           |  |
|  +---------------------+    +----------------------------------+  |
+-------------------------------+------------------------------------+
                                | HTTP (internal VPC / mTLS)
+-------------------------------v------------------------------------+
|  Python Service (container / worker)                              |
|                                                                   |
|  +-------------------------------------------------------------+  |
|  |  FastAPI / Uvicorn (multi-worker)                           |  |
|  |  * POST /v1/prompt -- SSE streaming                          |  |
|  |  * POST /v1/elicitation -- async elicitation response        |  |
|  |  * GET /v1/sessions -- list/resume for a tenant             |  |
|  |  * POST /v1/tools -- add/remove @tool servers               |  |
|  |                                                             |  |
|  |  Per-tenant client pool:                                    |  |
|  |  +-----------------------------------------------------+    |  |
|  |  |  Client(["claude", "--agent"], options=...)          |    |  |
|  |  |  Client(["goose"], options=...)                      |    |  |
|  |  |  Client(["gemini-cli"], options=...)                 |    |  |
|  |  +-----------------------------------------------------+    |  |
|  +-------------------------------------------------------------+  |
+-------------------------------+------------------------------------+
                                |
              +-----------------+-----------------+
              v                 v                   v
+--------------------+ +-----------------+ +--------------------+
|  Agent Subprocess  | | Agent Subprocess| | Agent Subprocess  |
|  (tenant A)        | | (tenant B)      | | (tenant C)        |
|  claude --agent    | | goose           | | gemini-cli         |
|  isolated cwd/env  | | isolated        | | isolated           |
+--------------------+ +-----------------+ +--------------------+
                                |
                                v
              +---------------------------------+
              |  Neon Postgres                  |
              |  * conduit_sessions + events     |
              |  * auth / users / tenants       |
              |  * telemetry & usage            |
              |  * elicitation audit log        |
              |                                 |
              |  Pooler endpoint (port 5432     |
              |  with -pooler suffix) for       |
              |  serverless pooling             |
              +---------------------------------+
```

### Connection pooling for serverless

Neon provides connection pooling via PgBouncer. Use the `-pooler` endpoint
suffix to avoid connection exhaustion from serverless Python workers:

```
postgresql+asyncpg://myuser:password@ep-winter-sun-123456-pooler.us-east-2.aws.neon.tech/conduit
?sslmode=require
```

For SQLAlchemy async, configure a small pool:

```python
engine = create_async_engine(
    NEON_DATABASE_URL,        # pooled endpoint above
    pool_size=5,
    max_overflow=3,
    pool_pre_ping=True,       # health-check before use
    pool_recycle=300,         # recycle every 5 min
)
```

For ephemeral serverless functions (AWS Lambda, Vercel Functions), dispose
the engine after each invocation or use the Neon HTTP driver from SS3.

## 8. Concrete Wiring Example

The following ties every layer together: a Neon-backed `SqlSessionStore`, an
agent with permission callback, a `@tool` for deployment with MCP server,
and a `prompt_stream` that persists every update. The TS edge call site is
shown as a comment.

```python
"""
Concrete wiring: Neon -> SqlSessionStore -> AgentOptions -> Client.

The TS edge calls:
    POST /v1/prompt  {"prompt": "Deploy the API", "session_id": "..."}
and receives SSE events.
"""

import asyncio
import json
import os

from sqlalchemy.ext.asyncio import create_async_engine

from conduit_sdk import Client, AgentOptions, SessionUpdate, UpdateKind
from conduit_sdk.permissions import allow_all
from conduit_sdk.session_store import SqlSessionStore
from conduit_sdk.tools import tool


# ---------------------------------------------------------------------------
# 1. Neon engine + SqlSessionStore
# ---------------------------------------------------------------------------

NEON_DSN = os.environ.get(
    "NEON_DATABASE_URL",
    "postgresql+asyncpg://user:password@ep-example-123456-pooler.us-east-2.aws.neon.tech/conduit"
    "?sslmode=require",
)

engine = create_async_engine(
    NEON_DSN,
    pool_size=5,
    max_overflow=3,
    pool_pre_ping=True,
)
store = SqlSessionStore(engine)


# ---------------------------------------------------------------------------
# 2. @tool -- declarative tool, served via in-process MCP HTTP server
# ---------------------------------------------------------------------------

@tool("deploy_service", description="Deploy a service to the staging environment.")
async def deploy_service(service_name: str, region: str = "us-east-1") -> str:
    """Deploy a named service; the agent calls this when it needs to deploy."""
    # In production: call an orchestration API.
    return f"Deploying {service_name} to {region}... OK"


@tool("get_status", description="Get the current status of a deployment.")
async def get_status(deploy_id: str) -> str:
    return f"Deploy {deploy_id}: running (started 30s ago)"


# ---------------------------------------------------------------------------
# 3. AgentOptions -- session_store, can_use_tool, model, MCP servers
# ---------------------------------------------------------------------------

options = AgentOptions(
    system_prompt="You deploy and monitor microservices.",
    model="claude-sonnet-4-20250514",
    can_use_tool=allow_all,                              # production: your policy
    session_store=store,                                  # <-- Neon persistence
    mcp_servers={
        "sdk-tools": {
            "type": "http",
            "name": "SDK Tools",
            "url": "http://127.0.0.1:9000/sse",           # started by Client
        },
    },
)


# ---------------------------------------------------------------------------
# 4. Main loop -- single prompt, Neon-backed session
# ---------------------------------------------------------------------------

async def main():
    # One client per agent, or per tenant.
    async with Client(["claude", "--agent"], options=options) as client:
        # Ensure the MCP server for @tool functions starts.
        await client._start_sdk_mcp_servers()

        # Stream a prompt; every update is persisted to Neon.
        session_id: str | None = None

        async for update in client.prompt_stream("Deploy the API service"):
            if session_id is None:
                session_id = update.session_id              # first update carries it

            if update.kind == UpdateKind.TextDelta:
                print(update.text, end="", flush=True)
            elif update.kind == UpdateKind.ToolUseStart:
                print(f"\n[Tool: {update.tool_name} <- {update.tool_input}]")
            elif update.kind == UpdateKind.ToolUseEnd:
                print(f"[Tool done: {update.tool_use_id}]")
            elif update.kind == UpdateKind.Done:
                print(f"\n[Session {session_id} complete: {update.stop_reason}]")
                break

        # Session is now persisted in Neon:
        #   conduit_sessions -> session metadata
        #   conduit_session_events -> full ordered event log
        #
        # To resume later:
        #   events = await store.load_updates(session_id)


if __name__ == "__main__":
    asyncio.run(main())

# ---------------------------------------------------------------------------
# TS edge (call site only -- no ACP logic):
#
#   // app/api/agent/prompt/route.ts
#   export async function POST(req: Request) {
#     const body = await req.json();               // {prompt, session_id}
#     const py = await fetch(PYTHON_URL + "/v1/prompt", { method:"POST", body:JSON.stringify(body) });
#     return new Response(py.body, { headers: { "content-type": "text/event-stream" } });
#   }
#
# Browser:
#   const events = promptAgent("Deploy the API");
#   for await (const e of events) { /* render delta */ }
# ---------------------------------------------------------------------------
```

---

**Summary:** Python is the center of gravity. conduit-agent-sdk provides every
building block -- `Client`, `AgentOptions`, `SqlSessionStore`, `@tool`,
`AgentServer`, hooks, permissions. PyO3 bridges only the ACP wire protocol.
Neon gives it durable, replayable session storage with a serverless Postgres
DX. TypeScript is a transparent transport shim -- authenticates, proxies,
streams -- and never touches ACP internals.
