# References and Research Map

Accessed: 2026-06-17

Use these references before changing the SDK direction. Prefer official docs over blog posts or guesses.

---

## OpenAI Agents SDK

Search for:

```txt
OpenAI Agents SDK Agent Runner tools handoffs guardrails sessions tracing
```

Primary docs:

- `https://developers.openai.com/api/docs/guides/agents`
- `https://openai.github.io/openai-agents-js/guides/agents/`

Use for:

- `Agent`
- `Runner`
- tools
- handoffs
- guardrails
- sessions
- tracing
- app-owned orchestration

---

## Claude Agent SDK

Search for:

```txt
Claude Agent SDK query permissions hooks tools sessions checkpointing OpenTelemetry
```

Primary docs:

- `https://code.claude.com/docs/en/agent-sdk/overview`
- `https://code.claude.com/docs/en/agent-sdk/permissions`

Use for:

- streaming `query()` shape
- allowed tools
- permissions
- hooks
- session behavior
- checkpointing ideas
- telemetry ideas

---

## Agent Client Protocol

Search for:

```txt
Agent Client Protocol JSON-RPC session prompt update permissions file operations
```

Primary docs:

- `https://agentclientprotocol.com/protocol/v1/overview`
- `https://github.com/agentclientprotocol/rust-sdk`

Use for:

- ACP adapter implementation
- JSON-RPC methods
- session lifecycle
- prompts
- updates
- permission requests
- file operations
- cancellation

---

## ACP conductor and proxy chains

Search for:

```txt
ACP conductor proxy chains rust sdk agent-client-protocol-conductor
```

Primary docs:

- `https://docs.rs/agent-client-protocol-conductor/latest/agent_client_protocol_conductor/`
- `https://agentclientprotocol.github.io/rust-sdk/conductor.html`

Use for:

- conductor adapter
- proxy chain design
- process orchestration
- message routing
- policy/redaction/event normalization proxies

---

## OpenCode

Search for:

```txt
OpenCode SDK ACP plugins hooks
```

Primary docs:

- `https://opencode.ai/docs/sdk/`
- `https://opencode.ai/docs/acp/`
- `https://opencode.ai/docs/plugins/`

Use for:

- OpenCode-native adapter
- ACP mode
- plugins/hooks
- local coding-agent execution

---

## Pi coding-agent SDK

Search for:

```txt
@oh-my-pi/pi-coding-agent createAgentSession hooks skills contextFiles sessionManager
```

Primary docs/source:

- `https://github.com/can1357/oh-my-pi/tree/main/packages/coding-agent/examples/sdk`

Use for:

- Pi wrapper
- programmatic coding-agent sessions
- explicit tool filtering
- hooks
- skills
- context files
- session management
- event bridge

---

## Project-specific docs to read first

Before implementing, read:

1. `SOURCE.md`
2. `README.md`
3. `AGENTS.md`
4. `STRUCTURE.md`
5. `docs/EVENTS.md`
6. `docs/API.md`
7. `docs/IMPLEMENTATION.md`
8. `docs/DECISIONS.md`

---

## Search queries for future chats

Use these exact prompts in future chats when you need current details:

```txt
Review the current OpenAI Agents SDK docs for Agent, Runner, tools, guardrails, handoffs, sessions, tracing. Summarize only details that affect this SDK design.
```

```txt
Review the current Claude Agent SDK docs for query streaming, permissions, hooks, tool allow/deny, sessions, checkpointing, and telemetry. Map them to this SDK.
```

```txt
Review the Agent Client Protocol v1 overview and conductor/proxy docs. Tell me what a TypeScript adapter needs to implement without leaking ACP to the public API.
```

```txt
Review OpenCode SDK, ACP, and plugins docs. Tell me whether to integrate through ACP or native SDK first.
```

```txt
Review the Pi coding-agent SDK examples. Tell me how to compile a run contract into explicit model, tools, hooks, skills, context files, and session manager config.
```
