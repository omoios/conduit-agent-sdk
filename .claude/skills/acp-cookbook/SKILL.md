---
name: acp-cookbook
description: "Complete cookbook for the Agent Client Protocol (ACP) using the conduit-agent-sdk. Use when working with ACP, the Agent Client Protocol, the ACP conductor + proxy chains, ProxyChain, authoring an ACP agent with AgentServer, building an ACP server, driving an agent with Client, session/update streaming, elicitation, MCP-over-ACP, fs/terminal client capabilities, conduit_sdk, or any conduit-agent-sdk API. Also use when asked about proxy chain patterns, session persistence, @tool decorator, in-process MCP servers, skill activation, ACP streaming events, permission callbacks, lifecycle hooks, SessionStore, or the unstable feature map. Prefer this skill over the narrower conduit-sdk-guide when the task touches proxy chains, conductor integration, or agent authoring."
---

# ACP Cookbook

The Agent Client Protocol (ACP) and the `conduit-agent-sdk` — a Python SDK that drives, serves, and
proxies ACP agents. This guide covers all three surfaces: **driving** an agent (Client),
**being** an agent (AgentServer), and **intercepting/extending** messages between them (Proxy + Conductor).

## 1. Orientation

ACP is a JSON-RPC 2.0 protocol spoken over stdio between two roles:

- **Client** — spawns the agent subprocess, sends prompts, receives streaming responses
- **Agent** — receives prompts, streams updates (text/thought/tool-calls), returns a stop reason
- **Proxy** — transforms messages between client and agent in an ordered chain
- **Conductor** — orchestrates proxy chains; sits BETWEEN every component (components never talk peer-to-peer)

All messages are newline-delimited JSON-RPC 2.0. The ACP session lifecycle is:

```
Client                Agent
  │                     │
  │── initialize ──────→│  (handshake — negotiate capabilities)
  │←─── response ──────│
  │── session/new ─────→│  (or session/load for resume)
  │←─── sessionId ─────│
  │── session/prompt ──→│  (send a turn)
  │←── session/update ←─│  (streaming: text/thought/tool-use)
  │←─── response ──────│  (stopReason)
```

For the full architecture and protocol details see:
- `docs/conductor-integration.md` — conductor + proxy chain design
- `docs/skills-and-commands.md` — slash command discovery and activation
- `docs/api-reference.md` — complete API surface
- `docs/architecture.md` — system layers (ACP → Rust → Python)

## 2. Driving an Agent (Client)

Connect to any ACP-compatible agent, send prompts, stream responses, activate slash commands.

### Connection — three ways

```python
from conduit_sdk import Agent, Client

# 1. Agent enum + registry (recommended)
client = await Client.from_registry(Agent.OPENCODE)

# 2. String registry ID
client = await Client.from_registry("claude-acp")

# 3. Direct command
client = Client(["claude", "--agent"])
```

### Prompting

```python
from conduit_sdk._conduit_sdk import UpdateKind

async with client:
    # Non-streaming — collect all messages
    messages = await client.prompt_sync("What is ACP?")
    for msg in messages:
        print(msg.text())

    # Streaming — token by token
    async for update in client.prompt_stream("Explain ACP"):
        if update.kind == UpdateKind.TextDelta:
            print(update.text, end="")
        elif update.kind == UpdateKind.Done:
            break
```

### Skill activation

```python
from conduit_sdk import SkillResult

# Single skill
text = await client.activate_skill("/help")
text = await client.activate_skill("compact")  # auto-prefixed with /

# Batch
results = await client.activate_skills(["/help", "compact", "/cost"])
for r in results:
    print(f"{r.command}: {'OK' if r.success else r.error}")
```

### Streaming UpdateKind table

| Kind | Field(s) | Description |
|------|----------|-------------|
| `TextDelta` | `text` | Agent text token |
| `ThoughtDelta` | `text` | Agent thinking/reasoning |
| `ToolUseStart` | `tool_name`, `tool_input`, `tool_use_id` | Tool invocation begins |
| `ToolUseUpdate` | `tool_use_id`, `tool_status` | Tool progress |
| `ToolUseEnd` | `tool_name`, `tool_use_id` | Tool invocation ends |
| `ModeChange` | `mode_id` | Agent mode changed |
| `Plan` | `plan_json` | Agent plan output |
| `ConfigUpdate` | `config_json` | Config option updated |
| `CommandsUpdate` | `commands_json` | Available slash commands changed |
| `Usage` | `usage_json` | Token usage stats |
| `SessionInfo` | `session_info_json` | Session metadata |
| `Done` | `stop_reason` | Response complete |
| `RateLimit` | `rate_limit_json` | Rate limit notification |
| `Error` | `error` | Error occurred |

## 3. Authoring an ACP Server (AgentServer)

Turn any Python process into an ACP agent using `AgentServer`. The agent reads JSON-RPC
from stdin and writes responses to stdout — the standard ACP transport.

### Minimal echo agent

```python
from conduit_sdk import AgentServer

server = AgentServer(name="echo")

@server.on_prompt
async def echo(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    await ctx.send_text(f"echo: {text}")
    return "end_turn"  # stop reason

if __name__ == "__main__":
    server.run()  # blocking — reads stdin line-by-line
```

### Streaming inside the handler

From inside a prompt handler, call `ctx` methods to push updates to the client:

```python
@server.on_prompt
async def my_handler(ctx, session_id, content):
    await ctx.send_thought("Thinking...")
    await ctx.send_text("Hello from ACP!")
    # Arbitrary session update (tool_call, plan, etc.)
    await ctx.send_update({
        "sessionUpdate": "tool_call",
        "content": {"type": "text", "text": "…"},
    })
    return "end_turn"
```

### Lifecycle handlers

```python
@server.on_initialize
async def on_init(params):
    return {"agentCapabilities": {"custom_feature": True}}

@server.on_new_session
async def on_session(params):
    # Return session_id (string or dict with sessionId)
    return {"sessionId": "my-session-123", "modes": ["ask", "code"]}

@server.on_session_load
async def on_load(params):
    return {"sessionId": params.get("sessionId")}

@server.on_cancel
async def on_cancel(params):
    print("Session cancelled")
```

### Calling MCP tools the client provided

The client can pass MCP servers at session creation via `AgentOptions(mcp_servers=...)`.
The agent calls them with `ctx.call_tool`:

```python
@server.on_prompt
async def handler(ctx, session_id, content):
    result = await ctx.call_tool("my-tools", "read_file", {"path": "data.txt"})
    for block in result.get("content", []):
        await ctx.send_text(block.get("text", ""))
    return "end_turn"
```

`ctx.call_tool` works with **http** MCP servers. The server list comes from the
`mcpServers` field in the `session/new` request the client sends.

## 4. Custom Tools (@tool + MCP)

The `@tool` decorator registers Python functions as tool definitions that an agent
can discover and call. This is **WORKING** — an in-process HTTP MCP server serves
the tools to the connected agent.

```python
from conduit_sdk import tool, create_sdk_mcp_server, AgentOptions

@tool(description="Read a file from disk")
async def read_file(path: str) -> str:
    return open(path).read()

@tool(description="Compute a checksum")
async def checksum(data: str) -> str:
    import hashlib
    return hashlib.sha256(data.encode()).hexdigest()

# Bundle tools into an MCP server config
server = create_sdk_mcp_server("my-tools", tools=[read_file, checksum])

# Pass to Client — the server starts automatically on connect
options = AgentOptions(mcp_servers={"my-tools": server})
async with await Client.from_registry("claude-acp", options=options) as client:
    async for msg in client.prompt("Read data.txt and checksum its content"):
        print(msg.text())
```

`create_sdk_mcp_server` returns an `McpSdkServerConfig` — when passed via
`AgentOptions(mcp_servers=...)`, the `Client` auto-starts the HTTP server during
`connect()` and stops it on `disconnect()`. The agent discovers them via
standard MCP tool listing and can call them during prompt turns.

## 5. The Conductor + Proxy Chains

The conductor is an external binary (`agent-client-protocol-conductor`) that
orchestrates proxy chains by sitting BETWEEN every component. Components
**never** talk peer-to-peer.

### Protocol

- A proxy receives messages from the conductor and forwards via
  `_proxy/successor/request` (wrapped request downstream) and
  `_proxy/successor/notification`
- Conductor unwraps and forwards; responses ride back via JSON-RPC response IDs
- During `initialize`, the conductor offers `_meta.proxy: true` to every component
  **except** the last (the agent); each proxy echoes `proxy: true` back, or init fails

### Message-ordering invariant

**Critical**: ALL forwarding — requests, responses, notifications — must preserve
send order between any two endpoints. The conductor enforces this by routing
every forwarded message through **one central `ConductorMessage` channel/actor**.
Skipping this causes a classic race: a fast session/prompt response overtakes a
slower session/update notification and the client loses data.

### MCP bridge (two modes)

When a proxy exposes MCP servers with `"url": "acp:$UUID"`:
- If the agent has `mcpCapabilities.acp`, pass declarations through unchanged
  (agent speaks `_mcp/*` natively)
- Otherwise, conductor binds a TCP port per server, rewrites spec to
  `conductor mcp $port`, spawns bridge processes, routes
  stdio ↔ TCP ↔ conductor ↔ `_mcp/*`

### Running the conductor

```json
{
  "proxies": [
    {"command": ["cargo", "run", "--bin", "my-proxy"]}
  ],
  "agent": {
    "command": ["claude-code", "--agent"]
  }
}
```

```bash
agent-client-protocol-conductor --config conductor.json
```

### In-repo integration: ProxyChain

```python
from conduit_sdk import ProxyChain, ContextInjector, ResponseFilter

chain = ProxyChain()
chain.add(ContextInjector(context="Be concise."))
chain.add(ResponseFilter(max_tokens=1000))
# await chain.build()  # ⚠️ NOT YET IMPLEMENTED — requires conductor
```

`ProxyChain.build()` is the in-repo integration point. The Python-side plumbing
(`ContextInjector`, `ResponseFilter`) exists, and the Rust-side `RustProxyChain` stores
config, but `build()` is a TODO — it needs the external `agent-client-protocol-conductor`
to spawn subprocesses and wire the `_proxy/successor/*` protocol.

See `docs/conductor-integration.md` for the full plan.

## 6. Elicitation (Unstable)

Elicitation is an **unstable** ACP extension (not yet finalized in spec). The
agent can send an `elicitation/create` request to the client to elicit input
via a form or URL.

- **Capability**: `ClientCapabilities.elicitation = { form, url }`
- **Method**: `elicitation/create` (agent → client request)
- **Params (form mode)**: `mode`, `message`, `requested_schema` (JSON Schema for the form)
- **Params (url mode)**: `elicitation_id`, `url` (open in browser)
- **Response action**: `accept`, `decline`, or `cancel`, plus optional `content`

> **Status**: NOT YET IMPLEMENTED in conduit-agent-sdk. Listed here for awareness.
> The feature requires agent→client request handling (same architecture as fs/terminal
> capabilities) and is behind the `unstable` feature flag in `agent-client-protocol`.

## 7. Persistence & Hooks

### Session store

Plug in a store to persist streaming events for replay, audit, or recovery:

```python
from conduit_sdk import (
    InMemorySessionStore, FileSessionStore,
    SqlSessionStore, RedisSessionStore, AgentOptions,
)

# File-based
store = FileSessionStore(root_dir="./sessions")
await store.init()

# SQL (SQLite/Postgres via SQLAlchemy)
store = SqlSessionStore(database_url="sqlite+aiosqlite:///sessions.db")
await store.init()

# Redis
store = RedisSessionStore(redis_url="redis://localhost:6379")
await store.init()

options = AgentOptions(session_store=store)
async with Client(["claude", "--agent"], options=options) as client:
    ...
```

Store interface: `init()`, `append_update()`, `get_session()`, `list_sessions()`,
`get_updates()`, `delete_session()`, `close()`.

### Lifecycle hooks (8 types)

```python
from conduit_sdk import HookType, HookContext

@client.hooks.on(HookType.PreToolUse)
async def log_tool(ctx: HookContext):
    print(f"Tool called: {ctx.get('tool_name')}")
    return ctx

@client.hooks.on(HookType.PostToolUse, priority=10)
async def audit(ctx: HookContext):
    ...
    return ctx
```

| HookType | When |
|----------|------|
| `PreToolUse` | Before tool execution |
| `PostToolUse` | After tool execution |
| `PromptSubmit` | When prompt is submitted |
| `ResponseReceived` | When response is received |
| `SessionCreated` | When session is created |
| `SessionDestroyed` | When session is destroyed |
| `Connected` | When client connects |
| `Disconnected` | When client disconnects |

### Permission callbacks

```python
from conduit_sdk import permissions, AgentOptions

async def can_use_tool(tool_name, tool_input, context):
    if tool_name.startswith("file.delete"):
        return permissions.PermissionResultDeny("deletions not allowed")
    return permissions.PermissionResultAllow()

# Built-in presets
options = AgentOptions(can_use_tool=permissions.allow_all)
options = AgentOptions(can_use_tool=permissions.deny_all)
options = AgentOptions(can_use_tool=permissions.console_approve)
```

## 8. Unstable Feature Map

The Cargo.toml enables the `unstable` feature on `agent-client-protocol` (v0.14),
which gates several session-management capabilities:

### WORKING (behind unstable gate, already implemented)

| Feature | Methods | Status |
|---------|---------|--------|
| Session cancel | `client.cancel(session_id)` | Working |
| Session fork | `client.fork_session(session_id)` → `Session` | Working |
| Session list | `client.list_sessions()` → `list[dict]` | Working |
| Session resume | `client.resume_session(session_id)` → `Session` | Working |
| Usage tracking | Streamed as `UpdateKind.Usage` | Working |
| Session info | Streamed as `UpdateKind.SessionInfo` | Working |

### NOT YET BUILT

| Feature | Description | Blocked by |
|---------|-------------|------------|
| Client fs capabilities | `fs/read_text_file` + `fs/write_text_file` agent→client requests | Agent→client request handler architecture |
| Client terminal capabilities | `terminal/create`, output, wait, kill, release | Agent→client request handler architecture |
| MCP-over-ACP | In-process MCP transport via ACP connection | Draft spec (RFD), no agent support |
| `ProxyChain.build()` | Conductor-mediated proxy chain | External `agent-client-protocol-conductor` binary |
| HTTP transport | Connect to remote agents over HTTP instead of stdio | Draft spec |
| Elicitation | `elicitation/create` agent→client request | Agent→client request handler architecture |

## 9. Decision Flowchart

```
┌─ What do you want to do? ──────────────────────────┐
│                                                     │
│  ┌─ Drive an ACP agent? ──────────────────────┐    │
│  │                                             │    │
│  │   Use conduit_sdk.Client                    │    │
│  │   · Client.from_registry("agent-id")        │    │
│  │   · Client(["command", "--flag"])           │    │
│  │   · client.prompt() / prompt_stream()       │    │
│  │   · client.activate_skill("/help")          │    │
│  │                                             │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ┌─ BE an ACP agent? ─────────────────────────┐    │
│  │                                             │    │
│  │   Use conduit_sdk.AgentServer               │    │
│  │   · @server.on_prompt                       │    │
│  │   · ctx.send_text / send_thought            │    │
│  │   · ctx.call_tool (http MCP servers)        │    │
│  │   · server.run()                            │    │
│  │                                             │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ┌─ Intercept / extend messages between them? ─┐    │
│  │                                             │    │
│  │   Use Proxy + Conductor                     │    │
│  │   · ProxyChain.add(ContextInjector(...))    │    │
│  │   · ProxyChain.add(ResponseFilter(...))     │    │
│  │   · await chain.build()  ⚠️ TODO            │    │
│  │   · External: conductor --config cfg.json   │    │
│  │                                             │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ┌─ Add custom tools for agent to call? ───────┐    │
│  │                                             │    │
│  │   Use @tool + create_sdk_mcp_server         │    │
│  │   · @tool(description="...")                │    │
│  │   · server = create_sdk_mcp_server(...)     │    │
│  │   · AgentOptions(mcp_servers={...})          │    │
│  │   · ✅ WORKING (in-process HTTP MCP)        │    │
│  │                                             │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Quick reference

| Goal | Class / Function | Entry point |
|------|-----------------|-------------|
| Drive an agent | `Client` | `conduit_sdk.Client` / `from_registry` |
| Be an agent | `AgentServer` | `conduit_sdk.AgentServer` |
| Intercept messages | `ProxyChain` + `Proxy` | `conduit_sdk.proxy` |
| Register a tool | `@tool` decorator | `conduit_sdk.tool` |
| Persist sessions | `*SessionStore` | `conduit_sdk.session_store` |
| Intercept lifecycle | `HookRunner` / `HookType` | `conduit_sdk.HookType` |
| Permission policy | `permissions.*` | `conduit_sdk.permissions` |

For complete API details see `docs/api-reference.md`. For the conductor + proxy
chain design see `docs/conductor-integration.md`.
