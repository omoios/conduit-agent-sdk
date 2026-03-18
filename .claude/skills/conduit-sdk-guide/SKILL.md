---
name: conduit-sdk-guide
description: "Quick-start guide for the Conduit Agent SDK (conduit-agent-sdk) — a Python SDK for the Agent Client Protocol (ACP). Use when someone asks how to use the SDK, connect to an agent, send prompts, discover skills, activate commands, stream responses, or integrate with ACP-compatible agents like Claude, OpenCode, Goose, Gemini, or Codex. Also use when asked about the Agent enum, Client.from_registry, activate_skill, activate_skills, SkillResult, streaming updates, or any conduit_sdk import."
---

# Conduit Agent SDK

Python SDK for the Agent Client Protocol (ACP). Connect to any ACP agent, send prompts, stream responses, discover and activate skills.

## Install

```bash
pip install conduit-agent-sdk
```

## Connect to an Agent

Three ways, from simplest to most flexible:

```python
from conduit_sdk import Agent, Client

# 1. Agent enum + registry (recommended)
client = await Client.from_registry(Agent.OPENCODE)

# 2. String registry ID
client = await Client.from_registry("claude-acp")

# 3. Direct command
client = Client(["opencode", "acp"])
```

### Available Agents

| Enum | Registry ID | Agent |
|------|------------|-------|
| `Agent.CLAUDE` | `claude-acp` | Claude Code |
| `Agent.OPENCODE` | `opencode` | OpenCode |
| `Agent.GOOSE` | `goose` | Goose |
| `Agent.CODEX` | `codex-acp` | Codex CLI |
| `Agent.GEMINI` | `gemini` | Gemini CLI |
| `Agent.AUGGIE` | `auggie` | Auggie |
| `Agent.HERMES` | — (local) | Hermes (`hermes acp`) |

Local-only agents (not in registry): use `Client(agent.to_command())`.

## Send Prompts

```python
async with client:
    # Non-streaming — collect all messages
    messages = await client.prompt_sync("What is ACP?")
    for msg in messages:
        print(msg.text())

    # Streaming — token by token
    from conduit_sdk._conduit_sdk import UpdateKind
    async for update in client.prompt_stream("Explain ACP"):
        if update.kind == UpdateKind.TextDelta:
            print(update.text, end="")
        elif update.kind == UpdateKind.Done:
            break
```

## Discover Skills (Slash Commands)

Agents advertise available slash commands via `CommandsUpdate` streaming events:

```python
import json

async for update in client.prompt_stream("Hello"):
    if update.kind == UpdateKind.CommandsUpdate:
        commands = json.loads(update.commands_json)
        for cmd in commands:
            print(f"{cmd['name']}: {cmd.get('description', '')}")
```

## Activate Skills

```python
from conduit_sdk import SkillResult

# Single skill — returns response text
text = await client.activate_skill("/help")
text = await client.activate_skill("compact")  # auto-prefixed with /

# Batch — returns list[SkillResult]
results = await client.activate_skills(["/help", "compact", "/cost"])
for r in results:
    if r.success:
        print(f"{r.command}: {r.text[:80]}")
    else:
        print(f"{r.command}: FAILED — {r.error}")
```

`SkillResult` fields: `command` (str), `text` (str), `success` (bool), `error` (str | None).

## Sessions

```python
# Create a session for multi-turn conversation
session = await client.new_session()
msgs = await session.prompt("Hello")
msgs = await session.prompt("Follow up")

# Skills work on sessions too
text = await session.activate_skill("/help")
results = await session.activate_skills(["/help", "/cost"])
```

## Streaming Update Kinds

| Kind | Field | Description |
|------|-------|-------------|
| `TextDelta` | `text` | Agent text token |
| `ThoughtDelta` | `text` | Agent thinking/reasoning |
| `ToolUseStart` | `tool_name` | Tool invocation begins |
| `ToolUseEnd` | `tool_name` | Tool invocation ends |
| `ModeChange` | `mode_id` | Agent mode changed |
| `CommandsUpdate` | `commands_json` | Available slash commands updated |
| `Usage` | `usage_json` | Token usage stats |
| `Done` | `stop_reason` | Response complete |
| `Error` | `error` | Error occurred |

## Full Example

```python
import asyncio
from conduit_sdk import Agent, Client

async def main():
    client = await Client.from_registry(Agent.OPENCODE)
    async with client:
        text = await client.activate_skill("/help")
        print(text)

asyncio.run(main())
```

## Architecture

Three layers: ACP protocol (JSON-RPC over stdio) → Rust core (PyO3) → Python async API.

- `Client` — connection, prompting, streaming, skill activation
- `Session` — multi-turn conversations within a client
- `Registry` — ACP agent discovery and resolution
- `Agent` enum — known agents mapped to registry IDs

For detailed API reference, see `docs/api-reference.md` and `docs/skills-and-commands.md`.
