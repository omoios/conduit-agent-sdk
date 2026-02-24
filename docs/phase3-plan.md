# Phase 3: Advanced ACP Features Plan

> **Status**: Draft — awaiting approval before implementation
> **Prerequisite**: Phase 2 complete (Items 1-8)
> **Baseline**: Phase 2 final state with all new features tested
> **Sources**: ACP protocol docs (all pages), RFDs (proxy-chains, mcp-over-acp), ACP schema v0.10.8

## Context

Phase 2 wires dead code to ACP, adds config options, cancel, slash commands, rich tool calls, mode updates, and plans. Phase 3 covers **everything else the ACP protocol supports** — the advanced features that turn the SDK from a basic client into a full-featured ACP platform.

These items range from straightforward additive work (StopReason surfacing) to architecturally complex (proxy chains, MCP-over-ACP). They're organized in four tiers by complexity and dependency.

## Scope

Fourteen work items in four tiers. Each item is independently shippable unless noted.

---

## Tier 1: Low-Hanging Fruit (Additive, No Architecture Changes)

### 1. StopReason Surfacing

**Priority**: 🔴 Critical — callers currently have no way to know *why* a prompt turn ended.

**ACP mechanism**: `PromptResponse.stop_reason` returns one of: `end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`. This tells the caller whether the agent finished naturally, hit a limit, refused the request, or was interrupted.

**What exists today**:
- `prompt()` returns the accumulated text via `StreamEvent::Done { text }`
- `stop_reason` is available in the `PromptResponse` but never extracted or surfaced
- Python callers have no way to distinguish a normal completion from a truncation or refusal

**What needs to happen**:
- `src/client.rs`: Extract `stop_reason` from `PromptResponse` after the turn completes
- Add `stop_reason` field to `StreamEvent::Done`
- `src/types.rs`: Add `StopReason` enum (EndTurn, MaxTokens, MaxTurnRequests, Refusal, Cancelled)
- `python/conduit_sdk/client.py`: Surface `stop_reason` on the final `Message` returned by `prompt()`

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `message.stop_reason` returns a string like `"end_turn"` or `"cancelled"`
- [ ] Streaming consumers see `stop_reason` in the final `SessionUpdate`
- [ ] Unit test for each stop reason variant
- [ ] All existing tests still pass

---

### 2. Initialize Enrichment (Client Info & Capabilities)

**Priority**: 🟡 Medium — the ACP spec states `clientInfo` will be **required** in a future version. Better to implement now.

**ACP mechanism**: During the `initialize` handshake, the client sends:
- `clientInfo: { name, title?, version? }` — identifies the SDK
- `clientCapabilities: { fs?, terminal? }` — advertises what the client can do
- `promptCapabilities: { image?, audio?, embeddedContext? }` — what content types the client handles

The agent responds with:
- `agentInfo: { name, title?, version? }` — identifies the agent
- `agentCapabilities` — what the agent supports
- `mcpCapabilities: { http?, sse? }` — which MCP transports the agent supports
- `configOptions` — available config selectors (Phase 2 Item 4)

**What exists today**:
- `InitializeRequest` is sent but `clientInfo` and `clientCapabilities` are minimal/defaults
- `InitializeResponse` fields beyond session setup are not parsed or surfaced

**What needs to happen**:
- `src/client.rs`: Populate `clientInfo` with SDK name/version from Cargo.toml
- Send `clientCapabilities` based on what the SDK actually supports (evolves as we add fs/terminal)
- Parse `agentInfo` from `InitializeResponse` and store it
- Parse `mcpCapabilities` and `configOptions`
- `python/conduit_sdk/client.py`: Expose `client.agent_info`, `client.agent_capabilities`

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] SDK sends `clientInfo: { name: "conduit-agent-sdk", version: "X.Y.Z" }` during initialize
- [ ] `client.agent_info` returns agent name and version after connection
- [ ] `client.agent_capabilities` reflects what the agent advertised
- [ ] No breaking changes to existing connect/initialize flow

---

### 3. Usage Tracking (`UsageUpdate`)

**Priority**: 🟢 Low — informational, but useful for cost monitoring and context window management.

**ACP mechanism**: `UsageUpdate` notification (unstable feature, already enabled in Cargo.toml) provides:
- Context window usage (tokens used / total)
- Cost information (if agent provides it)
- Sent during prompt turns as context grows

**What exists today**:
- `UsageUpdate` is silently ignored in the `_ => {}` catch-all
- No usage tracking in the SDK

**What needs to happen**:
- Handle `UsageUpdate` in notification handler → `StreamEvent::Usage { usage_json }`
- `src/types.rs`: Add `UpdateKind::Usage` variant
- `python/conduit_sdk/client.py`: Expose `client.usage` property with last-known usage state
- Accumulate usage across turns in the session

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] Usage updates appear in `prompt_stream()` as `SessionUpdate(kind=Usage)`
- [ ] `client.usage` returns token counts after a prompt turn
- [ ] Usage accumulates across multiple turns in the same session

---

### 4. Session Info Updates (`SessionInfoUpdate`)

**Priority**: 🟢 Low — informational, useful for session management UIs.

**ACP mechanism**: `SessionInfoUpdate` notification (unstable feature, enabled) provides:
- Session title (agent-generated summary)
- Timestamps
- Custom metadata

**What exists today**:
- Silently ignored in `_ => {}` catch-all

**What needs to happen**:
- Handle `SessionInfoUpdate` → `StreamEvent::SessionInfo { info_json }`
- `src/types.rs`: Add `UpdateKind::SessionInfo` variant
- `python/conduit_sdk/client.py`: Expose `client.session_info` or `session.info` property

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`, `python/conduit_sdk/session.py`

**Acceptance criteria**:
- [ ] Session info updates appear in streaming
- [ ] `session.title` returns the agent-generated session title
- [ ] Session info is accessible after any prompt turn

---

## Tier 2: Session Management (Unstable Features)

### 5. Session Fork (`session/fork`)

**Priority**: 🟡 Medium — enables branching conversations for exploration/comparison.

**ACP mechanism**: `ForkSessionRequest { session_id }` creates a new session that shares the conversation history up to the fork point. The new session gets its own `session_id` and evolves independently. Behind `unstable_session_fork` feature (enabled).

**What exists today**:
- No fork support anywhere in the SDK

**What needs to happen**:
- `src/client.rs`: Add `AcpCommand::ForkSession { session_id, reply }` variant
- Handle with `cx.send_request(ForkSessionRequest::new(session_id))` → returns new session ID
- `RustClient`: Add `fork_session(session_id)` PyO3 method
- `python/conduit_sdk/session.py`: Add `session.fork()` → returns new `Session` object
- The forked session should be fully functional (prompt, stream, cancel, etc.)

**Files**: `src/client.rs`, `python/conduit_sdk/session.py`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `new_session = await session.fork()` creates a forked session
- [ ] Forked session has independent conversation from fork point
- [ ] Both original and forked sessions remain functional
- [ ] Integration test: fork mid-conversation, send different prompts to each

---

### 6. Session List & Resume

**Priority**: 🟡 Medium — enables session persistence across SDK restarts.

**ACP mechanism**:
- `ListSessionsRequest` → returns available sessions (behind `unstable_session_list`)
- `ResumeSessionRequest { session_id }` → resumes a previous session (behind `unstable_session_resume`). Different from `session/load` (which loads session data from the client to the agent) — `session/resume` reconnects to an existing agent-side session.

**What exists today**:
- `session/load` exists in the SDK (used during initialization)
- No list or resume support

**What needs to happen**:
- `src/client.rs`: Add `AcpCommand::ListSessions { reply }` and `AcpCommand::ResumeSession { session_id, reply }`
- `RustClient`: Add `list_sessions()` and `resume_session(session_id)` PyO3 methods
- `python/conduit_sdk/client.py`: Add `client.list_sessions()` → list of session metadata, `client.resume_session(session_id)` → `Session` object

**Files**: `src/client.rs`, `python/conduit_sdk/client.py`, `python/conduit_sdk/session.py`

**Acceptance criteria**:
- [ ] `sessions = await client.list_sessions()` returns available sessions
- [ ] `session = await client.resume_session(session_id)` reconnects to existing session
- [ ] Resumed session retains conversation history
- [ ] Integration test: create session, disconnect, resume, verify continuity

---

## Tier 3: Client Capabilities (Agent → Client Requests)

These features flip the direction: instead of the SDK sending requests to the agent, the **agent sends requests to the SDK** and the SDK must handle them. This requires the SDK to register request handlers on the ACP connection.

### 7. Client File System Capabilities

**Priority**: 🟡 Medium — agents that support ACP file operations (rather than using their own tool calls) rely on the client providing fs access.

**ACP mechanism**: Two request methods the agent can send to the client:
- `fs/read_text_file { path, range? }` → client reads file, returns content
- `fs/write_text_file { path, content }` → client writes file

These are negotiated during `initialize`: the client sets `clientCapabilities.fs = true` to indicate support. If the client doesn't advertise fs capabilities, agents use their own tool implementations.

**What exists today**:
- No client-side request handlers
- The ACP connection is client→agent only (we send requests, agent sends notifications)

**What needs to happen**:
- `src/client.rs`: Register request handlers on the ACP `JrHandlerChain` for `fs/read_text_file` and `fs/write_text_file`
- Request handlers need access to a Python callback (for permission checks, sandboxing, etc.)
- `python/conduit_sdk/client.py`: Add `client.on_file_read(callback)` and `client.on_file_write(callback)` hooks
- Default implementations: read/write from cwd with basic path validation
- Set `clientCapabilities.fs = true` during initialize when handlers are registered

**Design considerations**:
- Security: must validate paths (no escaping cwd, no reading sensitive files)
- Permission: integrate with existing `can_use_tool` permission callback
- Async: Python callbacks need to be awaitable since file I/O may be async

**Files**: `src/client.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] Agent can request file reads and receives file content
- [ ] Agent can request file writes and SDK writes to disk
- [ ] Path validation prevents escaping cwd
- [ ] Permission callback is invoked before file operations
- [ ] `clientCapabilities.fs` is advertised during initialize
- [ ] Integration test: agent requests a file read, SDK returns content

---

### 8. Client Terminal Capabilities

**Priority**: 🟢 Low — only needed for agents that delegate command execution to the client rather than running commands themselves.

**ACP mechanism**: Five request methods the agent can send:
- `terminal/create { command, args?, cwd?, env? }` → client spawns subprocess, returns terminal_id
- `terminal/output { terminal_id }` → client returns stdout/stderr from the terminal
- `terminal/wait_for_exit { terminal_id, timeout? }` → client waits for process to finish
- `terminal/kill { terminal_id }` → client kills the process
- `terminal/release { terminal_id }` → client cleans up the terminal

Negotiated via `clientCapabilities.terminal = true`.

**What exists today**:
- No terminal management in the SDK

**What needs to happen**:
- `src/client.rs`: Register request handlers for all 5 terminal methods
- `src/terminal.rs` (new): Terminal manager that spawns and tracks subprocesses
- Map terminal_ids to `tokio::process::Child` handles
- `python/conduit_sdk/client.py`: Expose terminal events and control
- Set `clientCapabilities.terminal = true` during initialize

**Design considerations**:
- Security: command allowlisting/blocklisting (integrate with permissions)
- Resource management: track and clean up subprocesses on session end
- Output streaming: terminal output may need to be streamed back to agent

**Files**: `src/client.rs`, `src/terminal.rs` (new), `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] Agent can create terminal, run commands, get output
- [ ] Terminal lifecycle managed properly (create → output → wait/kill → release)
- [ ] Permission callback invoked before command execution
- [ ] Subprocesses cleaned up on session end
- [ ] `clientCapabilities.terminal` advertised during initialize

---

## Tier 4: Architecture Extensions (Significant Complexity)

### 9. SDK-Hosted MCP Server for `@tool` Functions

**Priority**: 🔴 Critical — without this, the `@tool` decorator is decorative. This is the **most requested feature**.

**Note**: This was Phase 2 Item 8 (stretch goal), promoted to Phase 3 Tier 4 due to complexity.

**Approach**: When the SDK has `@tool`-decorated functions, it must make them available to the agent as MCP tools. Two strategies:

**Strategy A — Subprocess MCP Server (simpler, works with all agents)**:
1. SDK generates a Python script that runs an MCP stdio server
2. The script imports and exposes `@tool` functions via `tools/list` and `tools/call`
3. SDK passes the server as `McpServer::Stdio { command: "python", args: [script_path] }` in `NewSessionRequest`
4. Depends on Phase 2 Item 2 (MCP server passthrough)

**Strategy B — MCP-over-ACP Transport (elegant, requires agent support)**:
1. SDK hosts tools in-process
2. Uses `mcp/connect`, `mcp/message`, `mcp/disconnect` ACP methods
3. No subprocess needed, but requires agent to support ACP MCP transport
4. See Item 11 for the transport implementation

**Recommended**: Implement Strategy A first (universal compatibility), add Strategy B when MCP-over-ACP is available (Item 11).

**What exists today**:
- `@tool` decorator registers functions in `RustToolRegistry`
- `McpSdkServerConfig` exists with tool definitions
- `create_sdk_mcp_server()` and `handle_mcp_request()` exist in `python/conduit_sdk/tools.py`
- None of this is wired to the ACP agent

**What needs to happen (Strategy A)**:
- `python/conduit_sdk/mcp_server.py` (new or extend `tools.py`): Generate a standalone MCP server script
- The script uses `fastmcp` or the `mcp` PyPI package (or raw JSON-RPC over stdio)
- `python/conduit_sdk/client.py`: On `connect()`, if tools are registered, auto-spawn the MCP server and include it in `NewSessionRequest.mcp_servers`
- Clean up the subprocess on disconnect

**Files**: `python/conduit_sdk/tools.py`, `python/conduit_sdk/mcp_server.py` (new), `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `@tool`-decorated functions are callable by the agent during prompt turns
- [ ] SDK auto-spawns MCP server subprocess when tools are registered
- [ ] `tools/list` returns correct tool definitions (name, description, parameters)
- [ ] `tools/call` invokes the Python function and returns the result
- [ ] MCP server subprocess cleaned up on client disconnect
- [ ] Integration test: agent discovers and calls an `@tool` function

---

### 10. Rich Content in Prompts

**Priority**: 🟡 Medium — enables sending images, audio, and embedded resources to agents.

**ACP mechanism**: Prompt messages can contain multiple content blocks:
- `Text { text }` — required, always supported
- `Image { data, mimeType }` — optional, negotiated via `promptCapabilities.image`
- `Audio { data, mimeType }` — optional, negotiated via `promptCapabilities.audio`
- `EmbeddedResource { resource }` — optional, negotiated via `promptCapabilities.embeddedContext`
- `ResourceLink { uri, name?, description?, mimeType? }` — required support

**What exists today**:
- SDK only sends `ContentBlock::Text` in prompts
- No support for multi-modal content

**What needs to happen**:
- `src/client.rs`: Extend `AcpCommand::Prompt` to accept content blocks (JSON array) instead of just text
- `src/types.rs`: Add `ContentBlock` variants for Image, Audio, EmbeddedResource, ResourceLink
- `python/conduit_sdk/client.py`: Accept rich content in `prompt()`:
  ```python
  await client.prompt([
      Text("Describe this image:"),
      Image(data=b"...", mime_type="image/png"),
  ])
  ```
- Check `promptCapabilities` from initialize response to validate content types

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`, `python/conduit_sdk/types.py`

**Acceptance criteria**:
- [ ] `prompt()` accepts list of content blocks
- [ ] Image content blocks sent correctly to agent
- [ ] Audio content blocks sent correctly to agent
- [ ] ResourceLink content blocks sent correctly
- [ ] Error raised if agent doesn't support the content type (based on capabilities)
- [ ] Backward compatible: `prompt("text")` still works as before

---

### 11. MCP-over-ACP Transport

**Priority**: 🟢 Low — advanced feature from RFD, enables in-process MCP tools without subprocess.

**ACP mechanism** (RFD — not yet finalized):
- `mcp/connect { server_name }` → establish MCP channel over the ACP connection
- `mcp/message { server_name, message }` → forward MCP JSON-RPC message
- `mcp/disconnect { server_name }` → close the channel
- Negotiated via `mcpCapabilities.acp = true` in agent capabilities

**Benefit**: Instead of spawning a subprocess for `@tool` functions (Item 9 Strategy A), tools can communicate directly over the existing ACP connection. Lower latency, simpler lifecycle.

**What exists today**:
- Nothing — this is a new transport

**What needs to happen**:
- `src/client.rs`: Register handlers for `mcp/connect`, `mcp/message`, `mcp/disconnect`
- Route MCP messages to the SDK's tool registry
- `python/conduit_sdk/tools.py`: Bridge between MCP JSON-RPC messages and `@tool` function calls
- Advertise `mcpCapabilities.acp` during initialize when tools are registered

**Depends on**: Item 9 (tool registry must work first via subprocess, then this provides an alternative transport)

**Files**: `src/client.rs`, `python/conduit_sdk/tools.py`

**Acceptance criteria**:
- [ ] Agent can discover SDK tools via MCP-over-ACP without a subprocess
- [ ] `mcp/connect` establishes channel, `mcp/message` routes correctly, `mcp/disconnect` cleans up
- [ ] Falls back to subprocess MCP (Item 9) when agent doesn't support ACP transport
- [ ] Integration test with agent that supports MCP-over-ACP

---

### 12. Proxy Chain Implementation

**Priority**: 🟡 Medium — enables middleware-style message interception and transformation.

**ACP mechanism** (RFD — `sacp-conductor`):
- Conductor component sits between client and agent
- `proxy/initialize` — proxy registers with the conductor
- `proxy/successor` — conductor forwards messages through the chain
- Proxies can: inject context, filter responses, transform prompts, coordinate agents, enforce policies

**What exists today**:
- `src/proxy.rs`: `RustProxyChain` stores config but `build()` is a no-op
- `python/conduit_sdk/proxy.py`: `ProxyChain` with `ContextInjector` and `ResponseFilter` — Python-only, doesn't use ACP
- Example 11 demonstrates the Python-only proxy chain

**Two approaches**:

**Approach A — Python-level proxies (enhance what exists)**:
- Intercept prompts/responses in the Python layer before/after Rust
- `ContextInjector` prepends context to prompts before sending to Rust
- `ResponseFilter` filters/transforms responses after receiving from Rust
- Simple, works today, no ACP protocol changes needed

**Approach B — ACP-level conductor (full protocol support)**:
- Implement `sacp-conductor` integration
- SDK can act as a proxy in a conductor chain
- Enables multi-agent orchestration, external proxy components
- Requires conductor server (separate process)

**Recommended**: Enhance Approach A to be fully functional first. Add Approach B as an optional advanced mode.

**What needs to happen (Approach A)**:
- `python/conduit_sdk/proxy.py`: Wire `ContextInjector.process()` into `client.prompt()` pipeline
- Wire `ResponseFilter.process()` into response pipeline
- Ensure proxies can be async and access session state
- `ProxyChain.build()` validates and orders the chain

**Files**: `python/conduit_sdk/proxy.py`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `ContextInjector` actually prepends context to prompts sent to agent
- [ ] `ResponseFilter` actually transforms responses before returning to caller
- [ ] Proxy chain ordering is respected
- [ ] `chain.build()` validates the chain (no cycles, required proxies present)
- [ ] Integration test: context injector changes agent behavior

---

### 13. Streamable HTTP Transport

**Priority**: 🟢 Low — draft proposal, not yet finalized in ACP spec.

**ACP mechanism** (Draft):
- Alternative to stdio transport
- Connect to remote agents over HTTP instead of spawning local subprocess
- Enables cloud-hosted agents, shared agent pools, remote development

**What exists today**:
- `src/transport.rs`: Only supports stdio (subprocess spawning)
- `AgentProcess` manages a single child process

**What needs to happen**:
- `src/transport.rs`: Add HTTP transport variant
- New `HttpTransport` struct that connects to a URL
- JSON-RPC messages sent via HTTP requests, responses via streaming
- `python/conduit_sdk/client.py`: Allow `Client(url="https://agent.example.com/acp")` syntax

**Design considerations**:
- Authentication (API keys, tokens)
- Connection management (keep-alive, reconnection)
- Streaming (SSE or chunked transfer for notifications)
- This is a draft spec — implementation should be behind a feature flag

**Files**: `src/transport.rs`, `src/client.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `Client(url="https://...")` connects to remote agent over HTTP
- [ ] All existing operations work identically over HTTP transport
- [ ] Reconnection on connection drop
- [ ] Behind feature flag / opt-in (draft spec)

---

### 14. Custom Extension Methods

**Priority**: 🟢 Low — enables vendor-specific features without breaking the standard protocol.

**ACP mechanism**: Per the [extensibility spec](https://agentclientprotocol.com/protocol/extensibility):
- Method names starting with `_` are reserved for custom extensions
- Custom capabilities can be advertised via `_meta` on capability objects
- Agents and clients can implement vendor-specific features using `_` methods

**What exists today**:
- No support for custom methods

**What needs to happen**:
- `src/client.rs`: Add generic `send_custom_request(method, params_json)` and `send_custom_notification(method, params_json)` methods
- Validate method names start with `_`
- `python/conduit_sdk/client.py`: Expose `client.send_request("_vendor/method", params)` and `client.send_notification("_vendor/method", params)`
- Allow registering custom notification handlers

**Files**: `src/client.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `await client.send_request("_vendor/feature", {"key": "value"})` sends custom request
- [ ] `await client.send_notification("_vendor/event", {...})` sends custom notification
- [ ] Method name validation (must start with `_`)
- [ ] Custom notification handler registration works
- [ ] Does not interfere with standard protocol methods

---

## Implementation Order

```
Tier 1 (Low-Hanging Fruit — no architecture changes):
  Item 1 (StopReason)                  ← Highest value, simplest change
    ↓
  Item 2 (Initialize enrichment)       ← Required by Items 7, 8, 10
    ↓
  Item 3 (Usage tracking)              ← Same pattern as Phase 2 notifications
    ↓
  Item 4 (Session info updates)        ← Same pattern

Tier 2 (Session Management):
  Item 5 (Session fork)                ← Independent
    ↓
  Item 6 (Session list/resume)         ← Independent, pairs with fork

Tier 3 (Client Capabilities — agent→client requests):
  Item 7 (File system)                 ← Requires Item 2 (capabilities negotiation)
    ↓
  Item 8 (Terminals)                   ← Same pattern as Item 7

Tier 4 (Architecture Extensions):
  Item 9 (SDK-hosted MCP server)       ← Requires Phase 2 Item 2 (MCP passthrough)
    ↓
  Item 10 (Rich content)               ← Requires Item 2 (capabilities negotiation)
    ↓
  Item 11 (MCP-over-ACP)              ← Requires Item 9 (tool registry working)
    ↓
  Item 12 (Proxy chains)              ← Independent, but benefits from all above
    ↓
  Item 13 (HTTP transport)            ← Independent, draft spec
    ↓
  Item 14 (Custom extensions)          ← Independent, lowest priority
```

### Dependency Graph

```
Phase 2 Item 2 (MCP passthrough) ──→ Item 9 (SDK MCP server) ──→ Item 11 (MCP-over-ACP)
Item 2 (Initialize enrichment) ──→ Item 7 (File system)
Item 2 (Initialize enrichment) ──→ Item 8 (Terminals)
Item 2 (Initialize enrichment) ──→ Item 10 (Rich content)
Item 5 (Fork) ←──→ Item 6 (List/Resume)  [independent but related]
```

## Technical Notes

### Unstable Features (Already Enabled)

`Cargo.toml` has `sacp = { ..., features = ["unstable"] }` which enables all of:
- `unstable_cancel_request` → used in Phase 2 Item 3
- `unstable_session_model` → superseded by Phase 2 Item 4
- `unstable_session_fork` → Item 5
- `unstable_session_list` → Item 6
- `unstable_session_resume` → Item 6
- `unstable_session_usage` → Item 3
- `unstable_session_info_update` → Item 4

No Cargo.toml changes needed — all features already compiled in.

### Agent → Client Request Handling (Items 7, 8)

This is the biggest architectural shift. Currently the SDK only sends requests and receives notifications. Items 7 and 8 require the SDK to **handle incoming requests** from the agent.

The `sacp` crate's `JrHandlerChain` supports registering request handlers. The pattern:
1. Before calling `cx.initialize()`, register handlers on the handler chain
2. Handler receives the request, processes it, returns a response
3. The response is sent back to the agent via the JSON-RPC connection

Challenge: handlers run in Rust (tokio) but need to call Python callbacks (for permission, custom logic). This requires `pyo3-asyncio` or channel-based callback pattern similar to how permission callbacks work today.

### MCP-over-ACP vs Subprocess (Items 9, 11)

Two paths to the same goal (agent calling `@tool` functions):

| Aspect | Subprocess (Item 9) | MCP-over-ACP (Item 11) |
|---|---|---|
| Compatibility | All agents | Only agents with `mcpCapabilities.acp` |
| Latency | Higher (IPC over stdio) | Lower (same connection) |
| Lifecycle | Must manage subprocess | No subprocess |
| Complexity | Medium | High |
| Spec status | Stable | RFD (draft) |

Implement subprocess first for universal compatibility, then add MCP-over-ACP as optimization.

### Proxy Chain Design (Item 12)

Two levels of proxy support:

**Python-level (Approach A)**: Intercept in the Python layer. Simple, works today.
```python
chain = ProxyChain()
chain.add(ContextInjector(context="Always respond in JSON"))
chain.add(ResponseFilter(filter_fn=lambda r: r.strip()))

client = Client(["agent"], proxy_chain=chain)
# ContextInjector modifies prompt before sending to Rust
# ResponseFilter modifies response after receiving from Rust
```

**ACP-level (Approach B)**: SDK participates in a conductor chain. Complex, requires conductor.
```python
# SDK as a proxy component in a conductor chain
conductor = Conductor(url="ws://conductor:8080")
await conductor.register_proxy("context-injector", handler=inject_context)
```

Approach A should be the default. Approach B is for advanced orchestration scenarios.

### HTTP Transport (Item 13)

The Streamable HTTP transport is still a draft. Key design questions:
- Request/response pattern: HTTP POST for JSON-RPC requests, SSE for notifications?
- Authentication: Bearer token, API key header, mTLS?
- Connection lifecycle: persistent connection vs request-per-message?

Implement behind a feature flag and track the draft spec for changes.

### Testing Strategy

- **Unit tests**: Serialization, type mapping, capability negotiation logic
- **Integration tests**: Require a running ACP agent (`opencode`), gated behind `@pytest.mark.integration`
- **Mock tests**: For agent→client request handling (Items 7, 8), mock the ACP connection
- **Existing tests**: Must continue passing. All changes are additive.

### Build & Verify

```bash
maturin develop --uv          # Build Rust + install Python extension
python -m pytest tests/ -v    # Run all tests
uv run examples/13_opencode_direct.py  # Smoke test with real agent
```

## What's NOT in Scope (Beyond ACP)

These features are agent-specific or outside the ACP protocol:

- **File checkpointing and rewind** — Claude-specific feature, not ACP
- **Cost control (`max_budget_usd`)** — Claude-specific parameter
- **Thinking control / extended thinking** — agent-specific, may be added via `_meta` extensibility
- **Sandbox environments** — agent-specific runtime feature
- **Output format control** — agent-specific, may be added via `_meta`
- **Multi-agent orchestration** — higher-level concern, built on top of proxy chains
- **Agent marketplace / plugin system** — application layer, not SDK scope

These could be supported through the custom extension methods (Item 14) using `_`-prefixed method names, but they're not part of the core ACP protocol and therefore not planned as SDK features.
