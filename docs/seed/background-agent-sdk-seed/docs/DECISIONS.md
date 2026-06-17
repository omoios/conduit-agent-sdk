# Decisions and Alternatives

This file records the core design decisions for the Background Agent SDK seed.

---

## Decision 1: The core object is `Run`, not `Chat`

Background coding agents do work over time. They execute tools, run commands, create diffs, need approvals, and produce artifacts.

A chat transcript is not enough.

Decision: public SDK centers on `Run` and `RunResult`.

---

## Decision 2: ACP is an adapter, not the public API

ACP is valuable for interoperability, but raw JSON-RPC methods are not the intended developer experience.

Decision: expose `adapters.acp()`, not raw `session/prompt` calls.

---

## Decision 3: Support both Agent/Runner and query APIs

OpenAI-style `Agent`/`Runner` APIs are good for orchestration. Claude-style `query()` streams are good for scripts and CLIs.

Decision: expose both over the same runtime core.

---

## Decision 4: Events are standardized from day one

UI, tests, webhooks, audit logs, and result collection all depend on events.

Decision: define the event envelope and event catalog before real adapters.

---

## Decision 5: Mock adapter first

Real agents introduce model variability, process failures, environment issues, and external dependencies.

Decision: implement and test `adapters.mock()` first.

---

## Decision 6: Policy is structured

Prompts cannot reliably enforce secrets, approvals, budgets, or file restrictions.

Decision: policy must be structured, testable, and available to adapters/proxies.

---

## Decision 7: Conductor is optional middleware

Conductor/proxy chains are powerful, but they should not be required for the smallest SDK.

Decision: implement conductor after direct mock/process/ACP paths work.

---

## Decision 8: Pi is an inner harness, not the authority

Pi can own the coding loop, tools, hooks, skills, and session mechanics. The platform/SDK owns run policy, secrets, events, and results.

Decision: use a Pi wrapper when integrating Pi. Do not let Pi randomly discover production context.

---

## Decision 9: No model gateway at the start

A model gateway may be useful later for spend control, routing, failover, billing, and per-run model tokens.

Decision: do not require a model gateway for v0/v1. Use scoped direct provider credentials only when real model integration begins.

---

## Decision 10: Every phase must be demoable

Large background-agent systems fail when too many layers are built before proof.

Decision: each phase must have an objective passing result.

---

## Decision 11: Avoid production auto-discovery

Auto-discovery is convenient locally but risky in multi-tenant sandboxes.

Decision: production runs compile explicit tools, hooks, context files, skills, slash commands, and policies.

---

## Decision 12: Keep raw events optional

Raw adapter events are useful for debugging.

Decision: store or expose raw events only behind debug options. Standard events are the default user-facing surface.
