# conduit-agent-sdk

General-purpose Python SDK for the [Agent Client Protocol (ACP)](https://github.com/agentclientprotocol/). Works with **any** ACP-compatible coding agent — Claude Code, Gemini CLI, OpenCode, Codex, Goose, and more.

## Architecture

```
┌─────────────────────────────────────────┐
│          Python (conduit_sdk)           │  ← Developer-facing API
│  Client, Session, @tool, hooks, proxy   │
├─────────────────────────────────────────┤
│       Rust (_conduit_sdk via PyO3)      │  ← Performance-critical core
│  ACP client, transport, serialization   │
├─────────────────────────────────────────┤
│          sacp / sacp-tokio              │  ← ACP protocol implementation
│  JrHandlerChain, ByteStreams, types     │
└─────────────────────────────────────────┘
```

## Installation

Requires Python 3.12+ and Rust toolchain.

```bash
# Clone and install in development mode
git clone <repo-url>
cd conduit-agent-sdk
uv sync
maturin develop --uv
```

## Quick Start

### One-liner with `query()`

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

### Custom Tools

```python
from conduit_sdk import tool

@tool(description="Read a file from disk")
async def read_file(path: str) -> str:
    return open(path).read()
```

### Lifecycle Hooks

```python
from conduit_sdk import HookType

@client.hooks.on(HookType.PreToolUse)
async def log_tool(ctx):
    print(f"Tool called: {ctx.get('tool_name')}")
    return ctx
```

### Proxy Chains

```python
from conduit_sdk import ProxyChain, ContextInjector

chain = ProxyChain()
chain.add(ContextInjector(context="Be concise."))
await chain.build()
```

### Sessions

```python
session = await client.new_session()
await session.set_mode("code")
response = await session.prompt("Fix the bug in main.py")
```

## Examples

Run any example with `uv run`:

```bash
uv run examples/01_hello_world.py
```

| # | File | What it demonstrates |
|---|------|---------------------|
| 01 | `01_hello_world.py` | Simplest `query()` call |
| 02 | `02_registry_browse.py` | Fetch registry, list agents, search |
| 03 | `03_streaming.py` | `Client.from_registry()` + streaming |
| 04 | `04_multi_agent.py` | Connect to different agents |
| 05 | `05_permissions.py` | Custom `can_use_tool` callback |
| 06 | `06_custom_tools.py` | `@tool` decorator + MCP server |
| 07 | `07_file_operations.py` | Agent reads/lists/summarizes files |
| 08 | `08_code_generation.py` | Agent writes a Python module |
| 09 | `09_multi_turn.py` | Multi-turn sessions, mode/model changes |
| 10 | `10_hooks.py` | PreToolUse/PostToolUse hooks |
| 11 | `11_proxy_chain.py` | ContextInjector + ResponseFilter |
| 12 | `12_parallel_agents.py` | `asyncio.gather()` multiple agents |

## API Overview

| Module | Purpose |
|---|---|
| `conduit_sdk.query` | One-shot registry-based agent query |
| `conduit_sdk.Client` | Connect to agents, send prompts, stream responses |
| `conduit_sdk.Registry` | Fetch and query the ACP agent registry |
| `conduit_sdk.Session` | Manage conversation sessions |
| `conduit_sdk.tool` | Register Python functions as agent tools |
| `conduit_sdk.HookRunner` | Lifecycle hook system |
| `conduit_sdk.ProxyChain` | Compose message-intercepting proxies |

## Development

```bash
# Install dev dependencies
uv sync --extra dev

# Build Rust extension
maturin develop --uv

# Run tests
uv run pytest tests/ -v

# Lint
uv run ruff check python/ tests/
```

## Status

This SDK is in early development. The current implementation provides:

- **Complete project scaffold** with Rust/Python hybrid architecture
- **ACP agent registry** integration with automatic command resolution
- **Stub implementations** for all ACP operations (connect, prompt, session, tools, hooks, proxy)
- **Full type coverage** with PEP 561 type stubs
- **12 runnable examples** demonstrating the full SDK surface
- **Test suite** covering Python-layer logic

The Rust core has TODO markers where sacp crate integration needs to be wired up for actual ACP protocol communication.

## License

MIT
