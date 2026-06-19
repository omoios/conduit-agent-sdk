# Implementation Plan — Stage C: bare queue-backed run-loop spike

> Source spec: `docs/superpowers/specs/2026-06-19-agent-coordinator-sdk-queue-prototype-design.md` (§4 Stage C, §6 queue contract, §9 acceptance).
> Lands in `conduit-agent-sdk` as a new `queue/` module + worker + CLIs (locked decision §11). Each phase is self-contained and executable in a fresh context. Verify > assume: copy from the cited locations; do not invent APIs.

---

## Phase 0 — Allowed APIs (documentation discovery output)

**conduit-agent-sdk (`python/conduit_sdk/`), verified against source:**
- `Runner.start(agent: Agent, *, task: str, adapter: Adapter, policy=None, on_approval=None, redaction=None, approval_timeout_s=300.0, on_elicitation=None, elicitation_timeout_s=300.0, timeout: float|None=None, clock=time.monotonic, id_factory=_new_id) -> Run` — `runlayer.py:1074`
- `Agent(name: str, instructions: str = "", policy=None)` — `runlayer.py:37`
- `mock_adapter(script: list, *, source="adapter", redaction_status="none") -> Adapter` — `runlayer.py:104`
- `process_adapter(command: list[str], *, cwd=None, env=None, source="adapter") -> Adapter` — `runlayer.py:473`
- `acp_adapter(client, *, coalesce_messages=False, include_thoughts="summary") -> Adapter` — `runlayer.py:209`
- `Run.events() -> AsyncIterator[AgentEvent]` — `runlayer.py:812`; `Run.result() -> Result` — `:817`; `Run.run_id`, `Run.status()`, `Run.cancel(reason=None)` — `:615,:830`
- `AgentEvent` fields: `id, type, run_id, sequence, timestamp, source, redaction_status, summary?, payload?, version=1, trace_id?, span_id?, workspace_id?, tenant_id?` — `runcore.py:86`
- **Wire bridges for AgentEvent:** `from conduit_sdk.runcore import to_record, from_record` — `runcore.py:601` (camelCase, sparse: `None` omitted)
- **Redaction:** `from conduit_sdk.redaction import redact_patterns, redact_events, RedactionFilter` — `redaction.py:67 / :146 / :50`
- **Terminal types:** `{"run.completed","run.failed","run.cancelled","run.timed_out"}` — `runcore.py:61` / `schema/event_catalog.json`

**redis-py 8.0.0 async (`redis.asyncio.Redis`), verified against installed source `redis/commands/core.py`:**
- `await r.xgroup_create(name, groupname, id="$", mkstream=False)` — `core.py:7180`
- `await r.xadd(name, fields: dict, id="*", maxlen=None, approximate=True, nomkstream=False)` — `core.py:6740`
- `await r.xreadgroup(groupname, consumername, streams: dict, count=None, block=None, noack=False)` — `core.py:7717` (use `">"` for new entries, `"0"` for the consumer's pending)
- `await r.xack(name, groupname, *ids)` — `core.py:6664`
- `await r.xread(streams: dict, count=None, block=None)` — `core.py:7645` (tail only; **not** for groups)
- `await r.xrange(name, min="-", max="+", count=None)` — `core.py:7596`
- `await r.xpending(name, groupname)` — `core.py:7489`; `await r.xautoclaim(name, groupname, consumername, min_idle_time, start_id="0-0", ...)` — `core.py:6938`

**Parity-test pattern to mirror (`agent-coordinator`), verified:**
- Source-of-truth module → generator → frozen artifact → test: `packages/sandbox-runtime/tests/harness/protocol.py` `as_dict()` `:91`, `to_typescript()` `:103`, `__main__ --ts` `:120`; `package.json:17` `protocol:gen`; emitted `src/schemas/bridge-protocol.generated.ts`; vitest `test/bridge-protocol-parity.test.ts:31-73` with `sorted()/set()` helpers `:28`.

**Anti-patterns (DO NOT):**
- `SessionStore.subscribe(...)` **does not exist** — `SessionStore` is `append_update` + `load_updates` (one-shot poll) only (`session_store.py:41`). The `queue/` module is net-new.
- `RedisSessionStore` is **lists/sets** (RPUSH/SADD), **not Streams** (`session_store.py:414`) — do not reuse it as the queue.
- Use `conduit_sdk.runcore.to_record` for `AgentEvent`; **not** `conduit_sdk.events.to_record` (that's for `SessionEvent`) and mind the top-level `conduit_sdk.to_record` re-export — import explicitly from `runcore`.
- `xread` for group consume is wrong — group reads use `xreadgroup`. Redis IDs are **bytes** unless `decode_responses=True`.
- Use the **live** `protocol.py`; the `docs/design/architecture-retrospective/_bridge-harness-zip-variant/protocol.py` copy is stale (no `to_typescript`, missing `file_get_ack`).

---

## Phase 1 — Queue primitives (`conduit_sdk/queue/`)

**Implement:** a transport-agnostic `Queue` Protocol + a `RedisStreamsQueue` impl over `redis.asyncio` (`decode_responses=True`):
- `publish(stream, record: dict) -> str` → `xadd(stream, {"e": json.dumps(record)}, maxlen=…, approximate=True)`
- `consume(group, consumer, stream, block_ms) -> list[(id, record)]` → `xgroup_create(stream, group, mkstream=True)` (ignore BUSYGROUP) then `xreadgroup(group, consumer, {stream: ">"}, count=…, block=block_ms)`
- `ack(stream, group, *ids) -> int` → `xack`
- `subscribe(stream, last_id="$", block_ms) -> list[(id, record)]` → `xread({stream: last_id}, block=block_ms)`
- `replay(stream, start="-", end="+") -> list[(id, record)]` → `xrange`
- `reclaim(stream, group, consumer, min_idle_ms) -> list[(id, record)]` → `xautoclaim`

**Copy from:** the redis-py signatures in Phase 0 (`core.py` lines).
**Verify:** unit test against a real Redis (`docker run -p 6379:6379 redis`): publish 3 → `replay` returns 3 in order; `consume`+`ack` drains; an unacked entry is returned by `reclaim` after `min_idle`.
**Anti-pattern guards:** `xreadgroup("…", ">")` for `consume`, `xread` only for `subscribe`; `decode_responses=True` so IDs/fields are `str`.

## Phase 2 — Wire contract + parity (`conduit_sdk/queue/protocol.py`)

**Implement:** the source-of-truth contract module mirroring `agent-coordinator`'s `protocol.py`:
- `COMMAND_TYPES = {"prompt": ["runId","task","agent?","model?"], "stop": ["runId"], "cancel": ["runId"]}`
- `AGENT_EVENT_KEYS` = the camelCase keys produced by `runcore.to_record` (derive from `runcore._EVENT_FIELD_MAP`, do not hand-copy).
- `as_dict()` + `to_typescript()` + `__main__ --ts` (copy verbatim from `protocol.py:91-126`, change provenance strings + const name to `QUEUE_PROTOCOL`).

**Copy from:** `packages/sandbox-runtime/tests/harness/protocol.py:91-126`; the vitest test `test/bridge-protocol-parity.test.ts:31-73` (this TS test is for **Stage A** to consume the generated module).
**Verify:** a Python test asserts `set(AGENT_EVENT_KEYS) == set(to_record(sample_event).keys() | optional-keys)`; a `Command` round-trips build→validate; running `python -m conduit_sdk.queue.protocol --ts` emits a stable `const QUEUE_PROTOCOL`.
**Anti-pattern guards:** derive `AGENT_EVENT_KEYS` from `_EVENT_FIELD_MAP`, never a hand-maintained list (drift is the exact pain we're removing).

## Phase 3 — The worker (`conduit_sdk/queue/worker.py`)

**Implement:** `consume(commands, group="workers", consumer=<id>)` loop. For each `Command{type:"prompt"}`:
- idempotency: skip if `replay("run:{runId}:events")` is non-empty.
- `run = await Runner.start(Agent(name="worker"), task=cmd["task"], adapter=<mock|process>, redaction=<RedactionFilter>)`
- `async for ev in run.events(): await publish(f"run:{run.run_id}:events", to_record(ev))`
- `ack` the command after the first durable publish (`run.started`).
- crash policy = **fail-fast** for Stage C (the agent died with the worker → on reclaim, publish a synthetic `run.failed` and ack).

**Copy from:** the Phase 0 copy-ready worker snippet (`Runner.start(mock_adapter(...))` + `async for` + `to_record` + terminal check).
**Verify:** worker against `mock_adapter([...])` → the run's event stream contains the scripted events ending in a terminal type; a secret in a payload is `[REDACTED]`; a redelivered identical command produces no second run.
**Anti-pattern guards:** `from conduit_sdk.runcore import to_record`; do not call any `SessionStore` method for transport.

## Phase 4 — CLIs (`conduit_sdk/queue/cli.py`: `enqueue`, `tail`)

**Implement:**
- `enqueue --task "…" [--agent …]` → build `Command`, `publish("commands", cmd)`.
- `tail <run_id>` → `replay("run:{run_id}:events")` then `subscribe` from the last id, render each record, stop on terminal type.

**Verify:** `enqueue` then `tail` reconstructs the full transcript end-to-end.
**Anti-pattern guards:** `tail` uses `subscribe` (xread), not `consume`.

## Phase 5 — Verification (Stage C acceptance)

1. `docker run -p 6379:6379 redis` is the only dependency — **no Cloudflare / Modal / OpenCode**.
2. Start the worker; `enqueue` a mock task; `tail` reconstructs the transcript. ✅ spec §9 Stage-C acceptance.
3. `replay` (XRANGE) reproduces the identical ordered transcript. ✅
4. Kill the worker mid-run, restart; a redelivered command produces **no duplicate run**; the dead run ends `run.failed`. ✅
5. `python -m conduit_sdk.queue.protocol --ts` emits the frozen module; the contract test (Phase 2) is green. ✅
6. Grep guards: no `SessionStore.subscribe`, no `conduit_sdk.events.to_record` in the worker, no `xread(` inside `consume`. ✅
7. Run only the new tests (`pytest tests/queue/`) — do not run the whole suite unless asked.

**Exit → Stage A** (separate plan): the `queue/` module + `QUEUE_PROTOCOL` artifact are frozen; Stage A adds the TS `RedisStreamChannel`/`EventSink`/`SessionStateLog` adapters in `agent-coordinator` against them, fanned out per the spec §10 goal graph.
