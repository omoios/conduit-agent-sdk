# conduit-agent-sdk

General-purpose Python SDK for the [Agent Client Protocol (ACP)](https://github.com/agentclientprotocol/). Works with **any** ACP-compatible coding agent: Claude Code, Gemini CLI, OpenCode, Goose, Codex, and more.

This is a Python/Rust hybrid: a developer-friendly Python API backed by a performance-critical Rust core via PyO3.

Repository: https://github.com/omoios/conduit-agent-sdk

## Architecture

```
┌─────────────────────────────────────────┐
│          Python (conduit_sdk)           │  ← Developer-facing API
│  Client, Session, Registry, @tool       │
│  hooks, permissions, query(), options   │
├─────────────────────────────────────────┤
│       Rust (_conduit_sdk via PyO3)      │  ← Performance-critical core
│  ACP client, transport, streaming,     │
│  permission dispatch, hook dispatch     │
├─────────────────────────────────────────┤
│   agent-client-protocol / -schema       │  ← ACP protocol implementation
│  ByteStreams, Client builder, types     │
└─────────────────────────────────────────┘
```

## Installation

Requires Python 3.12+ and Rust toolchain.

```bash
# Clone and install in development mode
git clone https://github.com/omoios/conduit-agent-sdk.git
cd conduit-agent-sdk
uv sync
maturin develop --uv
```

## Quick Start

### One-liner with query()

```python
import asyncio
from conduit_sdk import query

async def main():
    async for message in query(prompt="Hello!", agent="claude-acp"):
        print(message.text())

asyncio.run(main())
```

### Registry-based client

```python
from conduit_sdk import Client

async with await Client.from_registry("claude-acp") as client:
    async for message in client.prompt("Hello!"):
        print(message.text())
```

### Manual command (no registry)

```python
from conduit_sdk import Client

async with Client(["claude", "--agent"]) as client:
    async for message in client.prompt("Hello!"):
        print(message.text())
```

## Authoring an Agent

`conduit_sdk` is bidirectional: as well as *driving* agents as a client, you can
*author* an agent in Python that any ACP client can spawn. Register async
handlers on an `AgentServer`, then call `run()` — it speaks ACP over stdio.

```python
from conduit_sdk import AgentServer

server = AgentServer(name="my-agent")

@server.on_prompt
async def answer(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    await ctx.send_text(f"You said: {text}")   # streamed as agent_message_chunk
    return "end_turn"                           # stop reason

if __name__ == "__main__":
    server.run()
```

`on_new_session`, `on_initialize`, `on_session_load`, and `on_cancel` are
optional (sensible defaults are provided). Inside `on_prompt`, stream output via
`ctx.send_text(...)` / `ctx.send_thought(...)` / `ctx.send_update(dict)` and
return the turn's `stop_reason`. The agent is driven by clients over the same
wire protocol, so it loops back with this SDK's own `Client`.

## Custom Tools (MCP)

Give the agent extra tools defined in your own Python. The SDK runs an
in-process MCP server for them and passes it to the agent as an `http` MCP
server at session creation, so any agent that supports HTTP MCP (Claude Code,
Codex, ...) can discover and call them.

```python
from conduit_sdk import AgentOptions, Client, create_sdk_mcp_server, tool

@tool(description="Add two integers")
async def add(a: int, b: int) -> int:
    return a + b

server = create_sdk_mcp_server("math", tools=[add])
options = AgentOptions(mcp_servers={"math": server})

async with Client(["claude", "--agent"], options=options) as client:
    async for message in client.prompt("What is 2+2?"):
        print(message.text())
```

Input JSON Schema is generated from the function's type hints (`Optional[X]`,
`list[X]`, defaults, `Annotated[T, "desc"]`); pass `input_schema=` to override.

## Session Persistence

A `SessionStore` durably records each session's stream of `session/update`
events plus metadata, enabling replay and cross-process sharing. Backends share
one async interface: `InMemorySessionStore`, `FileSessionStore`,
`SqlSessionStore` (SQLAlchemy async — SQLite for tests, Postgres via `asyncpg`
in production), and `RedisSessionStore`.

```python
from conduit_sdk import AgentOptions, Client, FileSessionStore

store = FileSessionStore("./sessions")
options = AgentOptions(session_store=store)

async with Client(["claude", "--agent"], options=options) as client:
    async for update in client.prompt_stream("hello", session_id="s1"):
        ...           # each update is appended to the store under "s1"

events = await store.load_updates("s1")   # replay the session later
```

For SQL, point one store at any dialect — SQLite for tests, Postgres in prod:

```python
from sqlalchemy.ext.asyncio import create_async_engine
from conduit_sdk import SqlSessionStore

SqlSessionStore(create_async_engine("sqlite+aiosqlite:///:memory:"))   # tests
SqlSessionStore(create_async_engine("postgresql+asyncpg://user:pw@host/db"))  # prod
```

## Agent Registry

The SDK integrates with the [ACP agent registry](https://agentclientprotocol.com/get-started/registry), which provides a catalog of available agents with distribution metadata.

```python
from conduit_sdk import Registry

registry = Registry()
await registry.fetch()

# List all agents
agents = await registry.list_agents()

# Search by keyword
results = registry.search("claude")

# Resolve to a shell command
cmd, env = await registry.resolve_command("claude-acp")
# cmd = ["npx", "@zed-industries/claude-agent-acp@0.18.0"]
```

Resolution automatically detects your platform and preferred runtime (npx → uvx → binary).

## Features

### Full ACP Protocol

Complete protocol implementation: spawn → initialize → create session → prompt → stream responses. All streaming events are handled: text deltas, thought deltas, tool use events, mode changes, plan updates, config updates, command updates, usage tracking, session info, and rate limit notifications.

### Sessions

Create, manage, fork, and resume conversation sessions.

```python
session = await client.new_session()
await session.set_mode("code")
messages = await session.prompt("Fix the bug in main.py")

# Fork a session with shared history
forked = await session.fork()

# List and resume sessions
sessions = await client.list_sessions()
resumed = await client.resume_session(sessions[0]["id"])
```

### Permissions

Control which tools the agent can use via `AgentOptions.can_use_tool`.

```python
from conduit_sdk import AgentOptions, allow_all, deny_all, console_approve

# Custom callback
async def my_permission(tool_name, tool_input, context):
    if tool_name.startswith("file.delete"):
        return PermissionResultDeny("Not allowed")
    return PermissionResultAllow()

options = AgentOptions(can_use_tool=my_permission)
async with await Client.from_registry("claude-acp", options=options) as client:
    ...

# Or use presets: allow_all, deny_all, console_approve
```

### Lifecycle Hooks

Intercept and modify the conversation flow.

```python
from conduit_sdk import HookType

@client.hooks.on(HookType.PreToolUse)
async def log_tool(ctx):
    print(f"Tool called: {ctx.get('tool_name')}")
    return ctx
```

Available hooks: PreToolUse, PostToolUse, PromptSubmit, ResponseReceived, SessionCreated, SessionDestroyed, Connected, Disconnected.

### Agent Options

Pass configuration to agents via the `_meta` field.

```python
from conduit_sdk import AgentOptions

options = AgentOptions(
    system_prompt="You are a Python expert",
    model="claude-sonnet-4-20250514",
    permission_mode="ask",
    max_turns=10,
    allowed_tools=["read_file", "edit_file"],
    mcp_servers={"github": {...}}
)

async with await Client.from_registry("agent", options=options) as client:
    ...
```

### Cancel and Interrupt

Both control-protocol interrupts and ACP CancelNotification are supported.

```python
# Cancel via ACP notification (preferred)
await session.cancel()

# Interrupt via control protocol (legacy)
await client.interrupt()
```

### Rich Content

Send multi-modal prompts with images, audio, and resource links.

```python
from conduit_sdk import ImageBlock, AudioBlock, ResourceLinkBlock

async for msg in client.prompt([
    "Describe this image:",
    ImageBlock(data="base64...", mime_type="image/png"),
    ResourceLinkBlock(uri="file:///path/to/doc.pdf", name="Report"),
]):
    print(msg.text())
```

### Custom Tools

Register Python functions as tools in the SDK.

```python
from conduit_sdk import tool

@tool(description="Read a file from disk")
async def read_file(path: str) -> str:
    return open(path).read()
```

Note: Tools are registered in the SDK but not yet callable by agents. This requires MCP server subprocess wiring (Phase 3).

### Proxy Chains

Compose message-intercepting proxies.

```python
from conduit_sdk import ProxyChain, ContextInjector

chain = ProxyChain()
chain.add(ContextInjector(context="Be concise."))
await chain.build()  # TODO: pending sacp-conductor integration
```

Note: ProxyChain.build() is not yet implemented and requires sacp-conductor support.

## Examples

Run any example with uv run:

```bash
uv run examples/01_hello_world.py
```

| # | File | What it demonstrates |
|---|------|---------------------|
| 01 | 01_hello_world.py | Simplest query() call |
| 02 | 02_registry_browse.py | Fetch registry, list agents, search |
| 03 | 03_streaming.py | Client.from_registry() + streaming |
| 04 | 04_multi_agent.py | Connect to different agents |
| 05 | 05_permissions.py | Custom can_use_tool callback |
| 06 | 06_custom_tools.py | @tool decorator + MCP server config |
| 07 | 07_file_operations.py | Agent reads/lists/summarizes files |
| 08 | 08_code_generation.py | Agent writes a Python module |
| 09 | 09_multi_turn.py | Multi-turn sessions, mode/model changes |
| 10 | 10_hooks.py | PreToolUse/PostToolUse hooks |
| 11 | 11_proxy_chain.py | ContextInjector + ResponseFilter |
| 12 | 12_parallel_agents.py | asyncio.gather() multiple agents |
| 13 | 13_opencode_direct.py | Direct connection to OpenCode |
| 14 | 14_system_prompt.py | Custom system prompt via _meta |
| 15 | 15_model_selection.py | Model selection via AgentOptions |
| 16 | 16_max_turns.py | Limit conversation turns |
| 17 | 17_mcp_servers.py | MCP server configuration passthrough |
| 18 | 18_config_options.py | Session config options (set_config) |
| 19 | 19_cancel_session.py | Cancel/interrupt a session |
| 20 | 20_session_fork.py | Fork a session with shared history |
| 21 | 21_list_sessions.py | List available sessions |
| 22 | 22_resume_session.py | Resume a previous session |
| 23 | 23_streaming_updates.py | All streaming update types |
| 24 | 24_stop_reason.py | Inspect why a prompt turn ended |
| 25 | 25_interrupt_with_session.py | ACP CancelNotification |
| 26 | 26_agent_info.py | Agent name/version/title |
| 27 | 27_rich_content.py | Multi-modal prompts (images, etc.) |
| 28 | 28_rate_limit_awareness.py | Rate limit handling |
| 29 | 29_skill_activation.py | Discover and activate slash commands (skills) |
| 30 | 30_integration_skills.py | Full integration: discover all skills, activate_skill/activate_skills |

## API Overview

| Module | Purpose |
|---|---|
| conduit_sdk.query / query() | One-shot registry-based agent query |
| conduit_sdk.Client | Connect to agents, send prompts, stream responses |
| conduit_sdk.Registry | Fetch and query the ACP agent registry |
| conduit_sdk.Session | Manage conversation sessions (create, fork, resume) |
| conduit_sdk.AgentOptions | Comprehensive agent configuration |
| conduit_sdk.tool | Register Python functions as agent tools |
| conduit_sdk.HookRunner | Lifecycle hook system (pre/post tool use, etc.) |
| conduit_sdk.ProxyChain | Compose message-intercepting proxies (partial) |
| conduit_sdk.permissions | Permission callbacks and presets |
| conduit_sdk.types | Content blocks, streaming events, messages |
| conduit_sdk.exceptions | Error hierarchy |

## Development

```bash
# Install dependencies
uv sync

# Build Rust extension
maturin develop --uv

# Run tests
uv run pytest tests/ -v

# Lint
uv run ruff check python/ tests/
```

## Status

Working ACP protocol implementation with a full streaming pipeline: spawn → initialize → session → prompt → stream.

- Full ACP protocol: complete
- Agent registry: fetch, list, search, resolve to command
- Streaming: all event types handled
- Sessions: create, load, set mode, set config, cancel, fork, list, resume
- Permissions: callbacks and presets
- Hooks: all lifecycle hooks working
- Agent options: passed through to ACP via _meta
- Agent info: name, version, title after connection
- Rich content: images, audio, resources
- Cancel/Interrupt: both control and ACP notification
- 28 runnable examples
- 12 test modules, 123+ tests passing

Known limitations:
- ProxyChain.build() is a TODO (needs sacp-conductor)
- @tool functions are registered but not yet callable by agents (Phase 3, needs MCP server subprocess)

See docs/phase2-plan.md and docs/phase3-plan.md for the roadmap.

## License

MIT
