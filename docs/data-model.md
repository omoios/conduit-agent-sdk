# Data Model — Stage-2 Rust Port Manifest

This document is the cross-language source of truth for every core data type in
the Background Agent SDK run layer. Each table lists:

1. The **Python field name** (snake\_case) as defined in `conduit_sdk/runcore.py`
2. The **wire key** (camelCase) used in JSON serialization
3. The **Rust serde type** that a Stage-2 port should use

The `Reducer` is the single mutation point (`step` → `finalize`). The Policy
trait defines the permission-bridge contract. All serialization is **sparse**:
`None` → `Option::None` → wire omission via `#[serde(skip_serializing_if =
"Option::is_none")]`.

---

## AgentEvent (`agent_event.schema.json`)

| Python (snake) | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `id` | `id` | `String` | Required |
| `type` | `type` | `String` (tagged enum variant) | Dotted catalog name or `x.*` escape |
| `run_id` | `runId` | `String` | Required |
| `sequence` | `sequence` | `u32` | Starts at 1, monotonically increasing |
| `timestamp` | `timestamp` | `String` (ISO‑8601) | Required |
| `source` | `source` | `String` (enum: sdk\|adapter\|proxy\|agent…) | Required |
| `redaction_status` | `redactionStatus` | `String` (enum: none\|redacted\|blocked\|unknown) | Required |
| `summary` | `summary` | `Option<String>` | Optional, sparse |
| `payload` | `payload` | `Option<serde_json::Value>` | Optional, sparse; shape depends on `type` |
| `version` | `version` | `Option<u32>` | Forward‑compat A3, default 1 |
| `trace_id` | `traceId` | `Option<String>` | Reserved |
| `span_id` | `spanId` | `Option<String>` | Reserved |
| `workspace_id` | `workspaceId` | `Option<String>` | Reserved |
| `tenant_id` | `tenantId` | `Option<String>` | Reserved |

### Type dispatch

The `type` field is a tagged union discriminator. In Rust:

```rust
#[derive(Serialize, Deserialize)]
struct AgentEvent {
    // … envelope fields …
    #[serde(flatten)]
    payload: AgentEventPayload,  // or use enum dispatch
}
```

Payload shapes on the wire are plain `serde_json::Value` for maximum
forward‑compatibility. A Rust port MAY define a `Payload` enum with typed
variants for known types, but must fall through to `x.*` escape:

```rust
#[derive(Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum AgentEventPayload {
    RunStarted { … },
    AgentMessageDelta { text: String, channel: Option<String> },
    // … known variants …
    #[serde(untagged)]
    Unknown(serde_json::Value),  // x.* escape
}
```

---

## Result (`result.schema.json`)

| Python (snake) | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `run_id` | `runId` | `String` | Required |
| `status` | `status` | `String` (enum: completed\|failed\|cancelled\|timed\_out) | Required |
| `event_count` | `eventCount` | `u32` | SDK extra |
| `summary` | `summary` | `Option<String>` | Sparse |
| `error` | `failure` | `Option<Failure>` | Python `error` → wire `failure`; see Failure below |
| `started_at` | `startedAt` | `String` (ISO‑8601) | SDK extra |
| `ended_at` | `endedAt` | `String` (ISO‑8601) | SDK extra |
| `final_output` | `finalOutput` | `Option<String>` | Sparse |
| `changed_files` | `changedFiles` | `Vec<String>` | Deduplicated, order‑preserving |
| `diff` | `diff` | `Option<String>` | Last unified diff wins |
| `tests` | `tests` | `Vec<TestEntry>` | Required, empty when none |
| `approvals` | `approvals` | `Vec<ApprovalEntry>` | Required, empty when none |
| `pr` | `pr` | `Option<PrEntry>` | Sparse |
| `artifacts` | `artifacts` | `Vec<ArtifactEntry>` | Required, empty when none |
| `usage` | `usage` | `Option<Usage>` | Sparse; camelCase keys inside |

### Failure (seed `failure`)

| Python (snake) / Wire (camel) | Rust serde | Notes |
|---|---|---|
| `code` | `String` | Required |
| `message` | `String` | Required |
| `diagnosis` | `Option<String>` | Sparse |
| `retryable` | `Option<bool>` | Sparse |

### TestEntry

| Wire (camel) | Rust serde | Notes |
|---|---|---|
| `command` | `String` | Required |
| `exitCode` | `i32` | Required |
| `passed` | `Option<bool>` | Sparse |
| `durationMs` | `Option<u64>` | Sparse |
| `output` | `Option<String>` | Sparse |
| `name` | `Option<String>` | Sparse |

### ApprovalEntry

| Wire (camel) | Rust serde | Notes |
|---|---|---|
| `id` | `String` | Required |
| `decision` | `String` (enum: approved\|rejected\|expired) | Required |
| `reason` | `Option<String>` | Sparse |
| `by` | `Option<String>` | Sparse |

### PrEntry

| Wire (camel) | Rust serde | Notes |
|---|---|---|
| `provider` | `String` (enum: github) | Required |
| `url` | `String` | Required |
| `number` | `u64` | Required |
| `draft` | `bool` | Required |

### ArtifactEntry

| Wire (camel) | Rust serde | Notes |
|---|---|---|
| `id` | `String` | Required |
| `kind` | `String` (enum: log\|diff\|report\|…) | Required |
| `url` | `Option<String>` | Sparse |
| `path` | `Option<String>` | Sparse |

### Usage

| Wire (camel) | Rust serde | Notes |
|---|---|---|
| `inputTokens` | `Option<u64>` | Sparse |
| `outputTokens` | `Option<u64>` | Sparse |
| `costUsd` | `Option<f64>` | Sparse |
| `durationMs` | `Option<u64>` | Sparse |

---

## Evidence

| Python (snake) | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `final_output` | `finalOutput` | `Option<String>` | Accumulated from `agent.message.delta` with `channel=final` or `.completed` |
| `changed_files` | `changedFiles` | `Vec<String>` | Deduplicated |
| `diff` | `diff` | `Option<String>` | Last `diff.preview_created` / `diff.applied` wins |
| `tests` | `tests` | `Vec<TestEntry>` | Appended on `test.completed` |
| `approvals` | `approvals` | `Vec<ApprovalEntry>` | Appended on `approval.*` |
| `pr` | `pr` | `Option<PrEntry>` | Set on `pr.opened` |
| `artifacts` | `artifacts` | `Vec<ArtifactEntry>` | Appended on `artifact.created` |
| `usage` | `usage` | `Option<Usage>` | Merged on `cost.updated` / `budget.updated` / `usage` |

---

## RunState

Not exposed on the wire — internal mutable state consumed by the reducer.

| Python | Type | Notes |
|---|---|---|
| `run_id` | `String` | Scoped to one run |
| `seq_counter` | `u32` | Monotonically increasing counter |
| `status` | `String` | `"running"` → `"completed"` / `"failed"` / `"cancelled"` / `"timed_out"` |
| `evidence` | `Evidence` | Mutable evidence accumulator |
| `started_at` | `String` (ISO‑8601) | Copied from first `run.started` timestamp |
| `last_timestamp` | `String` (ISO‑8601) | Last observed event timestamp |
| `terminal_summary` | `Option<String>` | Summary from terminal event |
| `terminal_payload` | `Option<Value>` | Failure object for `status=="failed"` |
| `deadline` | `Option<f64>` | Monotonic seconds; `now >= deadline` → timeout |
| `cost_limit` | `Option<f64>` | USD; cumulative cost ≥ limit → `budget.exceeded` |
| `pending_approval` | `Option<(String, Option<AgentEvent>, Action)>` | (approval\_id, gated event or None for permission bridge, action) |

---

## Action

Gatable action extracted from an event (A6).

| Python | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `kind` | `kind` | `String` (command\|file\_write\|diff\_apply\|tool) | |
| `command` | `command` | `Option<String>` | Present when `kind == "command"` |
| `path` | `path` | `Option<String>` | Present when `kind == "file_write"` |
| `tool_name` | `toolName` | `Option<String>` | Present when `kind == "tool"` |

Serialized via `to_record()` → `HashMap<String, String>`.

---

## PolicyDecision

| Python | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `effect` | `effect` | `String` (allow\|deny\|require\_approval) | |
| `kind` | `kind` | `Option<String>` | Sparse |
| `reason` | `reason` | `Option<String>` | Sparse |
| `risk` | `risk` | `Option<String>` | `"low"` \| `"med"` \| `"high"` |
| `approval_id` | `approvalId` | `Option<String>` | Sparse |

---

## ApprovalRequest

Surfaced to the `on_approval` callback.

| Python | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `approval_id` | `approvalId` | `String` | |
| `kind` | `kind` | `String` | |
| `risk` | `risk` | `String` | |
| `action_preview` | `actionPreview` | `HashMap<String, String>` | `to_record()` of the Action |

---

## ApprovalDecision

Returned by the `on_approval` callback.

| Python | Wire (camel) | Rust serde | Notes |
|---|---|---|---|
| `decision` | `decision` | `String` (approve\|reject) | |
| `reason` | `reason` | `Option<String>` | Sparse |
| `by` | `by` | `Option<String>` | Sparse |

---

## Reducer Signature

```rust
pub struct Reducer;

impl Reducer {
    /// Advance `state` by one raw event (or a deadline tick when `raw` is None).
    /// Returns the events to emit (sequence‑assigned).
    /// Terminal states drop further input.
    pub fn step(
        state: &mut RunState,
        raw: Option<&AgentEvent>,      // None → deadline tick
        now: f64,                       // monotonic clock
        new_id: &mut dyn FnMut() -> String,
        policy: Option<&dyn Policy>,    // None → no gating
    ) -> Vec<AgentEvent>;

    /// Freeze `state` into a `Result`. Pure; no wall‑clock.
    pub fn finalize(state: &RunState) -> Result;
}
```

---

## Policy Trait

```rust
/// Maps an Action to a PolicyDecision.
pub trait Policy {
    fn evaluate(&self, action: &Action) -> PolicyDecision;
}
```

### Built‑in factories

| Factory | Effect |
|---|---|
| `read_only()` | Allow reads/tools; deny commands, file writes, diff applies |
| `deny(reason)` | Deny every action |
| `require_approval_for(commands, files)` | Require approval for matching commands (prefix token) and files (fnmatch) |
| `safe_local()` | Require approval for risky commands + protected paths |
| `compose(policies…)` | Deny > require\_approval > allow precedence |
| `max_runtime_minutes(n)` | Allow everything with runtime budget (seconds via `budget.max_runtime_s`) |
| `max_cost_usd(usd)` | Allow everything with cost budget (`budget.max_cost_usd`) |

---

## Permission-bridge contract (ACP `can_use_tool`)

When the ACP agent calls a tool, the `can_use_tool` permission callback routes
through the Run's Policy:

1. The client's `can_use_tool` handler constructs an `Action(kind="tool",
   tool_name=…)` and calls `_resolve_permission(action)`.
2. The Run consumer evaluates the policy:
   - **allow** → `PermissionResultAllow()` — tool proceeds.
   - **deny** → emit `policy.blocked` → `PermissionResultDeny(reason)` — tool
     blocked.
   - **require\_approval** → emit `approval.required` → the approval machinery
     resolves (callback or external Future) → **approved** → tool proceeds;
     **rejected/expired** → `PermissionResultDeny` + `policy.blocked`.
3. The permission bridge is **deadlock-free**: the evaluation runs on the
   single consumer task (not the client's I/O task), so the adapter can
   continue to enqueue events while the approval is pending.
