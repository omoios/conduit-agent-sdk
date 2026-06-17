# Architecture

The Conduit Agent SDK is a Python/Rust hybrid implementation of the Agent Client Protocol (ACP). This document explains how the SDK works internally for developers who want to understand the codebase or contribute to it.

## Overview

The SDK bridges high-level Python developer ergonomics with performance-critical Rust protocol handling.

**Python layer** provides the developer-facing API including the Client, Session, Registry, options, hooks, and permissions systems.

**Rust layer** handles all ACP protocol operations via PyO3 bindings. This includes subprocess management, message framing, streaming, and the async runtime.

**Dependencies** include the `sacp` crate for ACP protocol schema definitions and message framing, `sacp-tokio` for async ACP client operations, and `pyo3` for Python interoperability.

## Three-Layer Architecture

### Layer 1: Python API (python/conduit_sdk/)

The Python layer provides an async developer API that abstracts the complexity of the ACP protocol.

**client.py** contains the main `Client` class. It provides factory methods like `from_registry()` for automatic agent discovery and manual command configuration. The class manages connection lifecycle with `connect()` and `disconnect()`, handles prompting through `prompt()`, `prompt_stream()`, and `prompt_sync()`, and manages sessions via `new_session()`, `fork_session()`, `list_sessions()`, and `resume_session()`. It also supports cancel/interrupt operations and exposes configuration options and agent information.

**session.py** provides the `Session` class for session-specific operations. Sessions can be created or loaded, configured with `set_mode()` and `set_config()`, cancelled, forked, and used for prompting.

**session_store.py** provides session persistence backends for durable replay of `session/update` streams. All backends implement the async `SessionStore` protocol: `InMemorySessionStore` (process-local, no dependencies), `FileSessionStore` (filesystem, JSONL+metadata), `SqlSessionStore` (SQLAlchemy 2.0 async — SQLite for tests, Postgres via `asyncpg` in production), and `RedisSessionStore` (via `redis.asyncio`).

**activate.py** exports the `query()` convenience function. This is a one-liner that handles the entire flow: registry lookup, client creation, connection, prompting, and cleanup. It is useful for simple use cases where you want to prompt an agent without managing the client lifecycle manually.

**registry.py** implements the `Registry` class. It fetches the ACP agent registry from a CDN and caches it locally with a one-hour TTL. The registry resolves agent IDs to shell commands, handling various distribution formats including npx, uvx, and direct binaries. It also handles platform detection (such as darwin-aarch64) and runtime detection.

**options.py** defines the `AgentOptions` dataclass. This includes fields for system_prompt, model, max_turns, permission_mode, allowed_tools, disallowed_tools, mcp_servers, env, and cwd. These options serialize to ACP `_meta` JSON for NewSessionRequest.

**hooks.py** provides the `HookRunner` class. It supports decorator-based hook registration with priority ordering and a dispatch system for lifecycle events.

**proxy.py** contains `ProxyChain`, the `Proxy` base class, `ContextInjector`, and `ResponseFilter`. These provide a proxy chain builder, though the `build()` method is still pending implementation.

**tools.py** exports the `@tool` decorator, `McpSdkServerConfig`, and `create_sdk_mcp_server()`. Tools ARE callable by agents via the in-process MCP HTTP server — `Client._start_sdk_mcp_servers()` starts these during `connect()` and passes them as HTTP MCP servers to the agent at session creation.

**permissions.py** provides permission callbacks including `allow_all`, `deny_all`, and `console_approve`, along with the `ToolPermissionContext` for structured permission handling.

**query.py** contains the `Query` class which manages the legacy control protocol alongside ACP.

**types.py** defines all content block types, message types, and streaming event types used throughout the SDK.

**exceptions.py** establishes the error hierarchy: `ConduitError` as the base, with subclasses like `ConnectionError`, `SessionError`, and `TransportError`.

### Layer 2: Rust Core (src/)

The Rust layer handles all performance-critical ACP protocol operations and is exposed to Python via PyO3.

**client.rs** contains `RustClient`, a `#[pyclass]` that implements the full ACP client. It spawns the agent subprocess, runs the ACP initialize handshake, manages sessions, sends prompts, and receives streaming updates. All ACP operations flow through an internal command channel represented by the `AcpCommand` enum variants. A background tokio task runs the `JrHandlerChain` command loop to handle async operations without blocking Python.

**transport.rs** provides `AgentProcess` which spawns a subprocess with piped stdin/stdout and creates `sacp::ByteStreams` transport for ACP message framing. This is internal-only and not exposed directly to Python.

**types.rs** exports all PyO3 types including `Capabilities`, `Message`, `ContentBlock`, `SessionUpdate`, `UpdateKind`, `StreamEvent`, `ClientConfig`, `PermissionRequest`, `PermissionResponse`, and others.

**control.rs** implements `RustControlProtocol`, a bidirectional JSON control protocol that runs alongside ACP for legacy permission, hook, and MCP callbacks.

**hooks.rs** provides `RustHookDispatcher` for Rust-side hook dispatch. It includes the `HookType` enum and supports register, dispatch, and clear operations.

**tools.rs** contains `RustToolRegistry` which registers Python callbacks as tool definitions and allows invocation by name with JSON input.

**proxy.rs** has `RustProxyChain` which stores proxy configurations. The `build()` method is marked TODO pending sacp-conductor integration.

**error.rs** defines the `ConduitError` enum with variants mapped to corresponding Python exceptions.

**session.rs** exists but is empty. Session logic currently lives in client.rs command handling.

### Layer 3: ACP Protocol (sacp / sacp-tokio crates)

These external Rust crates implement the Agent Client Protocol specification.

`sacp::ByteStreams` provides byte-stream transport for ACP message framing over stdio.

`sacp::JrHandlerChain` implements the JSON-RPC handler chain for request and notification dispatch.

`sacp::JrConnectionCx` provides the connection context for sending requests and notifications.

ACP schema types include `InitializeRequest`/`Response`, `NewSessionRequest`/`Response`, `PromptRequest`/`Response`, `SessionNotification`, `CancelNotification`, `SetSessionModeRequest`, `ForkSessionRequest`, `ListSessionsRequest`, and `ResumeSessionRequest`.

`sacp::UntypedMessage` enables sending custom or unstable ACP methods like `session/set_config_option`.

Cargo features include an `unstable` feature that enables cancel, session model, fork, list, resume, usage, and session info functionality.

## Data Flow

### Connection Flow

When a client connects to an agent, the following sequence occurs:

1. `Client.__init__()` creates a `RustClient` instance with `ClientConfig` containing the command, cwd, env, and timeout settings.

2. `Client.connect()` calls `RustClient.connect()` which:
   - Spawns the agent subprocess via `AgentProcess::spawn()` with piped stdin/stdout
   - Creates the transport layer with `ByteStreams::new()` wrapping stdio into ACP message framing
   - Sets up the `JrHandlerChain` with notification handlers for streaming and permission request handlers
   - Performs the ACP handshake via `cx.initialize(InitializeRequest)` to receive agent capabilities
   - Creates a default session via `new_session(NewSessionRequest)` with `_meta` options and MCP servers
   - Spawns a background tokio task to run the ACP command loop

3. Returns `Capabilities` with agent information to the caller.

### Prompt Flow

Sending a prompt to an agent follows this path:

1. `Client.prompt(text)` or `Client.prompt_stream(text)` normalizes the input through `_prepare_prompt()`, handling both text strings and content block lists.

   - For `prompt()`: `RustClient.prompt()` sends the prompt, collects all response messages, and yields `Message` objects.
   - For `prompt_stream()`: `RustClient.send_prompt()` initiates the prompt, then `recv_update()` polls for `SessionUpdate` objects.

2. In Rust, the prompt sends an `AcpCommand::Prompt` variant to the background task via the internal command channel.

3. The background task sends a `PromptRequest` via `cx.send_request()` to the agent.

4. Streaming notifications arrive through the `JrHandlerChain` notification handler and map to stream events:

   - `AgentMessageChunk` maps to `StreamEvent::TextDelta`
   - `AgentThoughtChunk` maps to `StreamEvent::ThoughtDelta`
   - `ToolCall` maps to `StreamEvent::ToolUseStart` with kind and status
   - `ToolCallUpdate` maps to `StreamEvent::ToolUseUpdate` or `ToolUseEnd`
   - `Plan` maps to `StreamEvent::Plan`
   - `CurrentModeUpdate` maps to `StreamEvent::ModeChange`
   - `ConfigOptionUpdate` maps to `StreamEvent::ConfigUpdate`
   - `AvailableCommandsUpdate` maps to `StreamEvent::CommandsUpdate`
   - `UsageUpdate` maps to `StreamEvent::Usage`
   - `SessionInfoUpdate` maps to `StreamEvent::SessionInfo`

5. The `PromptResponse` signals completion with `StreamEvent::Done` containing the stop reason.

### Permission Flow

Permission requests follow this path:

1. The agent sends a permission request via ACP.

2. The `JrHandlerChain` request handler receives the permission request.

3. If a Python permission callback is configured, it is invoked with the tool_name and tool_input.

4. The callback returns an allow or deny decision.

5. The response is sent back to the agent through ACP.

### PyO3 Boundary

All complex data crosses the Python/Rust boundary as JSON strings. This design keeps PyO3 types simple while supporting the full ACP schema:

- Python serializes `AgentOptions` to a JSON string, and Rust deserializes it with `serde_json`.
- Rust serializes streaming updates to Python `SessionUpdate` objects with typed fields.

This approach avoids the complexity of mapping every ACP type directly through PyO3 while maintaining type safety on both sides.

## Current Status

### Fully Working

The following features are complete and functional:

- ACP connection lifecycle including spawn, initialize, session creation, prompting, streaming, and disconnect
- Agent registry with fetch, cache, search, and command resolution
- All streaming event types including text, thoughts, tools, plans, modes, configs, commands, usage, and session info
- Sessions supporting create, load, mode, config, cancel, fork, list, and resume operations
- Permissions with callback system and preset implementations
- Hooks with 8 lifecycle events and priority dispatch
- Agent options passthrough via ACP _meta
- MCP server config passthrough
- Agent info retrieval
- Rich and multi-modal content in prompts

- Custom tools via @tool decorator + in-process MCP HTTP server (callable by agents)
- Session persistence with InMemory/File/Sql/RedisSessionStore backends
### Not Yet Working

The following features are planned but not yet implemented:

- `ProxyChain.build()` is marked TODO and needs sacp-conductor integration.
- Client-side file system and terminal capabilities for agent-to-client requests.
- MCP-over-ACP transport.
- HTTP transport for remote agents.

See `docs/phase2-plan.md` and `docs/phase3-plan.md` for the complete development roadmap.
