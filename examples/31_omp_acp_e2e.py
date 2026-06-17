# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""31 — End-to-end against a real agent: ``omp acp``.

Spawns Oh My Pi in ACP (Agent Client Protocol) mode over stdio and drives the
full client path: connect (initialize handshake) -> new session -> prompt ->
stream -> result. This proves the Rust core + Python wrapper work against a
live ACP server, not just loopback fixtures.

Prerequisites:
  - ``omp`` installed and on PATH (``omp acp`` runs an ACP server over stdio).
  - a working model/provider configured for omp (so it can actually reply).

    CONDUIT_E2E_OMP=1 uv run examples/31_omp_acp_e2e.py
"""

import asyncio
import sys

from conduit_sdk import Client


async def main() -> int:
    async with Client(["omp", "acp"], timeout=60) as client:
        # agent_info is an async property -> await it WITHOUT calling it.
        info = await client.agent_info
        print("--- Connected to omp acp ---")
        if info:
            print(f"  agent: {info.get('name', '?')} v{info.get('version', '?')}")

        session = await client.new_session()
        print(f"  session: {session.session_id}")

        print("--- Prompt ---")
        chunks: list[str] = []
        async for msg in client.prompt(
            "Reply with exactly the word PONG and nothing else."
        ):
            t = msg.text()
            if t:
                chunks.append(t)
                print(f"  chunk: {t!r}")

        text = "".join(chunks).strip()
        print(f"--- Result: {text!r} ---")
        return 0 if "PONG" in text.upper() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
