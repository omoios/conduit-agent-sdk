# Design: Canonical Event Model & SDK Hardening

> **Status:** Draft v2 — design proposal (precursor to the background-agent-sdk plan files).
> Revised after an oracle pressure-test and a 4-dimension verification fan-out
> (code-grounding / design-soundness / pragmatic-SOLID / completeness); findings folded in.
> **Scope:** Make the *current* `conduit-agent-sdk` solid **before** the later phase that
> moves logic into Rust and adds a napi-rs (Node/TS) binding. Nothing here assumes that
> move; the event schema is *shaped* so it survives it.
> **Method:** pragmatic-SOLID — introduce an abstraction only at a seam with ≥2 real
> implementations **or** a fault to isolate and test; compose everywhere else.
> Line numbers are approximate (`~`) — treat the file/function as authoritative.

## 1. Summary

The SDK's core weakness is a **leaky Rust→Python boundary**: the Rust core hands Python a
flat 18-field `SessionUpdate` (`kind` + 17 optionals) whose payloads are raw JSON strings
and whose enums are `Debug`-formatted (`"EndTurn"`, `"Read"`, `"Pending"`). Four distinct
locations re-parse this; **one of them (`acp_adapter`) is buggy**, and a **separate
prompt-pipeline divergence** bug also rides on the same boundary. The leak is persisted
verbatim into the SessionStore. This document proposes:

1. **A canonical typed `SessionEvent` discriminated union** + a single pure, **total**
   `normalize(SessionUpdate) -> SessionEvent` seam, with an explicit **serialized JSON
   form** (the keystone).
2. **Unifying the prompt pipeline** so `prompt`/`prompt_sync`/`prompt_stream` share one path
   (fixes a real side-effect divergence bug).
3. **Typed `AgentContext` emit helpers** so agent authors stop hand-writing wire dicts.
4. Keeping the **earned ports** (`SessionStore`, `Adapter`) — adding the missing
   `Adapter` contract test and fixing their record/parse internals.
5. **Source bugs** to fix alongside (enum repr; tool status+output loss; terminal `Done`).
6. **Redaction-before-storage**, made real (currently bypassed on the persistence path).

Explicitly **out of scope this phase** (YAGNI): a Python transport/backend interface, a
unified permission+elicitation+hooks handler, and promoting the full typed surface into Rust.

## 2. The problem (grounded in the code)

### 2.1 The boundary leak
- `SessionUpdate` (`_conduit_sdk.pyi:103-121`) is flat: `kind` + 17 optionals, almost all
  `None` for any given event — illegal states are representable (a `TextDelta` still exposes
  `plan_json`, `tool_locations`, …).
- Payloads cross as **raw JSON strings**. `tool_input` is `raw_input.to_string()` (Display
  trait, `src/client.rs:~284`); `tool_content`/`plan_json`/`config_json`/`usage_json`/
  `session_info_json` are `serde_json::to_string(...)` (`~304,329,350,357,378`) — a
  deserialize→re-serialize→re-parse round-trip that is pure waste.
- Enums cross **`Debug`-formatted**: `format!("{:?}", …)` at `src/client.rs:~289,290,301,1585`
  → `"Read"`, `"Pending"`, `"Completed"`, `"EndTurn"`. The underlying values are real ACP
  enums (`ToolKind`/`ToolCallStatus`/`StopReason`); the **same file already emits wire
  strings via serde** (`~372-378`). So this is a **bug, not a design choice** — the wire form
  (`"read"`, `"pending"`, `"end_turn"`) is one serde call away.

### 2.2 Four locations re-parse the leak
Three pipeline stages + two helper functions, four chances to drift:
1. `toolview.tool_output_text` (json.loads, `~90-101`) + `parse_tool_input` (`~102-114`)
2. `acp_adapter` inline `json.loads` (`runlayer.py:~314,335,346`)
3. `client._record_update` (`client.py:~401-410`) — persists `str(update.kind)` →
   `"UpdateKind.ToolUseStart"` and copies Debug enum strings into the SessionStore.
4. `RateLimitInfo.from_json` (`types.py:~377-392`)

The leak shows up in tests as apologies (`test_sessionstore_e2e.py` comments that the
client "Debug-formats these ACP enum values"; asserts `"UpdateKind.ToolUseStart"` and
`stop_reason == "EndTurn"`).

### 2.3 Bugs riding on the boundary
- **`acp_adapter` always reports `ok=True`.** It derives success from
  `tool_status == "error"/"success"` (`runlayer.py:~321-330`), but `ToolUseEnd` carries
  **only** `tool_use_id` (`src/client.rs:~319-327`, mapped `~1166-1170`), and statuses are
  Debug strings (`"Completed"`/`"Failed"`) anyway. **Failed tools look successful.**
- **Tool OUTPUT never reaches the run-layer stream.** `tool_content` is attached only to
  `ToolUseUpdate`, which `acp_adapter` folds into the generic `agent.update` branch that
  **drops** it (`runlayer.py:~363-374`); the `tool.completed` branch reads `tool_content`
  off `ToolUseEnd`, which is always `None`. So the Run/AgentEvent stream contains no tool
  output at all (sibling of the `ok=True` bug, same region).
- **Prompt-path side-effect divergence.** Only `prompt_stream` fires
  `PreToolUse`/`PostToolUse`/`Stop` hooks and records to the SessionStore
  (`client.py:~363-382`). Batch `prompt()`/`prompt_sync()` fire **none** and persist
  **nothing** (`src/client.rs:~952-981` drains+discards non-text in Rust). Whether your
  hooks run and your session persists silently depends on *which prompt method* the caller
  chose — a latent correctness/data-loss bug.

## 3. Design principles

- **One seam, earned by a present fault.** The Rust→Python boundary has a real, testable
  fault (the parse). It gets one formalized model + one pure function. The justification is
  the *present* fault and "make illegal states unrepresentable"; cross-language reuse is a
  **shaping constraint** on the schema, not the reason to build it (keeps §9's YAGNI
  reasoning consistent).
- **Make illegal states unrepresentable.** A discriminated union, not a god-struct.
- **Total + pure.** `normalize()` never raises and handles unknown values; effects (the Rust
  call) stay at the edge.
- **The serialized schema is the artifact.** The union's JSON encoding — discriminator +
  fields + wire-string enums — is what persists, replays, and ports to Rust/TS.

## 4. Keystone: the canonical `SessionEvent` model

### 4.1 A typed discriminated union (new module `python/conduit_sdk/events.py`)
Each ACP update kind becomes a frozen dataclass with **parsed** payloads and **real enums**.
Fields below are derived from what the Rust boundary actually emits (corrected per review):

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

class ToolKind(str, Enum):
    READ="read"; EDIT="edit"; DELETE="delete"; MOVE="move"; SEARCH="search"
    EXECUTE="execute"; THINK="think"; FETCH="fetch"; SWITCH_MODE="switch_mode"; OTHER="other"
class ToolStatus(str, Enum):
    PENDING="pending"; IN_PROGRESS="in_progress"; COMPLETED="completed"; FAILED="failed"
class StopReason(str, Enum):
    END_TURN="end_turn"; MAX_TOKENS="max_tokens"; MAX_TURN_REQUESTS="max_turn_requests"
    REFUSAL="refusal"; CANCELLED="cancelled"

@dataclass(frozen=True)
class TextDelta:        text: str
@dataclass(frozen=True)
class ThoughtDelta:     text: str
@dataclass(frozen=True)
class ToolCallStart:
    id: str; title: str; kind: ToolKind
    input: Any                              # raw_input is ANY JSON value (obj/array/scalar)
    status: ToolStatus = ToolStatus.PENDING
@dataclass(frozen=True)
class ToolCallUpdate:                       # carries progress AND the terminal transition
    id: str
    status: ToolStatus | None = None        # completed/failed => terminal
    output: str | None = None               # decoded text of the content blocks
    raw_content: list | None = None         # structured blocks, if a consumer needs them
    locations: list | None = None
@dataclass(frozen=True)
class Plan:             entries: list
@dataclass(frozen=True)
class AvailableCommands: commands: list
@dataclass(frozen=True)
class ModeChange:      mode_id: str
@dataclass(frozen=True)
class ConfigUpdate:    config: dict
@dataclass(frozen=True)
class Usage:                               # matches the real UsageUpdate payload
    used: int | None = None; size: int | None = None
    cost_amount: float | None = None; cost_currency: str | None = None
@dataclass(frozen=True)
class SessionInfo:     info: dict
@dataclass(frozen=True)
class RateLimit:                           # typed so normalize() truly owns the parse
    status: str | None = None; resets_at: str | None = None
    rate_limit_type: str | None = None; utilization: float | None = None
    is_using_overage: bool | None = None; surpassed_threshold: bool | None = None
@dataclass(frozen=True)
class Done:            stop_reason: StopReason
@dataclass(frozen=True)
class Unknown:         kind: str; raw: dict   # forward-compat catch-all (future ACP kinds)

SessionEvent = (
    TextDelta | ThoughtDelta | ToolCallStart | ToolCallUpdate | Plan | AvailableCommands
    | ModeChange | ConfigUpdate | Usage | SessionInfo | RateLimit | Done | Unknown
)
```

Schema decisions resolving review findings:
- **No `ToolCallEnd`.** A terminal ACP `tool_call_update` currently makes Rust emit *both*
  a `ToolUseUpdate` (with status+content) *and* a synthetic `ToolUseEnd` (id-only) — a
  double-emit that would yield two terminal `SessionEvent`s. **Collapse it** (step 1, Rust):
  a terminal update produces one `ToolUseUpdate` carrying `status`+`content`; `normalize`
  maps any residual `ToolUseEnd` to a `ToolCallUpdate` with no new info. The terminal status
  **and** output live on the same event. The tool **name** is not on the terminal event;
  the consumer (adapter) correlates it from `ToolCallStart` via `id` (§4.3).
- **No `Error` variant.** Prompt/agent failures surface as `ConduitError` **exceptions**
  through the reply channels (`src/client.rs:~987,1201-1206`), never as a `session/update`.
  `normalize()` therefore has no Error branch; the run-layer `run.failed` event comes from
  the **adapter catching the exception** (§4.3), a path orthogonal to `normalize()`.
- **`Unknown(kind, raw)`** preserves today's catch-all (Rust ignores unknown ACP variants;
  `acp_adapter` has an `else → agent.update`) so a future 15th kind is never dropped or
  raised — required for both totality and the Rust port's exhaustive match + serde
  `unknown-variant` tolerance.

### 4.2 The single, total parse owner + its serialized form
```python
def normalize(update: SessionUpdate) -> SessionEvent: ...        # pure, total, never raises
def to_record(event: SessionEvent) -> dict: ...                  # JSON-safe canonical form
def from_record(record: dict) -> SessionEvent: ...               # replay
```
- **Totality:** wrap every `json.loads` with a raw-string fallback; look up enums via a safe
  map defaulting to `OTHER`/`None`; route any unmapped `kind` to `Unknown`. Unit-tested as a
  table (each Rust `SessionUpdate` shape → expected `SessionEvent`), including malformed
  payloads and unknown enum/kind values.
- **`to_record`/`from_record` define the canonical JSON shape** — `{"event": "tool_call_start",
  "id": …, "kind": "read", "input": …, …}` (discriminator key + fields + enums as wire
  strings). This is the persisted/replayed form **and** the language-agnostic contract §12
  invokes; it is **first-class work in step 2**, not a step-6 afterthought. Every store
  backend already `json.dumps` a dict (`session_store.py:~44,156,308,417`), so the store
  persists `to_record(event)`.
- **All four re-parse sites route through `normalize()`** and consume `SessionEvent`:
  `toolview.collect_tool_calls`, `acp_adapter`, `client._record_update` (persists
  `to_record`), and the `RateLimit` variant subsumes `RateLimitInfo.from_json`.
  `tool_output_text`/`parse_tool_input` become internal helpers of `normalize()` (removed
  from the public API).

### 4.3 Relationship to `runlayer.AgentEvent` — keep them DISTINCT
`AgentEvent` (`runlayer.py:~64-80`) is a **run-scoped audit envelope** (`id`, `type` catalog
string, `run_id`, `sequence`, `timestamp`, `source`, `redaction_status`, untyped `payload`).
It answers a different question than the content union.
- `AgentEvent` **wraps** a `SessionEvent`: its `payload` carries `to_record(event)` (a
  JSON-safe dict — never a frozen dataclass, so redaction can walk it; see §7).
- `acp_adapter` shrinks to: `normalize()` → `to_record` into `payload` → **map variant →
  catalog string**. That mapping is a **contract** (AGENTS.md rule 3 ties catalog names to
  `docs/seed/.../EVENTS.md` + the event-type union), so the doc must ship the explicit table:

  | SessionEvent | run-layer catalog `type` |
  |---|---|
  | `TextDelta` | `agent.message.delta` |
  | `ThoughtDelta` | `agent.thought_summary` |
  | `ToolCallStart` | `tool.started` |
  | `ToolCallUpdate` (non-terminal) | `tool.progress` *(new — or fold to `agent.update`)* |
  | `ToolCallUpdate` (terminal) | `tool.completed` (with `ok` from status, `output`, and `toolName` correlated by `id`) |
  | `Plan`/`AvailableCommands`/`ModeChange`/`ConfigUpdate`/`SessionInfo`/`RateLimit`/`Usage` | `agent.update` (or named events) |
  | `Done` | `run.completed` |
  | *(adapter catches `ConduitError`)* | `run.failed` |
  | `Unknown` | `agent.update` |

  Introducing any **new** catalog name (e.g. `tool.progress`) requires the matching
  EVENTS.md + type-union edits — call out which, if any, in step 4. **`tool.completed` must
  carry the output and `ok` derived from the terminal status** (fixes §2.3), and the adapter
  must keep an `id → title` map populated at `ToolCallStart` to fill `toolName`.

### 4.4 `prompt_stream` yields the union
`prompt_stream` flips from yielding `SessionUpdate` to yielding `SessionEvent`.
`SessionUpdate` remains **only** as the internal Rust-boundary DTO `normalize()` consumes.
Clean cutover (pre-1.0, no shims); migrate consumers (§11).

## 5. Unify the prompt pipeline

Make the streaming generator canonical; `prompt`/`prompt_sync` collect the `SessionEvent`
stream and fold it into **exactly one** assistant `Message` per turn (ACP has no intra-turn
message boundary, so "Message(s)" is one Message). Details the review surfaced:
- **`stop_reason` home:** derive `Message.stop_reason` from the terminal `Done`. A turn with
  no text still needs a terminal — see the `Done` fix in §8: Rust must **always** emit a
  terminal `Done` (today `recv_update` returns `Done` only when `stop_reason.is_some()`,
  else `Ok(None)`, and raises on failure — so the collector can otherwise get no `Done`).
  Until that lands, the collector synthesizes a `Done(stop_reason=None→?)` on stream end.
- **Thoughts:** preserve current precedence (thoughts only when no assistant text) and do
  **not** fold chain-of-thought into `Message.text()` by default (AGENTS.md rule 5).
- Tool/`Stop` hooks and SessionStore persistence then fire on **all** prompt methods (fixes
  §2.3 — a **behavior change** to document: batch callers now fire hooks + persist).
- The Rust batch `prompt()` (`src/client.rs:~871-1009`) becomes dead; remove it (no shim).
- **Sequenced after the union (§4).**

## 6. Typed `AgentContext` emit helpers

Add `ctx.tool_call(...)`, `ctx.tool_result(...)`, `ctx.plan(...)`, `ctx.usage(...)`,
`ctx.mode_change(...)` — mirroring `send_text`/`send_thought` — so agent authors stop
hand-building camelCase wire dicts (`agent.py:~65-66,99-111`). Keep `send_update` as the
documented escape hatch (purely additive). **Not a port** — concrete convenience methods
(Open-Closed). Note the *three distinct shapes* in play (not "one schema"): emit helpers
produce the **ACP camelCase wire** dict → the Rust core restructures it into the snake_case
**`SessionUpdate` DTO** → `normalize()` produces the **`SessionEvent`**. Keep field names
analogous across them for legibility, but they are joined by mappings, not shared.

## 7. Redaction before storage (made real)

Today redaction is an opt-in **run-layer** proxy (`redact_events` over `AgentEvent`,
`redaction.py:~131-187`); the **persistence path bypasses it entirely** — `prompt_stream`
calls `store.append_update(_record_update(update))` directly (`client.py:~363-364`), and
`_deep_scrub` only walks `str/dict/list` (a typed dataclass would pass through unscrubbed).
Persisting richer **parsed** tool input/output without redaction *increases* secret exposure
— against EVENTS.md ("redaction occurs before event storage") and AGENTS.md rule 6.

Design: define `redact(record: dict) -> dict` over the **`to_record` JSON form** (scrubbing
`text`, tool `input`, tool `output`/`raw_content`) and apply it **once, before any sink** —
the prompt pipeline runs `event → to_record → redact → {persist, yield/AgentEvent}`. This
makes the store and the run layer share one redaction stage instead of leaving persistence
uncovered. This is **first-class in the build order**, not a checklist line.

## 8. Source bugs to fix alongside (Rust, step 1)

1. **Enum repr** — replace `format!("{:?}", …)` (`src/client.rs:~289,290,301,1585`) with
   serde wire serialization (pattern at `~372-378`). ~20 lines; benefits Python **and** the
   future napi binding.
2. **Tool status + output on the terminal event** — collapse the `ToolUseUpdate`+`ToolUseEnd`
   double-emit (`~319-327`) so the terminal update carries `status` **and** `content`; this
   fixes both the `ok=True` bug and the missing-output bug (§2.3).
3. **Always emit a terminal `Done`** (`~1209-1217,1585`) with `stop_reason: Option`, so the
   unified collector and the `Stop` hook are uniform across normal/empty/error turns.
4. **Tests assert on these bugs** — the fix breaks `test_sessionstore_e2e.py` (enum values
   *and* the `"UpdateKind.*"` discriminator assertions), `test_streaming_e2e.py:~91`,
   `test_runlayer_e2e.py:~51-56` (`ok is True`). Update them to wire format / correct
   behavior in the same change; **do not "revert the regression."**

## 9. Explicitly NOT this phase (YAGNI)

- **A Python transport/backend interface "for napi-rs."** One backend exists; the `Adapter`
  protocol already provides the seam at the right layer. napi-rs is a *binding* of the same
  Rust core, not a new Python transport — the interface buys the move nothing. Reject.
- **A unified permission+elicitation+hooks handler.** Three structurally different
  mechanisms (gate / request-response / fire-and-forget observer); folding them is an
  ISP-violating god-interface. Align *registration ergonomics* only.
- **Promoting the full typed surface into Rust now.** Only the §8 source fixes belong in
  Rust this phase; the typed union stays in Python until the napi phase.

## 10. Build order

| # | Effort | Layer | Work | Depends on |
|---|--------|-------|------|-----------|
| 1 | Short | Rust | §8 fixes (enum repr; terminal status+output; always-emit `Done`). Update the asserting tests. | — (∥ step 5) |
| 2 | Medium | Python | Define `SessionEvent` union + total `normalize()` **+ `to_record`/`from_record`** (the JSON contract); unit-test in isolation incl. malformed/unknown. **Design crux.** | 1 |
| 3 | Short | Python | Route the 4 re-parse sites through `normalize()`; persist `to_record` (not Debug strings). | 2 |
| 4 | Medium | Python | Unify the prompt pipeline; `prompt_stream` yields the union; hooks+store on all paths; **redaction-before-storage stage** (§7); remove dead Rust batch `prompt()`; rewrite `acp_adapter` to the §4.3 catalog table (+ `id→title` map; `ok`/output on `tool.completed`) and add the **`AdapterContract`** test (§7-style shared contract run against mock + acp). | 2,3 |
| 5 | Short | Python | Typed `AgentContext` emit helpers; keep `send_update`. | — (∥ step 1) |
| 6 | Short | Cleanup | Migrate all consumers (§11); finalize SessionStore record shape; update `api-reference.md` + EVENTS.md (any new catalog names); confirm redaction on typed payloads. | 1–5 green |

## 11. Migration inventory & risks

**Public-API breaks (cutover, no shims):**
- `prompt_stream` yield type `SessionUpdate` → `SessionEvent` (the headline break).
- Exported symbols affected (`conduit_sdk/__init__.py __all__`): `collect_tool_calls`
  (now consumes `SessionEvent`), `tool_output_text` + `parse_tool_input` (removed from
  public API), `RateLimitInfo.from_json` (subsumed by the `RateLimit` variant),
  `observe_turn` (return shape unchanged but internals re-routed).

**Tests to migrate (full set, separated by cause):**
- *Enum-value* breakage: `test_sessionstore_e2e.py` (`Read`/`Pending`/`Completed`/`EndTurn`),
  `test_streaming_e2e.py:~91`.
- *Discriminator/record-shape* breakage: `test_sessionstore_e2e.py` `kind ==
  "UpdateKind.ToolUseStart"/"ToolUseUpdate"/"Done"` lines.
- *Stub/mapping* breakage: `test_toolview.py` (builds `SessionUpdate` stubs, asserts
  `collect_tool_calls` output), `test_runlayer.py` (`_StubUpdate` + `TestAcpNormalization`
  encoding the kind→catalog map), `test_runlayer_e2e.py:~51-56` (`ok is True`).

**Examples to migrate:** `23_streaming_updates.py`, `29_skill_activation.py`,
`30_integration_skills.py`, `31_comprehensive_demo.py` (two `.kind` sites), and
`28_rate_limit_awareness.py` — which is **already broken** (it `await`s the
`prompt_stream` async-generator and calls `client.recv_update()`, which doesn't exist on
`Client`) and consumes `RateLimitInfo.from_json`; fix or remove it in the cutover.

**Other risks:** schema-design risk concentrates in step 2 (variant boundaries become the
napi contract — spend the effort there); step 1 changes live enum strings consumed by
`toolview`/`_record_update`, so add **unit** coverage (stubs hide it from unit tests today,
only e2e catches it); and normalizing in Python does **not** remove the Rust
serialize→parse double round-trip — no perf win until the Rust move (don't claim it).

## 12. Why this de-risks the Rust / napi-rs move

The **serialized `SessionEvent` schema** (`to_record`'s JSON shape) is the language-agnostic
event contract: a Python tagged union ≈ a Rust `enum` (with serde `unknown-variant`
tolerance ↔ the `Unknown` arm) ≈ a TS discriminated union. Specifying it now — with a pure,
portable `normalize()` as the executable reference and the §8 enum-repr fix making it correct
on both bindings — means the napi phase ports a *specified, tested* contract instead of
reverse-engineering one from a flat struct. This is a shaping constraint on a schema the
*present* parse fault already earns — not a reason to build Rust now (§9).
