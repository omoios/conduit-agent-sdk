# AGENT INTRO \u2014 Background Agent SDK Seed

> Read this first. It orients you to the seed, relates it to the code already in
> this repo (`conduit-agent-sdk`), and tells you what to do next.

## What this seed is

`docs/seed/background-agent-sdk-seed/` is the **north-star design** for a
*Background Agent SDK* \u2014 a hypothetical TypeScript SDK for running **safe,
observable, long-running background coding-agent runs**. It is deliberately

- **direction-first**, not an implementation. It is a portable source of truth
  (purpose \u2192 customer outcome \u2192 API shape \u2192 implementation proof).
- **run-first, not chat-first.** The central object is a bounded `Run` that
  produces a normalized event stream and a final `Result` full of evidence
  (diffs, tests, approvals, artifacts, PR, cost, audit) \u2014 not a chat log.
- **adapter-pluggable.** `mock`, `fakeProcess`, `acp`, `conductor`, `opencode`,
  `pi`, `custom` are interchangeable execution backends behind one API.
- **policy-first-class.** Approvals, denies, budgets, secrets, file/command
  restrictions are structured, not prompt-only.

The seed assumes a TS package (`@background-agent/sdk`). This repo is
`conduit-agent-sdk` (Python + Rust). **The seed is the vision; this repo is the
runtime substrate it should sit on.**

## Reading order (start here)

1. `SOURCE.md` \u2014 the full design memo. Read sections 1\u20137, then the
   principles (\u00a75) and end-state API (\u00a76).
2. `README.md` \u2014 quick starts (mock run, OpenAI-style, Claude-style,
   cloud client). These are the API surfaces to target.
3. `AGENTS.md` \u2014 agent-facing build instructions.
4. `STRUCTURE.md` \u2014 proposed module layout.
5. `docs/EVENTS.md` \u2014 the normalized event contract (the product surface).
6. `docs/API.md`, `docs/IMPLEMENTATION.md`, `docs/DECISIONS.md`,
   `docs/REFERENCES.md` \u2014 deeper detail and rationale.

## What `conduit-agent-sdk` already provides (the substrate)

The lower layers the seed assumes are **already built and tested** here:

| Seed concept | conduit-agent-sdk status |
|---|---|
| ACP transport / client | ✅ Rust core on `agent-client-protocol` 0.14 (`Client`, `prompt`, streaming, permissions, rich content, session/delete) |
| ACP agent/server side | ✅ `AgentServer` (author agents in Python; client↔agent loopback verified) |
| Adapters: `acp` | ✅ the `Client` IS the ACP adapter; also wrapped by `runlayer.acp_adapter` |
| Adapters: `mock` | ✅ `runlayer.mock_adapter` (deterministic; ships+tests first per seed principle 6) |
| Adapters: `conductor` / proxy chains | 🟡 `conductor_command()` builds the `[conductor, "agent", ...proxies, base]` wrap; **live subprocess chaining needs the external `agent-client-protocol-conductor` binary** (not bundled) |
| Tools / MCP | ✅ `@tool` + `@constrained_tool` (JSON-Schema output validation) + in-process `McpSdkServerConfig` (HTTP); agent calls SDK tools over MCP (loopback verified) |
| Session persistence / replay | ✅ `SessionStore` (InMemory/File/SQL/Redis) with verified Postgres + Redis integration suites |
| Elicitation (unstable) | ✅ `elicitation/create` client-handler routing + agent `request_elicitation`; loopback verified |
| Hooks | ✅ matcher + priority + timeout + blocking; 15 Claude-SDK event types |
| Redaction (proxy stage) | ✅ `redaction.redact_events` over the normalized event stream (`DEFAULT_SECRET_PATTERNS`) |
| Policy | 🟡 partial (`AgentOptions`, permission callbacks, `permission_mode`, blocking hooks); NOT the seed's structured `policy.*` (`requireApprovalFor`/`deny`/budgets) |
| Normalized events | ✅ `runlayer.AgentEvent` envelope (per `docs/EVENTS.md`); all 14 `UpdateKind` mapped |
| Run / Runner / Result | ✅ `runlayer.Run` / `Runner` / `Result` (mock + acp adapters) |
| Evidence: diffs / tests / artifacts / PR | ✗ not yet — `Result` carries status/summary/error but no captured evidence |
| Sandbox / `approval.*` events / fakeProcess+opencode+pi adapters / cloud client | ✗ not yet |

## Your job (when you pick this up)

Build the **Run layer** on top of the substrate, in priority order:

1. **Normalized event envelope** (`docs/EVENTS.md`) wrapping the existing ACP
   `session/update` stream \u2014 every UI/test/webhook/audit consumer uses it.
2. **`Run` + `Runner`** \u2014 a bounded execution that emits the event stream
   and resolves a `Result`.
3. **`Adapter` abstraction** with `mock` (ship+test first, per seed principle 6)
   and `acp` (wrap the existing `Client`).
4. **Structured `Policy`** (`requireApprovalFor`, `deny`, budgets, secrets)
   over the existing permission hooks.
5. **`conductor`/proxy adapter** \u2014 implement the scaffolded proxy chain.
6. Evidence capture: diffs, tests, artifacts, PR \u2192 `Result`.

Hard rules while doing this:
- **Mock before real.** The SDK must pass tests with `adapters.mock()` before
  any real agent is wired (seed principle 6).
- **Evidence over vibes.** Each milestone ships an objective passing test.
- **Public API stable, internals evolve** (seed principle 10).
- **Adapt tests, never delete them** (`.omp/rules/preserve-tests-adapt-dont-delete.md`).

## Map: seed adapter \u2192 this repo

- `adapters.acp(...)` \u2192 `conduit_sdk.Client(...)` (already works).
- `adapters.conductor({ baseAgent, proxies })` \u2192 the unimplemented
  `RustProxyChain` + a new `conductor` composition that wraps an `acp` adapter.
- `adapters.mock(...)` \u2192 new; replay a canned event list (great first adapter).
- `proxy.policy()` / `proxy.redaction()` / `proxy.eventNormalizer()` /
  `proxy.diffReview()` \u2192 proxy-chain stages over the ACP connection.
- `query({...})` (Claude-style streaming) \u2192 thin sugar over `Client.prompt_stream`.
