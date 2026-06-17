# Public SDK API

This document sketches the public API. Implementation should start with the smallest subset and grow without breaking this direction.

---

## Package exports

```ts
export {
  Agent,
  Runner,
  query,
  BackgroundAgent,
  adapters,
  proxy,
  policy,
  sandbox,
  tool,
}

export type {
  AgentEvent,
  RunResult,
  RunStatus,
  AgentAdapter,
  AgentRun,
  ApprovalDecision,
}
```

---

## Agent

```ts
type AgentInput = {
  name: string
  instructions?: string
  model?: string
  tools?: ToolDefinition[]
  policy?: PolicyDefinition
  handoffs?: Agent[]
  metadata?: Record<string, unknown>
}

class Agent {
  constructor(input: AgentInput)
}
```

Example:

```ts
const coder = new Agent({
  name: "BackgroundCoder",
  model: "openai/gpt-5.5",
  instructions: "Make minimal, reviewable code changes.",
  tools: [tool.readFiles(), tool.searchCode(), tool.editFiles(), tool.shell()],
  policy: policy.safeLocal(),
})
```

---

## Runner

```ts
type RunnerStartInput = {
  task: string
  workspace: WorkspaceSpec
  adapter: AgentAdapter
  policy?: PolicyDefinition
  sandbox?: SandboxSpec
  context?: string[]
  metadata?: Record<string, unknown>
}

class Runner {
  static start(agent: Agent, input: RunnerStartInput): Promise<AgentRun>
  static run(agent: Agent, input: RunnerStartInput): Promise<RunResult>
}
```

`Runner.start()` returns a live run.
`Runner.run()` starts and waits for final result.

---

## Run

```ts
interface AgentRun {
  id: string
  status(): RunStatus
  events(): AsyncIterable<AgentEvent>
  result(): Promise<RunResult>
  cancel(reason?: string): Promise<void>
  steer(input: SteerInput): Promise<void>
  approve(input: ApprovalDecision): Promise<void>
  reject(input: ApprovalRejection): Promise<void>
}
```

Example:

```ts
const run = await Runner.start(agent, input)

for await (const event of run.events()) {
  render(event)
}

await run.approve({ approvalId: "apr_123", reason: "Safe test command" })

const result = await run.result()
```

---

## Query API

```ts
type QueryInput = {
  prompt: string
  options: {
    cwd?: string
    adapter?: string | AgentAdapter
    allowedTools?: string[]
    permissionMode?: "readonly" | "ask" | "auto"
    maxTurns?: number
    model?: string
  }
}

function query(input: QueryInput): AsyncIterable<AgentEvent>
```

Use this for local scripts and CLIs.

---

## Cloud client

```ts
type BackgroundAgentInput = {
  apiKey: string
  baseUrl?: string
}

class BackgroundAgent {
  constructor(input: BackgroundAgentInput)
  runs: {
    create(input: CloudRunCreateInput): Promise<{ id: string }>
    events(runId: string): AsyncIterable<AgentEvent>
    result(runId: string): Promise<RunResult>
    cancel(runId: string, reason?: string): Promise<void>
    approve(runId: string, input: ApprovalDecision): Promise<void>
    reject(runId: string, input: ApprovalRejection): Promise<void>
  }
}
```

---

## Adapters

```ts
const adapter = adapters.mock.success()

const acp = adapters.acp({
  command: "opencode",
  args: ["acp"],
  cwd: process.cwd(),
})

const conducted = adapters.conductor({
  baseAgent: acp,
  proxies: [proxy.policy(), proxy.redaction(), proxy.eventNormalizer()],
})
```

Adapter interface:

```ts
export interface AgentAdapter {
  readonly name: string
  start(input: AdapterStartInput): Promise<AdapterRunHandle>
}
```

---

## Policy

```ts
const safe = policy.compose(
  policy.noProductionSecrets(),
  policy.noDirectPushToMain(),
  policy.requireApprovalFor({
    commands: ["git push", "pnpm add", "npm install", "rm"],
    files: [".github/workflows/**", "migrations/**", ".env*"],
  }),
  policy.maxRuntimeMinutes(30),
  policy.maxCostUsd(5),
)
```

Policy should be testable without an LLM.

---

## Tools

```ts
const createIssue = tool({
  name: "create_issue",
  description: "Create a GitHub issue.",
  schema: z.object({
    title: z.string(),
    body: z.string(),
  }),
  async execute(input, ctx) {
    return ctx.github.issues.create(input)
  },
})
```

Built-in tool factories can include:

```txt
tool.readFiles()
tool.searchCode()
tool.editFiles()
tool.shell()
tool.git()
tool.githubPR()
tool.environment()
tool.artifacts()
```

---

## Sandbox

```ts
const local = sandbox.local({ cwd: process.cwd() })
const docker = sandbox.localDocker({ image: "node:22-bookworm" })
const managed = sandbox.managed({ provider: "daytona" })
```

A sandbox is an execution boundary. It should not be an authority for policy.

---

## Proxies

```ts
const adapter = adapters.conductor({
  baseAgent: adapters.acp({ command: "opencode", args: ["acp"] }),
  proxies: [
    proxy.policy(),
    proxy.redaction(),
    proxy.eventNormalizer(),
    proxy.trace(),
  ],
})
```

Proxies should be optional. The same user-facing API should work without them.

---

## Minimal implementation subset

Implement this first:

```ts
new Agent(...)
Runner.start(...)
run.events()
run.result()
adapters.mock(...)
policy.readOnly()
```

Do not implement cloud, GitHub, Docker, Pi, or ACP before this subset passes tests.
