"""Capstone e2e: the Background Agent SDK drives `omp acp` to snoop its OWN
codebase and write a markdown findings doc.

This is the SDK running a real, complex local ACP agent (Oh My Pi) end-to-end:
define a task, stream normalized events, route the agent's permission requests
through the run's Policy/approval machinery, capture evidence, and return a
Result — then confirm the agent actually wrote the doc.

Run:  CONDUIT_E2E_ACP=1 uv run python examples/34_sdk_snoops_itself.py
(uses `omp acp`; set CONDUIT_E2E_ACP_CMD to override).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sys

from conduit_sdk.options import AgentOptions
from conduit_sdk.runlayer import Agent, Runner, acp_agent

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DOC = os.path.join(REPO_ROOT, "docs", "sdk-self-snoop.md")

TASK = f"""\
You are auditing the `conduit-agent-sdk` Python repository (your cwd is its root).

Do a real exploration — actually READ files with your tools, do not guess:
1. Read README.md and docs/seed/background-agent-sdk-seed/SOURCE.md for intent.
2. Read the run-layer core: python/conduit_sdk/runcore.py (the pure reducer:
   `step`/`finalize`/`RunState`/`Evidence`/`to_record`) and
   python/conduit_sdk/runlayer.py (the async glue: `Run`/`Runner`,
   `mock_adapter`/`acp_adapter`/`acp_agent`/`process_adapter`/`conductor_adapter`/`query`).
3. Read python/conduit_sdk/policy.py (Policy factories, the approval gate).
4. Skim schema/ (agent_event/result/policy schemas) and tests/vectors/ (golden
   conformance vectors).

Then WRITE a markdown file at docs/sdk-self-snoop.md with:
- A one-paragraph summary of what this SDK is and does.
- An "Architecture" section: the core-vs-glue split, the single-consumer queue
  driver, the pure reducer, and WHY that design (deadlock-free approvals,
  Rust-portable core). Cite the actual files + symbols you read.
- A "Key APIs" section listing the public run-layer surface with one-line each.
- A "Findings" section: 4-6 concrete, specific observations (e.g. how the
  approval gate works, how evidence is collected, the wire contract, anything
  surprising or notable). Quote real symbol names / event types you saw.
- A short "Confidence & gaps" note: what you could NOT verify from reading.

Be specific and grounded in what you actually read — no filler. Keep it under
~120 lines. Write the file, then reply with a one-sentence summary of what you
wrote.
"""


def _resolve_spec() -> list[str] | None:
    cmd = os.environ.get("CONDUIT_E2E_ACP_CMD")
    if cmd:
        return shlex.split(cmd)
    if shutil.which("omp"):
        return ["omp", "acp"]
    return None


async def main() -> int:
    spec = _resolve_spec()
    if spec is None:
        print("No local ACP agent found. Install `omp`, or run with:")
        print("  CONDUIT_E2E_ACP=1 CONDUIT_E2E_ACP_CMD=\"opencode acp\" "
              "uv run python examples/34_sdk_snoops_itself.py")
        return 0
    if os.environ.get("CONDUIT_E2E_ACP") != "1":
        print("Set CONDUIT_E2E_ACP=1 to run the live self-snoop (it spawns `"
              + " ".join(spec) + "` and lets it write to the repo).")
        return 0

    # Clean slate so we can prove the agent wrote it.
    if os.path.exists(OUT_DOC):
        os.remove(OUT_DOC)

    print(f"Launching {' '.join(spec)} to snoop {REPO_ROOT} ...")
    # Programmatic use: coalesce streamed message tokens into one completed
    # message, and drop the agent's reasoning stream entirely (observability
    # only — not evidence). The permission bridge auto-allows tool calls.
    adapter = acp_agent(
        spec,
        options=AgentOptions(cwd=REPO_ROOT),
        timeout=900,
        coalesce_messages=True,
        include_thoughts=False,
    )

    run = await Runner.start(
        Agent(name="snoop", instructions="You are a careful code auditor."),
        task=TASK,
        adapter=adapter,
        approval_timeout_s=600,
    )

    n = 0
    async for ev in run.events():
        n += 1
        tail = ""
        if ev.type in ("tool.started", "tool.completed"):
            tail = f"  [{(ev.payload or {}).get('toolName', '?')}]"
        elif ev.type in ("agent.message.delta", "agent.message.completed"):
            txt = (ev.payload or {}).get("text", "")
            tail = f"  {txt[:80]!r}"
        elif ev.type == "policy.blocked":
            tail = f"  -> {(ev.payload or {}).get('reason')}"
        print(f"  {n:>3} {ev.type}{tail}")

    result = await run.result()
    print("\n" + "=" * 60)
    print(f"status        : {result.status}")
    print(f"event_count   : {result.event_count}")
    print(f"final_output  : {(result.final_output or '')[:200]!r}")
    print(f"changed_files : {result.changed_files}")
    print(f"diff (bytes)  : {len(result.diff or '')}")
    if result.error:
        print(f"error         : {result.error}")

    written = os.path.exists(OUT_DOC)
    print(f"\ndocs/sdk-self-snoop.md written: {written}")
    if written:
        size = os.path.getsize(OUT_DOC)
        print(f"  size: {size} bytes")
        with open(OUT_DOC) as fh:
            head = fh.read()[:400]
        print("  head:\n" + head)
    return 0 if (result.status == "completed" and written) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
