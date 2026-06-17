# Implementation Plan

This document describes how to build the smallest working SDK and then iterate toward real agent execution.

---

## 1. Build philosophy

The SDK should be implemented as a stable runtime core plus replaceable adapters.

Start with deterministic tests. Add real processes only after the mock adapter proves the event and result contracts.

---

## 2. Smallest working implementation

Files:

```txt
packages/sdk/src/index.ts
packages/sdk/src/agent.ts
packages/sdk/src/runner.ts
packages/sdk/src/run.ts
packages/sdk/src/events.ts
packages/sdk/src/result.ts
packages/sdk/src/policy.ts
packages/sdk/src/adapters/mock.ts
packages/sdk/tests/mock-run.test.ts
packages/sdk/tests/event-order.test.ts
packages/sdk/tests/result.test.ts
```

### `events.ts`

Defines:

- `AgentEvent`
- `RunStatus`
- event type union
- event envelope validator
- sequence helper

### `agent.ts`

Stores:

- name
- instructions
- tools
- model
- default policy

No model calls.

### `runner.ts`

Responsibilities:

1. Create `runId`.
2. Compile agent + input.
3. Start adapter.
4. Create `AgentRun` object.
5. Connect event stream to result collector.

### `run.ts`

Responsibilities:

- expose `events()`
- expose `result()`
- expose `cancel()`
- later expose `approve()`, `reject()`, `steer()`

### `adapters/mock.ts`

Emits fixture events and returns fixture result.

---

## 3. Event stream implementation

Use an async queue internally.

```ts
class AsyncEventQueue<T> {
  push(event: T): void
  close(): void
  fail(error: Error): void
  [Symbol.asyncIterator](): AsyncIterator<T>
}
```

`Runner.start()` should create a queue, give `emit()` to adapter, and expose queue through `run.events()`.

---

## 4. Result collector

The result collector observes events and adapter final result.

For mock adapter, result is given directly.

For real adapters, result collector can infer:

- changed files from diff events
- tests from test events
- approvals from approval events
- PR from `pr.opened`
- failure from `run.failed`

---

## 5. First tests

### Mock run test

```ts
it("streams events and resolves result", async () => {
  const run = await Runner.start(agent, {
    task: "Say done",
    workspace: { cwd: process.cwd() },
    adapter: adapters.mock.success(),
  })

  const events = []
  for await (const event of run.events()) {
    events.push(event)
  }

  const result = await run.result()

  expect(events.map(e => e.type)).toEqual([
    "run.started",
    "agent.message.delta",
    "tool.started",
    "tool.completed",
    "run.completed",
  ])
  expect(result.status).toBe("completed")
})
```

### Event envelope test

Every mock fixture event must include:

- id
- type
- runId
- sequence
- timestamp
- source
- redactionStatus
- payload

### Failure test

A mock failure should emit `run.failed` and resolve a failed result.

---

## 6. Fake process adapter

Add after mock tests pass.

Input:

```ts
adapters.fakeProcess({
  command: "node",
  args: ["fixtures/fake-agent/emit-success.js"],
})
```

Fake process outputs NDJSON:

```json
{"type":"run.started","summary":"Started"}
{"type":"agent.message.delta","payload":{"text":"Hello"}}
{"type":"run.completed","summary":"Done"}
```

The adapter should:

- spawn process
- parse stdout lines as JSON events
- map them into event envelope
- capture stderr
- emit `run.failed` on non-zero exit if no terminal event occurred

---

## 7. ACP adapter

Add after fake process adapter passes.

Responsibilities:

- start ACP process over stdio
- send JSON-RPC initialize
- create or load session
- send prompt
- listen for updates
- handle permission requests
- map raw events to standard events
- handle cancellation

Do not expose ACP methods to public SDK users.

---

## 8. Conductor adapter

Add after ACP direct adapter passes.

Responsibilities:

- start conductor
- configure base ACP agent
- configure proxies
- ensure SDK still sees a single run
- normalize output to the same events as direct ACP

Conductor should be an adapter detail:

```ts
adapters.conductor({
  baseAgent: adapters.acp({ command: "opencode", args: ["acp"] }),
  proxies: [proxy.policy(), proxy.redaction(), proxy.eventNormalizer()],
})
```

---

## 9. Policy and approval implementation

Policy should evaluate actions before execution where possible.

Inputs:

- tool name
- command string
- file path
- network host
- secret scope
- git operation

Possible decisions:

```ts
type PolicyDecision =
  | { kind: "allow" }
  | { kind: "deny"; reason: string }
  | { kind: "approval_required"; reason: string; risk: "low" | "medium" | "high" }
```

Approval flow:

1. Adapter/proxy asks policy.
2. Policy returns `approval_required`.
3. SDK emits `approval.required`.
4. Run enters `awaiting_approval`.
5. User calls `run.approve()` or `run.reject()`.
6. Adapter/proxy continues or blocks.

---

## 10. Redaction implementation

Redaction must run before events reach:

- console
- UI
- webhooks
- storage
- tests snapshots

Redact:

- exact secret values
- common API key patterns
- database URLs
- private keys
- GitHub tokens
- provider keys

Output event should set:

```ts
redactionStatus: "redacted"
```

If an action is blocked because it would reveal a secret:

```ts
redactionStatus: "blocked"
```

---

## 11. Local fixture proof

Add `examples/buggy-calculator`.

Bug:

```ts
export function add(a: number, b: number) {
  return a - b
}
```

Test:

```ts
expect(add(2, 3)).toBe(5)
```

The first real coding-agent proof passes only when:

- file changes from subtraction to addition
- test passes
- diff is captured
- result includes changed file

---

## 12. Docker proof

After local fixture passes without Docker, move execution into Docker.

The test should prove:

- workspace is mounted
- command runs inside container
- events stream to host
- sandbox is cleaned up
- secrets are redacted

---

## 13. GitHub proof

After Docker passes, add GitHub.

The test should prove:

- installation token clones repo
- branch is created
- changed file is committed
- draft PR opens
- result includes PR URL

---

## 14. Environment setup proof

Implement three roles:

- detective: read-only repo inspection
- builder: install/test/build with policy hooks
- verifier: independent pass/fail confidence report

The proof passes when:

- unknown repo produces proposed profile
- validation command runs
- profile is saved with validation status

---

## 15. What not to build first

Do not build these before the mock SDK passes:

- model gateway
- full auth system
- production UI
- GitHub App integration
- Docker sandbox
- ACP conductor
- Pi wrapper
- billing
- memory

They depend on the event and run contracts.
