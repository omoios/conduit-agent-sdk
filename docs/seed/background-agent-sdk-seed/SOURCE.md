# Background Agent SDK Source Document

Status: seed design document
Date: 2026-06-17
Audience: founder, implementation agents, future chats, SDK maintainers
Project name used in examples: `@background-agent/sdk`

This document is the portable source of truth for a background coding-agent SDK. It is written in the style of a pre-build executive design memo: purpose first, customer outcome second, API shape third, implementation proof fourth.

The SDK should let a developer safely start, observe, steer, approve, and collect evidence from long-running coding agents. Internally it may use ACP, ACP conductor/proxies, OpenCode, Pi, Claude Agent SDK, OpenAI Agents SDK concepts, or custom adapters. Externally it should feel like a clean agent runtime.

---

## 1. One-sentence thesis

Build a TypeScript SDK for safe background coding-agent runs where users define a task, workspace, policy, sandbox, adapter, and expected evidence, then receive a normalized event stream and final result.

The public SDK must hide protocol ugliness while preserving observability, approvals, diffs, tests, artifacts, cost, and auditability.

---

## 2. Press release from the future

`@background-agent/sdk` gives teams a simple way to run coding agents in the background without building their own orchestration, policy layer, event system, sandbox abstraction, or PR workflow.

Developers can run locally:

```ts
import { Agent, Runner, adapters, policy } from "@background-agent/sdk"

const coder = new Agent({
  name: "LocalCoder",
  instructions: "Make minimal, reviewable code changes.",
})

const run = await Runner.start(coder, {
  task: "Inspect this repo and explain how to run the tests.",
  workspace: { cwd: process.cwd() },
  adapter: adapters.mock.success(),
  policy: policy.readOnly(),
})

for await (const event of run.events()) {
  console.log(event.type, event.summary ?? "")
}

console.log(await run.result())
```

And later run in the cloud:

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
    requireApprovalFor: ["dependency.install", "migration.write", "git.push"],
    deny: ["production-secret-access", "push.main", "read.env"],
  },
})

for await (const event of client.runs.events(run.id)) {
  render(event)
}

const result = await client.runs.result(run.id)
console.log(result.pr?.url)
```

---

## 3. Customer problem

Coding-agent products are powerful but hard to make safe. The hard parts are not only model calls. The hard parts are:

- repo checkout
- sandbox lifecycle
- environment setup
- tool execution
- permission decisions
- secret handling
- diff review
- event streaming
- cancellations
- PR creation
- audit trail
- repeatable tests
- customer-facing proof

The SDK should package the runtime boundary so product builders can start with a mock event stream and eventually plug in real ACP/OpenCode/Pi/Claude/OpenAI-powered execution without changing the app-facing API.

---

## 4. Customer motivation and behavior story

Use Motivation-Driven Behavior Stories for every major SDK feature.

### Customer motivation

When the user is responsible for a repo, client project, CI failure, or internal developer platform, they are motivated by safe delegation. They want to avoid broken repos, leaked secrets, unreviewed changes, hallucinated progress, and untraceable automation.

They are trying to achieve a reviewable outcome: a report, a passing test, a diff, a draft PR, an environment profile, or a failure diagnosis.

So they need an SDK that turns agent work into trusted evidence.

### Behavior story

Given a workspace, repo, task, policy, adapter, and sandbox,
when the developer starts an agent run,
then the SDK should create a run, stream standard events, enforce policy, handle approvals, capture diffs/tests/artifacts, and return a final result,
and the result should prove what happened without exposing secrets or raw protocol details.

### Evidence that matters

A run is not successful merely because the agent says it is done. A successful run produces evidence:

- ordered events
- tool cards
- terminal output
- diff preview
- tests and exit codes
- approvals and decisions
- changed files
- cost/usage if available
- PR URL if opened
- final summary
- failure diagnosis if failed
- audit trail

---

## 5. Product principles

1. Public API first. Do not design from ACP method names outward.
2. Run-first, not chat-first. A background coding agent is a long-running run with evidence.
3. Events are the product surface. Every UI, test, webhook, and audit record depends on normalized events.
4. Adapters are replaceable. ACP, conductor, OpenCode, Pi, Claude, OpenAI, and mock execution are adapter choices.
5. Policy is first-class. Approvals, denies, budgets, secrets, and file restrictions are not prompt-only features.
6. Mock before real. The smallest working SDK must pass with a mock adapter before any real agent is integrated.
7. No raw platform key in a sandbox. Runtime credentials must be scoped and short-lived.
8. No production auto-discovery. Production runs must compile explicit tools, hooks, skills, context files, and policies.
9. Evidence over vibes. Every milestone needs an objective passing test.
10. Keep the public API stable while internals evolve.

---

## 6. End-state SDK shape

The SDK exposes two primary public faces over the same runtime.

### 6.1 OpenAI-style API

This is for developers who like `Agent`, `Runner`, tools, guardrails/policies, and handoffs.

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
  model: "anthropic/claude-sonnet-4.5",
  instructions: `
    You are a careful coding agent.
    Make minimal, reviewable changes.
    Run tests when you edit code.
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
    requireApprovalFor: [
      "git.push",
      "dependency.install",
      "migration.write",
      "workflow.edit",
    ],
    deny: ["read.env", "push.main", "delete.repo"],
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
    baseAgent: adapters.acp({
      command: "opencode",
      args: ["acp"],
    }),
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
console.log(result.status)
console.log(result.changedFiles)
console.log(result.tests)
console.log(result.pr?.url)
```

### 6.2 Claude-style streaming API

This is for scripts, CLIs, and lightweight automation.

```ts
import { query } from "@background-agent/sdk"

for await (const event of query({
  prompt: "Inspect this repo and tell me how to run it.",
  options: {
    cwd: process.cwd(),
    adapter: "mock",
    allowedTools: ["Read", "Grep", "Glob"],
    permissionMode: "readonly",
    maxTurns: 20,
  },
})) {
  if (event.type === "agent.message.delta") {
    process.stdout.write(event.payload.text)
  }
}
```

### 6.3 Cloud client API

This is for product companies, CI systems, agencies, and platform teams.

```ts
import { BackgroundAgent } from "@background-agent/sdk"

const client = new BackgroundAgent({
  apiKey: process.env.BACKGROUND_AGENT_API_KEY!,
})

const run = await client.runs.create({
  agent: "background-coder",
  task: "Analyze this repo and propose an environment profile.",
  repo: "github:acme/web",
  mode: "environment-detect",
  policy: {
    permissionMode: "readonly",
    deny: ["bash", "edit", "read.env"],
  },
})

for await (const event of client.runs.events(run.id)) {
  console.log(event.sequence, event.type)
}
```

---

## 7. Core concepts

### Agent

Reusable instructions, model preference, tool set, policy defaults, and optional handoffs.

### Runner

The thing that starts a run. It compiles the task, agent, workspace, adapter, sandbox, and policy into a run contract.

### Run

A bounded execution attempt with a status, events, approvals, artifacts, diff, tests, and result.

### Adapter

A bridge to an execution backend: mock, fake process, ACP stdio, ACP conductor, OpenCode-native, Pi wrapper, Claude Agent SDK, OpenAI Agents SDK, or custom.

### Proxy

A middleware layer around an ACP-compatible agent or another adapter. Common proxies: policy, redaction, event normalization, tracing, approvals, diff review.

### Policy

A structured set of permissions, denies, approval requirements, budgets, secret scopes, and file/command restrictions.

### Workspace

The working directory, repo, branch, environment profile, context files, secrets, and runtime metadata.

### Result

The final structured output: status, summary, changed files, diff, test results, approvals, PR link, artifacts, usage, trace URL, and failure diagnosis.

---

## 8. Standard event envelope

All events use the same envelope.

```ts
export type AgentEventEnvelope<TType extends string = string, TPayload = unknown> = {
  id: string
  type: TType
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
  payload: TPayload
}
```

Required rules:

- `sequence` is strictly increasing per run.
- `timestamp` is ISO 8601.
- `type` must be from the standard catalog or explicitly marked experimental.
- Secrets must not appear in `summary` or `payload` after redaction.
- Every event must be safe to store in an audit log after redaction.
- Raw adapter events may be retained in debug mode but must not be the default UI/API surface.

---

## 9. Standard event catalog

### Run lifecycle

- `run.created`
- `run.queued`
- `run.allocating`
- `run.booting`
- `run.bootstrapping`
- `run.started`
- `run.status_changed`
- `run.heartbeat`
- `run.awaiting_approval`
- `run.cancelling`
- `run.cancelled`
- `run.timed_out`
- `run.completed`
- `run.failed`
- `run.cleanup_started`
- `run.destroyed`

### Agent output

- `agent.message.delta`
- `agent.message.completed`
- `agent.plan.created`
- `agent.plan.updated`
- `agent.subtask.started`
- `agent.subtask.completed`
- `agent.thought_summary`

`agent.thought_summary` must be a safe summary, not hidden chain-of-thought.

### Tools

- `tool.started`
- `tool.completed`
- `tool.failed`
- `tool.result_modified`

### Commands

- `command.started`
- `command.output`
- `command.completed`
- `command.failed`

### Files and diffs

- `file.read`
- `file.write_requested`
- `file.edited`
- `diff.preview_created`
- `diff.updated`
- `diff.applied`
- `diff.discarded`

### Approvals

- `approval.required`
- `approval.approved`
- `approval.rejected`
- `approval.expired`
- `approval.bypass_denied`

### Tests and evals

- `test.started`
- `test.output`
- `test.completed`
- `eval.started`
- `eval.completed`

### Git and PRs

- `git.branch_created`
- `git.commit_created`
- `pr.opened`
- `pr.updated`

### Environment setup

- `environment.detect.started`
- `environment.repo.inspected`
- `environment.stack_detected`
- `environment.plan_proposed`
- `environment.validation_started`
- `environment.validation_failed`
- `environment.validation_passed`
- `environment.profile_saved`
- `environment.profile_stale`

### Security, policy, and budget

- `secret.missing`
- `secret.redacted`
- `policy.violation`
- `policy.blocked`
- `budget.updated`
- `budget.exceeded`

### Artifacts and tracing

- `artifact.created`
- `trace.linked`
- `cost.updated`

---

## 10. Final result shape

```ts
export type RunResult = {
  runId: string
  status: "completed" | "failed" | "cancelled" | "timed_out"
  finalOutput?: string
  summary?: string
  changedFiles: string[]
  diff?: string
  tests: Array<{
    name?: string
    command: string
    exitCode: number
    durationMs?: number
    output?: string
  }>
  approvals: Array<{
    id: string
    decision: "approved" | "rejected" | "expired"
    reason?: string
  }>
  pr?: {
    provider: "github"
    url: string
    number: number
    draft: boolean
  }
  artifacts: Array<{
    id: string
    kind: "log" | "diff" | "report" | "trace" | "test-output" | "profile"
    url?: string
    path?: string
  }>
  usage?: {
    inputTokens?: number
    outputTokens?: number
    costUsd?: number
    durationMs?: number
  }
  failure?: {
    code: string
    message: string
    diagnosis?: string
    retryable?: boolean
  }
}
```

---

## 11. The way the code works

The SDK has a small stable core and many replaceable adapters.

```txt
User code
  -> Agent + Runner.start()
  -> Runtime core creates run
  -> Policy compiled
  -> Workspace resolved
  -> Adapter started
  -> Adapter emits raw events
  -> Event normalizer converts to AgentEvent
  -> Policy/redaction filters events and actions
  -> Run exposes AsyncIterable events
  -> Result collector builds RunResult
```

### Adapter interface

```ts
export interface AgentAdapter {
  readonly name: string
  start(input: AdapterStartInput): Promise<AdapterRunHandle>
}

export type AdapterStartInput = {
  runId: string
  task: string
  agent: CompiledAgent
  workspace: WorkspaceSpec
  policy: CompiledPolicy
  context: RuntimeContext
  emit: (event: AgentEvent) => void | Promise<void>
}

export interface AdapterRunHandle {
  events?(): AsyncIterable<AgentEvent>
  approve?(input: ApprovalDecision): Promise<void>
  steer?(input: SteerInput): Promise<void>
  cancel(): Promise<void>
  result(): Promise<RunResult>
}
```

### Mock adapter first

The first adapter should not call any real model. It should prove the API, event stream, result collector, and tests.

```ts
const run = await Runner.start(new Agent({ name: "MockAgent" }), {
  task: "List files and say done.",
  workspace: { cwd: process.cwd() },
  adapter: adapters.mock({
    events: [
      { type: "run.started", summary: "Run started" },
      { type: "agent.message.delta", payload: { text: "Inspecting files." } },
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
```

---

## 12. Smallest working SDK

The smallest working SDK is not a real coding agent. It is a tested runtime with a mock adapter.

It must have:

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
```

It must pass:

```ts
expect(result.status).toBe("completed")
expect(events.map(e => e.type)).toEqual([
  "run.started",
  "agent.message.delta",
  "tool.started",
  "tool.completed",
  "run.completed",
])
expect(result.finalOutput).toContain("Done")
```

This is the first milestone because it proves the public API, event contract, and result flow without external dependencies.

---

## 13. Real proof milestones

### Milestone A: Mock run proof

Passes when:

- SDK creates a run.
- Events stream in order.
- Result resolves.
- Failure is represented cleanly.

### Milestone B: Fake process proof

A local script emits newline-delimited JSON events. Passes when:

- SDK spawns a child process.
- SDK parses events.
- SDK handles process exit.
- SDK converts output into `RunResult`.

### Milestone C: ACP stdio proof

Passes when:

- SDK starts an ACP-compatible process.
- SDK initializes a session.
- SDK sends a prompt.
- SDK receives streaming updates.
- SDK normalizes raw events.

### Milestone D: Conductor/proxy proof

Passes when:

- Conductor starts.
- Proxy chain starts.
- Base agent starts.
- SDK still sees one run.
- Normalized events are identical with or without conductor for the same fixture.

### Milestone E: Approval proof

Passes when:

- Dangerous action emits `approval.required`.
- Run enters `awaiting_approval`.
- `approve()` continues execution.
- `reject()` blocks or redirects safely.

### Milestone F: Buggy calculator proof

Fixture repo:

```ts
export function add(a: number, b: number) {
  return a - b
}
```

Passing result:

```json
{
  "status": "completed",
  "changedFiles": ["src/add.ts"],
  "tests": [{ "command": "pnpm test", "exitCode": 0 }],
  "diffContains": ["- return a - b", "+ return a + b"]
}
```

### Milestone G: Redaction proof

Passes when:

- Secret-like values are redacted in every event.
- Raw platform API keys never enter sandbox input.
- Logs do not contain provider credentials.

### Milestone H: Draft PR proof

Passes when:

- GitHub App token clones repo.
- Agent creates branch.
- Agent edits code.
- Tests pass.
- Draft PR opens.
- `result.pr.url` exists.

---

## 14. Implementation sequence

Build in small complete slices.

### v0: Source documents and API sketch

- README, SOURCE, AGENTS, STRUCTURE.
- Event catalog.
- API examples.
- Test fixtures described.

### v0.1: Mock SDK

- `Agent`
- `Runner.start`
- `Run.events()`
- `Run.result()`
- `adapters.mock`
- event-order tests

### v0.2: Fake process adapter

- child process adapter
- NDJSON event parsing
- process exit handling
- stderr capture

### v0.3: ACP stdio adapter

- JSON-RPC transport
- initialize/session/new/session/prompt
- map raw ACP updates into standard events

### v0.4: Conductor adapter and proxies

- start conductor
- configure proxy chain
- policy proxy
- redaction proxy
- event normalizer proxy

### v0.5: Local repo fixture

- buggy calculator repo
- test runner
- diff collector
- result evidence

### v1: Local Docker sandbox

- mount workspace
- run adapter inside sandbox
- stream events to host
- cleanup sandbox

### v1.5: GitHub draft PR

- GitHub App installation token
- branch creation
- commit
- draft PR
- PR result evidence

### v2: Environment setup

- detective: read-only stack detection
- builder: setup/test/build with policy hooks
- verifier: independent pass/fail confidence report

### v3: Cloud platform

- API keys
- tenants
- secrets
- environment profiles
- run history
- webhooks
- billing/usage

---

## 15. Alternatives considered

### Alternative 1: expose ACP directly

Rejected as public API. ACP is useful plumbing, but the user should not think in JSON-RPC methods. Use ACP behind `adapters.acp()`.

### Alternative 2: build only an OpenAI-style SDK

Rejected as too narrow. OpenAI-style `Agent`/`Runner` is excellent for orchestration, but streaming `query()` is better for scripts and CLI use.

### Alternative 3: build only a Claude-style query SDK

Rejected as too narrow. Query streams are simple, but background runs need approvals, results, PRs, artifacts, and audit metadata.

### Alternative 4: start with real GitHub/Pi/OpenCode immediately

Rejected for v0. Start with mock events. Real agents add too many variables before the public API and tests are stable.

### Alternative 5: make conductor mandatory

Rejected for v0/v1. Conductor is powerful for proxy chains, but the SDK should work with a mock adapter and direct ACP adapter before conductor is added.

### Alternative 6: build a model gateway first

Rejected for v0/v1. Direct provider-key injection can work early if credentials are scoped, redacted, and never exposed to the frontend or passed as platform API keys.

---

## 16. Non-negotiable quality gates

- The README examples must compile or be intentionally marked pseudocode.
- Event types must live in one TypeScript union and one docs catalog.
- Every event emitted by a test must match the documented event envelope.
- No new event type may be added without docs and tests.
- A run is not successful unless `RunResult` includes evidence.
- Redaction tests must run before any real provider key is used.
- Platform API keys must never be passed into sandbox processes.
- Production adapters must avoid random auto-discovery of skills/hooks/context files.
- Every iteration must be demoable.

---

## 17. Where to search for references

Search these sources before changing architecture:

- OpenAI Agents SDK docs: agents, Runner, tools, handoffs, guardrails, sessions, tracing.
- Claude Agent SDK docs: query, tools, permissions, hooks, sessions, checkpointing, telemetry.
- Agent Client Protocol docs: JSON-RPC methods, sessions, prompts, updates, file operations, permissions.
- ACP Rust SDK and conductor docs: proxy chains, process management, message routing.
- OpenCode docs: SDK, ACP mode, plugins/hooks.
- Pi coding-agent SDK examples: `createAgentSession`, tools, skills, hooks, context files, session manager, full-control mode.
- Your own `SOURCE.md`, `docs/EVENTS.md`, and `STRUCTURE.md` before adding features.

---

## 18. Final direction

This SDK should become a typed, event-driven, policy-aware background-agent runtime.

The public promise:

```txt
I define the task.
I define the policy.
I choose the workspace.
I choose or hide the adapter.
I watch evidence stream in.
I approve risky actions.
I receive a result with proof.
```

Everything else is implementation detail.
