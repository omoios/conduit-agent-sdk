# SDK Self-Snoop — `conduit-agent-sdk` Run Layer

_A read-grounded audit of the background-run layer. All symbols cited below were
read directly from source on 2026-06-19._

## Summary

`conduit-agent-sdk` is a Python/Rust-hybrid SDK for the [Agent Client Protocol](https://github.com/agentclientprotocol/) (ACP). It is bidirectional — it *drives* any ACP-compatible coding agent (Claude Code, OpenCode, Gemini CLI, Goose, Codex) as a client and *authors* Python agents that speak ACP over stdio (`AgentServer`). On top of that protocol core sits a **local run layer** (`conduit_sdk.runlayer`) that wraps any execution backend in a normalized, sequence-numbered event stream, a policy-gated approval flow, an evidence collector, and a structured `Result`. The seed design (`docs/seed/background-agent-sdk-seed/SOURCE.md`) frames the goal as: turn agent work into *trusted evidence* (diffs, tests, approvals, artifacts, cost) while hiding "protocol ugliness."

## Architecture

### Core-vs-glue split

The run layer is deliberately two files with one seam between them:

- **`python/conduit_sdk/runcore.py` — the pure reducer.** Sync, data-oriented, no `asyncio`, no I/O. The single mutation point is `step(state, raw, *, now, new_id, policy)` (`runcore.py:453`): it assigns the next `sequence`, updates `Evidence`, applies terminal status, evaluates the policy gate, and returns the events to emit. `finalize(state)` (`:497`) freezes `RunState` into a `Result`. Side-effecting inputs (`now`, `new_id`) are **injected callables**, so the core has no wall-clock or RNG of its own. `to_record`/`from_record`/`result_to_record` bridge snake_case Python ↔ camelCase wire.
- **`python/conduit_sdk/runlayer.py — the async glue.** Owns everything that touches an event loop: `Run` (the driver), `Runner` (factory), the `Adapter` protocol, and the five built-in adapters (`mock_adapter`, `acp_adapter`, `process_adapter`, `conductor_adapter`, `acp_agent`). It never re-implements reducer logic — it calls `step` via `Run._process` (`runlayer.py:553`).

### Single-consumer queue driver

`Run` (`runlayer.py:436`) decouples producers from the one consumer through an `asyncio.Queue`:

- `_pump` (`:522`) drains the adapter onto the queue; exhaustion is signalled by the `_DRAIN` sentinel.
- `_timeout_watcher` (`:536`) sleeps until the deadline, then pushes a `_TICK` so the *reducer* re-evaluates the deadline — no `asyncio.wait_for`.
- `cancel()` (`:701`) enqueues synthetic `run.cancelling` + `run.cancelled`.
- The permission bridge (`_resolve_permission`, `:753`) pushes a `_PERMISSION_REQUEST` sentinel + a `Future`.

Exactly **one** loop (`_iter_events`, `:632`) dequeues and runs `step`. Only that task mutates `RunState`, so there are no sequence races.

### Why this design

1. **Deadlock-free approvals.** The live-ACP approval path is the load-bearing case (`acp_agent`, `runlayer.py:938`). The agent's `can_use_tool` callback runs on the *Client's* task; if it blocked on the policy, the whole run would stall while the adapter awaits the agent. Instead the bridge enqueues `_PERMISSION_REQUEST` onto the run queue, and the single consumer evaluates the policy and resolves the `Future`. Because the consumer reads a queue (not a blocking adapter pull), side-task events surface even mid-await — `_iter_events`'s docstring calls this out explicitly.
2. **Rust-portable core.** The reducer has no `asyncio`, no globals, injected time/ids, and plain dataclasses (`RunState`, `Evidence`, `AgentEvent`, `Result`). That is the "Stage-2 port manifest core" the header comment names — it maps 1:1 onto `#[serde(skip_serializing_if)]` Rust structs (the sparse-serialization invariant is documented at `runcore.py:556-567`).

## Key APIs

**Reducer (`runcore.py`):**
- `step(state, raw, *, now, new_id, policy)` — advance run state by one event (or a deadline tick when `raw is None`); returns emitted events.
- `finalize(state)` — freeze state into a `Result`; defaults non-terminal runs to `completed`.
- `RunState` — mutable accumulator (`seq_counter`, `status`, `evidence`, `deadline`, `cost_limit`, `pending_approval`).
- `Evidence` — accumulates `final_output`, `changed_files`, `diff`, `tests`, `approvals`, `pr`, `artifacts`, `usage`.
- `AgentEvent` / `Result` — the wire envelopes; `to_record`/`from_record`/`result_to_record` round-trip them.

**Glue (`runlayer.py`):**
- `Runner.start(agent, *, task, adapter, ...)` / `Runner.run(...)` — create/drive a `Run`.
- `Run` — `events()`, `result()`, `cancel()`, `approve()`, `reject()`; the queue driver.
- `query(prompt, *, adapter, ...)` — Claude-style streaming sugar over `Runner.start` + `Run.events`.
- Adapters: `mock_adapter` (deterministic script), `process_adapter` (ndjson subprocess), `acp_adapter` (wraps a `Client`), `conductor_adapter` (Client behind a proxy chain), `acp_agent` (local launch + permission bridge).

**Policy (`policy.py`):** `Action`, `Policy` protocol (`evaluate`), `PolicyDecision`, `ApprovalRequest`, `ApprovalDecision`; factories `read_only`, `deny`, `require_approval_for`, `safe_local`, `compose`, `max_runtime_minutes`, `max_cost_usd`.

## Findings

1. **The approval gate is a state machine inside the reducer.** `step` extracts a gateable `Action` via `_extract_action` (`runcore.py:322`) for four kinds: `command` (`command.started`), `file_write` (`file.write_requested`), `diff_apply` (`diff.preview_created` w/ `requiresApproval`), and `tool` (`tool.started`). On `require_approval` it emits `approval.required` and stashes `state.pending_approval = (approval_id, gated_raw, action)`. **While pending, `step` drops every event except `approval.approved/rejected/expired`** (`:475-479`). Approve → `_passthrough` releases the gated event; reject/expire → `_emit_blocked` emits `policy.blocked`. Every path funnels through `_check_cost` afterward.

2. **Evidence is accumulated event-by-event from camelCase payloads** (`_collect_evidence`, `runcore.py:202`). Notable rules: `agent.message.completed` *resets* `final_output` (authoritative), while `agent.message.delta` with `channel=="final"` *concatenates* — a comment notes "last-wins was a bug." `changed_files` is dedup-preserving-order. `cost.updated`/`budget.updated`/`usage` all feed `evidence.usage` through `_USAGE_KEY_MAP`, which accepts both snake_case and camelCase keys.

3. **The wire contract is sparse, dual-cased, and schema-locked.** `additionalProperties: false` in all three schemas (`schema/agent_event.schema.json`, `result.schema.json`, `policy.schema.json`). None-valued fields are omitted on the wire. A subtle point: the Python `Result.error` field carries the seed `failure` object and is emitted under the wire key **`failure`** (not `error`) so it matches the seed/Rust/TS shape. The envelope reserves `version`/`traceId`/`spanId`/`workspaceId`/`tenantId` (the "A3" forward-compat fields). `schema/event_catalog.json` is explicitly *not a validation schema* — a reference list; unknown dotted names remain wire-valid as "experimental."

4. **Deferred events make the pending-approval window safe.** In the consumer, while an approval is pending, non-resolution events are pushed to `_deferred` and replayed after resolution (`runlayer.py:662-674`). Critically, `_DRAIN` is *ignored* while an approval is pending (`:641-644`), so an adapter that emits its terminal event before the human approves does not prematurely end the run.

5. **Budgets are enforced in the reducer, not via `asyncio`.** A deadline breach in `step` synthesizes `run.cancelling` + `run.timed_out` (`_emit_timeout`); cost ≥ limit synthesizes `budget.exceeded` + a `run.failed` whose payload `code` is `"budget_exceeded"` (`_check_cost`). Policy budgets flow in via the `_Budget` policy's `.budget` attribute, read by `Run.__init__` into `RunState.deadline`/`cost_limit` — an explicit `timeout` overrides the runtime budget.

6. **`safe_local()` is a curated deny-list.** It composes `require_approval_for` over commands `git push, npm install, pnpm add, rm, curl, wget` and fnmatch files `.github/workflows/**, migrations/**, .env*`. Command matching (`_command_matches`) is whitespace-token **prefix** match — `rule == "*"` means any. `compose` precedence is deny > require_approval > allow (`:208`).

## Confidence & gaps

What I **verified by reading**: the reducer's purity and gate logic, the queue driver's producer/consumer structure, the wire bridges and their sparse-serialization contract, all four schema files, and four golden vectors (`mock_basic`, `evidence`, `approval_reject` plus `timeout`/`redaction` paths by name).

What I **could not** verify from reading alone: I did not *execute* the suite, so I cannot confirm the pump/consumer concurrency is race-free under real load, nor that the ACP permission bridge is truly deadlock-free against a live agent (the design argument is sound; runtime proof needs the Phase-8 e2e). I did not read the Rust core (`src/events.rs`, `src/lib.rs`) to confirm the claimed 1:1 portability, the full `redact_event` body, the agent-authoring side (`python/conduit_sdk/agent.py`), or how `tests/test_conformance.py` actually drives the vectors through `step`. `conductor_adapter` depends on an external `agent-client-protocol-conductor` binary I did not check for on PATH.
