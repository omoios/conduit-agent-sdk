# Background Agent SDK Seed

A hypothetical SDK for running safe, observable background coding agents.

This repository is a seed project. It is meant to be copied into a real codebase as the direction document, API sketch, event contract, test plan, and implementation structure for a background-agent SDK.

The SDK should eventually support:

- local mock runs
- fake process runs
- ACP stdio agents
- ACP conductor/proxy chains
- OpenCode-compatible agents
- Pi coding-agent wrapper runs
- cloud runs through a control plane
- sandboxed repo work
- approvals
- redaction
- diffs
- tests
- draft PRs
- audit events

The first implementation must be small: a TypeScript SDK with a mock adapter, standard events, and passing tests.

---

## Installation

Hypothetical package:

```bash
pnpm add @background-agent/sdk
```

For this seed repo, start with a workspace package:

```bash
pnpm install
pnpm test
```

---

## Quick start: local mock run

```ts
import { Agent, Runner, adapters, policy } from "@background-agent/sdk"

const agent = new Agent({
  name: "MockCoder",
  instructions: "You are a careful coding agent.",
})

const run = await Runner.start(agent, {
  task: "List files and say done.",
  workspace: { cwd: process.cwd() },
  policy: policy.readOnly(),
  adapter: adapters.mock({
    events: [
      { type: "run.started", summary: "Run started" },
      { type: "agent.message.delta", payload: { text: "I am inspecting files." } },
      { type: "tool.started", payload: { toolName: "list_files" } },
      { type: "tool.completed", payload: { toolName: "list_files", ok: true } },
      { type: "run.completed", summary: "Done" },
    ],
    result: {
      status: "completed",
      finalOutput: "Done.",
      changedFiles: [],
      tests: [],
      approvals: [],
      artifacts: [],
    },
  }),
})

for await (const event of run.events()) {
  console.log(event.sequence, event.type, event.summary ?? "")
}

const result = await run.result()
console.log(result.finalOutput)
```

---

## Quick start: OpenAI-style agent API

```ts
import {
  Agent,
  Runner,
  tool,
  policy,
  sandbox,
  adapters,
  proxy,
} from "@background-agent/sdk"

const coder = new Agent({
  name: "BackgroundCoder",
  model: "openai/gpt-5.5",
  instructions: `
    Make minimal, reviewable code changes.
    Inspect before editing.
    Run tests after editing.
    Never push directly to main.
  `,
  tools: [
    tool.readFiles(),
    tool.searchCode(),
    tool.editFiles(),
    tool.shell(),
    tool.git(),
    tool.githubPR(),
  ],
  policy: policy.draftPrOnly({
    requireApprovalFor: ["dependency.install", "migration.write", "git.push"],
    deny: ["read.env", "push.main"],
  }),
})

const run = await Runner.start(coder, {
  task: "Fix the failing login redirect test and open a draft PR.",
  workspace: {
    repo: "github:acme/web",
    branch: "agent/fix-login-redirect",
    environment: "node-22-postgres",
  },
  sandbox: sandbox.localDocker(),
  adapter: adapters.conductor({
    baseAgent: adapters.acp({ command: "opencode", args: ["acp"] }),
    proxies: [
      proxy.policy(),
      proxy.redaction(),
      proxy.eventNormalizer(),
      proxy.diffReview(),
    ],
  }),
})

for await (const event of run.events()) {
  render(event)
}

const result = await run.result()
console.log(result.pr?.url)
```

---

## Quick start: Claude-style streaming query

```ts
import { query } from "@background-agent/sdk"

for await (const event of query({
  prompt: "Inspect this repo and explain how to run it.",
  options: {
    cwd: process.cwd(),
    allowedTools: ["Read", "Grep", "Glob"],
    permissionMode: "readonly",
    adapter: "mock",
  },
})) {
  if (event.type === "agent.message.delta") {
    process.stdout.write(event.payload.text)
  }
}
```

---

## Quick start: cloud API client

```ts
import { BackgroundAgent } from "@background-agent/sdk"

const client = new BackgroundAgent({
  apiKey: process.env.BACKGROUND_AGENT_API_KEY!,
})

const run = await client.runs.create({
  agent: "background-coder",
  task: "Fix issue #481 and open a draft PR.",
  repo: "github:acme/web",
  environment: "node-22-postgres",
  mode: "draft-pr",
  policy: {
    requireApprovalFor: ["dependency.install", "migration.write"],
    deny: ["production-secret-access", "push.main"],
  },
})

for await (const event of client.runs.events(run.id)) {
  console.log(event.type, event.summary)
}

const result = await client.runs.result(run.id)
console.log(result.status, result.pr?.url)
```

---

## Core concepts

### Agent

Reusable instructions, tools, model preference, default policy, and optional handoffs.

### Runner

Starts a run from an agent plus task, workspace, sandbox, adapter, and policy.

### Run

A bounded execution attempt with status, events, approvals, cancellation, diff, tests, artifacts, and final result.

### Adapter

Execution backend. The public SDK should support at least:

- `adapters.mock()`
- `adapters.fakeProcess()`
- `adapters.acp()`
- `adapters.conductor()`
- `adapters.opencode()`
- `adapters.pi()`
- `adapters.custom()`

### Policy

Structured rules for allowed tools, denied actions, approval requirements, secrets, budgets, files, and commands.

### Event

A normalized record of what happened. UI, tests, webhooks, and audit logs should all consume the same event stream.

### Result

Structured evidence of the final outcome: status, summary, changed files, diff, tests, approvals, PR, artifacts, usage, and failure diagnosis.

---

## Standard events

Every event uses this envelope:

```ts
type AgentEvent = {
  id: string
  type: string
  runId: string
  workspaceId?: string
  tenantId?: string
  sequence: number
  timestamp: string
  source: "sdk" | "adapter" | "proxy" | "agent" | "sandbox" | "controller" | "server"
  traceId?: string
  spanId?: string
  redactionStatus: "none" | "redacted" | "blocked" | "unknown"
  summary?: string
  payload: unknown
}
```

Common events:

```txt
run.started
agent.message.delta
tool.started
tool.completed
command.started
command.output
command.completed
diff.preview_created
diff.applied
approval.required
approval.approved
approval.rejected
test.started
test.completed
pr.opened
run.completed
run.failed
```

See `docs/EVENTS.md` for the full catalog.

---

## What counts as working

### Mock SDK works when

- A run can start.
- Events stream in order.
- Result resolves.
- Failure result resolves cleanly.

### Fixture repo works when

- The agent fixes `examples/buggy-calculator/src/add.ts`.
- Diff includes `return a + b`.
- Tests pass with exit code `0`.
- Result includes changed files and test evidence.

### Approval works when

- Risky action emits `approval.required`.
- Run pauses.
- `approve()` continues.
- `reject()` blocks safely.

### Redaction works when

- Event text does not contain known secret values.
- Platform API keys never enter sandbox inputs.

### Conductor works when

- Conductor and proxies can be inserted without changing user-facing SDK code.
- Normalized event output remains stable.

---

## First build target

Build only this first:

```txt
packages/sdk/src/
  index.ts
  agent.ts
  runner.ts
  run.ts
  events.ts
  result.ts
  policy.ts
  adapters/mock.ts

tests/
  mock-run.test.ts
  event-order.test.ts
  result.test.ts
```

Do not build real sandboxing, GitHub, Pi, OpenCode, or ACP before the mock SDK contract is passing.

---

## Documentation map

- `SOURCE.md` - strategy, purpose, end API, standards, alternatives, criteria.
- `AGENTS.md` - instructions for coding agents working in this repo.
- `STRUCTURE.md` - repository structure and evolution path.
- `docs/EVENTS.md` - event envelope and catalog.
- `docs/API.md` - public SDK API design.
- `docs/IMPLEMENTATION.md` - smallest implementation and test strategy.
- `docs/DECISIONS.md` - decisions and alternatives.
- `docs/REFERENCES.md` - docs to read before changing architecture.

---

## North star

The user-facing SDK should make this feel true:

```txt
I define the task.
I define the policy.
I choose the workspace.
I watch evidence stream in.
I approve risky actions.
I receive a result with proof.
```

Everything else is implementation detail.
