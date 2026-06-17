# AGENTS.md

Instructions for AI coding agents working on this repository.

This project is a seed for a background coding-agent SDK. Your job is to preserve the direction, implement in small working slices, and never replace the public SDK with raw protocol plumbing.

---

## 1. Project mission

Build a TypeScript SDK that lets developers start, observe, steer, approve, cancel, and collect evidence from background coding-agent runs.

The SDK should eventually support ACP, conductor/proxy chains, OpenCode, Pi, Claude Agent SDK style streaming, OpenAI Agents SDK style orchestration, and cloud control-plane runs.

The first working version must be much smaller: a mock adapter, standard events, a run object, and passing tests.

---

## 2. Non-negotiable rules

1. Do not expose raw ACP commands as the primary public API.
2. Do not make the SDK chat-only. The core object is `Run`.
3. Do not add new event names without updating `docs/EVENTS.md` and the event type union.
4. Do not mark work complete unless tests prove the behavior.
5. Do not pass platform API keys into sandbox processes.
6. Do not print secrets in logs, events, test output, or snapshots.
7. Do not rely on production auto-discovery of tools, hooks, skills, or context files.
8. Do not skip the mock adapter. It is the foundation for every later adapter.
9. Do not collapse policy into prompts. Policy must be structured and testable.
10. Do not create half-finished slices. Every phase should be demoable.

---

## 3. Preferred implementation order

Build in this order:

1. Types and docs.
2. `Agent` class.
3. `Runner.start()`.
4. `Run.events()` async iterable.
5. `Run.result()`.
6. Standard event envelope.
7. Mock adapter.
8. Event order tests.
9. Result collector tests.
10. Fake process adapter.
11. ACP stdio adapter.
12. Conductor adapter.
13. Policy proxy.
14. Redaction proxy.
15. Local fixture repo proof.
16. Local Docker proof.
17. GitHub draft PR proof.
18. Environment setup flow.

---

## 4. Public API design constraints

The API should feel like this:

```ts
const agent = new Agent({
  name: "BackgroundCoder",
  instructions: "Make minimal, reviewable code changes.",
})

const run = await Runner.start(agent, {
  task: "Fix the failing test.",
  workspace: { cwd: process.cwd() },
  adapter: adapters.mock.success(),
  policy: policy.safeLocal(),
})

for await (const event of run.events()) {
  render(event)
}

const result = await run.result()
```

The API should not force users to write this:

```ts
sendJsonRpc("session/prompt", ...)
```

ACP and conductor are implementation details behind adapters.

---

## 5. Event rules

Every event must have:

```ts
{
  id: string,
  type: string,
  runId: string,
  sequence: number,
  timestamp: string,
  source: string,
  redactionStatus: string,
  payload: unknown,
}
```

Events must be safe for:

- UI rendering
- database storage
- webhook delivery
- audit logs
- test assertions
- replay

Never emit hidden chain-of-thought. Use `agent.thought_summary` only for safe summaries.

---

## 6. Testing requirements

A feature is not complete unless there is a test proving it.

Minimum tests:

```txt
mock-run.test.ts
  starts run
  streams ordered events
  resolves result

failure.test.ts
  emits run.failed
  resolves failed result

approval.test.ts
  pauses on approval.required
  approve continues
  reject blocks

redaction.test.ts
  removes known secret strings

event-schema.test.ts
  every fixture event matches envelope
```

Later tests:

```txt
fake-process-adapter.test.ts
acp-adapter.test.ts
conductor-adapter.test.ts
fixture-buggy-calculator.test.ts
local-docker.test.ts
github-draft-pr.test.ts
```

---

## 7. Definition of done

For any slice, provide:

- code
- tests
- docs update
- example usage
- explicit evidence of passing behavior

For SDK slices, evidence is usually:

- event sequence
- final result object
- passing test output

For real coding-agent slices, evidence is:

- changed files
- diff
- test command and exit code
- approvals if any
- final summary

---

## 8. File ownership guidance

- Public SDK API lives in `packages/sdk/src`.
- Runtime-agnostic core lives in `packages/core/src` once the repo grows.
- Adapter-specific code lives in `packages/adapters/*`.
- React hooks live in `packages/react`.
- Server/cloud client helpers live in `packages/server`.
- Fixtures live in `examples` or `fixtures`.
- Docs are product-contract files. Update them with code changes.

---

## 9. Security rules

Never log:

- platform API keys
- provider API keys
- GitHub installation tokens
- database URLs
- `.env` contents
- private keys
- webhook secrets

The platform API key is a machine identity for the control plane. It must never be passed to the sandbox.

The sandbox should receive only run-scoped credentials, such as:

- run token
- GitHub installation token
- provider runtime credential or gateway token
- scoped environment secrets

---

## 10. Adapter rules

### Mock adapter

Must be deterministic and fast. It should be the default test adapter.

### Fake process adapter

Must parse newline-delimited JSON events and handle process failures.

### ACP adapter

Must hide JSON-RPC behind normalized SDK events.

### Conductor adapter

Must preserve the same public SDK surface as direct ACP. Adding conductor should not require changing user app code.

### Pi adapter

Must treat Pi as an inner harness. The platform/SDK controls policy, events, secrets, and run records.

### OpenCode adapter

Can use OpenCode ACP mode or native SDK if that gives cleaner events and control.

---

## 11. References to check before changing architecture

Search official docs first.

- OpenAI Agents SDK: `https://developers.openai.com/api/docs/guides/agents`
- OpenAI Agents JS: `https://openai.github.io/openai-agents-js/guides/agents/`
- Claude Agent SDK: `https://code.claude.com/docs/en/agent-sdk/overview`
- Claude Agent SDK permissions: `https://code.claude.com/docs/en/agent-sdk/permissions`
- ACP overview: `https://agentclientprotocol.com/protocol/v1/overview`
- ACP Rust SDK: `https://github.com/agentclientprotocol/rust-sdk`
- ACP conductor docs: `https://docs.rs/agent-client-protocol-conductor/latest/agent_client_protocol_conductor/`
- OpenCode SDK: `https://opencode.ai/docs/sdk/`
- OpenCode ACP: `https://opencode.ai/docs/acp/`
- OpenCode plugins: `https://opencode.ai/docs/plugins/`
- Pi coding-agent examples: `https://github.com/can1357/oh-my-pi/tree/main/packages/coding-agent/examples/sdk`

---

## 12. When unsure

Prefer the smaller working slice.

A mock SDK with stable events is better than a half-integrated real agent.

A local fixture that proves a diff and passing test is better than a broad cloud demo that cannot be reproduced.

A clean adapter interface is better than leaking one harness into the public API.
