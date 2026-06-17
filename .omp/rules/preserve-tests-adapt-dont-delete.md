---
name: preserve-tests-adapt-dont-delete
description: "Never delete, skip, or weaken tests/tools to make a change pass — adapt them and preserve coverage."
condition: ["\\b([Dd]ropped?|[Dd]eleting?|[Dd]rops?|[Dd]elete|[Rr]emov(?:ing|es?|ed)?|[Gg]et rid of|cutover|prune)\\b[\\s\\S]{0,60}\\b(tests?|test_|Test[A-Z]\\w*|pytest|asserts?|@tool|class|def|function)", "(@pytest\\.mark\\.(skip|xfail)|@unittest\\.skip|\\b(skip|xfail|comment(?:ing)? out|disable|weaken(?:ed|ing)?|loosen(?:ed)?|relax(?:ed)?)\\b[\\s\\S]{0,50}\\b(tests?|test|assert))"]
scope: ["text", "tool:edit(*.py)"]
---

**Stop: do not delete, drop, disable, or weaken existing tests or tools to make a change compile or pass.** That is exactly the mistake that triggered this rule (dropping `TestHandleMcpRequest`, which encoded real MCP semantics like `isError` on tool failure and multi-server aggregation).

Before removing ANY test, test class, or public tool/function:

1. **Adapt, don't delete.** If an API changed, rewrite the test against the new API so it still asserts the same behavior. A test encoding real semantics (e.g. MCP `isError` on tool failure, error paths, aggregation across servers) is load-bearing — port it, never drop it.
2. **Never delete to get green.** Skipping, `xfail`, commenting out, loosening assertions, or removing a failing test to make the suite pass is forbidden. A test failing because your change altered behavior is a *signal* — fix the root cause (often your change is wrong, e.g. wrong MCP error convention), don't suppress the symptom.
3. **Get agreement before removing.** If a function/behavior is genuinely obsolete, say so plainly to the user and wait for agreement before deleting its tests. No quiet drops during a "clean cutover."
4. **Coverage must not net-shrink.** Count behavioral assertions before and after; if fewer exist after your change, you lost something — restore it.

If you're about to write one of the matched phrases, switch to: port the test/tool to the new API, fix the underlying bug, or ask the user before removing.