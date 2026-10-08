# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""33 — Local ACP Run: end-to-end run-layer demo with a real ACP agent.

Uses ``acp_agent()`` to connect to a local ACP agent (``omp acp`` by default),
streams events as they happen, and prints the final ``Result`` summary.

    uv run python examples/33_acp_local_run.py
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sys

from conduit_sdk import Runner
from conduit_sdk.runlayer import Agent, acp_agent


def _pick_spec() -> list[str] | str | None:
    """Return an ``acp_agent``-compatible *spec*, or ``None`` if unavailable.

    Priority:
    1. ``CONDUIT_E2E_ACP_CMD`` — shlex-split shell command
    2. ``CONDUIT_E2E_ACP_ID`` — raw registry id string
    3. ``["omp", "acp"]`` — if ``omp`` is on PATH
    """
    cmd = os.environ.get("CONDUIT_E2E_ACP_CMD")
    if cmd:
        return shlex.split(cmd)
    rid = os.environ.get("CONDUIT_E2E_ACP_ID")
    if rid:
        return rid
    if shutil.which("omp"):
        return ["omp", "acp"]
    return None


async def main() -> None:
    spec = _pick_spec()
    if spec is None:
        print(
            "No local ACP agent found.\n"
            "\n"
            "To run this demo, install `omp` or set one of:\n"
            "  CONDUIT_E2E_ACP_CMD  shell command, e.g. 'opencode acp'\n"
            "  CONDUIT_E2E_ACP_ID   registry id for Client.from_registry\n"
        )
        sys.exit(0)

    print(f"Starting ACP run via: {spec!r}")
    print("─" * 60)

    # Stream events in real time, then collect the final Result.
    run = await Runner.start(
        Agent(name="demo"),
        task="In one sentence, what is the Agent Client Protocol?",
        adapter=acp_agent(spec, timeout=120),
    )

    async for event in run.events():
        summary = event.summary or ""
        line = f"  {event.type}"
        if summary:
            line += f"  {summary}"
        print(line)

    result = await run.result()

    print("─" * 60)
    print(f"Status:         {result.status}")
    print(f"Final output:   {(result.final_output or '(none)')[:300]}")
    print(f"Changed files:  {result.changed_files or '(none)'}")
    print(f"Tests:          {result.tests or '(none)'}")


if __name__ == "__main__":
    asyncio.run(main())
