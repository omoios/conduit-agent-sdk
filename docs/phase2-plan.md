# Phase 2: Feature Completion Plan

> **Status**: Draft — awaiting approval before implementation
> **Baseline**: Commit `946d96c` + selective improvements (notification drain, hook dispatch, error mapping, query import fix)
> **Tests**: 123/123 passing, all 13 examples verified
> **Sources**: ACP protocol docs (session-modes, session-config-options, slash-commands, extensibility, tool-calls), ACP schema v0.10.8, claude-agent-sdk-python comparison

## Context

The SDK has a working ACP connection pipeline: spawn agent → initialize handshake → create session → send prompts → stream responses. Permission callbacks work. Hooks dispatch. All examples run.

However, several `AgentOptions` fields exist in the Python layer but are dead code — they're never passed through to the ACP protocol. Additionally, a review of the ACP protocol docs reveals several protocol features the SDK doesn't surface at all.

## Current Coverage Gap

The ACP `SessionUpdate` enum has **9 variants**. Our notification handler handles **4**, ignores **5**:

| SessionUpdate variant | Handled? | Notes |
|---|---|---|
| `AgentMessageChunk` | ✅ | → `StreamEvent::TextDelta` |
| `AgentThoughtChunk` | ✅ | → `StreamEvent::ThoughtDelta` |
| `ToolCall` | ✅ | → `StreamEvent::ToolUseStart` |
| `ToolCallUpdate` | ✅ | → `StreamEvent::ToolUseEnd` |
| `Plan` | ❌ | Silently ignored (`_ => {}`) |
| `AvailableCommandsUpdate` | ❌ | Silently ignored |
| `CurrentModeUpdate` | ❌ | Silently ignored |
| `ConfigOptionUpdate` | ❌ | Silently ignored |
| `UserMessageChunk` | ❌ | Silently ignored (echo, low priority) |

Additionally, the SDK has no client-side methods for:
- `session/set_config_option` (the preferred replacement for both `session/set_mode` and `session/set_model`)
- `session/cancel` (interrupt)
- Slash command awareness (commands are just text in prompts, but discovering available commands requires handling the notification)

## Scope

Eight work items in three tiers. Each is independently shippable.

---

## Tier 1: Wire What We Already Have (Dead Code → Working Code)

### 1. Session Options via `_meta` on `NewSessionRequest`

**Priority**: 🔴 Critical — `system_prompt`, `model`, `max_turns` are the most commonly used options and all are dead code today.

**ACP mechanism**: The `_meta` extensibility field. `NewSessionRequest` has `meta: Option<Meta>` where `Meta = serde_json::Map<String, serde_json::Value>`. This is the protocol's intended extension point. Agents like Claude Code read `systemPrompt`, `model`, `maxTurns`, `permissionMode` from `_meta`.

**What exists today**:
- Python: `AgentOptions` has `system_prompt`, `model`, `max_turns`, `permission_mode`, `allowed_tools`, `disallowed_tools` — all dead code
- Python: `AgentOptions.to_dict()` serializes to `{"systemPrompt": "...", "model": "...", ...}` — but nobody calls it for ACP
- Rust: `AcpCommand::NewSession` only has `cwd` field, passes `meta: None` to `NewSessionRequest`

**What needs to happen**:
- `src/client.rs`: Add `meta_json: Option<String>` to `AcpCommand::NewSession`
- In the command loop: deserialize JSON string → `Meta`, pass to `NewSessionRequest::new(cwd).meta(meta)`
- `RustClient::new_session` PyO3 method: add `meta_json: Option<String>` parameter
- `python/conduit_sdk/client.py`: build `_meta` dict from `AgentOptions` fields, pass as JSON to Rust

**Files**: `src/client.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `AgentOptions(system_prompt="Be concise")` → `_meta: {"systemPrompt": "Be concise"}` in ACP request
- [ ] `model`, `max_turns`, `permission_mode`, `allowed_tools`, `disallowed_tools` all pass through
- [ ] Unit test for meta serialization round-trip
- [ ] Integration test: agent receives and respects system prompt

---

### 2. MCP Server Passthrough on `NewSessionRequest`

**Priority**: 🔴 Critical — without this, `AgentOptions.mcp_servers` is dead code and agents can't connect to external tool servers.

**ACP mechanism**: `NewSessionRequest.mcp_servers: Vec<McpServer>`. The `McpServer` enum has three transport variants:
- `McpServer::Stdio(McpServerStdio { name, command, args, env })` — **all agents MUST support**
- `McpServer::Http(McpServerHttp { name, url, headers })` — optional
- `McpServer::Sse(McpServerSse { name, url, headers })` — optional

**What exists today**:
- Python: `AgentOptions.mcp_servers` accepts `dict[str, McpSdkServerConfig | dict]`
- Rust: `NewSessionRequest` built with `mcp_servers: vec![]` always

**What needs to happen**:
- `src/client.rs`: Add `mcp_servers_json: Option<String>` to `AcpCommand::NewSession`
- In the command loop: deserialize JSON → `Vec<McpServer>`, pass to `NewSessionRequest::new(cwd).mcp_servers(servers)`
- `RustClient::new_session` PyO3 method: add `mcp_servers_json: Option<String>` parameter
- `python/conduit_sdk/client.py`: serialize MCP server configs to JSON matching ACP schema shape

**Design note**: Use JSON string across PyO3 boundary. Rust deserializes to `Vec<McpServer>` since the sacp schema types implement `Deserialize`. Python side serializes to the same JSON shape (camelCase field names matching ACP wire format).

**Files**: `src/client.rs`, `python/conduit_sdk/client.py`, `python/conduit_sdk/options.py` (add helper to serialize to ACP wire format)

**Acceptance criteria**:
- [ ] `AgentOptions(mcp_servers={"my-server": {"name": "...", "command": "...", "args": [...]}})` passes through to ACP `NewSessionRequest`
- [ ] All three transport types (Stdio, Http, Sse) supported
- [ ] Unit test for JSON serialization → `Vec<McpServer>` deserialization
- [ ] Integration test: agent connects to an external MCP server

---

### 3. Session Cancel (Interrupt)

**Priority**: 🟡 Medium — enables graceful interruption of long-running agent operations.

**ACP mechanism**: `CancelNotification { session_id }` — a JSON-RPC **notification** (no response), method `session/cancel`. Per the spec, the agent MUST return `StopReason::Cancelled`. Behind `unstable_cancel_request` feature (enabled).

**What exists today**:
- Python: `Client.interrupt()` calls `Query.interrupt()` via legacy control protocol
- Rust: No `AcpCommand::Cancel` variant

**What needs to happen**:
- `src/client.rs`: Add `AcpCommand::Cancel { session_id }`, handle with `cx.send_notification(CancelNotification::new(session_id))`
- `RustClient`: Add `cancel_session(session_id)` PyO3 method
- `python/conduit_sdk/client.py`: Wire `interrupt()` to new Rust method

**Files**: `src/client.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] `await client.interrupt()` sends `CancelNotification` via ACP
- [ ] Agent stops and returns `StopReason::Cancelled`
- [ ] Unit test for cancel command routing

---

## Tier 2: New Protocol Features (Not Yet Surfaced)

### 4. Session Config Options (`session/set_config_option`)

**Priority**: 🟡 Medium — this is the **preferred replacement** for both `session/set_mode` and `session/set_model`. The ACP docs explicitly state: "If an Agent provides `configOptions`, Clients SHOULD use them instead of the `modes` field. Modes will be removed in a future version."

**ACP mechanism**: During session setup, agents return `configOptions: ConfigOption[]` listing available selectors (mode, model, thought_level, custom). Clients change values via `session/set_config_option { sessionId, configId, value }`. Response returns the **complete** updated config state (because changing one option can affect others — e.g., changing model may change available thought levels).

Agent-side changes arrive via `ConfigOptionUpdate` session notification.

**What exists today**:
- Rust: `AcpCommand::SetSessionMode` handles mode changes via the legacy `session/set_mode` method
- Python: `Client.set_model()` and `Client.set_permission_mode()` use legacy control protocol
- Neither config options nor the `session/set_config_option` method are implemented
- `ConfigOptionUpdate` notifications are silently ignored

**What needs to happen**:
- Parse `config_options` from `NewSessionResponse` and expose to Python
- `src/client.rs`: Add `AcpCommand::SetConfigOption { session_id, config_id, value, reply }` variant
- Handle `ConfigOptionUpdate` in notification handler → new `StreamEvent::ConfigUpdate` variant
- `RustClient`: Add `set_config_option(session_id, config_id, value)` PyO3 method
- `python/conduit_sdk/client.py`: Add `set_config(config_id, value)` method. Deprecate `set_permission_mode()` in favor of `set_config("mode", value)`

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`, `python/conduit_sdk/session.py`

**Acceptance criteria**:
- [ ] Config options from session setup are accessible via `client.config_options`
- [ ] `await client.set_config("model", "claude-sonnet-4-20250514")` sends `SetSessionConfigOptionRequest`
- [ ] `ConfigOptionUpdate` notifications update local config state
- [ ] Backward compatible: `set_session_mode()` still works via legacy path

---

### 5. Slash Commands (Available Commands)

**Priority**: 🟡 Medium — enables SDK users to discover and use agent commands like `/web`, `/test`, `/plan`.

**ACP mechanism**: After session creation, agents send `AvailableCommandsUpdate` notification with a list of `AvailableCommand { name, description, input? }`. Commands are invoked as regular prompt text (e.g., `"/web agent client protocol"`). The agent can update the list at any time during the session.

**What exists today**:
- `AvailableCommandsUpdate` notifications are silently ignored in the `_ => {}` catch-all
- No Python API to discover available commands
- Users can already send `/slash` commands via `client.prompt("/web query")` — it's just text

**What needs to happen**:
- Handle `AvailableCommandsUpdate` in notification handler → new `StreamEvent::CommandsUpdate` variant
- Store available commands on the client/session so Python can access them
- `python/conduit_sdk/client.py`: Add `client.available_commands` property
- `python/conduit_sdk/client.py`: Add convenience `client.command(name, input)` that sends `"/{name} {input}"` as prompt text

**Files**: `src/client.rs`, `src/types.rs`, `python/conduit_sdk/client.py`

**Acceptance criteria**:
- [ ] Available commands populated from agent notifications
- [ ] `client.available_commands` returns list of `{name, description, input_hint?}`
- [ ] `await client.command("web", "agent client protocol")` sends `"/web agent client protocol"` as prompt
- [ ] Commands update dynamically when agent sends new `AvailableCommandsUpdate`

---

### 6. Rich Tool Call Events

**Priority**: 🟡 Medium — currently we extract minimal info from tool calls. The ACP protocol has much richer data.

**ACP mechanism**: `ToolCall` has `title`, `kind` (read/edit/delete/execute/search/think/fetch/other), `status` (pending/in_progress/completed/failed), `content` (text, diffs, terminal refs), `locations` (file paths the tool is working with), `rawInput`, `rawOutput`. `ToolCallUpdate` can update any of these fields incrementally.

**What exists today**:
- `ToolCall` → `StreamEvent::ToolUseStart { tool_name, tool_input, tool_use_id }` — only extracts `title` and `raw_input`
- `ToolCallUpdate` → `StreamEvent::ToolUseEnd { tool_use_id }` — only extracts `tool_call_id`, ignores status/content/locations
- Missing: `kind`, `status`, `content`, `locations`, `rawOutput`

**What needs to happen**:
- Enrich `StreamEvent::ToolUseStart` with `kind`, `status` fields
- Replace `StreamEvent::ToolUseEnd` with `StreamEvent::ToolUseUpdate` that carries `status`, `content` (as JSON string), `locations`
- `src/types.rs`: Add fields to `SessionUpdate` for `tool_kind`, `tool_status`, `tool_content`, `tool_locations`
- Python consumers get richer tool call information for UI/logging

**Files**: `src/client.rs`, `src/types.rs`

**Acceptance criteria**:
- [ ] Tool kind (read/edit/execute/etc.) available in streaming updates
- [ ] Tool status transitions (pending → in_progress → completed/failed) surfaced
- [ ] Tool content (text, diffs) accessible in `SessionUpdate`
- [ ] Tool locations (file paths) accessible in `SessionUpdate`
- [ ] Backward compatible: existing `ToolUseStart`/`ToolUseEnd` pattern still works

---

### 7. Current Mode Update + Plan Display

**Priority**: 🟢 Low — informational streaming events. The mode update is useful for agents that auto-switch modes (e.g., from architect → code after planning).

**ACP mechanism**:
- `CurrentModeUpdate { current_mode_id }` — agent changed its own mode
- `Plan { entries: [{ title, status, ... }] }` — agent's execution plan

**What exists today**:
- Both silently ignored in `_ => {}` catch-all

**What needs to happen**:
- Handle `CurrentModeUpdate` → `StreamEvent::ModeChange { mode_id }`
- Handle `Plan` → `StreamEvent::Plan { entries_json }` (serialize plan entries as JSON string to avoid complex types)
- `src/types.rs`: Add `UpdateKind::ModeChange` and `UpdateKind::Plan` variants with corresponding fields on `SessionUpdate`

**Files**: `src/client.rs`, `src/types.rs`

**Acceptance criteria**:
- [ ] Mode changes appear in `prompt_stream()` as `SessionUpdate(kind=ModeChange)`
- [ ] Plan entries appear as `SessionUpdate(kind=Plan)` with title and status
- [ ] Existing streaming tests still pass

---

## Tier 3: Stretch Goals

### 8. SDK-Hosted MCP Server for `@tool` Functions

**Priority**: 🟢 Low / Phase 3 candidate — architecturally complex, can defer.

For `@tool`-decorated functions to be callable by the agent, the SDK must:
1. Spawn a Python subprocess running an MCP stdio server
2. Pass that server's command as `McpServer::Stdio` to the agent via `NewSessionRequest.mcp_servers`
3. The subprocess handles `tools/list` → returns `@tool` definitions, `tools/call` → invokes the Python callback

This is the most complex item. It requires a new Python module (`conduit_sdk/mcp_server.py`) implementing the MCP JSON-RPC protocol over stdin/stdout. Alternatively, use an existing MCP server library like `fastmcp` or `mcp` from PyPI.

**Depends on**: Item 2 (MCP server passthrough must work first).

**Acceptance criteria**:
- [ ] `@tool`-decorated functions are callable by the agent
- [ ] SDK spawns MCP server subprocess automatically when tools are registered
- [ ] Server handles `tools/list` and `tools/call` correctly
- [ ] Integration test: agent calls an `@tool` function and receives the result

---

## Implementation Order

```
Tier 1 (Wire existing dead code):
  Item 1 (system_prompt/_meta)          ← Simplest, highest user impact
    ↓
  Item 2 (MCP server passthrough)       ← Enables external MCP servers
    ↓
  Item 3 (cancel notification)          ← Independent, low complexity

Tier 2 (New protocol features):
  Item 4 (config options)               ← Preferred replacement for set_mode + set_model
    ↓
  Item 5 (slash commands)               ← Discover/use agent commands
    ↓
  Item 6 (rich tool calls)              ← Enrich streaming data
    ↓
  Item 7 (mode update + plan display)   ← Additive streaming events

Tier 3 (Stretch):
  Item 8 (SDK-hosted MCP server)        ← Complex, may defer
```

Items 4-7 share a common pattern: add a `StreamEvent` variant, add a match arm in the notification handler, add `UpdateKind` variant in types, expose to Python. Once one is done, the others follow the same template.

## Technical Notes

### Python → Rust boundary

All new data flows use **JSON strings** across the PyO3 boundary:
- Python serializes options/configs to JSON string
- Rust deserializes with `serde_json::from_str`
- This keeps PyO3 types simple and avoids complex struct mapping
- ACP schema types (`McpServer`, `Meta`, etc.) implement `Deserialize` so this is trivial on the Rust side

### ACP `_meta` extensibility

`Meta = serde_json::Map<String, serde_json::Value>` — an arbitrary JSON object. Per the [extensibility spec](https://agentclientprotocol.com/protocol/extensibility):
- Implementations MUST NOT add custom fields at the root of spec types — use `_meta` instead
- Method names starting with `_` are reserved for custom extensions
- Capabilities can advertise custom features via `_meta` on capability objects

### Session Config Options vs Legacy Modes/Models

The ACP docs are explicit: **config options replace both modes and models**:
- `session/set_config_option` replaces `session/set_mode` and `session/set_model`
- Config options support dependent changes (changing model can change available thought levels)
- Response always returns complete state
- Categories: `mode`, `model`, `thought_level`, plus custom `_`-prefixed

Our plan supports both:
- Implement `session/set_config_option` as the primary path (Item 4)
- Keep legacy `session/set_mode` working for backward compat
- Deprecate `set_model()` / `set_permission_mode()` in favor of `set_config()`

### Slash Commands

Commands are NOT a separate protocol method — they're just prompt text. The protocol only provides:
- Discovery: agent sends `AvailableCommandsUpdate` notification listing commands
- Invocation: client sends `"/command_name args"` as regular prompt text

This means Item 5 is mostly about **surfacing** the available commands to SDK users, not implementing a new wire protocol.

### Tool Call Richness

Our current tool call handling is minimal:
- `ToolCall` → we only extract `title` (as `tool_name`) and `raw_input`
- `ToolCallUpdate` → we only extract `tool_call_id`, treat it as "end"

The protocol provides much more:
- `kind`: read, edit, delete, move, search, execute, think, fetch, other
- `status`: pending → in_progress → completed/failed
- `content`: text blocks, **diffs** (oldText/newText with file paths), terminal references
- `locations`: file paths the tool is working with (for "follow-along" features)
- `rawOutput`: the raw result from the tool

Item 6 enriches our streaming data to expose all of this.

### Feature flags

`Cargo.toml` has `sacp = { ..., features = ["unstable"] }` which enables:
- `unstable_cancel_request` → `CancelNotification` (Item 3)
- `unstable_session_model` → `SetSessionModelRequest` (superseded by Item 4's config options)
- `unstable_session_fork` → `ForkSessionRequest` (not planned)
- `unstable_session_list` → `ListSessionsRequest` (not planned)
- `unstable_session_resume` → `ResumeSessionRequest` (not planned)
- `unstable_session_usage` → `UsageUpdate` (not planned)
- `unstable_session_info_update` → `SessionInfoUpdate` (not planned)

### Testing strategy

- **Unit tests**: Serialization round-trips (AgentOptions → meta JSON, MCP configs → ACP schema JSON)
- **Integration tests**: Require a running ACP agent (`opencode` — locally installed, fast). Gated behind pytest marker `@pytest.mark.integration`
- **Existing tests**: Must continue passing (123/123). All changes are additive.

### Build & verify

```bash
maturin develop --uv          # Build Rust + install Python extension
python -m pytest tests/ -v    # Run all tests
uv run examples/13_opencode_direct.py  # Smoke test with real agent
```

## Out of Scope (Phase 3+)

- SDK-hosted MCP server for `@tool` functions (Item 8 — may be promoted if Items 1-2 go smoothly)
- Session forking (`ForkSessionRequest`)
- Session listing/resuming (`ListSessionsRequest`, `ResumeSessionRequest`)
- Usage tracking (`UsageUpdate`)
- Session info updates (`SessionInfoUpdate`)
- File checkpointing and rewind (Claude-specific, not ACP)
- Cost control (`max_budget_usd`) (Claude-specific, not ACP)
- Thinking control, sandbox, output format (Claude-specific, not ACP)
- Proxy chain `build()` implementation
- Custom extension methods (`_` prefixed methods)
