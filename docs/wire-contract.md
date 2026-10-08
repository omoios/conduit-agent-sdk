# Wire Contract — Human Field Reference

This document describes the JSON wire contract (camelCase, sparse) for the
Background Agent SDK run layer. It serves as the human-readable SSOT for every
field, supplementing the JSON Schema files in `schema/` and the Rust port
manifest in `docs/data-model.md`.

---

## Envelope structure

Every event on the wire is a JSON object conforming to
`schema/agent_event.schema.json`.

### Required vs optional

**Required** fields are always present (the schema marks them `required` and the
Python dataclass has no default):

| Key | Type | Description |
|---|---|---|
| `id` | `string` | Short unique event identifier |
| `type` | `string` | Dotted catalog name (see [Event catalog](#event-catalog)) |
| `runId` | `string` | Scoped to one run |
| `sequence` | `integer` | ≥1, monotonically increasing within a run |
| `timestamp` | `string` | ISO‑8601 date-time |
| `source` | `string` | One of: sdk, adapter, proxy, agent, sandbox, controller, server |
| `redactionStatus` | `string` | One of: none, redacted, blocked, unknown |

**Optional** fields are omitted when `null` (sparse rule). The Python serializer
(`result_to_record` / `to_record`) drops `None` values; Rust uses
`#[serde(skip_serializing_if = "Option::is_none")]`.

| Key | Type | Notes |
|---|---|---|
| `summary` | `string` or absent | Human-readable summary |
| `payload` | `object` or absent | Type-dependent payload |
| `version` | `integer` | Default 1; forward-compat A3 |
| `traceId` | `string` or absent | Reserved |
| `spanId` | `string` or absent | Reserved |
| `workspaceId` | `string` or absent | Reserved |
| `tenantId` | `string` or absent | Reserved |

---

## Result shape

The Result wire record (schema `result.schema.json`) mirrors the seed
specification (§10) with SDK extensions.

### Required fields

| Key | Type | Notes |
|---|---|---|
| `runId` | `string` | |
| `status` | `string` | One of: `completed`, `failed`, `cancelled`, `timed_out` |
| `changedFiles` | `array[string]` | Deduplicated, order-preserving; may be `[]` |
| `tests` | `array[object]` | Each test has `command`, `exitCode`; may be `[]` |
| `approvals` | `array[object]` | Each approval has `id`, `decision`; may be `[]` |
| `artifacts` | `array[object]` | Each artifact has `id`, `kind`; may be `[]` |

### Optional fields (sparse)

| Key | Type | Notes |
|---|---|---|
| `finalOutput` | `string` or absent | Final agent message text |
| `summary` | `string` or absent | Terminal summary |
| `diff` | `string` or absent | Unified diff (last writer wins) |
| `pr` | `object` or absent | Pull request info |
| `usage` | `object` or absent | Input tokens, output tokens, cost |
| `failure` | `object` or absent | Present only when `status == "failed"`. Python field `error` → wire `failure` |

### SDK extras (beyond seed §10)

| Key | Type | Notes |
|---|---|---|
| `eventCount` | `integer` | Total events emitted during the run |
| `startedAt` | `string` | ISO‑8601; copied from first `run.started` |
| `endedAt` | `string` | ISO‑8601; last observed event timestamp |

---

## Policy types

### PolicyDecision

| Key | Type | Notes |
|---|---|---|
| `effect` | `string` | `"allow"`, `"deny"`, or `"require_approval"` |
| `kind` | `string` or absent | `"command"`, `"file_write"`, `"diff_apply"`, `"tool"` |
| `reason` | `string` or absent | Human-readable explanation |
| `risk` | `string` or absent | `"low"`, `"med"`, or `"high"` |
| `approvalId` | `string` or absent | Set when effect is require_approval |

### ApprovalRequest

| Key | Type | Notes |
|---|---|---|
| `approvalId` | `string` | |
| `kind` | `string` | |
| `risk` | `string` | |
| `actionPreview` | `object` | Serialized Action |

### ApprovalDecision

| Key | Type | Notes |
|---|---|---|
| `decision` | `string` | `"approve"` or `"reject"` |
| `reason` | `string` or absent | |
| `by` | `string` or absent | Who made the decision |

---

## Event catalog

An authoritative list is in `schema/event_catalog.json`. Here is a summary:

| Group | Events |
|---|---|
| **run.lifecycle** | `run.created`, `run.queued`, … `run.started`, `run.status_changed`, `run.cancelled`, `run.timed_out`, `run.completed`, `run.failed`, … |
| **agent.output** | `agent.message.delta`, `agent.message.completed`, `agent.plan.created`, `agent.plan.updated`, `agent.subtask.*`, `agent.thought.delta`, `agent.thought_summary` |
| **tools** | `tool.started`, `tool.completed`, `tool.failed`, `tool.result_modified` |
| **commands** | `command.started`, `command.output`, `command.completed`, `command.failed` |
| **files.and.diffs** | `file.read`, `file.write_requested`, `file.edited`, `diff.preview_created`, `diff.updated`, `diff.applied`, `diff.discarded` |
| **approvals** | `approval.required`, `approval.approved`, `approval.rejected`, `approval.expired`, `approval.bypass_denied` |
| **tests.and.evals** | `test.started`, `test.output`, `test.completed`, `eval.started`, `eval.completed` |
| **git.and.prs** | `git.branch_created`, `git.commit_created`, `pr.opened`, `pr.updated` |
| **environment** | `environment.detect.started`, `environment.repo.inspected`, … |
| **security.policy.budget** | `secret.missing`, `secret.redacted`, `policy.violation`, `policy.blocked`, `budget.updated`, `budget.exceeded` |
| **artifacts.and.tracing** | `artifact.created`, `trace.linked`, `cost.updated` |

Experimental names use the `x.*` prefix.

### Thought stream (`include_thoughts`)

The agent's reasoning stream is **observability only** — never collected into
`Result` evidence. `acp_adapter` / `acp_agent` expose `include_thoughts` to
choose its shape:

- `"summary"` (default): buffer `ThoughtDelta` chunks, emit ONE
  `agent.thought_summary` per turn boundary (the seed wants safe summaries, not
  streamed chain-of-thought).
- `"delta"`: emit `agent.thought.delta` per chunk (raw stream, for UIs).
- `False`: drop thought events entirely (quietest programmatic stream).

### Terminal event types

- `run.completed` → status `completed`
- `run.failed` → status `failed`
- `run.cancelled` → status `cancelled`
- `run.timed_out` → status `timed_out`

---

## camelCase wire ↔ snake_case Python bridging

The `runcore` module maintains explicit field maps:

- `_EVENT_FIELD_MAP` — AgentEvent fields
- `_RESULT_FIELD_MAP` — Result fields

Each map is a list of `(python_field, wire_key)` tuples. The `to_record` and
`from_record` functions iterate these maps to convert between Python dataclasses
and JSON-safe dicts. The round-trip invariant
`from_record(to_record(e)) == e` is guaranteed for AgentEvent and Result.

Key Python–wire exceptions:
- Python `error` → wire `failure` (seed compatibility)
- Python `run_id` → wire `runId`
- Python `redaction_status` → wire `redactionStatus`
- Python `final_output` → wire `finalOutput`
- Python `changed_files` → wire `changedFiles`
- Python `event_count` → wire `eventCount`
- Python `started_at` → wire `startedAt`
- Python `ended_at` → wire `endedAt`
