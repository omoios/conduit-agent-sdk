# ACP Conductor & Proxy Chain Integration

> **Target audience:** SDK developers who want to (a) run an external conductor binary in front of a conduit client, and (b) understand what wiring `ProxyChain.build()` needs to land inside this repo.
>
> **Status:** Design reference — `build()` is TODO in both `python/conduit_sdk/proxy.py` and `src/proxy.rs`. This doc maps the upstream `agent-client-protocol-conductor` design to conduit's existing types and gives a phased implementation plan.

---

## 1. What the Conductor Is

The **conductor** (`agent-client-protocol-conductor`) is a binary that orchestrates proxy chains by sitting **between every component** in an ACP message path. Components — editor, proxies, agent — never talk directly to one another; the conductor routes every request, response, and notification through a single central channel.

```
┌──────────┐     ┌────────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  Editor   │ ←→ │ Conductor  │ ←→ │ Proxy 1  │ ←→ │ Proxy 2  │ ←→ │  Agent   │
│ (Client)  │     │  (binary)  │     │(process) │     │(process) │     │(process) │
└──────────┘     └────────────┘     └──────────┘     └──────────┘     └──────────┘
```

**Key insight:** There is **no peer-to-peer** communication. Every component's stdin/stdout connects to the conductor, and the conductor decides which messages to forward where. This gives the conductor total visibility into every message crossing the chain and lets it enforce ordering, inject/transform messages, and bridge protocols — all without the components knowing about each other.

From the editor's (client's) point of view, the conductor presents as a **normal ACP agent over stdio** — the editor talks ACP JSON-RPC to `conductor` the same way it would talk to any agent. The conductor internally fans messages out to the proxy subprocesses and the real agent at the end.

---

## 2. The `_proxy/successor/*` Protocol

Proxies communicate with the conductor using two custom ACP methods (reserved `_`-prefixed namespace, per the ACP extensibility spec):

### `_proxy/successor/request`

A proxy wraps an **outgoing JSON-RPC request** and sends it to the conductor, which unwraps it and forwards the inner request to the next component in the chain. The response flow is:

1. Proxy sends `_proxy/successor/request` to conductor with the wrapped request as a parameter.
2. Conductor unwraps the inner method/params and sends them as a normal JSON-RPC request to the successor (next proxy or the agent).
3. The successor's JSON-RPC response rides back to the conductor, which rewraps it as the response to the original `_proxy/successor/request`.
4. Conductor sends that response back to the requesting proxy.

Because responses carry the same JSON-RPC `id`, the proxy can correlate them across the chain without any shared state.

### `_proxy/successor/notification`

Same wrapping/unwrapping pattern, but for **JSON-RPC notifications** (no response expected):

1. Proxy sends `_proxy/successor/notification` to conductor.
2. Conductor unwraps and forwards the inner notification to the successor.
3. No response is returned.

### Wire format

```json
// Proxy → Conductor (forwarding a request)
{
  "jsonrpc": "2.0",
  "id": 42,
  "method": "_proxy/successor/request",
  "params": {
    "method": "session/new",
    "params": {
      "sessionId": "abc",
      "meta": { "systemPrompt": "You are helpful." }
    }
  }
}

// Conductor → Proxy (response)
{
  "jsonrpc": "2.0",
  "id": 42,
  "result": {
    "sessionId": "abc-def-ghi"
  }
}
```

```json
// Proxy → Conductor (forwarding a notification)
{
  "jsonrpc": "2.0",
  "method": "_proxy/successor/notification",
  "params": {
    "method": "session/update",
    "params": {
      "sessionId": "abc",
      "updateType": "agentMessageChunk",
      "content": "..."
    }
  }
}
```

---

## 3. Capability Handshake

During the ACP `initialize` handshake, the conductor plays a crucial role in establishing the proxy chain topology:

1. **Conductor starts** by spawning all proxy subprocesses and the final agent.
2. **Conductor initializes each component** one by one:
   - For every component **except the last** (the real agent), the conductor's `InitializeRequest` includes `_meta.proxy: true`.
   - For the **last component** (the real agent), the conductor omits `_meta.proxy`.
3. **Each proxy MUST echo back `_meta.proxy: true`** in its `InitializeResponse`. If a proxy fails to do so, the conductor rejects the initialization — that component is not a valid proxy.
4. **The final agent** responds normally (no `proxy` capability).
5. Once all components are initialized, the conductor knows the chain topology: `editor ↔ proxy1 ↔ proxy2 ↔ ... ↔ agent`.

When the editor (client) later initializes against the conductor, the conductor presents itself as a regular ACP agent and relays the agent's capabilities back. The editor never sees the proxy chain.

---

## 4. The Message-Ordering Invariant ⚠️

This constraint is **load-bearing** — ignoring it causes data loss.

### The race

ACP is a streaming protocol. A single `session/prompt` can produce:

- Many `session/notification` events (text deltas, tool calls, usage updates)
- A `session/prompt` response (the final result)

Without ordering guarantees, a fast `session/prompt` response can **overtake** a slower `session/notification`, reaching the client before a critical update (e.g., final usage stats, a tool response). The client processes the "done" signal and stops reading — the late notification is lost.

### How the conductor solves it

The conductor routes **ALL** messages — requests, responses, and notifications — through **one central `ConductorMessage` channel** (an actor or a single tokio task). This serializes all forwarding, guaranteeing that messages between any two endpoints arrive **in send order**.

```
┌──────────┐
│Conductor │   ← single actor / task →   ┌───────────────┐
│  message │                              │  outgoing     │
│  channel │ ────────── send order ──────▶│  buffer       │
└──────────┘                              └───────┬───────┘
                                                  │
                  ┌───────────────────┬────────────┼────────────┬──────────────┐
                  ▼                   ▼            ▼            ▼              ▼
            ┌────────────┐    ┌────────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐
            │  Editor    │    │  Proxy 1   │  │ Proxy 2  │  │    …     │  │ Agent  │
            └────────────┘    └────────────┘  └──────────┘  └──────────┘  └────────┘
```

**What this means for `ProxyChain.build()`:** The Rust implementation must use a single-channel ordering design — not separate per-proxy forwarding tasks that forward independently. The `src/proxy.rs` implementation should spawn a single background actor that receives wrapped messages from all proxies and forwards them in arrival order.

### Why conduit needs this

When a user calls `client.prompt_stream("...")`, they receive text deltas (notifications) followed by a done signal. If the done signal arrives before a late notification due to proxy-side routing, the last chunk of output is silently dropped. The invariant must hold from the **conductor's message channel through to the Rust client's stream merge**.

---

## 5. MCP Bridge (Two Modes)

Proxies can expose MCP servers via their `InitializeResponse` capabilities. When a proxy declares an MCP server with `"url": "acp:$UUID"`, the conductor has two paths depending on whether the final agent supports native ACP-wrapped MCP.

### Mode A: Agent has `mcpCapabilities.acp` (pass-through)

If the agent advertises `mcpCapabilities.acp` in its capabilities, it understands MCP declarations wrapped in `_mcp/*` ACP methods natively. The conductor passes the MCP server declarations through unchanged — the agent speaks `_mcp/list` and `_mcp/call` directly to the proxy using the same chain.

```
  Proxy MCP server
  (declares "url": "acp:tool-server-1")
       │
       ▼
  Conductor sees agent has mcpCapabilities.acp
       │
       ▼ (passes declaration through)
  Agent talks _mcp/list, _mcp/call via conductor → proxy
```

### Mode B: Agent lacks `mcpCapabilities.acp` (bridge)

If the agent does **not** support ACP-wrapped MCP, the conductor:

1. Binds a **local TCP port** per MCP server.
2. **Rewrites** the MCP server spec declaration from `"url": "acp:$UUID"` to `"url": "conductor mcp $PORT"`.
3. Spawns a **bridge subprocess** that translates between stdio MCP (`tools/list`, `tools/call`) and the ACP `_mcp/*` methods routed through the conductor.

```
  Proxy MCP server          Conductor              Bridge process
  (declares acp:UUID)  ──▶  binds port N      ──▶  speaks MCP stdio
                            rewrites spec          translates ↔ _mcp/*
                            spawns bridge           sends via conductor
```

Conduit's `@tool` + `McpSdkServerConfig` already provides an **in-process MCP HTTP server** — this maps most cleanly to **Mode A** (agent with `mcpCapabilities.acp`). The proxy would declare `"url": "acp:sdk-tools"` and the agent discovers SDK tools via `_mcp/*` methods. Mode B support (for agents like Claude Code that may not yet support ACP-wrapped MCP) would need the bridge subprocess, which is an integration task for the conductor binary, not the SDK.

---

## 6. Tree/Proxy Mode

When the **conductor itself receives** `_meta.proxy: true` during its own initialization (from an outer conductor or a parent chain), it enters **proxy mode**:

1. Conductor offers the `proxy` capability to **ALL** components in its chain, including the last one.
2. When the final component (which may itself be a conductor or nested chain) forwards messages via `_proxy/successor/*`, the conductor forwards them to **its own successor** in the parent chain.
3. This enables **modular sub-chains** — a conductor managing a group of proxies can be treated as a single proxy by an outer conductor.

```
┌──────────────────────────────────────────────────┐
│  Outer Conductor                                   │
│  ┌──────────┐    ┌──────────────────────────────┐ │
│  │ Editor   │ ←→ │  Inner Conductor (proxy mode) │ │
│  │ (client) │    │  ┌────────┐  ┌────────┐      │ │
│  └──────────┘    │  │Proxy A │→ │Proxy B │→ …   │ │
│                  │  └────────┘  └────────┘      │ │
│                  └──────────┬───────────────────┘ │
│                             ▼                     │
│                       ┌──────────┐                │
│                       │  Agent   │                │
│                       └──────────┘                │
└──────────────────────────────────────────────────┘
```

Conduit's `ProxyChain` could in theory wrap an inner `ProxyChain` as a single `Proxy` subclass, enabling nested composition without changing the core API.

---

## 7. Running the External Conductor Binary

The `agent-client-protocol-conductor` binary accepts a JSON config file describing the proxy chain and the final agent.

### JSON config format

```json
{
  "proxies": [
    {
      "command": ["conduit-proxy-context"],
      "args": ["--context", "You are a helpful coding assistant."]
    },
    {
      "command": ["conduit-proxy-filter"],
      "args": ["--max-tokens", "4000"]
    }
  ],
  "agent": {
    "command": ["claude", "--agent"]
  }
}
```

### Starting the conductor

```bash
agent-client-protocol-conductor --config conductor.json
```

### Using conduit's Client against a conductor

Since the conductor presents as a **normal ACP agent over stdio**, you pass the conductor command directly to `Client`:

```python
import asyncio
from conduit_sdk import Client

async def main():
    async with Client(
        ["agent-client-protocol-conductor", "--config", "conductor.json"]
    ) as client:
        async for msg in client.prompt("Explain proxies in one sentence."):
            print(msg.text())

asyncio.run(main())
```

The client has no idea it's talking through a proxy chain — it sends ACP JSON-RPC to the conductor, and the conductor handles all the proxying transparently.

### Using conduit's ProxyChain builder

For programmatic composition (the design intended for `ProxyChain.build()`), you'd write:

```python
from conduit_sdk import Client, ProxyChain, ContextInjector, ResponseFilter

chain = ProxyChain()
chain.add(ContextInjector(context="You are helpful."))
chain.add(ResponseFilter(max_tokens=4000))
await chain.build()

async with Client(["claude", "--agent"], proxy_chain=chain) as client:
    ...
```

> **Note:** `ProxyChain.build()` is currently a TODO in both `python/conduit_sdk/proxy.py` and `src/proxy.rs`. The `proxy_chain` parameter on `Client` does not exist yet — see the implementation plan below.

---

## 8. Landing `ProxyChain.build()` In-Repo: Design + Plan

### Integration points

#### Existing types

| Type | Location | Purpose |
|------|----------|---------|
| `Proxy` (ABC) | `python/conduit_sdk/proxy.py:23` | Base class: `name`, `command`, `to_config()` |
| `ProxyChain` | `python/conduit_sdk/proxy.py:46` | Builder: `add()`, `insert()`, `build()` — **TODO** |
| `RustProxyChain` | `src/proxy.rs:40` | Rust chain state + **TODO `build()`** |
| `ProxyConfig` | `src/proxy.rs:15` / `_conduit_sdk.pyi:169` | PyO3 config struct: `name`, `command` |
| `ContextInjector` | `python/conduit_sdk/proxy.py:94` | Proxy that injects system context |
| `ResponseFilter` | `python/conduit_sdk/proxy.py:114` | Proxy that truncates responses |
| `Client` | `python/conduit_sdk/client.py:43` | Main client — no proxy support yet |
| `McpSdkServerConfig` | `python/conduit_sdk/tools.py:238` | In-process MCP HTTP server |
| `create_sdk_mcp_server()` | `python/conduit_sdk/tools.py:476` | Factory for MCP server from `@tool` funcs |

#### Connection model

```
┌────────────────────────────────────────────────────────────┐
│ Client(["agent-client-protocol-conductor", ...])            │
│                                                              │
│  Client.__init__   ──▶  ClientConfig(command=[...])          │
│       │                                                     │
│  Client.connect()  ──▶  RustClient.connect()                │
│       │                     │                               │
│       │              Spawns conductor subprocess            │
│       │              Conductor spawns proxies + agent       │
│       │              Client sends ACP JSON-RPC to conductor │
│       │              Conductor unwraps/forwards to chain    │
│       ▼                                                     │
│  Client receives capabilities, streams notifications        │
└────────────────────────────────────────────────────────────┘
```

### Upstream cookbook patterns mapped to conduit

#### Global MCP server (one stateless server for all sessions)

The upstream pattern is `Proxy.builder().with_mcp_server()`. In conduit this maps to creating an `McpSdkServerConfig` (from `@tool` functions), starting it, and configuring a proxy that declares the MCP server in its initialize response.

```python
from conduit_sdk import ProxyChain, tool
from conduit_sdk.tools import create_sdk_mcp_server

@tool
async def search_docs(query: str) -> str:
    """Search documentation."""
    ...

mcp_server = create_sdk_mcp_server("docs", tools=[search_docs])
await mcp_server.start()

# A proxy declaring this MCP server uses the server's URL
# as "acp:docs" in its capabilities, and the conductor bridges
# _mcp/* requests to the server.
```

The proxy that wraps this in the chain would declare `mcpServers: [{"url": "acp:docs"}]` in its `InitializeResponse`.

#### Per-session MCP server (session-scoped, e.g., project-specific tools)

The proxy intercepts `session/new`, captures the `cwd` or custom fields from `_meta`, builds a per-session MCP server, and adds it to the session config before forwarding. Conduit's proxy can store a `dict[str, McpSdkServerConfig]` keyed by session ID.

```python
class PerSessionMcpProxy(Proxy):
    """Proxy that creates a project-specific MCP server per session."""

    def __init__(self) -> None:
        self._servers: dict[str, McpSdkServerConfig] = {}

    async def on_new_session(self, meta: dict) -> None:
        cwd = meta.get("cwd", os.getcwd())
        server = create_sdk_mcp_server(f"project-{cwd}")
        await server.start()
        self._servers[server_url] = server
        # Return server URL to be injected into the session's MCP config
```

#### Tool filtering (enable/disable)

A proxy that sits in the chain and intercepts `_mcp/call` or wraps tool declarations.

```python
class ToolFilterProxy(Proxy):
    """Proxy that filters which tools the agent can see/call."""

    def __init__(self, allow: set[str] | None = None, deny: set[str] | None = None):
        self._allow = allow
        self._deny = deny
```

The upstream `disable_tool`/`enable_tool` pattern is idempotent and errors on unknown names — conduit's proxy can maintain an in-memory set and intercept `_mcp/list` and `_mcp/call` to enforce the policy.

### Phased implementation plan

#### Phase 1: Central ordering channel + subprocess spawning (HARDEST)

**Objective:** Conductor subprocess spawning with single-channel message ordering.

**What to do:**

1. **`src/proxy.rs` — Replace `build()` TODO with real implementation:**
   - Spawn each proxy subprocess using `AgentProcess`-style stdio plumbing (reuse patterns from `src/transport.rs`).
   - Create a single `ConductorMessage` channel (tokio `mpsc`) that serializes all forwarding.
   - Perform the ACP initialize handshake with each proxy, checking `_meta.proxy: true`.
   - Perform the ACP initialize handshake with the final agent.
   - Create a `ConductorActor` (a tokio task) that receives wrapped messages from all proxies and forwards them in arrival order through the one channel.
   - When a proxy sends `_proxy/successor/request` or `_proxy/successor/notification`, unwrap and forward to the successor.
   - Collect responses and route them back to the requesting proxy by JSON-RPC `id`.

2. **`python/conduit_sdk/proxy.py` — Wire `build()` to Rust:**
   - `ProxyChain.build()` already delegates to `RustProxyChain.build()`. Once Rust side works, Python side works.

3. **Test:** Minimal proxy chain (two `echo`-style proxies) preserves notification ordering under concurrent prompts.

**Files:** `src/proxy.rs`, `src/transport.rs` (reuse), `src/client.rs` (maybe), `python/conduit_sdk/proxy.py`

**Acceptance:**
- `RustProxyChain.build()` spawns proxies as subprocesses.
- Conductor channel preserves message order between any two components.
- All ACP operations (initialize, session/new, prompt, streaming) work through a chain.
- Message-ordering invariant verified: rapid notifications followed by a prompt response arrive in send order.

---

#### Phase 2: Client integration (+ `proxy_chain` parameter)

**Objective:** Allow `Client` to accept and use a `ProxyChain`.

**What to do:**

1. Add `proxy_chain: ProxyChain | None = None` parameter to `Client.__init__()`.
2. In `Client.connect()`:
   - If `proxy_chain` is provided and non-empty, call `await proxy_chain.build()` **before** `RustClient.connect()`.
   - The `proxy_chain.build()` returns a conductor command (or the chain modifies `ClientConfig.command` to point at the conductor).
   - Alternatively, `Client` spawns the conductor as a subprocess and uses its stdio as the ACP transport.
3. Store the proxy chain state for cleanup in `Client.disconnect()`.
4. Add `to_proxy_config()` helper to `ProxyChain` that emits the conductor JSON config.

**Files:** `python/conduit_sdk/client.py`, `python/conduit_sdk/proxy.py`

**Acceptance:**
- `Client(["claude", "--agent"], proxy_chain=chain)` spawns conductor → proxies → agent.
- Client's ACP operations flow through the chain transparently.
- Client disconnects cleanly, terminating conductor and all proxy subprocesses.

---

#### Phase 3: Built-in proxy implementations (global MCP, per-session, tool filtering)

**Objective:** Ship useful proxy patterns that map the upstream cookbook to conduit's existing types.

**What to do:**

1. **Global MCP proxy:** A proxy that starts an `McpSdkServerConfig` and declares it in its capability handshake. The in-process HTTP MCP server already exists in `tools.py` — the proxy just wraps it.

   ```python
   class McpServerProxy(Proxy):
       """Proxy that exposes an SDK MCP server to the agent."""
       def __init__(self, server: McpSdkServerConfig):
           self._server = server
       # On init: declare mcpServers with "url": f"acp:{self._server.name}"
       # On _mcp/list, _mcp/call: forward to self._server.handle_request()
   ```

2. **Per-session MCP proxy:** Intercepts `session/new`, captures `_meta` fields (like `cwd`), creates an `McpSdkServerConfig` scoped to that session, and injects it into the session config.

3. **Tool filter proxy:** Implements `disable_tool`/`enable_tool` with idempotent semantics. Intercepts `_mcp/list` to filter the tool list and `_mcp/call` to reject blocked tools.

4. **Built-in proxy registry:** Add `ProxyChain.add_builtin(name)` for common patterns.

**Files:** `python/conduit_sdk/proxy.py`, `python/conduit_sdk/tools.py`

**Acceptance:**
- A chain with `McpServerProxy` makes `@tool` functions callable by the agent through a proxy.
- Tool filtering blocks specific tools with clear errors.
- Per-session MCP servers are isolated per session.

---

#### Phase 4: Tree/proxy mode + nesting

**Objective:** Support nested `ProxyChain` instances as single `Proxy` items.

**What to do:**

1. Add `ProxyChain.as_proxy()` or a `ChainedProxy` class that wraps an inner `ProxyChain` as a single `Proxy`.
   - The wrapped chain communicates with the outer conductor via `_proxy/successor/*`.
   - The wrapper can receive and respond to `_meta.proxy: true` during its own init.
2. Validate that a nested chain propagates the `proxy` capability from inner to outer conductors.

**Files:** `python/conduit_sdk/proxy.py`

**Acceptance:**
- `outer_chain.add(inner_chain.as_proxy())` works.
- Inner chain is transparent to the outer conductor — it sees one proxy.

---

### Summary of the plan

| Phase | Milestone | Risk | Dependencies |
|-------|-----------|------|-------------|
| 1 | Central channel + subprocess spawns `build()` | **HIGH** — ordering invariant is subtle, subprocess management complex | `transport.rs` patterns |
| 2 | `Client` integration + `proxy_chain` param | LOW — mostly wiring | Phase 1 |
| 3 | Built-in proxy implementations (MCP, filtering) | MEDIUM — MCP bridging logic | Phase 1, `tools.py` McpSdkServerConfig |
| 4 | Tree/proxy mode nesting | LOW — additive on top of Phase 1 | Phase 1 |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| **Proxy** | An ACP component that intercepts, transforms, or forwards messages between the client and agent. Lives as a subprocess. Must advertise `_meta.proxy: true` during initialization. |
| **Conductor** | The `agent-client-protocol-conductor` binary that orchestrates a chain of proxies by sitting between every component. The only component that talks to more than one peer. |
| **Successor** | The next component in the chain after a given proxy. For proxy 1 in a `proxy1 → proxy2 → agent` chain, proxy 2 is proxy 1's successor. Messages are forwarded via `_proxy/successor/request` and `_proxy/successor/notification`. |
| **Proxy mode** | A mode the conductor enters when it itself receives `_meta.proxy: true` during initialization. In this mode it offers the proxy capability to ALL components (including the last one), routing forwarded messages to its own successor in a parent chain — enabling nested/sub-chains. |
| **MCP bridge** | The mechanism by which the conductor translates between ACP-wrapped MCP (`_mcp/*` methods) and standard MCP stdio/HTTP when the agent does not support `mcpCapabilities.acp`. Involves binding a TCP port, rewriting the MCP specification from `acp:UUID` to `conductor mcp $PORT`, and spawning a bridge subprocess. |
| **Message-ordering invariant** | The requirement that ALL forwarded messages between any two endpoints in a proxy chain arrive in send order. Violating this causes a fast response to overtake a slow notification, dropping data. The conductor enforces this by routing everything through one central message channel. |
| **Agent Client Protocol (ACP)** | The JSON-RPC-based protocol that defines how editors/clients communicate with coding agents. The conductor is ACP-compliant — it speaks the same protocol as any ACP agent. |
