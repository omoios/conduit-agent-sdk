# API Reference

Complete reference for the conduit-agent-sdk Python API.

## conduit_sdk.query()

```python
async def query(
    *,
    prompt: str,
    agent: str,
    prefer: str | None = None,
    registry_url: str | None = None,
    options: AgentOptions | None = None,
    timeout: int = 30,
) -> AsyncIterator[Message]
```

One-shot convenience function. Handles registry lookup, connection, prompting, and cleanup.

**Parameters:**
- `prompt` (str): The text to send to the agent
- `agent` (str): Registry agent ID (e.g. `"claude-acp"`)
- `prefer` (str | None): Preferred distribution type (`"npx"`, `"uvx"`, `"binary"`)
- `registry_url` (str | None): Custom registry URL
- `options` (AgentOptions | None): Additional agent configuration
- `timeout` (int): Connection timeout in seconds (default: 30)

**Yields:** `Message` — Response messages as they arrive

**Example:**
```python
from conduit_sdk import query

async for message in query(prompt="Hello!", agent="claude-acp"):
    print(message.text())
```

## conduit_sdk.Client

```python
class Client:
    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 30,
        options: AgentOptions | None = None,
    ) -> None
```

Async client for communicating with an ACP-compatible agent.

**Parameters:**
- `command` (list[str]): Shell command to spawn the agent process
- `cwd` (str | None): Working directory for the agent process
- `env` (dict[str, str] | None): Additional environment variables for the agent
- `timeout` (int): Connection timeout in seconds (default: 30)
- `options` (AgentOptions | None): Comprehensive agent configuration

### Factory Methods

**`await Client.from_registry(agent_id, *, prefer=None, registry=None, timeout=30, options=None) -> Client`**

Create client from registry agent ID. Returns an unconnected client.

**Parameters:**
- `agent_id` (str): Registry identifier (e.g. `"claude-acp"`)
- `prefer` (str | None): Preferred distribution type (`"npx"`, `"uvx"`, `"binary"`)
- `registry` (Registry | None): Pre-configured Registry instance
- `timeout` (int): Connection timeout in seconds
- `options` (AgentOptions | None): Additional AgentOptions for the client

**Example:**
```python
async with await Client.from_registry("claude-acp") as client:
    async for msg in client.prompt("Hello!"):
        print(msg.text())
```

### Connection Lifecycle

**`await client.connect() -> Capabilities`**

Spawn agent, perform ACP handshake, return capabilities.

**`await client.disconnect()`**

Terminate agent subprocess and clean up.

**`client.connected -> bool`**

Connection state.

**`client.capabilities -> Capabilities | None`**

Agent capabilities from handshake.

**`async with client:`**

Context manager (auto connect/disconnect).

### Prompting

**`async for msg in client.prompt(text, *, session_id=None)`**

Stream response Messages (accumulated text, not deltas). `text` can be a string or list of content blocks.

**`async for update in client.prompt_stream(text, *, session_id=None)`**

Stream raw SessionUpdate objects (text deltas, tool events, etc.).

**`await client.prompt_sync(text, *, session_id=None) -> list[Message]`**

Non-streaming, collect all messages.

**Example:**
```python
# Streaming
async for msg in client.prompt("Fix this bug"):
    print(msg.text())

# Non-streaming
messages = await client.prompt_sync("Summarize this file")
for msg in messages:
    print(msg.text())
```

### Session Management

**`await client.new_session(cwd=None) -> Session`**

Create new session (passes system_prompt, model, etc. from options).

**`await client.fork_session(session_id, cwd=None) -> Session`**

Fork session with shared history.

**`await client.list_sessions(cwd=None) -> list[dict]`**

List available sessions.

**`await client.resume_session(session_id, cwd=None) -> Session`**

Resume existing session.

**Example:**
```python
session = await client.new_session()
await session.set_mode("code")

# Fork with shared history
forked = await session.fork()

# Resume later
sessions = await client.list_sessions()
await client.resume_session(sessions[0]["session_id"])
```

### Control

**`await client.interrupt(session_id=None)`**

Cancel current operation. If `session_id` is provided, sends ACP CancelNotification.

**`await client.cancel(session_id)`**

ACP CancelNotification for the given session.

**`await client.set_config(session_id, config_id, value) -> dict`**

Set config option on a session.

**`await client.set_permission_mode(mode)`**

Change permission mode (legacy, prefer AgentOptions).

**`await client.set_model(model)`**

Change model (legacy, prefer AgentOptions).

### Properties

**`client.hooks -> HookRunner`**

Hook runner for registering lifecycle hooks.

**`client.options -> AgentOptions | None`**

Current options.

**`await client.agent_info -> dict | None`**

Agent name, version, title (requires connection).

## conduit_sdk.Session

```python
class Session:
    def __init__(self, client: Client)
```

An ACP conversation session.

### Lifecycle

**`await session.create(cwd=None, *, meta_json=None, mcp_servers_json=None) -> str`**

Create ACP session, returns session ID.

**`await session.load(session_id, cwd=None) -> str`**

Resume existing session.

### Configuration

**`await session.set_mode(mode)`**

Set mode (ask/code/architect).

**`await session.set_config(config_id, value) -> dict`**

Set config option.

**`await session.cancel()`**

Cancel current operation.

**`await session.fork(cwd=None) -> Session`**

Fork into new session with shared history.

### Prompting

**`await session.prompt(text) -> list[Message]`**

Send prompt (non-streaming).

### Properties

**`session.session_id -> str | None`**

Current session ID.

**`session.mode -> str | None`**

Current mode.


## conduit_sdk.AgentServer

```python
class AgentServer:
    def __init__(self, name, *, version="1.0.0", description=None)
```

Author an ACP agent in Python. Register async handlers, then call `run()` — it speaks ACP over stdio.

### Handlers

**`@server.on_prompt`** — Required. Stream response via `ctx.send_text(...)` / `ctx.send_thought(...)` / `ctx.update(...)`, return stop reason.

**`@server.on_new_session`** (optional), **`@server.on_initialize`** (optional), **`@server.on_session_load`** (optional), **`@server.on_cancel`** (optional) — sensible defaults provided.

### AgentContext

Streaming handle passed to prompt handlers:

- `await ctx.send_text(text)` — stream text delta
- `await ctx.send_thought(text)` — stream thought delta
- `await ctx.update(partial)` — send arbitrary update dict
- `await ctx.call_tool(name, args)` — call an in-process MCP tool

**Example:**
```python
from conduit_sdk import AgentServer

server = AgentServer(name="my-agent")

@server.on_prompt
async def answer(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    await ctx.send_text(f"You said: {text}")
    return "end_turn"

if __name__ == "__main__":
    server.run()
```

## conduit_sdk.Registry

```python
class Registry:
    def __init__(
        self,
        *,
        registry_url: str = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json",
        cache_dir: Path | str | None = None,
        cache_ttl: int = 3600,
    )
```

Client for the ACP agent registry.

**Parameters:**
- `registry_url` (str): URL of the registry JSON file
- `cache_dir` (Path | str | None): Local directory for caching
- `cache_ttl` (int): Time-to-live in seconds for cached file (default: 3600)

### Methods

**`await registry.fetch()`**

Fetch registry (uses cache when fresh, falls back to stale cache on network failure).

**`await registry.list_agents() -> list[AgentInfo]`**

All agents in the registry.

**`await registry.get_agent(agent_id) -> AgentInfo`**

Single agent (raises AgentNotFoundError if not found).

**`registry.search(keyword) -> list[AgentInfo]`**

Filter by ID/name/description (case-insensitive).

**`await registry.resolve_command(agent_id, *, prefer=None) -> tuple[list[str], dict[str, str]]`**

Resolve to shell command + env vars.

**Example:**
```python
registry = Registry()
await registry.fetch()

# List all agents
agents = await registry.list_agents()

# Search
results = registry.search("claude")

# Resolve to command
cmd, env = await registry.resolve_command("claude-acp", prefer="npx")
```

### AgentInfo (dataclass)

Fields:
- `id` (str)
- `name` (str)
- `version` (str)
- `description` (str)
- `repository` (str)
- `authors` (list[str])
- `license` (str)
- `icon` (str)
- `distribution` (dict)

### Helper Functions

**`detect_platform() -> str`**

Returns platform string like `"darwin-aarch64"`.

**`find_runtime(name) -> str | None`**

Find runtime on PATH.

## conduit_sdk.AgentOptions

```python
@dataclass
class AgentOptions:
    system_prompt: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    can_use_tool: Callable | None = None
    tools: list[str] | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    mcp_servers: dict[str, Any] | None = None
    max_turns: int | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    include_partial_messages: bool = False
    session_store: SessionStore | None = None
    hooks: dict | None = None
```

Comprehensive configuration for an ACP agent connection.

**Fields:**
- `system_prompt` (str | None): Custom system instructions
- `model` (str | None): Model identifier (e.g. `"claude-sonnet-4-20250514"`)
- `permission_mode` (str | None): `"default"`, `"acceptEdits"`, `"plan"`, `"bypassPermissions"`
- `can_use_tool` (Callable | None): Async callback for tool permission checks
- `tools` (list[str] | None): List of built-in tool names
- `allowed_tools` (list[str]): Tool name allowlist
- `disallowed_tools` (list[str]): Tool name blocklist
- `mcp_servers` (dict[str, Any] | None): MCP server configurations
- `max_turns` (int | None): Maximum conversation turns
- `cwd` (str | None): Working directory
- `env` (dict[str, str]): Environment variables
- `include_partial_messages` (bool): Stream events as they arrive
- `hooks` (dict | None): Lifecycle hook configuration
- `session_store` (SessionStore | None): Session persistence backend for durable session/update replay

### Methods

**`to_dict() -> dict`**

Serialize for control protocol.

**`to_meta_json() -> str | None`**

Serialize to ACP _meta JSON for NewSessionRequest.

**`to_mcp_servers_json() -> str | None`**

Serialize MCP server configs for NewSessionRequest.

**Example:**
```python
from conduit_sdk import AgentOptions

options = AgentOptions(
    system_prompt="You are a Python expert",
    model="claude-sonnet-4-20250514",
    permission_mode="ask",
    max_turns=10,
    allowed_tools=["read_file", "edit_file"],
)

async with await Client.from_registry("agent", options=options) as client:
    ...
```

## conduit_sdk.permissions

Permission types and built-in policies.

### Classes

**`PermissionResult`** — Base class for permission decisions.

**`PermissionResultAllow`** — Approve a tool use request.

**`PermissionResultDeny(reason="")`** — Deny a tool use request.

**`ToolPermissionContext`** — Context passed to permission callbacks.

Attributes:
- `tool_name` (str)
- `tool_input` (str): JSON string of input parameters
- `tool_use_id` (str | None)
- `session_id` (str | None)

### Preset Functions

**`await allow_all(tool_name, tool_input, context) -> PermissionResult`**

Preset: allow all tool use.

**`await deny_all(tool_name, tool_input, context) -> PermissionResult`**

Preset: deny all tool use.

**`await console_approve(tool_name, tool_input, context) -> PermissionResult`**

Preset: prompt user in console for each tool use.

**Example:**
```python
from conduit_sdk import permissions, AgentOptions

# Custom callback
async def can_use_tool(tool_name, tool_input, context):
    if tool_name.startswith("file.delete"):
        return permissions.PermissionResultDeny("deletions not allowed")
    return permissions.PermissionResultAllow()

options = AgentOptions(can_use_tool=can_use_tool)

# Or use presets
options = AgentOptions(can_use_tool=permissions.console_approve)
```

## conduit_sdk.HookRunner

```python
class HookRunner:
    def on(self, hook_type: HookType, *, priority: int = 0) -> Callable
    async def dispatch(self, hook_type: HookType, context: HookContext) -> HookContext
    def clear(self, hook_type: HookType | None = None) -> None
```

Manages lifecycle hooks for a client connection.

### HookType (enum)

- `PreToolUse` — Before tool execution
- `PostToolUse` — After tool execution
- `PromptSubmit` — When prompt is submitted
- `ResponseReceived` — When response is received
- `SessionCreated` — When session is created
- `SessionDestroyed` — When session is destroyed
- `Connected` — When client connects
- `Disconnected` — When client disconnects

### hook() Decorator

```python
@hook(HookType.PreToolUse, priority=0)
async def my_hook(ctx: HookContext) -> HookContext | None:
    ...
```

Standalone decorator (must be manually registered with a HookRunner).

**Example:**
```python
from conduit_sdk import HookType

@client.hooks.on(HookType.PreToolUse)
async def log_tool(ctx):
    print(f"Tool called: {ctx.get('tool_name')}")
    return ctx
```

## conduit_sdk.tool

```python
@tool(description="...")
async def my_tool(param: str) -> str:
    ...
```

Registers the function as a tool definition in the SDK.

**Note:** Tools are callable by agents via the in-process MCP HTTP server (started automatically during `connect()`).

**Parameters:**
- `name` (str | None): Tool name (defaults to function name)
- `description` (str): Human-readable description
- `input_schema` (dict | None): JSON Schema for input parameters

### Functions

**`create_mcp_server(name, tools=None) -> dict`**

Create MCP server config from registered tools.

**`create_sdk_mcp_server(name, *, version="1.0.0", tools=None) -> McpSdkServerConfig`**

Create SDK MCP server config from @tool-decorated functions.

### McpSdkServerConfig

Configuration for an SDK-hosted MCP server.

**Attributes:**
- `name` (str): Display name
- `version` (str): Version string
- `tools` (list[Callable]): @tool-decorated functions

**Methods:**
- `to_dict() -> dict`
- `get_tool_definitions() -> list[dict]`
- `get_tool_callback(tool_name) -> Callable | None`

**Example:**
```python
from conduit_sdk import tool, create_sdk_mcp_server, AgentOptions

@tool(description="Read a file from disk")
async def read_file(path: str) -> str:
    return open(path).read()

server = create_sdk_mcp_server("my-tools", tools=[read_file])
options = AgentOptions(mcp_servers={"my-tools": server})
```


## conduit_sdk.SessionStore

Protocol for session persistence backends.

```python
class SessionStore(Protocol):
    async def save_metadata(self, session_id, metadata) -> None
    async def append_update(self, session_id, update) -> None
    async def load_updates(self, session_id) -> list[dict]
    async def list_sessions(self) -> list[str]
    async def get_metadata(self, session_id) -> dict | None
    async def delete_session(self, session_id) -> None
    async def clear(self) -> None
```

### Implementations

**`conduit_sdk.InMemorySessionStore()`** — Process-local dict-backed store, no dependencies.

**`conduit_sdk.FileSessionStore(root)`** — Filesystem store; each session writes `<root>/<session>/metadata.json` + `events.jsonl`.

**`conduit_sdk.SqlSessionStore(async_engine)`** — SQLAlchemy 2.0 async. Use `sqlite+aiosqlite://` for tests, `postgresql+asyncpg://user:pass@host/db` for production.

**`conduit_sdk.RedisSessionStore(redis_client)`** — Redis-backed via `redis.asyncio`.

### Usage

Pass a store via `AgentOptions`:

```python
from conduit_sdk import AgentOptions, FileSessionStore

store = FileSessionStore("./sessions")
options = AgentOptions(session_store=store)
```

Each prompt's updates are automatically persisted. Replay later with `await store.load_updates(session_id)`.
## conduit_sdk.ProxyChain

```python
class ProxyChain:
    def add(self, proxy: Proxy) -> ProxyChain
    def insert(self, index: int, proxy: Proxy) -> ProxyChain
    async def build(self) -> None  # TODO: not yet implemented
    proxies -> list[Proxy]
```

Builder for composing an ordered chain of proxies.

**Note:** `build()` is not yet implemented (requires sacp-conductor integration).

### Built-in Proxies

**`ContextInjector(context: str)`**

Injects system context into prompts.

**`ResponseFilter(max_tokens: int)`**

Filters or truncates agent responses.

**Example:**
```python
from conduit_sdk import ProxyChain, ContextInjector

chain = ProxyChain()
chain.add(ContextInjector(context="Be concise."))
chain.add(ResponseFilter(max_tokens=1000))
# await chain.build()  # TODO: pending sacp-conductor
```

## conduit_sdk.types

### Message Types

**`Message`** — Response message.

Methods:
- `text() -> str`: Extract text content

Attributes:
- `role` (MessageRole): User or Assistant
- `content` (list[ContentBlock]): Content blocks

**`MessageRole`** — Enum: `User`, `Assistant`

### Content Blocks

**`ContentBlock`** — Base content block.

**`TextBlock(text: str)`** — Text content.

**`ThinkingBlock(thinking: str)`** — Thinking/reasoning content.

**`ToolUseBlock(id: str, name: str, input: dict)`** — Tool invocation.

**`ToolResultBlock(tool_use_id: str, content: str)`** — Tool result.

**`ImageBlock(data: str, mime_type: str)`** — Image for multi-modal prompts.

**`AudioBlock(data: str, mime_type: str)`** — Audio for multi-modal prompts.

**`ResourceLinkBlock(uri: str, name=None, description=None, mime_type=None)`** — Resource reference by URI.

**`EmbeddedResourceBlock(uri: str, text=None, mime_type=None, blob=None)`** — Full resource contents inline.

### Streaming Types

**`SessionUpdate`** — Raw streaming event.

Attributes:
- `kind` (UpdateKind): Event type

**`UpdateKind`** — Enum:
- `TextDelta`
- `ThoughtDelta`
- `ToolUseStart`
- `ToolUseUpdate`
- `ToolUseEnd`
- `ModeChange`
- `Plan`
- `ConfigUpdate`
- `CommandsUpdate`
- `Usage`
- `SessionInfo`
- `Done`
- `RateLimit`

**`StreamEvent`** — Detailed streaming event type.

**`PromptContent`** — Union type for prompt content (str, TextBlock, ImageBlock, etc.).

### Other Types

**`Capabilities`** — Agent capabilities from handshake.

**`ClientConfig`** — Client configuration (command, cwd, env, timeout).

**`ToolDefinition`** — Tool definition (name, description, parameters).

**`ToolSchema`** — Tool parameter schema builder.

```python
schema = ToolSchema(
    properties={"path": {"type": "string"}},
    required=["path"]
)
```

**`HookContext`** — Context dict for hooks.

Methods:
- `get(key, default=None) -> Any`
- `set(key, value) -> None`

**`PermissionRequest`** / **`PermissionResponse`**

**`ControlMessage`** / **`ControlResponse`** — Control protocol messages.

**`ResultMessage`** — Result message type.

**`RateLimitInfo`** — Rate limit notification data.

Attributes:
- `status` (str)
- `resets_at` (int): Unix timestamp
- `rate_limit_type` (str)
- `utilization` (float): 0.0–1.0
- `is_using_overage` (bool)
- `surpassed_threshold` (float)

**Example:**
```python
from conduit_sdk.types import ImageBlock, AudioBlock

# Multi-modal prompt
async for msg in session.prompt([
    "Describe this image:",
    ImageBlock(data=base64_image, mime_type="image/png"),
]):
    print(msg.text())
```

## Skills & Commands (Slash Commands)

Agents advertise available slash commands via `AvailableCommandsUpdate` streaming notifications. Skills are activated by sending the command text as a prompt.

See [Skills & Commands Guide](skills-and-commands.md) for the full explanation.

### Discovery

Available commands appear as `SessionUpdate` events with `kind == UpdateKind.CommandsUpdate`:

```python
import json
from conduit_sdk._conduit_sdk import UpdateKind

async for update in client.prompt_stream("Hello"):
    if update.kind == UpdateKind.CommandsUpdate:
        commands = json.loads(update.commands_json)
        for cmd in commands:
            print(f"{cmd['name']}: {cmd.get('description', '')}")
```

The `commands_json` field contains a JSON array of command objects with at minimum a `name` field.

### Activation (Low-Level)

Send the slash command as prompt text:

```python
# Activate /help
async for msg in client.prompt("/help"):
    print(msg.text())
```

### Activation (High-Level)

**`await client.activate_skill(command, *, session_id=None) -> str`**

Activate a single slash command and return the collected response text.
The `/` prefix is added automatically if missing.

```python
text = await client.activate_skill("/help")
text = await client.activate_skill("compact")  # auto-prefixed to /compact
```

**`await client.activate_skills(commands, *, session_id=None) -> list[SkillResult]`**

Activate multiple slash commands sequentially. Returns one `SkillResult` per command.
Errors are captured per-command without stopping the batch.

```python
from conduit_sdk import SkillResult

results = await client.activate_skills(["/help", "compact", "/cost"])
for r in results:
    print(f"{r.command}: {'OK' if r.success else r.error}")
```

Both methods are also available on `Session`:

```python
text = await session.activate_skill("/help")
results = await session.activate_skills(["/help", "/cost"])
```

### SkillResult

```python
@dataclass
class SkillResult:
    command: str           # Normalized command (with /)
    text: str = ""         # Response text (empty on failure)
    success: bool = True   # Whether the command completed
    error: str | None = None  # Error message if failed
```

### Relevant Types

- `UpdateKind.CommandsUpdate` — Identifies a commands update event
- `SessionUpdate.commands_json` (str | None) — JSON string of available commands
- `SkillResult` — Result from `activate_skill` / `activate_skills`
- Commands are agent-specific; always discover dynamically via streaming
## conduit_sdk.exceptions

Exception hierarchy:

```
ConduitError (base)
├── ConnectionError
├── SessionError
├── TransportError
├── ProtocolError
├── ToolError
├── HookError
├── ProxyError
├── TimeoutError
├── CancelledError
├── PermissionError
├── RegistryError
│   ├── AgentNotFoundError
│   ├── DistributionError
│   └── RuntimeNotFoundError
```

**Example:**
```python
from conduit_sdk import ConduitError, AgentNotFoundError

try:
    agent = await registry.get_agent("unknown-agent")
except AgentNotFoundError:
    print("Agent not found in registry")
except ConduitError as e:
    print(f"SDK error: {e}")
```
