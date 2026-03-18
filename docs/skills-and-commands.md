# Skills & Commands

This document explains how slash commands (skills) are discovered, activated, and observed through the Agent Client Protocol, and how to programmatically activate them using the Conduit SDK.

## Overview

In ACP, agents advertise available **slash commands** (also called skills or abilities) via streaming notifications. These commands are things like `/help`, `/commit`, `/test`, `/compact`, etc. — the exact set depends on the connected agent.

**Key insight:** Skills are activated by sending the command text as a prompt. There is no separate "invoke command" RPC in ACP — you prompt with the `/`-prefixed command string, and the agent handles it.

## How Commands Flow Through the SDK

Commands flow through three layers:

```
┌──────────────────────────────────────────────────────────────┐
│  ACP Agent                                                    │
│  Sends AvailableCommandsUpdate notification                   │
│  with list of slash commands as JSON                          │
└──────────────────────┬───────────────────────────────────────┘
                       │ SessionNotification
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Rust Layer (client.rs)                                       │
│  JrHandlerChain notification handler receives                 │
│  AcpSessionUpdate::AvailableCommandsUpdate                    │
│  → serializes to StreamEvent::CommandsUpdate { commands_json } │
└──────────────────────┬───────────────────────────────────────┘
                       │ mpsc channel
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Python Layer                                                 │
│  SessionUpdate(kind=UpdateKind.CommandsUpdate,                │
│                commands_json="[...]")                          │
│  Available via prompt_stream() or recv_update()               │
└──────────────────────────────────────────────────────────────┘
```

### Layer 1: ACP Protocol

The agent sends `AvailableCommandsUpdate` as part of `SessionNotification`. This typically happens:

- After session creation (initial command list)
- When the command set changes during a session

The notification contains a JSON array of command objects, each describing a slash command's name, description, and parameters.

### Layer 2: Rust Core (`src/client.rs`)

The `JrHandlerChain` notification handler matches `AcpSessionUpdate::AvailableCommandsUpdate`:

```rust
AcpSessionUpdate::AvailableCommandsUpdate(cmd_update) => {
    if let Ok(json) = serde_json::to_string(&cmd_update.available_commands) {
        let _ = notif_tx
            .send(StreamEvent::CommandsUpdate { commands_json: json })
            .await;
    }
}
```

This serializes the raw ACP command list to JSON and pushes it through the streaming channel.

### Layer 3: Python API

The `SessionUpdate` surfaces as:

```python
SessionUpdate(
    kind=UpdateKind.CommandsUpdate,
    commands_json='[{"name": "/help", "description": "Show help", ...}, ...]'
)
```

## Discovering Available Commands

Use `prompt_stream()` to observe all streaming events including command updates:

```python
import json
from conduit_sdk import Client, AgentOptions
from conduit_sdk._conduit_sdk import UpdateKind

async with Client(["claude", "--agent"]) as client:
    available_commands = []

    async for update in client.prompt_stream("Hello"):
        if update.kind == UpdateKind.CommandsUpdate:
            available_commands = json.loads(update.commands_json)
            print("Available commands:")
            for cmd in available_commands:
                print(f"  {cmd.get('name', 'unknown')}: {cmd.get('description', '')}")
        elif update.kind == UpdateKind.TextDelta:
            print(update.text, end="")
        elif update.kind == UpdateKind.Done:
            break
```

Commands are also emitted during session creation and mode changes, so you can discover them early.

## Activating Skills

### Low-Level: Send as Prompt

To activate a slash command manually, send it as the prompt text:

```python
# Activate a skill by sending it as a prompt
async for update in client.prompt_stream("/help"):
    if update.kind == UpdateKind.TextDelta:
        print(update.text, end="")
```

### High-Level: activate_skill() / activate_skills()

The SDK provides convenience methods that handle normalization and error collection:

```python
from conduit_sdk import Client

async with Client(["claude", "--agent"]) as client:
    # Single skill — returns response text directly
    text = await client.activate_skill("/help")
    print(text)

    # Slash prefix is optional
    text = await client.activate_skill("compact")

    # With arguments
    text = await client.activate_skill("/model claude-sonnet-4-20250514")
```

**Batch activation** runs multiple skills sequentially and returns a `SkillResult` per command:

```python
from conduit_sdk import Client, SkillResult

async with Client(["claude", "--agent"]) as client:
    results = await client.activate_skills(["/help", "compact", "/cost"])

    for r in results:
        if r.success:
            print(f"{r.command}: {r.text[:80]}")
        else:
            print(f"{r.command}: FAILED — {r.error}")
```

Each `SkillResult` contains:

| Field | Type | Description |
|-------|------|-------------|
| `command` | `str` | The normalized command (with `/` prefix) |
| `text` | `str` | Response text (empty on failure) |
| `success` | `bool` | Whether the command completed without error |
| `error` | `str \| None` | Error message if failed |

### Session-Level Activation

Skills can also be activated within a specific session:

```python
session = await client.new_session()
await session.set_mode("code")

# Single
text = await session.activate_skill("/help")

# Batch
results = await session.activate_skills(["/help", "/compact"])
```

### Command Normalization

All methods automatically normalize commands:

- `"help"` → `"/help"`
- `"/help"` → `"/help"` (no change)
- `"  compact  "` → `"/compact"` (strips whitespace)
- `"model claude-4"` → `"/model claude-4"` (args preserved)

## Full Workflow: Discover → Activate → Observe

```python
import asyncio
import json
from conduit_sdk import Client, SkillResult
from conduit_sdk._conduit_sdk import UpdateKind


async def main():
    async with Client(["claude", "--agent"]) as client:
        # Step 1: Discover available commands via streaming
        commands = []
        async for update in client.prompt_stream("Hello"):
            if update.kind == UpdateKind.CommandsUpdate:
                commands = json.loads(update.commands_json)
            elif update.kind == UpdateKind.Done:
                break

        print(f"Found {len(commands)} commands")
        for cmd in commands:
            print(f"  {cmd.get('name', 'unknown')}: {cmd.get('description', '')}")

        # Step 2: Activate skills using the convenience API
        results = await client.activate_skills(["/help", "/cost"])
        for r in results:
            print(f"\n--- {r.command} ---")
            print(r.text[:200] if r.success else f"Error: {r.error}")


asyncio.run(main())
```

## Watching for Command Changes

Commands can change during a session (e.g., after mode changes). To track changes:

```python
current_commands = []

async for update in client.prompt_stream("Switch to architect mode."):
    if update.kind == UpdateKind.CommandsUpdate:
        current_commands = json.loads(update.commands_json)
        print(f"Commands updated: {len(current_commands)} available")
    elif update.kind == UpdateKind.ModeChange:
        print(f"Mode changed to: {update.mode_id}")
    elif update.kind == UpdateKind.Done:
        break
```

## Relevant Types

| Type | Location | Purpose |
|------|----------|---------|
| `UpdateKind.CommandsUpdate` | `conduit_sdk._conduit_sdk` | Identifies a commands update event |
| `SessionUpdate.commands_json` | `conduit_sdk._conduit_sdk` | JSON string of available commands |
| `StreamEvent::CommandsUpdate` | `src/client.rs` | Internal Rust streaming event |
| `AcpSessionUpdate::AvailableCommandsUpdate` | `sacp` crate | ACP protocol notification type |

## Relationship to Other Features

- **Modes** (`set_mode()`): Changing modes may update the available command list
- **Config** (`set_config()`): Some configs affect which commands are available
- **Hooks** (`HookType.PreToolUse`): Hooks fire on tool use, not command invocation — commands and tools are separate concepts in ACP
- **Permissions** (`can_use_tool`): Permissions apply to tools, not slash commands

## Agent-Specific Commands

Different agents expose different commands. For example:

- **Claude Code** (`claude --agent`): `/help`, `/compact`, `/model`, `/permissions`, `/cost`, etc.
- **Gemini CLI**: Agent-specific commands
- **OpenCode**: Its own command set

The `AvailableCommandsUpdate` tells you exactly what's available for the connected agent — always discover dynamically rather than hardcoding command names.
