# Standard Events

This document defines the event envelope and catalog for the Background Agent SDK.

Events are the common language between:

- SDK runtime
- adapters
- proxies
- UI
- webhooks
- audit logs
- tests
- final result collector

The SDK should normalize raw adapter events into this standard catalog.

---

## Event envelope

```ts
export type EventSource =
  | "sdk"
  | "adapter"
  | "proxy"
  | "agent"
  | "sandbox"
  | "controller"
  | "server"

export type RedactionStatus = "none" | "redacted" | "blocked" | "unknown"

export type AgentEventEnvelope<TType extends string = string, TPayload = unknown> = {
  id: string
  type: TType
  runId: string
  workspaceId?: string
  tenantId?: string
  sequence: number
  timestamp: string
  source: EventSource
  traceId?: string
  spanId?: string
  redactionStatus: RedactionStatus
  summary?: string
  payload: TPayload
}
```

---

## Event ordering rules

1. `sequence` starts at `1` for each run.
2. `sequence` is strictly increasing.
3. Events are append-only.
4. Retried actions emit new events; they do not mutate old events.
5. Raw protocol events are not user-facing events unless explicitly requested in debug mode.
6. Redaction occurs before event storage, webhook delivery, or UI rendering.

---

## Required lifecycle baseline

A minimal successful run should emit at least:

```txt
run.started
agent.message.delta
run.completed
```

A useful tool-using successful run should emit:

```txt
run.started
agent.message.delta
tool.started
tool.completed
run.completed
```

A code-changing run should emit:

```txt
run.started
command.started
command.output
command.completed
diff.preview_created
test.started
test.completed
run.completed
```

A risky run should emit:

```txt
approval.required
approval.approved | approval.rejected | approval.expired
```

---

## Run lifecycle events

### `run.created`

The run record exists but execution has not started.

```ts
type RunCreatedPayload = {
  task: string
  mode?: string
  adapterName?: string
}
```

### `run.queued`

The run is waiting for capacity.

### `run.allocating`

The controller is allocating sandbox/runtime resources.

### `run.booting`

Sandbox or process is starting.

### `run.bootstrapping`

Runtime dependencies, repo checkout, or environment setup are being prepared.

### `run.started`

The agent execution has started.

### `run.status_changed`

```ts
type RunStatusChangedPayload = {
  from: RunStatus
  to: RunStatus
  reason?: string
}
```

### `run.heartbeat`

The runtime is still alive.

### `run.awaiting_approval`

The run is paused pending a human or policy decision.

### `run.cancelling`

Cancellation has been requested.

### `run.cancelled`

Cancellation completed.

### `run.timed_out`

The run exceeded its time limit.

### `run.completed`

The run completed successfully.

### `run.failed`

```ts
type RunFailedPayload = {
  code: string
  message: string
  retryable?: boolean
  diagnosis?: string
}
```

### `run.cleanup_started`

Cleanup is underway.

### `run.destroyed`

Sandbox/process resources were destroyed.

---

## Agent output events

### `agent.message.delta`

```ts
type AgentMessageDeltaPayload = {
  text: string
  channel?: "final" | "progress" | "summary"
}
```

### `agent.message.completed`

A message is complete.

### `agent.plan.created`

Agent created a plan.

### `agent.plan.updated`

Agent updated a plan.

### `agent.subtask.started`

A named subtask started.

### `agent.subtask.completed`

A named subtask completed.

### `agent.thought_summary`

A safe summary of reasoning or progress. Do not expose hidden chain-of-thought.

---

## Tool events

### `tool.started`

```ts
type ToolStartedPayload = {
  toolName: string
  callId?: string
  inputPreview?: unknown
}
```

### `tool.completed`

```ts
type ToolCompletedPayload = {
  toolName: string
  callId?: string
  ok: boolean
  outputPreview?: unknown
  durationMs?: number
}
```

### `tool.failed`

```ts
type ToolFailedPayload = {
  toolName: string
  callId?: string
  error: string
  retryable?: boolean
}
```

### `tool.result_modified`

A proxy or hook modified a tool result before it reached the agent or user.

---

## Command events

### `command.started`

```ts
type CommandStartedPayload = {
  commandId: string
  command: string
  cwd?: string
  approvalId?: string
}
```

### `command.output`

```ts
type CommandOutputPayload = {
  commandId: string
  stream: "stdout" | "stderr"
  text: string
}
```

### `command.completed`

```ts
type CommandCompletedPayload = {
  commandId: string
  exitCode: number
  durationMs?: number
}
```

### `command.failed`

The command failed to start or crashed before an exit code could be captured.

---

## File and diff events

### `file.read`

A file was read.

### `file.write_requested`

A file write was requested and may need approval.

### `file.edited`

A file was changed.

### `diff.preview_created`

```ts
type DiffPreviewCreatedPayload = {
  diffId: string
  files: string[]
  diff?: string
  requiresApproval?: boolean
}
```

### `diff.updated`

A diff changed after more edits.

### `diff.applied`

A previewed diff was applied.

### `diff.discarded`

A previewed diff was discarded.

---

## Approval events

### `approval.required`

```ts
type ApprovalRequiredPayload = {
  approvalId: string
  kind: "command" | "file_write" | "diff_apply" | "network" | "secret" | "git" | "other"
  reason: string
  risk: "low" | "medium" | "high"
  actionPreview?: unknown
  expiresAt?: string
}
```

### `approval.approved`

```ts
type ApprovalApprovedPayload = {
  approvalId: string
  approvedBy?: string
  reason?: string
}
```

### `approval.rejected`

```ts
type ApprovalRejectedPayload = {
  approvalId: string
  rejectedBy?: string
  reason?: string
}
```

### `approval.expired`

Approval expired before a decision.

### `approval.bypass_denied`

The agent or runtime attempted to bypass an approval rule.

---

## Test and eval events

### `test.started`

```ts
type TestStartedPayload = {
  command: string
  testRunId?: string
}
```

### `test.output`

```ts
type TestOutputPayload = {
  testRunId?: string
  stream: "stdout" | "stderr"
  text: string
}
```

### `test.completed`

```ts
type TestCompletedPayload = {
  command: string
  exitCode: number
  passed: boolean
  durationMs?: number
}
```

### `eval.started`

An evaluation started.

### `eval.completed`

An evaluation completed.

---

## Git and PR events

### `git.branch_created`

A branch was created.

### `git.commit_created`

A commit was created.

### `pr.opened`

```ts
type PrOpenedPayload = {
  provider: "github"
  url: string
  number: number
  draft: boolean
  title: string
}
```

### `pr.updated`

An existing PR was updated.

---

## Environment events

### `environment.detect.started`

Environment detection started.

### `environment.repo.inspected`

Repo files were inspected.

### `environment.stack_detected`

```ts
type EnvironmentStackDetectedPayload = {
  runtime?: string
  packageManager?: string
  framework?: string
  database?: string[]
  confidence?: number
}
```

### `environment.plan_proposed`

The system proposed install/test/build commands, services, and secrets.

### `environment.validation_started`

Validation began.

### `environment.validation_failed`

Validation failed with issues.

### `environment.validation_passed`

Validation passed.

### `environment.profile_saved`

Environment profile saved.

### `environment.profile_stale`

A profile became stale because relevant files or secrets changed.

---

## Security, policy, and budget events

### `secret.missing`

A required secret is missing.

### `secret.redacted`

A secret-like value was redacted.

### `policy.violation`

An attempted action violated policy.

### `policy.blocked`

Policy blocked an action.

### `budget.updated`

Usage or budget changed.

### `budget.exceeded`

Budget exceeded.

---

## Artifact and trace events

### `artifact.created`

```ts
type ArtifactCreatedPayload = {
  artifactId: string
  kind: "log" | "diff" | "report" | "trace" | "test-output" | "profile"
  path?: string
  url?: string
}
```

### `trace.linked`

A tracing URL or trace ID became available.

### `cost.updated`

Cost estimate changed.

---

## Experimental events

Experimental event names must use the prefix:

```txt
x.<namespace>.<event_name>
```

Example:

```txt
x.memory.proposed
```

Experimental events cannot be required by stable UI or tests until promoted into this catalog.
